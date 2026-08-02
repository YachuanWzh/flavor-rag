"""Tests for F8: Conversation analytics dashboard."""
from __future__ import annotations

# ─── F8.1 Aggregation logic ───


def test_analytics_empty_data():
    from app.services.analytics import ConversationAnalytics

    analytics = ConversationAnalytics()
    result = analytics.compute(
        conversations=[], messages=[], feedbacks=[], traces=[]
    )
    assert result.total_conversations == 0
    assert result.total_messages == 0
    assert result.avg_turns == 0.0
    assert result.satisfaction_trend == []
    assert result.top_topics == []


def test_analytics_basic_counts():
    from app.services.analytics import (
        AnalyticsMessage,
        ConversationAnalytics,
    )

    conversations = [{"id": "c1"}, {"id": "c2"}]
    messages = [
        AnalyticsMessage(conversation_id="c1", role="user"),
        AnalyticsMessage(conversation_id="c1", role="assistant"),
        AnalyticsMessage(conversation_id="c1", role="user"),
        AnalyticsMessage(conversation_id="c2", role="user"),
    ]
    analytics = ConversationAnalytics()
    result = analytics.compute(
        conversations=conversations, messages=messages, feedbacks=[], traces=[]
    )
    assert result.total_conversations == 2
    assert result.total_messages == 4
    # c1 has 2 user messages, c2 has 1 user message → 3/2 = 1.5
    assert result.avg_turns == 1.5


def test_analytics_satisfaction():
    from app.services.analytics import (
        AnalyticsFeedback,
        ConversationAnalytics,
    )

    feedbacks = [
        AnalyticsFeedback(vote=1, date="2026-08-01"),
        AnalyticsFeedback(vote=1, date="2026-08-01"),
        AnalyticsFeedback(vote=-1, date="2026-08-01"),
        AnalyticsFeedback(vote=1, date="2026-08-02"),
    ]
    analytics = ConversationAnalytics()
    result = analytics.compute(
        conversations=[], messages=[], feedbacks=feedbacks, traces=[]
    )
    assert len(result.satisfaction_trend) == 2
    day1 = result.satisfaction_trend[0]
    assert day1["positive"] == 2
    assert day1["negative"] == 1


def test_analytics_top_topics():
    from app.services.analytics import ConversationAnalytics, TraceRecord

    traces = [
        TraceRecord(query="如何部署Kubernetes集群", refusal=False),
        TraceRecord(query="Kubernetes部署配置方法", refusal=False),
        TraceRecord(query="Kubernetes集群怎么部署", refusal=False),
        TraceRecord(query="数据库备份策略", refusal=False),
    ]
    analytics = ConversationAnalytics()
    result = analytics.compute(
        conversations=[], messages=[], feedbacks=[], traces=traces
    )
    assert len(result.top_topics) > 0
    # The most frequent topic should mention Kubernetes/部署
    top = result.top_topics[0]
    assert top["count"] >= 2


def test_analytics_refusal_rate():
    from app.services.analytics import ConversationAnalytics, TraceRecord

    traces = [
        TraceRecord(query="q1", refusal=False),
        TraceRecord(query="q2", refusal=True),
        TraceRecord(query="q3", refusal=False),
        TraceRecord(query="q4", refusal=True),
    ]
    analytics = ConversationAnalytics()
    result = analytics.compute(
        conversations=[], messages=[], feedbacks=[], traces=traces
    )
    assert result.refusal_rate == 0.5


def test_analytics_peak_hours():
    from app.services.analytics import (
        AnalyticsMessage,
        ConversationAnalytics,
    )

    messages = [
        AnalyticsMessage(conversation_id="c1", role="user", hour=10),
        AnalyticsMessage(conversation_id="c1", role="user", hour=10),
        AnalyticsMessage(conversation_id="c1", role="user", hour=14),
    ]
    analytics = ConversationAnalytics()
    result = analytics.compute(
        conversations=[], messages=messages, feedbacks=[], traces=[]
    )
    assert len(result.peak_hours) > 0
    assert result.peak_hours[0]["hour"] == 10
    assert result.peak_hours[0]["count"] == 2
