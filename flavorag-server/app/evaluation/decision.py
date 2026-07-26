from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InvestmentDecision:
    capability: str
    decision: str
    reason: str
    recall_uplift: float


def decide_optional_retrieval(
    *,
    capability: str,
    labeled_case_count: int,
    baseline_recall: float,
    candidate_recall: float,
    latency_p95_ms: float,
    min_cases: int,
    min_recall_uplift: float,
    max_latency_p95_ms: float,
) -> InvestmentDecision:
    uplift = candidate_recall - baseline_recall
    if labeled_case_count < min_cases:
        return InvestmentDecision(
            capability, "HOLD", "insufficient_labeled_cases", uplift
        )
    if uplift >= min_recall_uplift and latency_p95_ms <= max_latency_p95_ms:
        return InvestmentDecision(capability, "ENABLE", "measured_uplift", uplift)
    return InvestmentDecision(
        capability, "DISABLE", "uplift_or_latency_gate_not_met", uplift
    )
