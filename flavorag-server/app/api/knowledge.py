"""Knowledge base CRUD API + document upload + URL source."""
from __future__ import annotations

import os
import shutil
import tempfile
import traceback

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import (
    User,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeChunk,
    gen_id,
)
from app.ingestion.chunker import ChunkConfig
from app.ingestion.pipeline import IngestionPipeline
from app.rag.search.vector import MilvusSearchChannel

router = APIRouter(prefix="/api/knowledge-base", tags=["knowledge-base"])


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
                "createTime": str(kb.create_time),
            }
            for kb in kbs
        ],
    }


@router.post("")
async def create_knowledge_base(
    name: str = Form(...),
    embedding_model: str = Form("qwen3-embedding-8b"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    collection_name = f"kb_{gen_id()}"

    kb = KnowledgeBase(
        name=name,
        embedding_model=embedding_model,
        collection_name=collection_name,
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

    kb.deleted = 1

    # Drop Milvus collection
    milvus = MilvusSearchChannel()
    milvus.drop_collection(kb.collection_name)

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

    # Save uploaded file to temp location
    tmp_path = os.path.join(tempfile.gettempdir(), f"rag_upload_{gen_id()}.{ext}")
    doc = None
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Create document record
        doc = KnowledgeDocument(
            kb_id=kb_id,
            doc_name=file.filename,
            file_url=tmp_path,
            file_type=ext,
            file_size=os.path.getsize(tmp_path),
            chunk_strategy=chunk_strategy,
            status="running",
            created_by=user.id,
        )
        db.add(doc)
        await db.flush()

        # Run ingestion pipeline
        pipeline = IngestionPipeline()
        chunk_config = ChunkConfig(
            strategy=chunk_strategy,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        chunk_count = await pipeline.run(
            doc_id=doc.id,
            kb_id=kb_id,
            file_path=tmp_path,
            collection_name=kb.collection_name,
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
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


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

            # Save to temp file
            import hashlib
            tmp_path = os.path.join(tempfile.gettempdir(), f"rag_url_{gen_id()}.{ext}")
            with open(tmp_path, "wb") as f:
                f.write(content)

        try:
            # Create document record
            chunk_config_meta = {
                "_etag": etag,
                "_last_modified": last_modified,
                "_content_hash": _compute_content_hash_value(etag, last_modified, url),
            }

            doc = KnowledgeDocument(
                kb_id=kb_id,
                doc_name=doc_name if doc_name.endswith(f".{ext}") else f"{doc_name}.{ext}",
                file_url=tmp_path,
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

            # Run ingestion pipeline
            pipeline = IngestionPipeline()
            chunk_config = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=512, overlap=128)
            chunk_count = await pipeline.run(
                doc_id=doc.id,
                kb_id=kb_id,
                file_path=tmp_path,
                collection_name=kb.collection_name,
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
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"获取URL失败: HTTP {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=400, detail=f"URL请求失败: {str(exc)}")
    except Exception as exc:
        traceback.print_exc()
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
