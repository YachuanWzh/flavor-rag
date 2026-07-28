"""Fetcher node — downloads documents from various sources."""

from __future__ import annotations

import os

import httpx

from app.config.logging_config import get_logger
from app.ingestion.nodes.base import IngestionContext, NodeResult

_log = get_logger("flavorag.ingestion.fetcher")


class FetcherNode:
    """Download documents from file/URL sources.

    Settings:
        timeout_sec (int): Download timeout in seconds (default 120).
    """

    NODE_TYPE = "fetcher"

    async def __call__(self, ctx: IngestionContext) -> NodeResult:
        import time
        t0 = time.time()

        try:
            if ctx.source_type == "file":
                await self._fetch_file(ctx)
            elif ctx.source_type == "url":
                await self._fetch_url(ctx)
            else:
                # Unknown source type — treat as file path
                await self._fetch_file(ctx)

            duration_ms = int((time.time() - t0) * 1000)
            _log.info(
                "fetcher_done",
                source_type=ctx.source_type,
                doc_id=ctx.doc_id,
                size=len(ctx.raw_content) if ctx.raw_content else 0,
                took_ms=duration_ms,
            )
            return NodeResult(
                node_type=self.NODE_TYPE,
                status="success",
                duration_ms=duration_ms,
                output={"size": len(ctx.raw_content) if ctx.raw_content else 0},
            )
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            _log.error("fetcher_failed", doc_id=ctx.doc_id, error=str(exc))
            return NodeResult(
                node_type=self.NODE_TYPE,
                status="error",
                error_message=str(exc),
                duration_ms=duration_ms,
            )

    async def _fetch_file(self, ctx: IngestionContext):
        path = ctx.source_location or ctx.source_file_name
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        with open(path, "rb") as f:
            ctx.raw_content = f.read()

    async def _fetch_url(self, ctx: IngestionContext):
        url = ctx.source_location
        timeout = int(ctx.settings.get("timeout_sec", 120))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ctx.raw_content = resp.content
            if not ctx.source_file_name:
                # Derive filename from URL or Content-Disposition
                ctx.source_file_name = self._guess_filename(url, resp.headers)

    @staticmethod
    def _guess_filename(url: str, headers) -> str:
        import re
        cd = headers.get("content-disposition", "")
        match = re.search(r'filename[^;=\n]*=["\']?([^"\';\n]*)', cd)
        if match:
            return match.group(1).strip()
        return url.rstrip("/").split("/")[-1].split("?")[0] or "document"
