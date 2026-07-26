from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.rag.search.base import SearchResult


@dataclass(frozen=True)
class RetrievalBudget:
    per_channel_top_k: int = 12
    max_candidates: int = 40
    final_top_k: int = 5
    channel_timeout_ms: int = 30000
    total_timeout_ms: int = 35000
    context_max_chars: int = 12000
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
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout_sec = max(0.0, recovery_timeout_sec)
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

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

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None


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
            return name, items, ChannelStatus(
                "success",
                int((time.monotonic() - started) * 1000),
                len(items),
            )
        except asyncio.TimeoutError:
            return name, [], ChannelStatus(
                "timeout",
                int((time.monotonic() - started) * 1000),
                error="channel_timeout",
            )
        except Exception as exc:
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


def select_context(
    candidates: list[SearchResult],
    budget: RetrievalBudget,
    *,
    min_score: float,
) -> tuple[list[SearchResult], RetrievalDecision]:
    eligible = [item for item in candidates if item.score >= min_score]
    below = len(candidates) - len(eligible)
    selected: list[SearchResult] = []
    used_chars = 0
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
        if used_chars + length > budget.context_max_chars:
            continue
        selected.append(item)
        used_chars += length
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
