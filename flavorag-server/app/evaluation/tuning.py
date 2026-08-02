"""Hyperparameter tuning suggestions based on evaluation run history."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalRunSummary:
    recall_at_k: float
    refusal_rate: float
    p95_latency_ms: int


@dataclass(frozen=True)
class TuningSuggestion:
    param: str
    direction: str  # "increase" | "decrease"
    reason: str
    current_hint: str = ""


# Thresholds that trigger suggestions
_LOW_RECALL = 0.6
_HIGH_REFUSAL = 0.30
_HIGH_LATENCY_MS = 5000


def suggest_hyperparams(runs: list[EvalRunSummary]) -> list[TuningSuggestion]:
    """Generate actionable tuning suggestions from evaluation metrics.

    Returns an empty list when metrics are healthy or no data is available.
    """
    if not runs:
        return []

    avg_recall = sum(r.recall_at_k for r in runs) / len(runs)
    avg_refusal = sum(r.refusal_rate for r in runs) / len(runs)
    max_latency = max(r.p95_latency_ms for r in runs)

    suggestions: list[TuningSuggestion] = []

    if avg_recall < _LOW_RECALL:
        suggestions.append(
            TuningSuggestion(
                param="RETRIEVAL_PER_CHANNEL_TOP_K",
                direction="increase",
                reason=f"avg Recall@K={avg_recall:.2f} < {_LOW_RECALL}; "
                "more candidates per channel may improve coverage",
                current_hint="default 12, try 20",
            )
        )

    if avg_refusal > _HIGH_REFUSAL:
        suggestions.append(
            TuningSuggestion(
                param="RETRIEVAL_RRF_MIN_SCORE",
                direction="decrease",
                reason=f"refusal rate={avg_refusal:.0%} > {_HIGH_REFUSAL:.0%}; "
                "lowering the fusion threshold may admit more relevant results",
                current_hint="default 0.012, try 0.008",
            )
        )

    if max_latency > _HIGH_LATENCY_MS:
        suggestions.append(
            TuningSuggestion(
                param="RETRIEVAL_MAX_CANDIDATES",
                direction="decrease",
                reason=f"P95 latency={max_latency}ms > {_HIGH_LATENCY_MS}ms; "
                "fewer candidates reduce reranker and fusion time",
                current_hint="default 40, try 25",
            )
        )

    return suggestions
