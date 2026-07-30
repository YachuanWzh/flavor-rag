"""Knowledge base CRUD API + document upload + URL source."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import traceback
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.auth.jwt import decode_access_token
from app.audit.middleware import get_audit_context
from app.audit.service import record_audit
from app.models import (
    User,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
    BatchImportJob,
    BatchImportFileRecord,
    gen_id,
)
from app.ingestion.chunker import ChunkConfig, ChunkStrategy
from app.ingestion.pipeline_engine import IngestionEngine
from app.ingestion.url_fetcher import SafeURLFetcher, URLSecurityError
from app.ingestion.dedup import DuplicateDetector, compute_content_hash
from app.ingestion.upload_validation import UploadValidationError
from app.rag.search.vector import MilvusSearchChannel
from app.services.ingestion_executor import execute_ingestion
from app.services.ingestion_jobs import enqueue_ingestion_job
from app.config.settings import settings
from app.config.logging_config import get_logger
from app.security.access import Permission
from app.security.service import (
    document_access_predicate,
    kb_access_predicate,
    principal_from_user,
    require_document,
    require_kb,
)

_SUPPORTED_FILE_TYPES = {
    "txt", "md", "pdf", "docx", "xlsx", "csv", "pptx", "html", "htm",
    "png", "jpg", "jpeg", "webp",
}

router = APIRouter(prefix="/api/knowledge-base", tags=["knowledge-base"])
_log = get_logger("flavorag.api.knowledge")

# Persistent storage for uploaded files (survives restarts)
_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
os.makedirs(_UPLOAD_DIR, exist_ok=True)


class URLUploadRequest(BaseModel):
    url: str = Field(..., description="文档URL地址")
    doc_name: str | None = Field(None, description="文档名称（可选，默认从URL提取）")
    schedule_enabled: bool = Field(False, description="是否启用定时刷新")
    schedule_cron: str | None = Field(None, description="Cron表达式（暂用间隔秒数）")


class ChunkStatusUpdate(BaseModel):
    enabled: bool = Field(..., description="是否允许该切片参与检索")


# ---- Knowledge Base CRUD ----


@router.get("")
async def list_knowledge_bases(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(KnowledgeBase).where(
            kb_access_predicate(principal_from_user(user), Permission.READ)
        )
    )
    kbs = result.scalars().all()
    return {
        "code": "0",
        "message": "success",
        "data": [
            {
                "id": kb.id,
                "name": kb.name,
                "embeddingModel": kb.embedding_model,
                "collectionName": kb.collection_name,
                "pipelineId": kb.pipeline_id,
                "createTime": str(kb.create_time),
            }
            for kb in kbs
        ],
    }


@router.post("")
async def create_knowledge_base(
    name: str = Form(...),
    embedding_model: str | None = Form(None),
    pipeline_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.llm.embedding import normalize_embedding_model

    embedding_model = normalize_embedding_model(embedding_model)
    collection_name = f"kb_{gen_id()}"

    kb = KnowledgeBase(
        name=name,
        embedding_model=embedding_model,
        collection_name=collection_name,
        active_collection_name=collection_name,
        active_index_generation="v1",
        pipeline_id=pipeline_id or None,
        tenant_id=user.tenant_id or "default",
        department_id=user.department_id,
        created_by=user.id,
    )
    db.add(kb)
    await db.flush()

    # Resolve the actual model dimension before creating the first immutable
    # index generation; configuration guesses must not define the schema.
    try:
        from app.llm.embedding import get_embedding_client
        from app.models import KnowledgeIndexGeneration

        probe = await get_embedding_client(
            model=embedding_model
        ).embed_query("dimension probe")
        embedding_dim = len(probe)
        milvus = MilvusSearchChannel()
        milvus.create_collection(collection_name, dim=embedding_dim)
        db.add(
            KnowledgeIndexGeneration(
                kb_id=kb.id,
                generation="v1",
                collection_name=collection_name,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
                parser_version="v0.0.5",
                chunker_version="v0.0.5",
                status="ACTIVE",
                expected_chunks=0,
                indexed_chunks=0,
                activated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                created_by=user.id,
            )
        )
    except Exception as e:
        # Rollback PG if Milvus creation fails
        raise HTTPException(status_code=500, detail=f"Failed to create Milvus collection: {e}")

    # Audit log
    ctx = get_audit_context()
    await record_audit(
        biz_type="knowledge_base",
        biz_id=kb.id,
        operation_type="CREATE",
        action_desc=f"创建知识库: {name}",
        after_snapshot={"name": kb.name, "collectionName": kb.collection_name},
        operator_id=ctx.get("operator_id"),
        operator_name=ctx.get("operator_name"),
        operator_role=ctx.get("operator_role"),
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent"),
        db=db,
    )

    return {
        "code": "0",
        "message": "success",
        "data": {"id": kb.id, "name": kb.name, "collectionName": kb.collection_name},
    }


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.ADMIN
    )
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    before_snapshot = {"name": kb.name, "collectionName": kb.collection_name}
    kb.deleted = 1

    from app.models import KnowledgeAsset
    from app.services.index_sync import IndexSyncService

    documents = list(
        (
            await db.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.kb_id == kb_id,
                    KnowledgeDocument.deleted == 0,
                )
            )
        ).scalars().all()
    )
    for document in documents:
        chunks = list(
            (
                await db.execute(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.doc_id == document.id,
                        KnowledgeChunk.deleted == 0,
                    )
                )
            ).scalars().all()
        )
        assets = list(
            (
                await db.execute(
                    select(KnowledgeAsset).where(
                        KnowledgeAsset.doc_id == document.id,
                        KnowledgeAsset.deleted == 0,
                    )
                )
            ).scalars().all()
        )
        document.deleted = 1
        for chunk in chunks:
            chunk.deleted = 1
        for asset in assets:
            asset.deleted = 1
        await IndexSyncService().delete_document(
            db,
            kb=kb,
            doc_id=document.id,
            chunks=chunks,
            assets=assets,
            source_location=document.file_url,
        )

    # Drop Milvus collection
    try:
        milvus = MilvusSearchChannel()
        milvus.drop_collection(kb.active_collection_name or kb.collection_name)
    except Exception as exc:
        _log.warning(
            "kb_collection_drop_failed",
            kb_id=kb_id,
            error=str(exc),
        )

    # Audit log
    ctx = get_audit_context()
    await record_audit(
        biz_type="knowledge_base",
        biz_id=kb_id,
        operation_type="DELETE",
        action_desc=f"删除知识库: {kb.name}",
        before_snapshot=before_snapshot,
        operator_id=ctx.get("operator_id"),
        operator_name=ctx.get("operator_name"),
        operator_role=ctx.get("operator_role"),
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent"),
        db=db,
    )

    return {"code": "0", "message": "success", "data": None}


# ---- Document Management ----


@router.get("/{kb_id}/docs")
async def list_documents(
    kb_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    principal = principal_from_user(user)
    await require_kb(db, principal, kb_id, Permission.READ)
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.kb_id == kb_id,
            document_access_predicate(principal, Permission.READ),
        )
    )
    docs = result.scalars().all()
    return {
        "code": "0",
        "message": "success",
        "data": [
            {
                "id": doc.id,
                "docName": doc.doc_name,
                "fileType": doc.file_type,
                "fileSize": doc.file_size,
                "chunkCount": doc.chunk_count,
                "status": doc.status,
                "createTime": str(doc.create_time),
            }
            for doc in docs
        ],
    }


@router.post("/{kb_id}/docs/upload")
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    chunk_strategy: str = Form("FIXED_WINDOW"),
    chunk_size: int = Form(512),
    overlap: int = Form(128),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.WRITE
    )
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")

    try:
        canonical_strategy = ChunkStrategy.from_value(chunk_strategy).name
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Determine file type
    ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
    if ext not in _SUPPORTED_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: .{ext}，支持: {', '.join(sorted(_SUPPORTED_FILE_TYPES))}",
        )

    # Save uploaded file to persistent uploads directory
    doc_id = gen_id()
    file_path = os.path.join(_UPLOAD_DIR, f"{doc_id}.{ext}")
    doc = None
    try:
        from app.ingestion.upload_validation import save_upload_bounded

        await asyncio.to_thread(
            save_upload_bounded,
            file,
            file_path,
            max_bytes=settings.upload_max_bytes,
            max_pdf_pages=settings.upload_max_pdf_pages,
            max_uncompressed_bytes=settings.archive_max_uncompressed_bytes,
            max_archive_entries=settings.archive_max_entries,
            max_compression_ratio=settings.archive_max_compression_ratio,
            max_image_pixels=settings.upload_max_image_pixels,
        )

        # Content hash for dedup and incremental indexing
        content_hash = compute_content_hash(file_path)

        # Duplicate check
        dedup = DuplicateDetector()
        dup_result = await dedup.check_file(
            file_path, kb_id, db, tenant_id=user.tenant_id or "default"
        )
        if dup_result.is_duplicate:
            # Remove the saved file
            try:
                os.remove(file_path)
            except Exception:
                pass
            return {
                "code": "0",
                "message": "duplicate",
                "data": {
                    "isDuplicate": True,
                    "existingDocId": dup_result.existing_doc_id,
                    "existingDocName": dup_result.existing_doc_name,
                },
            }

        # Create document record
        doc = KnowledgeDocument(
            id=doc_id,
            kb_id=kb_id,
            tenant_id=kb.tenant_id,
            department_id=kb.department_id,
            doc_name=file.filename,
            file_url=file_path,
            file_type=ext,
            file_size=os.path.getsize(file_path),
            content_hash=content_hash,
            chunk_strategy=canonical_strategy,
            chunk_config={
                "chunkSize": chunk_size,
                "overlapSize": overlap,
            },
            status="running",
            created_by=user.id,
        )
        db.add(doc)
        await db.flush()

        # Ingestion: enqueue to the outbox worker, or run inline when disabled
        chunk_config = ChunkConfig(
            strategy=canonical_strategy,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        if settings.ingestion_async_enabled:
            await enqueue_ingestion_job(
                db,
                kb=kb,
                doc=doc,
                file_path=file_path,
                source_type="file",
                chunk_config=chunk_config,
                user_id=user.id,
                tenant_id=user.tenant_id or "default",
            )
            return {
                "code": "0",
                "message": "success",
                "data": {
                    "id": doc.id,
                    "docName": doc.doc_name,
                    "chunkCount": 0,
                    "status": "queued",
                },
            }

        chunk_count = await _run_ingestion(
            kb=kb,
            doc=doc,
            file_path=file_path,
            source_type="file",
            user=user,
            db=db,
            chunk_config=chunk_config,
        )

        return {
            "code": "0",
            "message": "success",
            "data": {
                "id": doc.id,
                "docName": doc.doc_name,
                "chunkCount": chunk_count,
                "status": "success",
            },
        }
    except UploadValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        traceback.print_exc()
        if doc is not None:
            doc.status = "failed"
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{kb_id}/docs/paste")
async def paste_clipboard_document(
    kb_id: str,
    content: str = Form(""),
    doc_name: str = Form(""),
    chunk_strategy: str = Form("FIXED_WINDOW"),
    chunk_size: int = Form(512),
    overlap: int = Form(128),
    images: list[UploadFile] = File(default_factory=list),
    image_references: str = Form("[]"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Import clipboard text and images through the normal ingestion path."""
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.WRITE
    )
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    try:
        remote_images = json.loads(image_references or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="图片引用格式不合法") from exc
    if not isinstance(remote_images, list):
        raise HTTPException(status_code=400, detail="图片引用必须是数组")
    if not content.strip() and not images and not remote_images:
        raise HTTPException(status_code=400, detail="粘贴内容不能为空")
    if chunk_size < 100 or chunk_size > 5000:
        raise HTTPException(status_code=400, detail="chunk_size 必须在 100 到 5000 之间")
    if overlap < 0 or overlap >= chunk_size:
        raise HTTPException(
            status_code=400,
            detail="overlap 必须大于等于 0 且小于 chunk_size",
        )
    if len(images) + len(remote_images) > settings.upload_batch_max_files:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多粘贴 {settings.upload_batch_max_files} 张图片",
        )

    try:
        canonical_strategy = ChunkStrategy.from_value(chunk_strategy).name
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    text_bytes = content.encode("utf-8")
    total_size = len(text_bytes)
    if total_size > settings.upload_max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"粘贴内容超过大小限制（{settings.upload_max_bytes} 字节）",
        )

    doc_id = gen_id()
    requested_name = doc_name.strip()
    if requested_name:
        # Treat the supplied value as a display filename, never as a path.
        normalized_name = requested_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (
            not normalized_name
            or normalized_name in {".", ".."}
            or any(ord(char) < 32 for char in normalized_name)
        ):
            raise HTTPException(status_code=400, detail="文档名称不合法")
    else:
        normalized_name = f"粘贴文档-{doc_id}"

    image_payloads: list[dict] = []
    image_hashes: set[str] = set()
    canonical_content = content

    def append_image(
        *,
        raw: bytes,
        filename: str,
        content_type: str,
        original_id: str,
        supplied_alt: str,
        index: int,
    ) -> None:
        nonlocal total_size, canonical_content
        total_size += len(raw)
        if total_size > settings.upload_max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"文本和图片总大小超过限制（{settings.upload_max_bytes} 字节）",
            )
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        extension = os.path.splitext(filename)[1].lower().lstrip(".")
        try:
            from app.ingestion.upload_validation import validate_upload
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as parsed_image:
                if parsed_image.width * parsed_image.height > settings.upload_max_image_pixels:
                    raise UploadValidationError("image pixel count exceeds configured maximum")
                detected_extension = (parsed_image.format or "").lower().replace("jpeg", "jpg")
                detected_mime = Image.MIME.get(parsed_image.format or "", "")
                parsed_image.verify()
            if detected_extension not in {"png", "jpg", "webp"}:
                raise UploadValidationError(
                    f"unsupported clipboard image format: {detected_extension or 'unknown'}"
                )
            if extension not in {"png", "jpg", "jpeg", "webp"}:
                extension = detected_extension
                filename = f"{os.path.splitext(filename)[0] or f'clipboard-image-{index}'}.{extension}"
            normalized_mime = detected_mime or (
                content_type
                if content_type.startswith("image/")
                else f"image/{detected_extension}"
            )
            validate_upload(
                filename=filename,
                content_type=normalized_mime,
                header=raw[:16],
                size=len(raw),
                max_bytes=settings.upload_max_bytes,
            )
        except UploadValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"图片校验失败: {filename}") from exc

        digest = hashlib.sha256(raw).hexdigest()
        canonical_id = digest[:20]
        alt_match = re.search(
            rf"!\[([^\]]*)\]\(clipboard-image://{re.escape(original_id)}\)",
            canonical_content,
        )
        alt = supplied_alt or (alt_match.group(1) if alt_match else "") or f"粘贴图片 {index}"
        canonical_content = canonical_content.replace(
            f"clipboard-image://{original_id}",
            f"clipboard-image://{canonical_id}",
        )
        if digest in image_hashes:
            return
        image_hashes.add(digest)
        suffix = "jpg" if extension == "jpeg" else extension
        image_payloads.append({
            "id": canonical_id,
            "filename": f"clipboard-{digest[:16]}.{suffix}",
            "mimeType": normalized_mime,
            "alt": alt,
            "data": base64.b64encode(raw).decode("ascii"),
        })

    for index, image in enumerate(images, start=1):
        raw = await image.read(settings.upload_max_bytes + 1)
        filename = (
            image.filename or f"clipboard-image-{index}.png"
        ).replace("\\", "/").rsplit("/", 1)[-1]
        append_image(
            raw=raw,
            filename=filename,
            content_type=image.content_type or "",
            original_id=os.path.splitext(filename)[0],
            supplied_alt="",
            index=index,
        )

    for offset, reference in enumerate(remote_images, start=len(images) + 1):
        if not isinstance(reference, dict):
            raise HTTPException(status_code=400, detail="图片引用项不合法")
        reference_id = str(reference.get("id") or "")
        alt = str(reference.get("alt") or "")[:256]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", reference_id):
            raise HTTPException(status_code=400, detail="图片引用 ID 不合法")
        candidate_urls = reference.get("urls") or [reference.get("url")]
        if not isinstance(candidate_urls, list):
            raise HTTPException(status_code=400, detail="图片候选 URL 不合法")
        candidate_urls = [
            str(url) for url in candidate_urls
            if url and len(str(url)) <= 4096
        ]
        candidate_urls = list(dict.fromkeys(candidate_urls))[:5]
        if not candidate_urls:
            raise HTTPException(status_code=400, detail="图片引用 URL 不合法")
        fetched = None
        last_fetch_error: Exception | None = None
        for url in candidate_urls:
            try:
                fetched = await SafeURLFetcher(
                    max_bytes=min(
                        settings.url_ingestion_max_bytes,
                        max(1, settings.upload_max_bytes - total_size),
                    ),
                    timeout_sec=min(settings.url_ingestion_timeout_sec, 20),
                    max_redirects=settings.url_ingestion_max_redirects,
                    allow_private_networks=settings.url_allow_private_networks,
                ).fetch(url)
                break
            except (URLSecurityError, httpx.HTTPError) as exc:
                last_fetch_error = exc
        if fetched is None:
            raise HTTPException(
                status_code=400,
                detail=f"无法读取富文本图片「{alt or reference_id}」: {last_fetch_error}",
            ) from last_fetch_error
        filename = fetched.filename or f"{reference_id}{mimetypes.guess_extension(fetched.content_type) or ''}"
        append_image(
            raw=fetched.content,
            filename=filename,
            content_type=fetched.content_type,
            original_id=reference_id,
            supplied_alt=alt,
            index=offset,
        )

    has_images = bool(image_payloads)
    extension = "clipdoc" if has_images else "md"
    name_stem = normalized_name.rsplit(".", 1)[0] if "." in normalized_name else normalized_name
    doc_name = f"{name_stem}.{extension}"
    if len(doc_name) > 256:
        raise HTTPException(status_code=400, detail="文档名称不能超过 256 个字符")

    stored_content = (
        json.dumps(
            {
                "version": 1,
                "content": canonical_content,
                "images": image_payloads,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if has_images
        else text_bytes
    )
    file_path = os.path.join(_UPLOAD_DIR, f"{doc_id}.{extension}")
    doc = None
    try:
        await asyncio.to_thread(Path(file_path).write_bytes, stored_content)
        content_hash = compute_content_hash(file_path)

        dedup = DuplicateDetector()
        dup_result = await dedup.check_file(
            file_path, kb_id, db, tenant_id=user.tenant_id or "default"
        )
        if dup_result.is_duplicate:
            try:
                os.remove(file_path)
            except OSError:
                pass
            return {
                "code": "0",
                "message": "duplicate",
                "data": {
                    "isDuplicate": True,
                    "existingDocId": dup_result.existing_doc_id,
                    "existingDocName": dup_result.existing_doc_name,
                },
            }

        doc = KnowledgeDocument(
            id=doc_id,
            kb_id=kb_id,
            tenant_id=kb.tenant_id,
            department_id=kb.department_id,
            doc_name=doc_name,
            file_url=file_path,
            file_type=extension,
            file_size=total_size,
            content_hash=content_hash,
            source_type="file",
            chunk_strategy=canonical_strategy,
            chunk_config={
                "chunkSize": chunk_size,
                "overlapSize": overlap,
                "clipboardImageCount": len(image_payloads),
            },
            status="running",
            created_by=user.id,
        )
        db.add(doc)
        await db.flush()

        chunk_config = ChunkConfig(
            strategy=canonical_strategy,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        if settings.ingestion_async_enabled:
            await enqueue_ingestion_job(
                db,
                kb=kb,
                doc=doc,
                file_path=file_path,
                source_type="file",
                chunk_config=chunk_config,
                user_id=user.id,
                tenant_id=user.tenant_id or "default",
            )
            return {
                "code": "0",
                "message": "success",
                "data": {
                    "id": doc.id,
                    "docName": doc.doc_name,
                    "chunkCount": 0,
                    "status": "queued",
                },
            }

        chunk_count = await _run_ingestion(
            kb=kb,
            doc=doc,
            file_path=file_path,
            source_type="file",
            user=user,
            db=db,
            chunk_config=chunk_config,
        )
        return {
            "code": "0",
            "message": "success",
            "data": {
                "id": doc.id,
                "docName": doc.doc_name,
                "chunkCount": chunk_count,
                "status": "success",
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        if doc is not None:
            doc.status = "failed"
        else:
            try:
                os.remove(file_path)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{kb_id}/docs/upload-url")
async def upload_url_document(
    kb_id: str,
    req: URLUploadRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a document from a URL. Supports scheduled refresh via ETag."""
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.WRITE
    )
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="未提供URL")

    # Extract doc_name from URL if not provided
    doc_name = req.doc_name
    if not doc_name:
        from urllib.parse import urlparse
        path = urlparse(url).path
        doc_name = path.rsplit("/", 1)[-1] or "untitled"
        # Remove query params
        if "?" in doc_name:
            doc_name = doc_name.split("?")[0]
        if not doc_name:
            doc_name = "untitled"

    # Download URL content with SSRF, redirect, and response-size controls.
    try:
        fetched = await SafeURLFetcher(
            max_bytes=settings.url_ingestion_max_bytes,
            timeout_sec=settings.url_ingestion_timeout_sec,
            max_redirects=settings.url_ingestion_max_redirects,
            allow_private_networks=settings.url_allow_private_networks,
        ).fetch(url)
        content = fetched.content
        _validate_url_document_type(fetched.final_url, fetched.content_type)
        ext = _guess_extension(fetched.final_url, fetched.content_type)
        if ext not in {"txt", "md", "pdf", "docx", "html", "htm", "json", "csv"}:
            raise URLSecurityError("unsupported URL document type")
        etag = fetched.etag
        last_modified = fetched.last_modified
        if not req.doc_name:
            doc_name = fetched.filename

        doc_id = gen_id()
        file_path = os.path.join(_UPLOAD_DIR, f"{doc_id}.{ext}")
        await asyncio.to_thread(Path(file_path).write_bytes, content)

        try:
            # Create document record
            chunk_config_meta = {
                "_etag": etag,
                "_last_modified": last_modified,
                "_content_hash": _compute_content_hash_value(etag, last_modified, url),
            }

            doc = KnowledgeDocument(
                id=doc_id,
                kb_id=kb_id,
                tenant_id=kb.tenant_id,
                department_id=kb.department_id,
                doc_name=doc_name if doc_name.endswith(f".{ext}") else f"{doc_name}.{ext}",
                file_url=file_path,
                file_type=ext,
                file_size=len(content),
                content_hash=compute_content_hash(file_path),
                source_type="url",
                source_location=url,
                schedule_enabled=1 if req.schedule_enabled else 0,
                schedule_cron=req.schedule_cron or "3600",  # default: check every hour
                chunk_strategy="FIXED_WINDOW",
                chunk_config=chunk_config_meta,
                status="running",
                created_by=user.id,
            )
            db.add(doc)
            await db.flush()

            # Ingestion: enqueue to the outbox worker, or run inline when disabled
            chunk_config = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=512, overlap=128)
            if settings.ingestion_async_enabled:
                await enqueue_ingestion_job(
                    db,
                    kb=kb,
                    doc=doc,
                    file_path=file_path,
                    source_type="url",
                    chunk_config=chunk_config,
                    user_id=user.id,
                    tenant_id=user.tenant_id or "default",
                )
                return {
                    "code": "0",
                    "message": "success",
                    "data": {
                        "id": doc.id,
                        "docName": doc.doc_name,
                        "chunkCount": 0,
                        "status": "queued",
                        "sourceType": "url",
                        "scheduleEnabled": req.schedule_enabled,
                    },
                }

            chunk_count = await _run_ingestion(
                kb=kb,
                doc=doc,
                file_path=file_path,
                source_type="url",
                user=user,
                db=db,
                chunk_config=chunk_config,
            )

            return {
                "code": "0",
                "message": "success",
                "data": {
                    "id": doc.id,
                    "docName": doc.doc_name,
                    "chunkCount": chunk_count,
                    "status": "success",
                    "sourceType": "url",
                    "scheduleEnabled": req.schedule_enabled,
                },
            }
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    except URLSecurityError as exc:
        raise HTTPException(status_code=400, detail=f"URL安全校验失败: {exc}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"获取URL失败: HTTP {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=400, detail=f"URL请求失败: {str(exc)}")
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))


async def _run_ingestion(
    kb: KnowledgeBase,
    doc: KnowledgeDocument,
    file_path: str,
    source_type: str,
    user: User,
    db: AsyncSession,
    chunk_config: ChunkConfig,
    pipeline_id: str | None = None,
) -> int:
    """Run ingestion synchronously via the shared executor."""
    from app.ingestion.source_storage import persist_source

    durable_path = await persist_source(
        file_path,
        kb_id=kb.id,
        doc_id=doc.id,
        filename=doc.doc_name,
    )
    doc.file_url = durable_path
    generation = doc.pending_generation or f"g_{gen_id()}"
    doc.pending_generation = generation
    return await execute_ingestion(
        db,
        kb=kb,
        doc=doc,
        file_path=durable_path,
        source_type=source_type,
        user_id=user.id,
        tenant_id=user.tenant_id or "default",
        chunk_config=chunk_config,
        pipeline_id=pipeline_id,
        generation=generation,
    )


async def run_ingestion_for_doc(
    kb: KnowledgeBase,
    doc: KnowledgeDocument,
    file_path: str,
    source_type: str,
    user: User,
    db: AsyncSession,
    chunk_config: ChunkConfig,
) -> int:
    """Public helper for batch import to avoid circular imports."""
    return await _run_ingestion(kb, doc, file_path, source_type, user, db, chunk_config)


@router.post("/docs/{doc_id}/reprocess")
async def reprocess_document(
    doc_id: str,
    pipeline_id: str = Form(""),
    chunk_strategy: str = Form(""),
    chunk_size: int | None = Form(None),
    overlap: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-process an existing document through a pipeline.

    If pipeline_id is provided, use that pipeline. Otherwise use the KB's
    bound pipeline or fall back to the legacy pipeline.
    """
    doc = await require_document(
        db, principal_from_user(user), doc_id, Permission.WRITE
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    kb_result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == doc.kb_id, KnowledgeBase.deleted == 0)
    )
    kb = kb_result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if not os.path.exists(doc.file_url):
        # For URL documents, re-download if the local copy is gone
        if doc.source_type == "url" and doc.source_location:
            try:
                fetched = await SafeURLFetcher(
                    max_bytes=settings.url_ingestion_max_bytes,
                    timeout_sec=settings.url_ingestion_timeout_sec,
                    max_redirects=settings.url_ingestion_max_redirects,
                    allow_private_networks=settings.url_allow_private_networks,
                ).fetch(doc.source_location)
                ext = _guess_extension(fetched.final_url, fetched.content_type)
                new_path = os.path.join(_UPLOAD_DIR, f"{doc.id}.{ext}")
                with open(new_path, "wb") as f:
                    f.write(fetched.content)
                doc.file_url = new_path
                doc.file_type = ext
                doc.file_size = len(fetched.content)
                await db.flush()
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"下载URL失败: {str(exc)}")
        else:
            raise HTTPException(status_code=400, detail=f"源文件不存在: {doc.file_url}")

    # Use pipeline_id from param, then from KB, then fallback
    effective_pipeline_id = pipeline_id or kb.pipeline_id

    stored_config = doc.chunk_config if isinstance(doc.chunk_config, dict) else {}
    requested_strategy = chunk_strategy or doc.chunk_strategy or "FIXED_WINDOW"
    try:
        canonical_strategy = ChunkStrategy.from_value(requested_strategy).name
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective_chunk_size = (
        chunk_size
        if chunk_size is not None
        else int(stored_config.get("chunkSize") or 512)
    )
    effective_overlap = (
        overlap
        if overlap is not None
        else int(stored_config.get("overlapSize") or 128)
    )
    if effective_chunk_size <= 0:
        raise HTTPException(status_code=400, detail="chunk_size must be positive")
    if effective_overlap < 0 or effective_overlap >= effective_chunk_size:
        raise HTTPException(
            status_code=400,
            detail="overlap must be non-negative and smaller than chunk_size",
        )

    chunk_config = ChunkConfig(
        strategy=canonical_strategy,
        chunk_size=effective_chunk_size,
        overlap=effective_overlap,
    )
    doc.chunk_strategy = canonical_strategy
    doc.chunk_config = {
        "chunkSize": effective_chunk_size,
        "overlapSize": effective_overlap,
    }

    doc.status = "running"
    await db.flush()

    if settings.ingestion_async_enabled:
        await enqueue_ingestion_job(
            db,
            kb=kb,
            doc=doc,
            file_path=doc.file_url,
            source_type=doc.source_type or "file",
            chunk_config=chunk_config,
            user_id=user.id,
            tenant_id=user.tenant_id or "default",
            pipeline_id=pipeline_id or None,
            operation="REPROCESS",
        )
        return {
            "code": "0", "message": "success",
            "data": {"chunkCount": 0, "status": "queued"},
        }

    try:
        if pipeline_id:
            # Explicit pipeline specified: use it directly
            generation = f"g_{gen_id()}"
            doc.pending_generation = generation
            engine = IngestionEngine()
            result = await engine.execute_pipeline(
                pipeline_id=pipeline_id,
                source_type=doc.source_type or "file",
                source_location=doc.file_url,
                source_file_name=doc.doc_name,
                kb_id=kb.id,
                doc_id=doc.id,
                user_id=user.id,
                tenant_id=user.tenant_id or "default",
                generation=generation,
                db=db,
            )
            if result.status == "error":
                raise RuntimeError(result.error_message or "Pipeline execution failed")
            chunk_count = result.chunk_count
            doc.status = "success"
            doc.chunk_count = chunk_count
            await db.flush()
        else:
            # Use KB's bound pipeline or fallback to legacy
            chunk_count = await _run_ingestion(
                kb=kb, doc=doc,
                file_path=doc.file_url,
                source_type=doc.source_type or "file",
                user=user, db=db,
                chunk_config=chunk_config,
                pipeline_id=effective_pipeline_id,
            )

        return {
            "code": "0", "message": "success",
            "data": {"chunkCount": chunk_count, "status": "success"},
        }
    except Exception as exc:
        traceback.print_exc()
        doc.status = "failed"
        await db.flush()
        raise HTTPException(status_code=500, detail=str(exc))


def _guess_extension(url: str, content_type: str) -> str:
    """Guess file extension from URL path or Content-Type header."""
    from urllib.parse import urlparse
    path = urlparse(url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in ("txt", "md", "pdf", "docx", "html", "htm", "json", "csv"):
            return ext

    ct_map = {
        "text/plain": "txt",
        "text/markdown": "md",
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "text/html": "html",
        "application/json": "json",
        "text/csv": "csv",
    }
    for ct_prefix, ext in ct_map.items():
        if content_type.startswith(ct_prefix):
            return ext
    return "txt"


def _validate_url_document_type(url: str, content_type: str) -> None:
    from urllib.parse import urlparse

    allowed_extensions = {"txt", "md", "pdf", "docx", "html", "htm", "json", "csv"}
    allowed_content_types = {
        "text/plain",
        "text/markdown",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/html",
        "application/xhtml+xml",
        "application/json",
        "text/csv",
    }
    path = urlparse(url).path
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if extension not in allowed_extensions and content_type not in allowed_content_types:
        raise URLSecurityError("unsupported URL document content type")


def _compute_content_hash_value(etag: str, last_modified: str, url: str) -> str:
    import hashlib
    raw = f"{etag}|{last_modified}|{url}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@router.delete("/docs/{doc_id}")
async def delete_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = await require_document(
        db, principal_from_user(user), doc_id, Permission.WRITE
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc.deleted = 1

    # Also soft-delete all chunks
    chunk_result = await db.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id)
    )
    chunks = list(chunk_result.scalars().all())
    for chunk in chunks:
        chunk.deleted = 1

    from app.models import KnowledgeAsset
    asset_result = await db.execute(
        select(KnowledgeAsset).where(KnowledgeAsset.doc_id == doc_id)
    )
    assets = list(asset_result.scalars().all())
    for asset in assets:
        asset.deleted = 1

    kb = await require_kb(
        db, principal_from_user(user), doc.kb_id, Permission.WRITE
    )
    from app.services.index_sync import IndexSyncService

    sync_job = await IndexSyncService().delete_document(
        db,
        kb=kb,
        doc_id=doc_id,
        chunks=chunks,
        assets=assets,
        source_location=doc.file_url,
    )

    return {
        "code": "0",
        "message": "success",
        "data": {"syncStatus": sync_job.status, "syncJobId": sync_job.id},
    }


@router.get("/docs/{doc_id}/chunks")
async def list_chunks(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_document(
        db, principal_from_user(user), doc_id, Permission.READ
    )
    result = await db.execute(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.doc_id == doc_id, KnowledgeChunk.deleted == 0)
        .order_by(KnowledgeChunk.chunk_index)
    )
    chunks = result.scalars().all()
    return {
        "code": "0",
        "message": "success",
        "data": [
            {
                "id": c.id,
                "chunkIndex": c.chunk_index,
                "content": c.content,
                "charCount": c.char_count,
                "tokenCount": c.token_count,
                "enabled": 1 if c.enabled is None else c.enabled,
                "blockType": c.block_type,
                "pageStart": c.page_start,
                "pageEnd": c.page_end,
                "bboxes": c.bbox_json or [],
                "metadata": c.metadata_json or {},
                "createTime": str(c.create_time) if c.create_time else None,
                "updateTime": str(c.update_time) if c.update_time else None,
            }
            for c in chunks
        ],
    }


@router.patch("/docs/{doc_id}/chunks/{chunk_id}")
async def update_chunk_status(
    doc_id: str,
    chunk_id: str,
    req: ChunkStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_document(
        db, principal_from_user(user), doc_id, Permission.WRITE
    )
    result = await db.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.doc_id == doc_id,
            KnowledgeChunk.deleted == 0,
        )
    )
    chunk = result.scalar_one_or_none()
    if chunk is None:
        raise HTTPException(status_code=404, detail="切片不存在")

    before_enabled = 1 if chunk.enabled is None else chunk.enabled
    chunk.enabled = 1 if req.enabled else 0
    chunk.updated_by = user.id
    await db.flush()

    ctx = get_audit_context()
    await record_audit(
        biz_type="knowledge_chunk",
        biz_id=chunk.id,
        operation_type="UPDATE",
        action_desc=f"{'启用' if req.enabled else '禁用'}切片 #{chunk.chunk_index}",
        before_snapshot={"enabled": before_enabled},
        after_snapshot={"enabled": chunk.enabled},
        operator_id=ctx.get("operator_id") or user.id,
        operator_name=ctx.get("operator_name") or user.username,
        operator_role=ctx.get("operator_role") or user.role,
        db=db,
    )

    return {
        "code": "0",
        "message": "success",
        "data": {
            "id": chunk.id,
            "enabled": chunk.enabled,
            "updateTime": str(chunk.update_time) if chunk.update_time else None,
        },
    }


# ---- Dedup Check ----


@router.post("/{kb_id}/docs/check-duplicate")
async def check_duplicate(
    kb_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check if a file is a duplicate before uploading."""
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.READ
    )
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")

    ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
    if ext not in _SUPPORTED_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: .{ext}，支持: {', '.join(sorted(_SUPPORTED_FILE_TYPES))}",
        )

    # Write to temp file for hash computation
    tmp_id = gen_id()
    tmp_path = os.path.join(_UPLOAD_DIR, f"dedup_{tmp_id}.{ext}")
    try:
        from app.ingestion.upload_validation import save_upload_bounded

        await asyncio.to_thread(
            save_upload_bounded,
            file,
            tmp_path,
            max_bytes=settings.upload_max_bytes,
            max_pdf_pages=settings.upload_max_pdf_pages,
            max_uncompressed_bytes=settings.archive_max_uncompressed_bytes,
            max_archive_entries=settings.archive_max_entries,
            max_compression_ratio=settings.archive_max_compression_ratio,
            max_image_pixels=settings.upload_max_image_pixels,
        )

        dedup = DuplicateDetector()
        result = await dedup.check_file(
            tmp_path, kb_id, db, tenant_id=user.tenant_id or "default"
        )

        response = {
            "code": "0",
            "message": "success",
            "data": {
                "isDuplicate": result.is_duplicate,
            },
        }
        if result.is_duplicate:
            response["data"]["existingDocId"] = result.existing_doc_id
            response["data"]["existingDocName"] = result.existing_doc_name
        return response
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# ---- Batch Import ----


class BatchImportRequest(BaseModel):
    doc_names: list[str] = Field(default_factory=list, description="文件名称列表(URL导入时)")


@router.post("/{kb_id}/docs/batch-upload")
async def batch_upload_documents(
    kb_id: str,
    files: list[UploadFile] = File(default_factory=list),
    chunk_strategy: str = Form("FIXED_WINDOW"),
    chunk_size: int = Form(512),
    overlap: int = Form(128),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Batch upload multiple files with progress tracking and dedup."""
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.WRITE
    )
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if not files:
        raise HTTPException(status_code=400, detail="未提供文件")

    try:
        canonical_strategy = ChunkStrategy.from_value(chunk_strategy).name
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.services.batch_import import BatchImportHandler, BatchFileSpec

    handler = BatchImportHandler(_UPLOAD_DIR)
    if len(files) > settings.upload_batch_max_files:
        raise HTTPException(
            status_code=400,
            detail=(
                f"batch contains {len(files)} files; maximum is "
                f"{settings.upload_batch_max_files}"
            ),
        )

    # Save files and build specs first (no DB involved yet)
    file_specs: list[BatchFileSpec] = []
    for file in files:
        if not file.filename:
            continue
        ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
        if not ext:
            ext = "txt"
        if ext not in _SUPPORTED_FILE_TYPES:
            continue

        doc_id = gen_id()
        file_path = os.path.join(_UPLOAD_DIR, f"{doc_id}.{ext}")
        from app.ingestion.upload_validation import save_upload_bounded

        try:
            written = await asyncio.to_thread(
                save_upload_bounded,
                file,
                file_path,
                max_bytes=settings.upload_max_bytes,
                max_pdf_pages=settings.upload_max_pdf_pages,
                max_uncompressed_bytes=settings.archive_max_uncompressed_bytes,
                max_archive_entries=settings.archive_max_entries,
                max_compression_ratio=settings.archive_max_compression_ratio,
                max_image_pixels=settings.upload_max_image_pixels,
            )
        except UploadValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        from app.ingestion.source_storage import persist_source

        durable_path = await persist_source(
            file_path,
            kb_id=kb_id,
            doc_id=doc_id,
            filename=file.filename,
        )

        file_specs.append(BatchFileSpec(
            filename=file.filename,
            file_path=durable_path,
            file_size=written,
        ))

    if not file_specs:
        raise HTTPException(status_code=400, detail="无有效文件（不支持的格式）")

    # Run batch — handler manages its own sessions internally
    job_id = await handler.create_job(
        kb_id,
        file_specs,
        user,
        chunk_strategy=canonical_strategy,
        chunk_size=chunk_size,
        overlap=overlap,
    )

    return {
        "code": "0",
        "message": "success",
        "data": {
            "jobId": job_id,
            "status": "queued",
            "total": len(file_specs),
            "success": 0,
            "failed": 0,
            "skippedDuplicates": 0,
            "perFile": [],
        },
    }


@router.get("/batch-import/{job_id}")
async def get_batch_import_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the status and progress of a batch import job."""
    result = await db.execute(
        select(BatchImportJob).where(
            BatchImportJob.id == job_id,
            BatchImportJob.tenant_id == (user.tenant_id or "default"),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="批量导入任务不存在")

    files_result = await db.execute(
        select(BatchImportFileRecord).where(
            BatchImportFileRecord.job_id == job_id,
        ).order_by(BatchImportFileRecord.id)
    )
    files = files_result.scalars().all()

    return {
        "code": "0",
        "message": "success",
        "data": {
            "jobId": job.id,
            "status": job.status,
            "totalFiles": job.total_files,
            "completedFiles": job.completed_files,
            "failedFiles": job.failed_files,
            "skippedDuplicates": job.skipped_duplicates,
            "errorMessage": job.error_message,
            "createTime": str(job.create_time),
            "files": [
                {
                    "id": f.id,
                    "fileName": f.file_name,
                    "fileType": f.file_type,
                    "fileSize": f.file_size,
                    "status": f.status,
                    "docId": f.doc_id,
                    "chunkCount": f.chunk_count,
                    "errorMessage": f.error_message,
                }
                for f in files
            ],
        },
    }


# ---- Incremental Re-index ----


@router.post("/docs/{doc_id}/reindex-if-changed")
async def reindex_if_changed(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check if a document has changed and re-index only if needed.

    Returns skip status if content is unchanged.
    """
    doc = await require_document(
        db, principal_from_user(user), doc_id, Permission.WRITE
    )
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    from app.ingestion.incremental import IncrementalIndexer
    from app.ingestion.chunker import ChunkConfig

    if not os.path.exists(doc.file_url):
        raise HTTPException(status_code=400, detail=f"源文件不存在: {doc.file_url}")

    change = await IncrementalIndexer.check_document_changed(
        doc.file_url, doc_id, db
    )

    if not change.changed:
        return {
            "code": "0",
            "message": "unchanged",
            "data": {
                "docId": doc.id,
                "changed": False,
                "contentHash": change.content_hash,
                "reason": change.reason,
            },
        }

    # Content changed — re-ingest
    kb_result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == doc.kb_id,
            KnowledgeBase.deleted == 0,
        )
    )
    kb = kb_result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    # Re-run ingestion
    strategy = doc.chunk_strategy or "FIXED_WINDOW"
    cfg = doc.chunk_config or {}
    chunk_config = ChunkConfig(
        strategy=strategy,
        chunk_size=cfg.get("chunkSize", 512) if isinstance(cfg, dict) else 512,
        overlap=cfg.get("overlapSize", 128) if isinstance(cfg, dict) else 128,
    )

    chunk_count = await _run_ingestion(
        kb=kb,
        doc=doc,
        file_path=doc.file_url,
        source_type=doc.source_type or "file",
        user=user,
        db=db,
        chunk_config=chunk_config,
    )

    # Update hash
    await IncrementalIndexer.update_document_hash(doc_id, change.content_hash, db)

    return {
        "code": "0",
        "message": "reindexed",
        "data": {
            "docId": doc.id,
            "changed": True,
            "contentHash": change.content_hash,
            "chunkCount": chunk_count,
        },
    }


# ---- Document Preview ----

_CONTENT_TYPE_MAP = {
    "pdf": "application/pdf",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


@router.post("/{kb_id}/index-generations")
async def rebuild_index_generation(
    kb_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Build a new physical vector index and promote it after validation."""
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.WRITE
    )
    if not kb:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    from app.services.index_lifecycle import IndexLifecycleService

    service = IndexLifecycleService()
    generation_id = await service.plan(db, kb=kb, user_id=user.id)
    await db.commit()
    return {
        "code": "0",
        "message": "queued",
        "data": {"generationId": generation_id, "status": "BUILDING"},
    }


@router.get("/{kb_id}/index-generations")
async def list_index_generations(
    kb_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = await require_kb(
        db, principal_from_user(user), kb_id, Permission.READ
    )
    if not kb:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    from app.models import KnowledgeIndexGeneration

    records = list(
        (
            await db.execute(
                select(KnowledgeIndexGeneration)
                .where(
                    KnowledgeIndexGeneration.kb_id == kb_id,
                    KnowledgeIndexGeneration.deleted == 0,
                )
                .order_by(KnowledgeIndexGeneration.create_time.desc())
            )
        ).scalars().all()
    )
    return {
        "code": "0",
        "data": [
            {
                "id": item.id,
                "generation": item.generation,
                "collectionName": item.collection_name,
                "embeddingModel": item.embedding_model,
                "embeddingDim": item.embedding_dim,
                "status": item.status,
                "expectedChunks": item.expected_chunks,
                "indexedChunks": item.indexed_chunks,
                "error": item.error_message,
            }
            for item in records
        ],
    }


@router.get("/docs/{doc_id}/preview")
async def preview_document(
    doc_id: str,
    request: Request,
    token: str | None = Query(None, description="JWT token (for PDF.js / <img> tags)"),
    db: AsyncSession = Depends(get_db),
):
    """Serve the original document file for in-browser preview.

    Supports Range requests (required by PDF.js for large files).
    Auth: user must have READ permission on the document's knowledge base.
    Token can be provided via Authorization header OR ?token= query param.
    """
    # Resolve user from query param first, then Authorization header
    jwt = token
    if not jwt:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            jwt = auth_header[7:]
    if not jwt:
        raise HTTPException(status_code=401, detail="未提供认证Token")

    payload = decode_access_token(jwt)
    if not payload:
        raise HTTPException(status_code=401, detail="Token无效或已过期")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token格式错误")

    result = await db.execute(select(User).where(User.id == user_id, User.deleted == 0))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    doc = await require_document(
        db, principal_from_user(user), doc_id, Permission.READ
    )

    file_path = doc.file_url
    from app.ingestion.source_storage import is_object_source, presign_source

    if is_object_source(file_path or ""):
        return RedirectResponse(
            await presign_source(file_path),
            status_code=307,
        )
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="源文件不存在或已被移除")

    ext = (doc.file_type or os.path.splitext(file_path)[1].lstrip(".")).lower()
    content_type = _CONTENT_TYPE_MAP.get(ext)
    if not content_type:
        content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    return FileResponse(
        path=file_path,
        media_type=content_type,
        filename=doc.doc_name or os.path.basename(file_path),
        headers={"Accept-Ranges": "bytes"},
    )
