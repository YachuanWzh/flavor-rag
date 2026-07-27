"""Shared pytest fixtures."""
import asyncio
import contextlib

import pytest


@pytest.fixture(autouse=True, scope="session")
def _close_shared_es_client():
    """Close the module-level ES singleton after the test session so
    aiohttp sessions don't leak warnings at interpreter exit."""
    yield
    from app.rag.search import keyword

    if keyword._es_client is not None:
        with contextlib.suppress(Exception):
            asyncio.run(keyword.close_es_client())
