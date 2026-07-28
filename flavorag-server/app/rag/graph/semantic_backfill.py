"""Backfill evidence-grounded semantic edges from existing active chunks.

Preview:
    python -m app.rag.graph.semantic_backfill

Apply:
    python -m app.rag.graph.semantic_backfill --apply --concurrency 2
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import or_, select

from app.database.session import async_session_factory
from app.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.rag.graph.semantic_extractor import extract_and_store_semantic_graph


def _group_rows(rows) -> list[dict]:
    documents: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.kb_id), str(row.doc_id))
        item = documents.setdefault(
            key,
            {
                "kb_id": str(row.kb_id),
                "doc_id": str(row.doc_id),
                "collection_name": str(
                    row.active_collection_name or row.collection_name
                ),
                "chunks": [],
            },
        )
        item["chunks"].append(
            {
                "chunk_id": str(row.chunk_id),
                "doc_id": str(row.doc_id),
                "tenant_id": str(row.tenant_id or "default"),
                "content": str(row.content or ""),
            }
        )
    return list(documents.values())


async def backfill(
    *,
    apply: bool,
    kb_id: str = "",
    doc_id: str = "",
    limit: int = 0,
    concurrency: int = 2,
) -> dict:
    async with async_session_factory() as session:
        query = (
            select(
                KnowledgeBase.id.label("kb_id"),
                KnowledgeBase.collection_name,
                KnowledgeBase.active_collection_name,
                KnowledgeChunk.doc_id,
                KnowledgeChunk.id.label("chunk_id"),
                KnowledgeChunk.tenant_id,
                KnowledgeChunk.content,
            )
            .join(KnowledgeDocument, KnowledgeDocument.kb_id == KnowledgeBase.id)
            .join(KnowledgeChunk, KnowledgeChunk.doc_id == KnowledgeDocument.id)
            .where(
                KnowledgeBase.deleted == 0,
                KnowledgeDocument.deleted == 0,
                KnowledgeChunk.deleted == 0,
                or_(
                    KnowledgeChunk.index_status == "ACTIVE",
                    KnowledgeChunk.index_status.is_(None),
                ),
            )
            .order_by(
                KnowledgeBase.id,
                KnowledgeChunk.doc_id,
                KnowledgeChunk.chunk_index,
            )
        )
        if kb_id:
            query = query.where(KnowledgeBase.id == kb_id)
        if doc_id:
            query = query.where(KnowledgeChunk.doc_id == doc_id)
        rows = (await session.execute(query)).all()

    documents = _group_rows(rows)
    if limit > 0:
        documents = documents[:limit]
    summary = {
        "applied": apply,
        "documents": len(documents),
        "chunks": sum(len(item["chunks"]) for item in documents),
        "complete": 0,
        "failed": 0,
        "entities": 0,
        "edges": 0,
        "rejected": 0,
        "failures": [],
    }
    if not apply:
        return summary

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 8)))

    async def process(item: dict) -> None:
        async with semaphore:
            try:
                result = await extract_and_store_semantic_graph(
                    kb_id=item["kb_id"],
                    collection_name=item["collection_name"],
                    chunks=item["chunks"],
                )
                if result["status"] == "complete":
                    summary["complete"] += 1
                    for key in ("entities", "edges", "rejected"):
                        summary[key] += int(result.get(key) or 0)
                else:
                    summary["failed"] += 1
                    summary["failures"].append(
                        {
                            "docId": item["doc_id"],
                            "reason": result["status"],
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - isolate one document failure
                summary["failed"] += 1
                summary["failures"].append(
                    {
                        "docId": item["doc_id"],
                        "reason": type(exc).__name__,
                        "detail": str(exc)[:240],
                    }
                )

    await asyncio.gather(*(process(item) for item in documents))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从现有 active chunks 回填带原文证据的语义图，不重新分块或向量化"
    )
    parser.add_argument("--apply", action="store_true", help="实际调用 LLM 并写入")
    parser.add_argument("--kb-id", default="", help="只处理指定知识库")
    parser.add_argument("--doc-id", default="", help="只处理指定文档")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少篇文档")
    parser.add_argument("--concurrency", type=int, default=2, help="LLM 并发，最大 8")
    args = parser.parse_args()
    result = asyncio.run(
        backfill(
            apply=args.apply,
            kb_id=args.kb_id,
            doc_id=args.doc_id,
            limit=max(0, args.limit),
            concurrency=args.concurrency,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
