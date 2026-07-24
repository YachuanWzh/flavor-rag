"""Knowledge base CRUD API + document upload."""
from __future__ import annotations

import os
import shutil
import tempfile
import traceback

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
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
