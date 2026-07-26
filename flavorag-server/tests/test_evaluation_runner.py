import asyncio
import json

import pytest

from app.evaluation.runner import (
    DatasetValidationError,
    EvaluationCase,
    EvaluationResult,
    assess_quality_gates,
    calculate_metrics,
    load_dataset,
    run_evaluation,
)


def _case(
    case_id: str,
    *,
    expected: list[str],
    answerable: bool = True,
    category: str = "direct",
) -> EvaluationCase:
    return EvaluationCase(
        id=case_id,
        question=f"question {case_id}",
        expected_chunk_ids=expected,
        expected_doc_ids=["doc-1"] if expected else [],
        category=category,
        answerable=answerable,
    )


def test_metrics_cover_ranking_refusal_security_and_latency():
    cases = [
        _case("ranked", expected=["a", "b"]),
        _case("negative", expected=[], answerable=False, category="unanswerable"),
        _case("acl", expected=[], answerable=False, category="acl_denied"),
    ]
    results = [
        EvaluationResult(
            case_id="ranked",
            returned_chunk_ids=["x", "a", "b"],
            returned_doc_ids=["other", "doc-1", "doc-1"],
            scores=[0.9, 0.8, 0.7],
            answerable=True,
            latency_ms=100,
        ),
        EvaluationResult(
            case_id="negative",
            returned_chunk_ids=[],
            returned_doc_ids=[],
            scores=[],
            answerable=False,
            latency_ms=200,
        ),
        EvaluationResult(
            case_id="acl",
            returned_chunk_ids=["secret"],
            returned_doc_ids=["private"],
            scores=[0.99],
            answerable=True,
            latency_ms=300,
            leaked_chunk_ids=["secret"],
        ),
    ]

    metrics = calculate_metrics(cases, results, top_k=3)

    assert metrics["recall@3"] == 1
    assert metrics["precision@3"] == pytest.approx(2 / 3)
    assert metrics["mrr@3"] == 0.5
    assert metrics["map@3"] == pytest.approx((0.5 + 2 / 3) / 2)
    assert metrics["refusal_recall"] == 0.5
    assert metrics["acl_leakage_count"] == 1
    assert metrics["latency_p95_ms"] == 300
    assert metrics["pass_rate"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_runner_limits_concurrency_and_measures_stability():
    cases = [_case(str(index), expected=[f"c{index}"]) for index in range(5)]
    active = 0
    maximum = 0
    calls: dict[str, int] = {}

    async def retrieve(question: str, *, top_k: int):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        case_id = question.rsplit(" ", 1)[-1]
        calls[case_id] = calls.get(case_id, 0) + 1
        ids = [f"c{case_id}"] if calls[case_id] == 1 else [f"other-{case_id}"]
        return {
            "chunk_ids": ids,
            "doc_ids": ["doc-1"],
            "scores": [1.0],
            "answerable": True,
        }

    _, metrics = await run_evaluation(
        cases,
        retrieve,
        top_k=3,
        concurrency=2,
        repetitions=2,
    )

    assert maximum <= 2
    assert metrics["evaluated_cases"] == 5
    assert metrics["stability"] == 0.5


def test_dataset_validation_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "duplicate.jsonl"
    row = {
        "id": "duplicate",
        "question": "q",
        "category": "direct",
        "answerable": True,
    }
    path.write_text(
        "\n".join([json.dumps(row), json.dumps(row)]),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError):
        load_dataset(path)


def test_quality_gate_fails_closed_when_metrics_are_missing():
    gates = assess_quality_gates({"acl_leakage_count": 0}, top_k=5)

    assert gates["status"] == "failed"
    assert gates["total"] == 8
