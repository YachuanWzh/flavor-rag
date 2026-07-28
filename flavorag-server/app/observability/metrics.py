"""Prometheus metrics — low-cardinality operational metrics for the platform.

Metric names are prefixed with ``flavorag_``. Label values must stay bounded:
HTTP paths use the route template, channels/models/breakers come from a small
fixed configuration set.
"""
from __future__ import annotations

import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
_INGESTION_BUCKETS = (0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)

HTTP_REQUESTS = Counter(
    "flavorag_http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "path", "status"],
)
HTTP_LATENCY = Histogram(
    "flavorag_http_request_duration_seconds",
    "HTTP request latency by method and route template.",
    ["method", "path"],
    buckets=_LATENCY_BUCKETS,
)

RAG_RUNS = Counter(
    "flavorag_rag_runs_total",
    "Completed RAG pipeline runs by final status.",
    ["status"],
)
CHANNEL_REQUESTS = Counter(
    "flavorag_retrieval_channel_total",
    "Retrieval channel executions by channel and outcome.",
    ["channel", "status"],
)
CHANNEL_LATENCY = Histogram(
    "flavorag_retrieval_channel_duration_seconds",
    "Retrieval channel latency.",
    ["channel"],
    buckets=_LATENCY_BUCKETS,
)
RERANK_LATENCY = Histogram(
    "flavorag_rerank_duration_seconds",
    "Cross-encoder rerank call latency.",
    buckets=_LATENCY_BUCKETS,
)
RERANK_FALLBACK = Counter(
    "flavorag_rerank_fallback_total",
    "Rerank calls that fell back to the pre-rerank ordering.",
)

LLM_FIRST_TOKEN = Histogram(
    "flavorag_llm_first_token_seconds",
    "Time to first streamed token by model.",
    ["model"],
    buckets=_LATENCY_BUCKETS,
)
LLM_STREAM_DURATION = Histogram(
    "flavorag_llm_stream_duration_seconds",
    "Full LLM stream duration by model.",
    ["model"],
    buckets=_LATENCY_BUCKETS,
)
LLM_STREAM_FAILURES = Counter(
    "flavorag_llm_stream_failures_total",
    "Failed LLM stream calls by model.",
    ["model"],
)
RAG_E2E_LATENCY = Histogram(
    "flavorag_rag_end_to_end_duration_seconds",
    "End-to-end duration through final persisted streamed answer.",
    buckets=_LATENCY_BUCKETS,
)
LLM_TOKENS = Counter(
    "flavorag_llm_tokens_total",
    "LLM tokens reported by providers.",
    ["model", "type"],
)
RAG_EMPTY_RETRIEVALS = Counter(
    "flavorag_rag_empty_retrieval_total",
    "RAG requests rejected because no sufficiently relevant evidence exists.",
)
RAG_REFUSALS = Counter(
    "flavorag_rag_refusals_total",
    "RAG refusals by reason.",
    ["reason"],
)
CITATION_COVERAGE = Histogram(
    "flavorag_citation_coverage_ratio",
    "Fraction of retrieved sources cited by the final answer.",
    buckets=(0.0, 0.25, 0.5, 0.75, 1.0),
)
INDEX_LAST_SUCCESS_TIMESTAMP = Gauge(
    "flavorag_index_last_success_timestamp_seconds",
    "Unix timestamp of the latest successful ingestion index activation.",
)
INDEX_REPAIR_JOBS = Counter(
    "flavorag_index_repair_jobs_total",
    "External index repair attempts by channel and result.",
    ["channel", "result"],
)
INDEX_DRIFT = Gauge(
    "flavorag_index_missing_chunks",
    "Active PostgreSQL chunks missing from the vector index.",
    ["kb_id"],
)
INDEX_ORPHANS = Gauge(
    "flavorag_index_orphan_chunks",
    "Vector chunks not referenced by an active PostgreSQL generation.",
    ["kb_id"],
)

CIRCUIT_BREAKER_OPEN = Gauge(
    "flavorag_circuit_breaker_open",
    "1 when the named circuit breaker is open, 0 when closed.",
    ["name"],
)

INGESTION_JOBS = Counter(
    "flavorag_ingestion_jobs_total",
    "Ingestion outbox jobs finished by result.",
    ["result"],
)
INGESTION_JOB_LATENCY = Histogram(
    "flavorag_ingestion_job_duration_seconds",
    "Ingestion job execution duration.",
    buckets=_INGESTION_BUCKETS,
)
INGESTION_QUEUE_DEPTH = Gauge(
    "flavorag_ingestion_queue_depth",
    "Live ingestion outbox jobs by status.",
    ["status"],
)


def render_metrics() -> tuple[bytes, str]:
    """Render the default registry in Prometheus exposition format."""
    return generate_latest(), CONTENT_TYPE_LATEST


class MetricsMiddleware:
    """Pure ASGI metrics middleware that includes the full streaming body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = "500"

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = str(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", None)
            if path:  # skip unmatched paths to avoid label cardinality explosion
                method = scope.get("method", "UNKNOWN")
                HTTP_REQUESTS.labels(method=method, path=path, status=status).inc()
                HTTP_LATENCY.labels(method=method, path=path).observe(
                    time.monotonic() - started
                )
