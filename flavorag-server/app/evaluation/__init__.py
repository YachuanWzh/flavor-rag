"""Offline retrieval evaluation."""

from pathlib import Path


_KNOWLEDGE_ARCHIVE_DATASET = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "knowledge-archive-golden-v1.jsonl"
)
DATASET_PATH = (
    _KNOWLEDGE_ARCHIVE_DATASET
    if _KNOWLEDGE_ARCHIVE_DATASET.exists()
    else Path(__file__).resolve().parents[2] / "evaluation" / "minimal.jsonl"
)
