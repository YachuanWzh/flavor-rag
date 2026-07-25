"""Base types for ingestion pipeline nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IngestionContext:
    """Mutable context passed through the ingestion pipeline DAG."""

    # Input
    source_type: str = ""           # file / url
    source_location: str = ""       # file path or URL
    source_file_name: str = ""
    kb_id: str = ""
    doc_id: str = ""

    # Intermediate results
    raw_content: bytes | None = None
    parsed_text: str = ""
    chunks: list[dict] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    chunk_records: list = field(default_factory=list)

    # Configuration
    settings: dict[str, Any] = field(default_factory=dict)

    # Progress tracking
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeResult:
    """Output of a single pipeline node execution."""

    node_id: str = ""
    node_type: str = ""
    status: str = "success"       # success / error / skipped
    message: str = ""
    error_message: str = ""
    duration_ms: int = 0
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Final result of a pipeline execution."""

    task_id: str = ""
    status: str = "success"
    error_message: str = ""
    chunk_count: int = 0
    total_duration_ms: int = 0
    node_results: list[NodeResult] = field(default_factory=list)
