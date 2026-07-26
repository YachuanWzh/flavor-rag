from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Awaitable, Callable


class DatasetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    question: str
    expected_chunk_ids: list[str]
    expected_doc_ids: list[str]
    category: str
    answerable: bool
    active: bool = True
    relevance_grades: dict[str, float] = field(default_factory=dict)
    difficulty: str = "medium"
    tags: list[str] = field(default_factory=list)
    language: str = "zh-CN"
    inactive_reason: str | None = None


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    returned_chunk_ids: list[str]
    returned_doc_ids: list[str]
    scores: list[float]
    answerable: bool
    latency_ms: int
    leaked_chunk_ids: list[str] = field(default_factory=list)
    error: str | None = None
    stability: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


Retriever = Callable[..., Awaitable[dict]]


def load_dataset(path: str | Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetValidationError(
                    f"line {line_number}: invalid JSON: {exc.msg}"
                ) from exc
            case_id = str(raw.get("id", "")).strip()
            if not case_id or case_id in seen:
                raise DatasetValidationError(
                    f"line {line_number}: case id is empty or duplicated"
                )
            question = str(raw.get("question", "")).strip()
            category = str(raw.get("category", "")).strip()
            if not question or not category or "answerable" not in raw:
                raise DatasetValidationError(
                    f"line {line_number}: question, category and answerable are required"
                )
            seen.add(case_id)
            expected_chunks = list(dict.fromkeys(raw.get("expected_chunk_ids", [])))
            grades = {
                str(key): float(value)
                for key, value in raw.get("relevance_grades", {}).items()
            }
            for chunk_id in expected_chunks:
                grades.setdefault(chunk_id, 1.0)
            cases.append(
                EvaluationCase(
                    id=case_id,
                    question=question,
                    expected_chunk_ids=expected_chunks,
                    expected_doc_ids=list(
                        dict.fromkeys(raw.get("expected_doc_ids", []))
                    ),
                    category=category,
                    answerable=bool(raw["answerable"]),
                    active=bool(raw.get("active", True)),
                    relevance_grades=grades,
                    difficulty=str(raw.get("difficulty", "medium")),
                    tags=list(raw.get("tags", [])),
                    language=str(raw.get("language", "zh-CN")),
                    inactive_reason=raw.get("inactive_reason"),
                )
            )
    if not cases:
        raise DatasetValidationError("dataset is empty")
    return cases


async def run_evaluation(
    cases: list[EvaluationCase],
    retrieve: Retriever,
    *,
    top_k: int = 5,
    concurrency: int = 4,
    timeout_seconds: float = 30.0,
    repetitions: int = 1,
) -> tuple[list[EvaluationResult], dict]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def execute(case: EvaluationCase) -> EvaluationResult:
        runs: list[dict] = []
        latencies: list[int] = []
        async with semaphore:
            for _ in range(max(1, repetitions)):
                started = time.monotonic()
                try:
                    response = await asyncio.wait_for(
                        retrieve(case.question, top_k=top_k),
                        timeout=max(0.1, timeout_seconds),
                    )
                    latencies.append(int((time.monotonic() - started) * 1000))
                    runs.append(response)
                except Exception as exc:
                    latencies.append(int((time.monotonic() - started) * 1000))
                    return EvaluationResult(
                        case_id=case.id,
                        returned_chunk_ids=[],
                        returned_doc_ids=[],
                        scores=[],
                        answerable=False,
                        latency_ms=round(mean(latencies)),
                        error=f"{type(exc).__name__}: {exc}"[:500],
                        stability=0.0,
                    )

        first = runs[0]
        first_ids = list(first.get("chunk_ids", []))[:top_k]
        stability = mean(
            _jaccard(first_ids, list(item.get("chunk_ids", []))[:top_k])
            for item in runs
        )
        return EvaluationResult(
            case_id=case.id,
            returned_chunk_ids=first_ids,
            returned_doc_ids=list(first.get("doc_ids", []))[:top_k],
            scores=[
                float(score) for score in list(first.get("scores", []))[:top_k]
            ],
            answerable=bool(first.get("answerable", False)),
            latency_ms=round(mean(latencies)),
            leaked_chunk_ids=list(first.get("leaked_chunk_ids", [])),
            stability=stability,
        )

    active_cases = [case for case in cases if case.active]
    results = list(await asyncio.gather(*(execute(case) for case in active_cases)))
    return results, calculate_metrics(cases, results, top_k=top_k)


def calculate_case_metrics(
    case: EvaluationCase,
    result: EvaluationResult,
    *,
    top_k: int,
) -> dict:
    returned = result.returned_chunk_ids[:top_k]
    expected = set(case.expected_chunk_ids)
    hits = [chunk_id in expected for chunk_id in returned]
    hit_count = len(set(returned) & expected)
    precision = hit_count / top_k if case.answerable else 0.0
    recall = hit_count / len(expected) if expected else 0.0
    first_rank = next((index + 1 for index, hit in enumerate(hits) if hit), None)
    ap_sum = 0.0
    hits_so_far = 0
    for index, hit in enumerate(hits, start=1):
        if hit:
            hits_so_far += 1
            ap_sum += hits_so_far / index
    average_precision = (
        ap_sum / min(len(expected), top_k) if expected else 0.0
    )
    dcg = sum(
        case.relevance_grades.get(chunk_id, 0.0) / math.log2(index + 2)
        for index, chunk_id in enumerate(returned)
    )
    ideal = sorted(case.relevance_grades.values(), reverse=True)[:top_k]
    idcg = sum(grade / math.log2(index + 2) for index, grade in enumerate(ideal))
    doc_expected = set(case.expected_doc_ids)
    doc_hits = len(set(result.returned_doc_ids[:top_k]) & doc_expected)
    doc_recall = doc_hits / len(doc_expected) if doc_expected else 0.0
    passed = (
        not result.error
        and not result.leaked_chunk_ids
        and (
            (case.answerable and hit_count > 0 and result.answerable)
            or (not case.answerable and not result.answerable)
        )
    )
    return {
        "precision": precision,
        "recall": recall,
        "hit": 1.0 if hit_count else 0.0,
        "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
        "average_precision": average_precision,
        "ndcg": dcg / idcg if idcg else 0.0,
        "doc_recall": doc_recall,
        "passed": passed,
    }


def calculate_metrics(
    cases: list[EvaluationCase],
    results: list[EvaluationResult],
    *,
    top_k: int,
) -> dict:
    active = {case.id: case for case in cases if case.active}
    by_id = {result.case_id: result for result in results}
    ranked = [
        case
        for case in active.values()
        if case.answerable and case.expected_chunk_ids and case.id in by_id
    ]
    per_case = [
        calculate_case_metrics(case, by_id[case.id], top_k=top_k)
        for case in ranked
    ]
    refusal_cases = [
        case for case in active.values() if not case.answerable and case.id in by_id
    ]
    answerable_cases = [
        case for case in active.values() if case.answerable and case.id in by_id
    ]
    true_refusals = sum(not by_id[case.id].answerable for case in refusal_cases)
    false_refusals = sum(not by_id[case.id].answerable for case in answerable_cases)
    predicted_refusals = true_refusals + false_refusals
    refusal_recall = true_refusals / len(refusal_cases) if refusal_cases else 0.0
    refusal_precision = (
        true_refusals / predicted_refusals if predicted_refusals else 0.0
    )
    latencies = sorted(result.latency_ms for result in results)
    total_returned = sum(len(result.returned_chunk_ids[:top_k]) for result in results)
    unique_returned = sum(
        len(set(result.returned_chunk_ids[:top_k])) for result in results
    )
    case_passes = []
    for case_id, case in active.items():
        if case_id not in by_id:
            continue
        case_passes.append(
            calculate_case_metrics(case, by_id[case_id], top_k=top_k)["passed"]
        )

    def avg(name: str) -> float:
        return mean(item[name] for item in per_case) if per_case else 0.0

    quality_score = (
        avg("recall") * 0.30
        + avg("ndcg") * 0.20
        + avg("reciprocal_rank") * 0.15
        + refusal_recall * 0.15
        + (mean(r.stability for r in results) if results else 0.0) * 0.10
        + (mean(case_passes) if case_passes else 0.0) * 0.10
    )
    return {
        f"precision@{top_k}": avg("precision"),
        f"recall@{top_k}": avg("recall"),
        f"hit_rate@{top_k}": avg("hit"),
        f"mrr@{top_k}": avg("reciprocal_rank"),
        f"map@{top_k}": avg("average_precision"),
        f"ndcg@{top_k}": avg("ndcg"),
        f"doc_recall@{top_k}": avg("doc_recall"),
        "retrieval_coverage": (
            sum(bool(by_id[c.id].returned_chunk_ids) for c in answerable_cases)
            / len(answerable_cases)
            if answerable_cases
            else 0.0
        ),
        "empty_result_rate": (
            sum(not result.returned_chunk_ids for result in results) / len(results)
            if results
            else 0.0
        ),
        "duplicate_rate": (
            1 - unique_returned / total_returned if total_returned else 0.0
        ),
        "refusal_recall": refusal_recall,
        "refusal_precision": refusal_precision,
        "refusal_f1": (
            2 * refusal_precision * refusal_recall
            / (refusal_precision + refusal_recall)
            if refusal_precision + refusal_recall
            else 0.0
        ),
        "answerability_accuracy": (
            sum(
                by_id[c.id].answerable == c.answerable
                for c in active.values()
                if c.id in by_id
            )
            / len(results)
            if results
            else 0.0
        ),
        "acl_leakage_count": sum(
            len(result.leaked_chunk_ids) for result in results
        ),
        "acl_leakage_rate": (
            sum(bool(result.leaked_chunk_ids) for result in results) / len(results)
            if results
            else 0.0
        ),
        "error_rate": (
            sum(bool(result.error) for result in results) / len(results)
            if results
            else 0.0
        ),
        "stability": mean(result.stability for result in results) if results else 0.0,
        "pass_rate": mean(case_passes) if case_passes else 0.0,
        "quality_score": quality_score,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "latency_p99_ms": _percentile(latencies, 0.99),
        "evaluated_cases": len(results),
    }


def assess_quality_gates(metrics: dict, *, top_k: int) -> dict:
    specs = [
        (f"recall@{top_k}", ">=", 0.75),
        (f"ndcg@{top_k}", ">=", 0.70),
        (f"mrr@{top_k}", ">=", 0.70),
        ("refusal_recall", ">=", 0.90),
        ("acl_leakage_count", "==", 0),
        ("error_rate", "<=", 0.02),
        ("latency_p95_ms", "<=", 3000),
        ("stability", ">=", 0.90),
    ]
    checks = []
    for metric, operator, threshold in specs:
        value = float(metrics.get(metric, 0))
        passed = (
            value >= threshold
            if operator == ">="
            else value <= threshold
            if operator == "<="
            else value == threshold
        )
        checks.append(
            {
                "metric": metric,
                "operator": operator,
                "threshold": threshold,
                "value": value,
                "passed": passed,
            }
        )
    return {
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "passed": sum(item["passed"] for item in checks),
        "total": len(checks),
        "checks": checks,
    }


def _jaccard(left: list[str], right: list[str]) -> float:
    union = set(left) | set(right)
    return len(set(left) & set(right)) / len(union) if union else 1.0


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, math.ceil(len(values) * percentile) - 1)
    return values[index]
