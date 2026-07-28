"""Preview or apply the legacy Neo4j cross-knowledge-base graph backfill.

Usage:
    python -m app.rag.graph.backfill
    python -m app.rag.graph.backfill --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.database.session import async_session_factory, engine
from app.models import KnowledgeBase
from app.rag.graph.neo4j_store import (
    Neo4jGraphStore,
    close_neo4j_driver,
)


async def run(*, apply: bool) -> dict:
    async with async_session_factory() as db:
        result = await db.execute(
            select(KnowledgeBase.id, KnowledgeBase.tenant_id)
            .where(KnowledgeBase.deleted == 0)
            .order_by(KnowledgeBase.tenant_id, KnowledgeBase.id)
        )
        kb_tenants = {
            str(kb_id): str(tenant_id)
            for kb_id, tenant_id in result.all()
        }

    try:
        return await Neo4jGraphStore().backfill_cross_kb_relations(
            kb_tenants=kb_tenants,
            apply=apply,
        )
    finally:
        await close_neo4j_driver()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill tenant/name metadata on legacy graph nodes and build "
            "filtered, explainable CROSS_KB_RELATED bridges."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply changes; without this flag only a read-only preview is run",
    )
    args = parser.parse_args()
    summary = asyncio.run(run(apply=args.apply))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.apply:
        print("Preview only. Re-run with --apply to persist the migration.")


if __name__ == "__main__":
    main()
