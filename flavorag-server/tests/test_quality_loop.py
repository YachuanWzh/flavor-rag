"""Tests for F3: Retrieval quality operations loop."""
from __future__ import annotations

# ─── F3.2 Zero-result query clustering ───


def test_cluster_empty_data():
    from app.evaluation.query_clusters import QueryClusterAnalyzer

    analyzer = QueryClusterAnalyzer()
    clusters = analyzer.cluster_queries([])
    assert clusters == []


def test_cluster_single_query_no_cluster():
    from app.evaluation.query_clusters import QueryClusterAnalyzer, TraceQuery

    analyzer = QueryClusterAnalyzer(min_samples=2)
    queries = [TraceQuery(query="如何部署", embedding=[1.0, 0.0, 0.0])]
    clusters = analyzer.cluster_queries(queries)
    # Single point doesn't form a cluster with min_samples=2
    assert clusters == []


def test_cluster_similar_queries():
    from app.evaluation.query_clusters import QueryClusterAnalyzer, TraceQuery

    analyzer = QueryClusterAnalyzer(eps=0.5, min_samples=2)
    queries = [
        TraceQuery(query="如何配置数据库连接", embedding=[1.0, 0.0, 0.0]),
        TraceQuery(query="怎么设置数据库", embedding=[0.95, 0.05, 0.0]),
        TraceQuery(query="完全不相关的问题", embedding=[0.0, 0.0, 1.0]),
    ]
    clusters = analyzer.cluster_queries(queries)
    assert len(clusters) == 1
    assert clusters[0].count == 2
    assert "数据库" in clusters[0].representative


def test_cluster_multiple_groups():
    from app.evaluation.query_clusters import QueryClusterAnalyzer, TraceQuery

    analyzer = QueryClusterAnalyzer(eps=0.3, min_samples=2)
    queries = [
        TraceQuery(query="部署A", embedding=[1.0, 0.0, 0.0]),
        TraceQuery(query="部署B", embedding=[0.98, 0.02, 0.0]),
        TraceQuery(query="权限X", embedding=[0.0, 1.0, 0.0]),
        TraceQuery(query="权限Y", embedding=[0.0, 0.97, 0.03]),
    ]
    clusters = analyzer.cluster_queries(queries)
    assert len(clusters) == 2


# ─── F3.3 Hyperparameter tuning suggestions ───


def test_suggest_no_runs():
    from app.evaluation.tuning import suggest_hyperparams

    suggestions = suggest_hyperparams([])
    assert suggestions == []


def test_suggest_low_recall():
    from app.evaluation.tuning import EvalRunSummary, suggest_hyperparams

    runs = [
        EvalRunSummary(recall_at_k=0.3, refusal_rate=0.05, p95_latency_ms=2000),
        EvalRunSummary(recall_at_k=0.35, refusal_rate=0.04, p95_latency_ms=2100),
    ]
    suggestions = suggest_hyperparams(runs)
    params = [s.param for s in suggestions]
    assert "RETRIEVAL_PER_CHANNEL_TOP_K" in params


def test_suggest_high_refusal():
    from app.evaluation.tuning import EvalRunSummary, suggest_hyperparams

    runs = [
        EvalRunSummary(recall_at_k=0.8, refusal_rate=0.45, p95_latency_ms=2000),
    ]
    suggestions = suggest_hyperparams(runs)
    params = [s.param for s in suggestions]
    assert "RETRIEVAL_RRF_MIN_SCORE" in params


def test_suggest_high_latency():
    from app.evaluation.tuning import EvalRunSummary, suggest_hyperparams

    runs = [
        EvalRunSummary(recall_at_k=0.8, refusal_rate=0.05, p95_latency_ms=8000),
    ]
    suggestions = suggest_hyperparams(runs)
    params = [s.param for s in suggestions]
    assert "RETRIEVAL_MAX_CANDIDATES" in params


def test_suggest_all_good():
    from app.evaluation.tuning import EvalRunSummary, suggest_hyperparams

    runs = [
        EvalRunSummary(recall_at_k=0.85, refusal_rate=0.05, p95_latency_ms=2000),
    ]
    suggestions = suggest_hyperparams(runs)
    assert suggestions == []


# ─── F3.1 Feedback-driven quality alert ───


def test_quality_alert_threshold():
    from app.evaluation.quality_alert import should_trigger_alert

    # 3 negative feedbacks within window triggers alert
    assert should_trigger_alert(negative_count=3, threshold=3) is True
    assert should_trigger_alert(negative_count=2, threshold=3) is False
    assert should_trigger_alert(negative_count=0, threshold=3) is False


def test_auto_promote_criteria():
    from app.evaluation.quality_alert import meets_auto_promote

    assert meets_auto_promote(positive_count=3, negative_count=0) is True
    assert meets_auto_promote(positive_count=3, negative_count=1) is False
    assert meets_auto_promote(positive_count=2, negative_count=0) is False
