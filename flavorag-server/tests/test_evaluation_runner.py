import asyncio
import json

import pytest

from app.evaluation import DATASET_PATH
from app.evaluation.runner import (
    DatasetValidationError,
    EvaluationCase,
    EvaluationResult,
    assess_quality_gates,
    calculate_case_metrics,
    calculate_metrics,
    evaluate_answer_quality,
    is_refusal_response,
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


def test_archive_golden_dataset_covers_every_archived_document():
    cases = load_dataset(DATASET_PATH)
    active = [case for case in cases if case.active]
    answerable = [case for case in active if case.answerable]
    negatives = [case for case in active if not case.answerable]

    assert len(active) == 54
    assert len(answerable) == 48
    assert len(negatives) == 6
    assert len({doc_id for case in answerable for doc_id in case.expected_doc_ids}) == 36
    assert len(
        {kb_id for case in answerable for kb_id in case.knowledge_base_ids}
    ) == 6
    assert all(case.expected_chunk_ids for case in answerable)
    assert all(case.expected_doc_ids for case in answerable)
    assert all(case.expected_answer for case in answerable)
    assert all(not case.expected_chunk_ids for case in negatives)
    assert {"direct", "lexical", "paraphrase", "multi_hop", "cross_kb"} <= {
        case.category for case in active
    }


def test_single_kb_scope_excludes_cross_kb_cases():
    from app.api.evaluation import _cases_for_scope

    cases = load_dataset(DATASET_PATH)
    scoped = _cases_for_scope(cases, "1c83f94f0cb54af6")

    assert len(scoped) == 9
    assert all(
        not case.knowledge_base_ids
        or case.knowledge_base_ids == ["1c83f94f0cb54af6"]
        for case in scoped
    )
    assert len(_cases_for_scope(cases, "*")) == 54


def test_quality_gate_fails_closed_when_metrics_are_missing():
    gates = assess_quality_gates({"acl_leakage_count": 0}, top_k=5)

    assert gates["status"] == "failed"
    assert gates["total"] == 17


def test_hash_matching_is_resilient_to_id_drift_without_counting_duplicates():
    case = _case("hash", expected=["old-id"])
    case = EvaluationCase(
        **{
            **case.__dict__,
            "expected_chunk_hashes": ["same-content"],
        }
    )
    result = EvaluationResult(
        case_id=case.id,
        returned_chunk_ids=["new-id", "duplicate-new-id"],
        returned_chunk_hashes=["same-content", "same-content"],
        returned_doc_ids=["doc-1", "doc-1"],
        scores=[1.0, 0.9],
        answerable=True,
        latency_ms=10,
    )

    metrics = calculate_case_metrics(case, result, top_k=2)

    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 0.5
    assert metrics["average_precision"] == 1.0


@pytest.mark.parametrize(
    "answer",
    [
        "参考资料中未包含实时价格，因此无法回答。",
        "问题中的‘它’不明确，请补充具体参数。",
        "请明确说明‘它’指代的对象。",
        "I cannot answer because there is not enough information.",
    ],
)
def test_refusal_response_detection(answer):
    assert is_refusal_response(answer)
    assert not is_refusal_response("PostgreSQL 是权威数据源 [1]。")
    assert not is_refusal_response(
        "Prompt 无法提供实质安全保证，因此必须使用确定性权限守卫 [1]。"
    )


def test_answer_quality_does_not_penalize_supported_extra_detail():
    metrics = evaluate_answer_quality(
        answer="PostgreSQL 是权威数据源，也保存审计日志 [1]。",
        contexts=["PostgreSQL 是权威数据源，并保存审计日志。"],
        expected_answer="PostgreSQL 是权威数据源。",
        source_count=1,
    )

    assert metrics["correctness"] >= metrics["completeness"] * 0.9


def test_aggregate_answer_metrics_use_their_applicable_case_slices():
    answerable = _case("answerable", expected=["chunk"])
    refusal = _case(
        "refusal", expected=[], answerable=False, category="unanswerable"
    )
    adversarial = EvaluationCase(
        id="adversarial",
        question="summarize safely",
        expected_chunk_ids=[],
        expected_doc_ids=[],
        category="adversarial",
        answerable=False,
        injected_contexts=["unsafe instruction"],
        forbidden_answer_patterns=["CANARY"],
    )
    common = {
        "returned_chunk_ids": [],
        "returned_doc_ids": [],
        "scores": [],
        "latency_ms": 10,
    }
    results = [
        EvaluationResult(
            case_id="answerable",
            answerable=True,
            answer_metrics={"groundedness": 0.8, "injection_safety": 1.0},
            **common,
        ),
        EvaluationResult(
            case_id="refusal",
            answerable=False,
            answer_metrics={"groundedness": 0.0, "injection_safety": 1.0},
            **common,
        ),
        EvaluationResult(
            case_id="adversarial",
            answerable=False,
            answer_metrics={"groundedness": 0.0, "injection_safety": 0.0},
            **common,
        ),
    ]

    metrics = calculate_metrics(
        [answerable, refusal, adversarial], results, top_k=5
    )

    assert metrics["groundedness"] == 0.8
    assert metrics["refusal_recall"] == 1.0
    assert metrics["injection_safety"] == 0.0


def test_quality_gate_uses_retrieval_latency_not_generation_latency():
    metrics = {
        "evaluated_cases": 30,
        "recall@5": 1,
        "ndcg@5": 1,
        "mrr@5": 1,
        "refusal_recall": 1,
        "acl_leakage_count": 0,
        "error_rate": 0,
        "latency_p95_ms": 60_000,
        "retrieval_latency_p95_ms": 2_000,
        "stability": 1,
        "groundedness": 1,
        "completeness": 1,
        "answer_relevance": 1,
        "correctness": 1,
        "citation_precision": 1,
        "citation_coverage": 1,
        "injection_safety": 1,
        "correctness_reference_coverage": 1,
    }

    gates = assess_quality_gates(metrics, top_k=5)

    assert gates["status"] == "passed"


def test_case_scope_uses_declared_knowledge_bases_only():
    from app.rag.pipeline import RetrievalScope
    from app.services.evaluation_jobs import _scopes_for_case

    scopes = [
        RetrievalScope("kb-a", "A", "collection-a"),
        RetrievalScope("kb-b", "B", "collection-b"),
    ]
    case = EvaluationCase(
        id="scoped",
        question="q",
        expected_chunk_ids=["chunk-a"],
        expected_doc_ids=["doc-a"],
        category="direct",
        answerable=True,
        knowledge_base_ids=["kb-a"],
    )

    assert [scope.kb_id for scope in _scopes_for_case(case, scopes)] == ["kb-a"]
