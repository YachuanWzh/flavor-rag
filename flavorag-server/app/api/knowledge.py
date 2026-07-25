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
from app.ingestion.chunker import ChunkConfig
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.pipeline_engine import IngestionEngine
from app.rag.search.vector import MilvusSearchChannel

router = APIRouter(prefix="/api/knowledge-base", tags=["knowledge-base"])

# Persistent storage for uploaded files (survives restarts)
_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
os.makedirs(_UPLOAD_DIR, exist_ok=True)


class URLUploadRequest(BaseModel):
    url: str = Field(..., description="文档URL地址")
    doc_name: str | None = Field(None, description="文档名称（可选，默认从URL提取）")
    schedule_enabled: bool = Field(False, description="是否启用定时刷新")
    schedule_cron: str | None = Field(None, description="Cron表达式（暂用间隔秒数）")

# ---- Knowledge Base CRUD ----


@router.get("")
async def list_knowledge_bases(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.deleted == 0)
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
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.deleted == 0)
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    before_snapshot = {"name": kb.name, "collectionName": kb.collection_name}
    kb.deleted = 1

    # Drop Milvus collection
    milvus = MilvusSearchChannel()
    milvus.drop_collection(kb.collection_name)

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
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.kb_id == kb_id,
            KnowledgeDocument.deleted == 0,
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
    # Validate knowledge base
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.deleted == 0,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")

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
            doc_name=file.filename,
            file_url=file_path,
            file_type=ext,
            file_size=os.path.getsize(file_path),
            chunk_strategy=chunk_strategy,
            status="running",
            created_by=user.id,
        )
        db.add(doc)
        await db.flush()

        # Run ingestion — use DAG pipeline if KB has one bound
        chunk_config = ChunkConfig(
            strategy=chunk_strategy,
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
    # Validate knowledge base
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.deleted == 0,
        )
    )
    kb = result.scalar_one_or_none()
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

    # Download URL content
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content

            # Determine file extension
            from urllib.parse import urlparse
            content_type = resp.headers.get("content-type", "")
            ext = _guess_extension(url, content_type)

            # Store ETag for change detection
            etag = resp.headers.get("etag", "")
            last_modified = resp.headers.get("last-modified", "")

            # Save to persistent uploads directory
            import hashlib
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
                file_path=tmp_path,
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-process an existing document through a pipeline.

    If pipeline_id is provided, use that pipeline. Otherwise use the KB's
    bound pipeline or fall back to the legacy pipeline.
    """
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.deleted == 0,
        )
    )
    doc = result.scalar_one_or_none()
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
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    resp = await client.get(doc.source_location)
                    resp.raise_for_status()
                    ext = _guess_extension(doc.source_location, resp.headers.get("content-type", ""))
                    new_path = os.path.join(_UPLOAD_DIR, f"{doc.id}.{ext}")
                    with open(new_path, "wb") as f:
                        f.write(resp.content)
                    doc.file_url = new_path
                    doc.file_type = ext
                    doc.file_size = len(resp.content)
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
    old_chunks = chunk_result.scalars().all()
    old_ids = []
    for c in old_chunks:
        c.deleted = 1
        old_ids.append(c.id)
    if old_ids:
        try:
            milvus = MilvusSearchChannel()
            milvus.delete_by_ids(kb.collection_name, old_ids)
        except Exception:
            pass

    # Use pipeline_id from param, then from KB, then fallback
    effective_pipeline_id = pipeline_id or kb.pipeline_id

    chunk_config = ChunkConfig(strategy=doc.chunk_strategy or "FIXED_WINDOW")
    if doc.chunk_config and isinstance(doc.chunk_config, dict):
        cs = doc.chunk_config.get("chunkSize")
        if cs:
            chunk_config.chunk_size = int(cs)
        ov = doc.chunk_config.get("overlapSize")
        if ov:
            chunk_config.overlap_size = int(ov)

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
        "text/html": "html",
        "application/json": "json",
        "text/csv": "csv",
    }
    for ct_prefix, ext in ct_map.items():
        if content_type.startswith(ct_prefix):
            return ext
    return "txt"


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
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.deleted == 0,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc.deleted = 1

    # Also soft-delete all chunks
    chunk_result = await db.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id)
    )
    for chunk in chunk_result.scalars().all():
        chunk.deleted = 1

    return {"code": "0", "message": "success", "data": None}


@router.get("/docs/{doc_id}/chunks")
async def list_chunks(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
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
            }
            for c in chunks
        ],
    }
