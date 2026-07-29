from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.observability.metrics import (
    CHANNEL_LATENCY,
    CHANNEL_REQUESTS,
    CIRCUIT_BREAKER_OPEN,
)
from app.rag.search.base import SearchResult


def estimate_tokens(text: str) -> int:
    """Conservatively estimate mixed CJK/Latin tokens without model coupling."""
    if not text:
        return 0
    import re

    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    non_cjk = re.sub(r"[\u3400-\u9fff\uf900-\ufaff]", " ", text)
    groups = re.findall(r"\w+|[^\w\s]", non_cjk, flags=re.UNICODE)
    return cjk + sum(max(1, (len(group) + 3) // 4) for group in groups)


@dataclass(frozen=True)
class RetrievalBudget:
    per_channel_top_k: int = 12
    max_candidates: int = 40
    final_top_k: int = 5
    channel_timeout_ms: int = 30000
    total_timeout_ms: int = 35000
    context_max_chars: int = 12000
    context_max_tokens: int = 0
    max_subqueries: int = 3

    def __post_init__(self):
        if min(
            self.per_channel_top_k,
            self.max_candidates,
            self.final_top_k,
            self.context_max_chars,
            self.max_subqueries,
        ) <= 0:
            raise ValueError("retrieval budgets must be positive")


@dataclass(frozen=True)
class ChannelStatus:
    status: str
    duration_ms: int
    count: int = 0
    error: str = ""


@dataclass(frozen=True)
class RetrievalDecision:
    answerable: bool
    reason: str = ""
    dropped_below_threshold: int = 0
    dropped_by_budget: int = 0


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        name: str = "",
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout_sec = max(0.0, recovery_timeout_sec)
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self.name = name

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.recovery_timeout_sec:
            return "half_open"
        return "open"

    async def call(self, operation: Callable[[], Awaitable]):
        self.before_call()
        try:
            result = await operation()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def before_call(self) -> None:
        if self.state == "open":
            raise CircuitOpenError("circuit breaker is open")

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
        self._export_state()

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._export_state()

    def _export_state(self) -> None:
        if self.name:
            CIRCUIT_BREAKER_OPEN.labels(name=self.name).set(
                1 if self._opened_at is not None else 0
            )


async def run_search_channels(
    channels: dict[str, Callable[[], Awaitable[list[SearchResult]]]],
    budget: RetrievalBudget,
) -> tuple[dict[str, list[SearchResult]], dict[str, ChannelStatus]]:
    """Run optional channels concurrently and isolate their failures."""

    async def run_one(name: str, operation):
        started = time.monotonic()
        try:
            items = await asyncio.wait_for(
                operation(),
                timeout=budget.channel_timeout_ms / 1000,
            )
            items = list(items)[: budget.per_channel_top_k * budget.max_subqueries]
            for rank, item in enumerate(items, start=1):
                item.metadata.setdefault("retrievalChannel", name)
                item.metadata.setdefault("channelRank", rank)
                item.metadata.setdefault("rawScore", float(item.score))
            elapsed = time.monotonic() - started
            CHANNEL_REQUESTS.labels(channel=name, status="success").inc()
            CHANNEL_LATENCY.labels(channel=name).observe(elapsed)
            return name, items, ChannelStatus(
                "success",
                int(elapsed * 1000),
                len(items),
            )
        except asyncio.TimeoutError:
            CHANNEL_REQUESTS.labels(channel=name, status="timeout").inc()
            return name, [], ChannelStatus(
                "timeout",
                int((time.monotonic() - started) * 1000),
                error="channel_timeout",
            )
        except Exception as exc:
            CHANNEL_REQUESTS.labels(channel=name, status="error").inc()
            return name, [], ChannelStatus(
                "error",
                int((time.monotonic() - started) * 1000),
                error=type(exc).__name__,
            )

    tasks = {
        name: asyncio.create_task(run_one(name, operation))
        for name, operation in channels.items()
    }
    done, pending = await asyncio.wait(
        tasks.values(),
        timeout=budget.total_timeout_ms / 1000,
    )
    completed = [task.result() for task in done]
    for name, task in tasks.items():
        if task not in pending:
            continue
        task.cancel()
        completed.append(
            (
                name,
                [],
                ChannelStatus(
                    "timeout",
                    budget.total_timeout_ms,
                    error="total_timeout",
                ),
            )
        )
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    results = {name: items for name, items, _ in completed}
    statuses = {name: status for name, _, status in completed}

    bounded: dict[str, list[SearchResult]] = {name: [] for name in channels}
    remaining = budget.max_candidates
    rank = 0
    while remaining > 0:
        added = False
        for name in channels:
            if rank < len(results[name]) and remaining > 0:
                bounded[name].append(results[name][rank])
                remaining -= 1
                added = True
        if not added:
            break
        rank += 1
    return bounded, statuses


def _kb_distribution(items: list[SearchResult]) -> dict[str, int]:
    """Count items per kb_id (only tagged items)."""
    dist: dict[str, int] = {}
    for item in items:
        kb = item.metadata.get("kb_id", "")
        if kb:
            dist[kb] = dist.get(kb, 0) + 1
    return dist


def select_context(
    candidates: list[SearchResult],
    budget: RetrievalBudget,
    *,
    min_score: float,
    kb_quota: int | None = None,
    fallback_pool: list[SearchResult] | None = None,
) -> tuple[list[SearchResult], RetrievalDecision]:
    eligible = [item for item in candidates if item.score >= min_score]
    below = len(candidates) - len(eligible)
    selected: list[SearchResult] = []
    used_chars = 0
    used_tokens = 0
    # Prefer evidence diversity before taking a second chunk from the same doc.
    grouped: dict[str, list[SearchResult]] = {}
    order: list[str] = []
    for item in eligible:
        key = item.doc_id or f"chunk:{item.chunk_id}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)
    diversified: list[SearchResult] = []
    while len(diversified) < len(eligible):
        added = False
        for key in order:
            if grouped[key]:
                diversified.append(grouped[key].pop(0))
                added = True
        if not added:
            break
    for item in diversified:
        if len(selected) >= budget.final_top_k:
            break
        length = len(item.content)
        token_length = estimate_tokens(item.content)
        if budget.context_max_tokens > 0:
            if used_tokens + token_length > budget.context_max_tokens:
                continue
        elif used_chars + length > budget.context_max_chars:
            continue
        selected.append(item)
        used_chars += length
        used_tokens += token_length

    # ── Per-KB quota: swap-based diversity guarantee ──
    # When cross-KB retrieval is active and quota is configured, guarantee
    # each knowledge base at least *kb_quota* representative results.
    # Strategy: REPLACE the lowest-scoring items from over-represented KBs
    # with the best eligible items from under-represented KBs.  This keeps
    # the total count within final_top_k (no token-budget overflow) while
    # ensuring every searched KB has a voice.
    # fallback_pool (pre-rerank candidates) is used when the reranker output
    # does not contain enough items from a given KB.
    if kb_quota and kb_quota > 0:
        # Determine all KBs from both eligible AND fallback_pool
        all_kbs = {
            item.metadata.get("kb_id", "")
            for item in eligible
            if item.metadata.get("kb_id")
        }
        if fallback_pool:
            for item in fallback_pool:
                kb = item.metadata.get("kb_id", "")
                if kb:
                    all_kbs.add(kb)
        if len(all_kbs) > 1:
            selected_ids = {item.chunk_id for item in selected}
            kb_counts = _kb_distribution(selected)
            for kb in all_kbs:
                if kb_counts.get(kb, 0) >= kb_quota:
                    continue
                needed = kb_quota - kb_counts.get(kb, 0)
                # Best candidates from this KB not yet selected (prefer reranked)
                backfill = [
                    item for item in eligible
                    if item.metadata.get("kb_id") == kb
                    and item.chunk_id not in selected_ids
                ][:needed]
                # If reranked pool is insufficient, pull from fallback_pool
                if len(backfill) < needed and fallback_pool:
                    extra_needed = needed - len(backfill)
                    backfill_ids = {item.chunk_id for item in backfill}
                    extra = [
                        item for item in fallback_pool
                        if item.metadata.get("kb_id") == kb
                        and item.chunk_id not in selected_ids
                        and item.chunk_id not in backfill_ids
                    ][:extra_needed]
                    backfill.extend(extra)
                if not backfill:
                    continue
                # Find items to evict: lowest-score selected items from KBs
                # that have MORE than kb_quota representation.
                evictable = sorted(
                    (
                        item for item in selected
                        if kb_counts.get(item.metadata.get("kb_id", ""), 0) > kb_quota
                    ),
                    key=lambda x: x.score,
                )
                for fill_item in backfill:
                    if evictable:
                        victim = evictable.pop(0)
                        idx = selected.index(victim)
                        selected[idx] = fill_item
                        # Update counts
                        victim_kb = victim.metadata.get("kb_id", "")
                        if victim_kb:
                            kb_counts[victim_kb] = kb_counts.get(victim_kb, 1) - 1
                        kb_counts[kb] = kb_counts.get(kb, 0) + 1
                        selected_ids.discard(victim.chunk_id)
                        selected_ids.add(fill_item.chunk_id)
                    else:
                        # No evictable items; append (rare edge case)
                        selected.append(fill_item)
                        selected_ids.add(fill_item.chunk_id)
                        kb_counts[kb] = kb_counts.get(kb, 0) + 1

    dropped_budget = len(eligible) - len(selected)
    if not selected:
        return [], RetrievalDecision(
            False,
            "insufficient_relevance",
            dropped_below_threshold=below,
            dropped_by_budget=dropped_budget,
        )
    return selected, RetrievalDecision(
        True,
        dropped_below_threshold=below,
        dropped_by_budget=dropped_budget,
    )
