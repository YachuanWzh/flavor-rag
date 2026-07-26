"""Knowledge base CRUD API + document upload + URL source."""
from __future__ import annotations

import os
import shutil
import traceback

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.audit.middleware import get_audit_context
from app.audit.service import record_audit
from app.models import (
    User,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
    gen_id,
)
from app.ingestion.chunker import ChunkConfig, ChunkStrategy
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.pipeline_engine import IngestionEngine
from app.ingestion.url_fetcher import SafeURLFetcher, URLSecurityError
from app.rag.search.vector import MilvusSearchChannel
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
    embedding_model: str = Form("qwen3-embedding-8b"),
    pipeline_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    collection_name = f"kb_{gen_id()}"

    kb = KnowledgeBase(
        name=name,
        embedding_model=embedding_model,
        collection_name=collection_name,
        pipeline_id=pipeline_id or None,
        tenant_id=user.tenant_id or "default",
        department_id=user.department_id,
        created_by=user.id,
    )
    db.add(kb)
    await db.flush()

    # Create Milvus collection
    try:
        milvus = MilvusSearchChannel()
        milvus.create_collection(collection_name)
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
        )

    # Drop Milvus collection
    try:
        milvus = MilvusSearchChannel()
        milvus.drop_collection(kb.collection_name)
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
    supported = {"txt", "md", "pdf", "docx"}
    if ext not in supported:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: .{ext}，支持: {', '.join(sorted(supported))}",
        )

    # Save uploaded file to persistent uploads directory
    doc_id = gen_id()
    file_path = os.path.join(_UPLOAD_DIR, f"{doc_id}.{ext}")
    doc = None
    try:
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

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

        # Run ingestion — use DAG pipeline if KB has one bound
        chunk_config = ChunkConfig(
            strategy=canonical_strategy,
            chunk_size=chunk_size,
            overlap=overlap,
        )
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
    except Exception as e:
        traceback.print_exc()
        if doc is not None:
            doc.status = "failed"
        raise HTTPException(status_code=500, detail=str(e))


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
        with open(file_path, "wb") as f:
            f.write(content)

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

            # Run ingestion — use DAG pipeline if KB has one bound
            chunk_config = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=512, overlap=128)
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
) -> int:
    """Run ingestion: DAG pipeline if KB has pipeline_id, otherwise legacy pipeline."""
    pipeline_id = kb.pipeline_id
    if pipeline_id:
        engine = IngestionEngine()
        result = await engine.execute_pipeline(
            pipeline_id=pipeline_id,
            source_type=source_type,
            source_location=file_path,
            source_file_name=doc.doc_name,
            kb_id=kb.id,
            doc_id=doc.id,
            user_id=user.id,
            tenant_id=user.tenant_id or "default",
            db=db,
        )
        if result.status == "error":
            doc.status = "failed"
            raise RuntimeError(result.error_message or "Pipeline execution failed")
        doc.status = "success"
        doc.chunk_count = result.chunk_count
        await db.flush()
        return result.chunk_count

    # Legacy: use old IngestionPipeline
    pipeline = IngestionPipeline()
    chunk_count = await pipeline.run(
        doc_id=doc.id,
        kb_id=kb.id,
        file_path=file_path,
        collection_name=kb.collection_name,
        db=db,
        chunk_config=chunk_config,
    )
    doc.status = "success"
    doc.chunk_count = chunk_count
    await db.flush()
    return chunk_count


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

    # Soft-delete old chunks
    from app.rag.search.vector import MilvusSearchChannel
    chunk_result = await db.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id, KnowledgeChunk.deleted == 0)
    )
    old_chunks = list(chunk_result.scalars().all())
    for c in old_chunks:
        c.deleted = 1
    from app.models import KnowledgeAsset
    old_assets_result = await db.execute(
        select(KnowledgeAsset).where(
            KnowledgeAsset.doc_id == doc_id,
            KnowledgeAsset.deleted == 0,
        )
    )
    old_assets = list(old_assets_result.scalars().all())
    for asset in old_assets:
        asset.deleted = 1
    if old_chunks or old_assets:
        from app.services.index_sync import IndexSyncService

        await IndexSyncService().delete_document(
            db,
            kb=kb,
            doc_id=doc_id,
            chunks=old_chunks,
            assets=old_assets,
        )

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

    try:
        if pipeline_id:
            # Explicit pipeline specified: use it directly
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
