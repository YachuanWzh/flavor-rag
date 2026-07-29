"""Runtime hyperparameter overrides — DB cache with env-file fallback.

Usage::

    from app.config.hyperparam import get_hyperparam
    top_k = get_hyperparam("retrieval_final_top_k", settings.retrieval_final_top_k)

The admin API writes to DB and refreshes the cache so new values take effect
on the next RAG pipeline invocation without a server restart.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select, func
from app.database.session import async_session_factory
from app.models import HyperParameterConfig

from app.config.logging_config import get_logger
_log = get_logger("flavorag.hyperparam")

# In-memory cache: dict[tenant_id, dict[key, value]]
_cache: dict[str, dict[str, str]] = {}
_cache_lock = asyncio.Lock()


def _env_value(key: str) -> Any:
    """Return the env-file (settings) default for *key*."""
    from app.config.settings import settings

    mapping: dict[str, Any] = {
        # Retrieval governance
        "retrieval_per_channel_top_k": settings.retrieval_per_channel_top_k,
        "retrieval_max_candidates": settings.retrieval_max_candidates,
        "retrieval_final_top_k": settings.retrieval_final_top_k,
        "retrieval_channel_timeout_ms": settings.retrieval_channel_timeout_ms,
        "retrieval_total_timeout_ms": settings.retrieval_total_timeout_ms,
        "retrieval_context_max_chars": settings.retrieval_context_max_chars,
        "retrieval_context_max_tokens": settings.retrieval_context_max_tokens,
        "retrieval_min_relevance_score": settings.retrieval_min_relevance_score,
        "retrieval_rrf_min_score": settings.retrieval_rrf_min_score,
        "retrieval_vector_min_score": settings.retrieval_vector_min_score,
        "retrieval_reranker_min_score": settings.retrieval_reranker_min_score,
        "retrieval_channel_weights": settings.retrieval_channel_weights,
        # Reranker
        "reranker_enabled": settings.reranker_enabled,
        "reranker_timeout_sec": settings.reranker_timeout_sec,
        # Query
        "query_decomposition_enabled": settings.query_decomposition_enabled,
        "query_decomposition_max_queries": settings.query_decomposition_max_queries,
        "rewrite_enabled": settings.rewrite_enabled,
        # Intent
        "intent_llm_enabled": settings.intent_llm_enabled,
        "intent_min_score": settings.intent_min_score,
        "intent_max_matches": settings.intent_max_matches,
        # HyDE
        "hyde_enabled": settings.hyde_enabled,
        "hyde_channel_weight": settings.hyde_channel_weight,
        # Circuit breaker
        "circuit_breaker_failures": settings.circuit_breaker_failures,
        "circuit_breaker_recovery_sec": settings.circuit_breaker_recovery_sec,
    }
    return mapping.get(key)


def get_hyperparam(key: str, tenant_id: str = "default") -> str | None:
    """Return the cached override for *key*, or None when not overridden."""
    tenant_cache = _cache.get(tenant_id, {})
    return tenant_cache.get(key)


def get_hyperparam_typed(
    key: str, env_default: Any, tenant_id: str = "default"
) -> Any:
    """Return cached DB value (coerced to *env_default* type) or *env_default*.

    When an admin has persisted a value for *key* in the DB, that value is
    returned and coerced to the same Python type as *env_default*.  Otherwise
    *env_default* is returned unchanged.
    """
    raw = get_hyperparam(key, tenant_id=tenant_id)
    if raw is None:
        return env_default

    default_type = type(env_default)
    try:
        if default_type is bool:
            return raw.strip().lower() in ("true", "1", "yes", "on")
        return default_type(raw)
    except (ValueError, TypeError):
        _log.warning(
            "hyperparam_type_coercion_failed",
            key=key,
            raw=raw,
            expected_type=default_type.__name__,
        )
        return env_default


async def refresh_cache(tenant_id: str = "default") -> int:
    """Reload hyperparameter rows for *tenant_id* from DB into in-memory cache.

    Returns the number of rows loaded.
    """
    global _cache

    try:
        async with async_session_factory() as session:
            rows = await session.execute(
                select(
                    HyperParameterConfig.key,
                    HyperParameterConfig.value,
                ).where(
                    HyperParameterConfig.tenant_id == tenant_id,
                    HyperParameterConfig.deleted == 0,
                )
            )
            pairs = {row.key: row.value for row in rows}
    except Exception as exc:
        _log.error(
            "hyperparam_cache_refresh_failed",
            tenant_id=tenant_id,
            error=str(exc),
        )
        return 0

    async with _cache_lock:
        if tenant_id not in _cache:
            _cache[tenant_id] = {}
        _cache[tenant_id] = pairs

    count = len(pairs)
    _log.info(
        "hyperparam_cache_refreshed",
        tenant_id=tenant_id,
        count=count,
    )
    return count


async def update_hyperparam(
    key: str, value: str, tenant_id: str = "default"
) -> bool:
    """Persist an override to DB and immediately update the in-memory cache.

    Returns True when a new row was created, False when an existing row was
    updated.
    """
    from app.database.session import async_session_factory as factory

    try:
        async with factory() as session:
            result = await session.execute(
                select(HyperParameterConfig).where(
                    HyperParameterConfig.tenant_id == tenant_id,
                    HyperParameterConfig.key == key,
                    HyperParameterConfig.deleted == 0,
                )
            )
            existing = result.scalar_one_or_none()
            is_new = existing is None

            if is_new:
                from app.models import gen_id

                session.add(
                    HyperParameterConfig(
                        id=gen_id(),
                        tenant_id=tenant_id,
                        key=key,
                        value=value,
                    )
                )
            else:
                existing.value = value

            await session.commit()
    except Exception as exc:
        _log.error(
            "hyperparam_update_failed",
            key=key,
            tenant_id=tenant_id,
            error=str(exc),
        )
        raise

    # Refresh cache immediately so next RAG call picks up the new value
    await refresh_cache(tenant_id=tenant_id)
    return is_new


async def list_all_hyperparams(tenant_id: str = "default") -> dict[str, dict[str, str]]:
    """Return all hyperparameter configs: {key: {value, env_default}}.

    Also refreshes the in-memory cache so that simply opening the admin page
    heals a stale / empty cache (e.g. after a previous refresh_cache failure).

    Useful for the admin panel to display both the current override (DB value)
    and the env-file default.
    """
    try:
        async with async_session_factory() as session:
            rows = await session.execute(
                select(
                    HyperParameterConfig.key,
                    HyperParameterConfig.value,
                ).where(
                    HyperParameterConfig.tenant_id == tenant_id,
                    HyperParameterConfig.deleted == 0,
                )
            )
            db_values = {row.key: row.value for row in rows}
    except Exception as exc:
        _log.error(
            "hyperparam_list_failed",
            tenant_id=tenant_id,
            error=str(exc),
        )
        db_values = {}

    # Sync the in-memory cache so the pipeline picks up DB values immediately.
    async with _cache_lock:
        if tenant_id not in _cache:
            _cache[tenant_id] = {}
        _cache[tenant_id] = dict(db_values)

    result: dict[str, dict[str, str]] = {}
    # All known keys with their .env defaults
    all_keys = [
        "retrieval_per_channel_top_k",
        "retrieval_max_candidates",
        "retrieval_final_top_k",
        "retrieval_channel_timeout_ms",
        "retrieval_total_timeout_ms",
        "retrieval_context_max_chars",
        "retrieval_context_max_tokens",
        "retrieval_min_relevance_score",
        "retrieval_rrf_min_score",
        "retrieval_vector_min_score",
        "retrieval_reranker_min_score",
        "retrieval_channel_weights",
        "reranker_enabled",
        "reranker_timeout_sec",
        "query_decomposition_enabled",
        "query_decomposition_max_queries",
        "rewrite_enabled",
        "intent_llm_enabled",
        "intent_min_score",
        "intent_max_matches",
        "hyde_enabled",
        "hyde_channel_weight",
        "circuit_breaker_failures",
        "circuit_breaker_recovery_sec",
    ]
    for key in all_keys:
        env_val = _env_value(key)
        result[key] = {
            "value": db_values.get(key, str(env_val)),
            "envDefault": str(env_val),
            "valueType": type(env_val).__name__,
            "customized": key in db_values,
        }
    return result
