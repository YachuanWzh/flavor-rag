"""Liveness and dependency-aware readiness checks."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app.config.settings import settings


async def _bounded(name: str, operation: Callable[[], Awaitable]) -> tuple[str, str]:
    try:
        await asyncio.wait_for(operation(), timeout=3.0)
        return name, "ok"
    except Exception as exc:
        return name, f"error:{type(exc).__name__}"


async def readiness_checks() -> dict[str, str]:
    async def database() -> None:
        from sqlalchemy import text

        from app.database.session import async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))

    async def redis() -> None:
        from redis.asyncio import from_url

        client = from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    async def milvus() -> None:
        def check() -> None:
            from pymilvus import connections, utility

            if not connections.has_connection("default"):
                connections.connect(alias="default", uri=settings.milvus_uri)
            utility.list_collections()

        await asyncio.to_thread(check)

    async def elasticsearch() -> None:
        from app.rag.search.keyword import get_es_client

        client = await get_es_client()
        if not await client.ping():
            raise RuntimeError("Elasticsearch ping failed")

    async def object_storage() -> None:
        def check() -> None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
            )
            client.list_buckets()

        await asyncio.to_thread(check)

    operations: list[tuple[str, Callable[[], Awaitable]]] = [
        ("database", database),
        ("redis", redis),
        ("milvus", milvus),
    ]
    if settings.es_enabled:
        operations.append(("elasticsearch", elasticsearch))
    if (
        settings.pdf_asset_storage_required
        or settings.source_storage_backend.lower() == "s3"
    ):
        operations.append(("object_storage", object_storage))
    pairs = await asyncio.gather(
        *(_bounded(name, operation) for name, operation in operations)
    )
    checks = dict(pairs)
    if not (settings.bailian_api_key or settings.siliconflow_api_key):
        checks["llm"] = "error:not_configured"
    else:
        checks["llm"] = "configured"
    # 3.1: JWT default secret check
    if settings.jwt_secret_key == "change-me-in-production":
        checks["jwt"] = "error:default_secret"
    else:
        checks["jwt"] = "configured"
    # 4.4: Critical worker health check
    try:
        from app.main import _critical_worker_failures

        if _critical_worker_failures:
            checks["workers"] = f"error:{','.join(_critical_worker_failures)}"
        else:
            checks["workers"] = "ok"
    except ImportError:
        checks["workers"] = "ok"
    return checks


def is_ready(checks: dict[str, str]) -> bool:
    return all(value in {"ok", "configured"} for value in checks.values())
