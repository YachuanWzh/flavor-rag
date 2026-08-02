"""Conversation analytics — aggregation for the admin dashboard.

Pure computation layer; data fetching is done by the API layer and passed in
as simple dicts/dataclasses so this module stays testable without a database.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field


@dataclass
class AnalyticsMessage:
    conversation_id: str
    role: str
    hour: int = 0
    date: str = ""


@dataclass
class AnalyticsFeedback:
    vote: int  # 1 or -1
    date: str = ""


@dataclass
class TraceRecord:
    query: str
    refusal: bool = False


@dataclass
class AnalyticsResult:
    total_conversations: int = 0
    total_messages: int = 0
    avg_turns: float = 0.0
    satisfaction_trend: list[dict] = field(default_factory=list)
    top_topics: list[dict] = field(default_factory=list)
    peak_hours: list[dict] = field(default_factory=list)
    refusal_rate: float = 0.0
    cache_hit_rate: float = 0.0


# Chinese stop words for topic extraction
_STOP_WORDS = frozenset(
    "的了是在我你他她它们这那有和就不人都一一个上也很到说要去会着没看好"
    "自己什么怎么如何为什么可以能不能是否请问关于以及通过使用方法步骤配置"
)


def _extract_keywords(query: str, top_n: int = 3) -> list[str]:
    """Extract top keywords from a query using character bigrams + words."""
    # Split into tokens: CJK chars individually + latin words
    tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9_]*", query)
    # Filter stop words (single-char CJK that are stop words)
    filtered = []
    for token in tokens:
        if len(token) == 1 and token in _STOP_WORDS:
            continue
        if len(token) >= 2:
            filtered.append(token)
    return filtered[:top_n]


class ConversationAnalytics:
    """Compute dashboard analytics from pre-fetched data."""

    def compute(
        self,
        *,
        conversations: list[dict],
        messages: list[AnalyticsMessage],
        feedbacks: list[AnalyticsFeedback],
        traces: list[TraceRecord],
    ) -> AnalyticsResult:
        result = AnalyticsResult()
        result.total_conversations = len(conversations)
        result.total_messages = len(messages)

        # Average turns (user messages per conversation)
        if conversations:
            msg_per_conv: Counter = Counter()
            for msg in messages:
                if msg.role == "user":
                    msg_per_conv[msg.conversation_id] += 1
            total_user_msgs = sum(msg_per_conv.values())
            result.avg_turns = total_user_msgs / len(conversations)

        # Satisfaction trend
        if feedbacks:
            by_date: dict[str, dict] = defaultdict(
                lambda: {"positive": 0, "negative": 0}
            )
            for fb in feedbacks:
                if fb.vote == 1:
                    by_date[fb.date]["positive"] += 1
                elif fb.vote == -1:
                    by_date[fb.date]["negative"] += 1
            result.satisfaction_trend = sorted(
                (
                    {"date": date, **counts}
                    for date, counts in by_date.items()
                ),
                key=lambda x: x["date"],
            )

        # Top topics from trace queries
        if traces:
            keyword_counter: Counter = Counter()
            keyword_samples: dict[str, str] = {}
            for trace in traces:
                keywords = _extract_keywords(trace.query)
                for kw in keywords:
                    keyword_counter[kw] += 1
                    if kw not in keyword_samples:
                        keyword_samples[kw] = trace.query
            result.top_topics = [
                {
                    "topic": kw,
                    "count": count,
                    "sampleQuestion": keyword_samples.get(kw, ""),
                }
                for kw, count in keyword_counter.most_common(10)
            ]

            # Refusal rate
            refusal_count = sum(1 for t in traces if t.refusal)
            result.refusal_rate = refusal_count / len(traces)

        # Peak hours
        if messages:
            hour_counter: Counter = Counter()
            for msg in messages:
                if msg.role == "user":
                    hour_counter[msg.hour] += 1
            result.peak_hours = [
                {"hour": hour, "count": count}
                for hour, count in hour_counter.most_common(24)
            ]

        return result
