from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Callable


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    question: str
    expected_chunk_ids: list[str]
    expected_doc_ids: list[str]
    category: str
    answerable: bool
    active: bool = True


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    returned_chunk_ids: list[str]
    answerable: bool
    latency_ms: int
    leaked_chunk_ids: list[str] = field(default_factory=list)


def load_dataset(path: str | Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            cases.append(
                EvaluationCase(
                    id=raw["id"],
                    question=raw["question"],
                    expected_chunk_ids=list(raw.get("expected_chunk_ids", [])),
                    expected_doc_ids=list(raw.get("expected_doc_ids", [])),
                    category=raw["category"],
                    answerable=bool(raw["answerable"]),
                    active=bool(raw.get("active", True)),
                )
            )
    return cases


async def run_evaluation(
    cases: list[EvaluationCase],
    retrieve: Callable,
    *,
    top_k: int = 5,
) -> tuple[list[EvaluationResult], dict]:
    results: list[EvaluationResult] = []
    for case in cases:
        if not case.active:
            continue
        started = time.monotonic()
        response = await retrieve(case.question, top_k=top_k)
        results.append(
            EvaluationResult(
                case_id=case.id,
                returned_chunk_ids=list(response.get("chunk_ids", []))[:top_k],
                answerable=bool(response.get("answerable", False)),
                latency_ms=int((time.monotonic() - started) * 1000),
                leaked_chunk_ids=list(response.get("leaked_chunk_ids", [])),
            )
        )
    return results, calculate_metrics(cases, results, top_k=top_k)


def calculate_metrics(
    cases: list[EvaluationCase],
    results: list[EvaluationResult],
    *,
    top_k: int,
) -> dict:
    active = {case.id: case for case in cases if case.active}
    by_id = {result.case_id: result for result in results}
    ranked_cases = [
        case for case in active.values()
        if case.answerable and case.expected_chunk_ids and case.id in by_id
    ]
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    for case in ranked_cases:
        returned = by_id[case.id].returned_chunk_ids[:top_k]
        expected = set(case.expected_chunk_ids)
        hits = [1 if chunk_id in expected else 0 for chunk_id in returned]
        recalls.append(len(set(returned) & expected) / len(expected))
        first = next((index + 1 for index, hit in enumerate(hits) if hit), None)
        reciprocal_ranks.append(1 / first if first else 0.0)
        dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
        ideal_hits = [1] * min(len(expected), top_k)
        idcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(ideal_hits))
        ndcgs.append(dcg / idcg if idcg else 0.0)

    refusal_cases = [
        case for case in active.values()
        if not case.answerable and case.id in by_id
    ]
    refused_correctly = sum(not by_id[case.id].answerable for case in refusal_cases)
    false_refusals = sum(
        not by_id[case.id].answerable
        for case in active.values()
        if case.answerable and case.id in by_id
    )
    predicted_refusals = refused_correctly + false_refusals
    latencies = sorted(result.latency_ms for result in results)
    return {
        f"recall@{top_k}": mean(recalls) if recalls else 0.0,
        "mrr": mean(reciprocal_ranks) if reciprocal_ranks else 0.0,
        f"ndcg@{top_k}": mean(ndcgs) if ndcgs else 0.0,
        "refusal_recall": (
            refused_correctly / len(refusal_cases) if refusal_cases else 0.0
        ),
        "refusal_precision": (
            refused_correctly / predicted_refusals if predicted_refusals else 0.0
        ),
        "acl_leakage_count": sum(
            len(result.leaked_chunk_ids) for result in results
        ),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "evaluated_cases": len(results),
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, math.ceil(len(values) * percentile) - 1)
    return values[index]
