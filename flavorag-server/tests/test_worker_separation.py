"""Tests for F1: Worker process separation and distributed scheduling lock."""
from __future__ import annotations

import pytest

# ─── F1.1 Worker Registry ───


def test_registry_contains_all_known_workers():
    from app.worker.registry import WORKER_REGISTRY

    expected = {
        "ingestion",
        "evaluation",
        "retention",
        "reconciliation",
        "repair",
        "index_sync",
        "watchdog",
        "batch_import",
        "url_refresh",
        "doc_schedule",
        "profile",
        "index_build",
    }
    assert set(WORKER_REGISTRY.keys()) == expected


def test_registry_spec_has_required_fields():
    from app.worker.registry import WORKER_REGISTRY

    for name, spec in WORKER_REGISTRY.items():
        assert spec.module_path, f"{name} missing module_path"
        assert spec.class_name, f"{name} missing class_name"
        assert spec.description, f"{name} missing description"


def test_resolve_workers_all():
    from app.worker.registry import resolve_workers

    resolved = resolve_workers(["--all"])
    assert len(resolved) == 12


def test_resolve_workers_subset():
    from app.worker.registry import resolve_workers

    resolved = resolve_workers(["ingestion", "evaluation"])
    assert resolved == ["ingestion", "evaluation"]


def test_resolve_workers_invalid_name_raises():
    from app.worker.registry import resolve_workers

    with pytest.raises(ValueError, match="unknown_worker"):
        resolve_workers(["nonexistent"])


# ─── F1.2 Worker mode setting ───


def test_worker_mode_defaults_to_embedded():
    from app.config.settings import settings

    assert settings.worker_mode == "embedded"


# ─── F1.3 Distributed Lock ───


@pytest.mark.asyncio
async def test_distributed_lock_acquire_and_release_sqlite():
    from app.worker.distributed_lock import DistributedLock

    lock = DistributedLock(backend="local")
    assert await lock.acquire("test-worker", ttl_sec=60) is True
    # Second acquire on same key fails (already held)
    assert await lock.acquire("test-worker", ttl_sec=60) is False
    await lock.release("test-worker")
    # After release, can acquire again
    assert await lock.acquire("test-worker", ttl_sec=60) is True
    await lock.release("test-worker")


@pytest.mark.asyncio
async def test_distributed_lock_ttl_expiry():
    from app.worker.distributed_lock import DistributedLock

    lock = DistributedLock(backend="local")
    assert await lock.acquire("ttl-worker", ttl_sec=0) is True
    # TTL=0 means immediately expired
    assert await lock.acquire("ttl-worker", ttl_sec=60) is True
    await lock.release("ttl-worker")


@pytest.mark.asyncio
async def test_distributed_lock_different_keys_independent():
    from app.worker.distributed_lock import DistributedLock

    lock = DistributedLock(backend="local")
    assert await lock.acquire("worker-a", ttl_sec=60) is True
    assert await lock.acquire("worker-b", ttl_sec=60) is True
    await lock.release("worker-a")
    await lock.release("worker-b")


@pytest.mark.asyncio
async def test_distributed_lock_renewal():
    from app.worker.distributed_lock import DistributedLock

    lock = DistributedLock(backend="local")
    assert await lock.acquire("renew-worker", ttl_sec=1) is True
    renewed = await lock.renew("renew-worker", ttl_sec=60)
    assert renewed is True
    # Cannot renew a lock we don't hold
    assert await lock.renew("other-worker", ttl_sec=60) is False
    await lock.release("renew-worker")


# ─── F1.4 Lifespan respects worker_mode ───


def test_should_start_workers_embedded():
    from app.worker.registry import should_start_workers

    assert should_start_workers("embedded") is True
    assert should_start_workers("standalone") is False
    assert should_start_workers("") is True  # default fallback
