"""Search channel abstract base + shared types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SearchResult:
    chunk_id: str
    content: str
    score: float
    doc_id: str = ""
    doc_name: str = ""
    chunk_index: int = 0
    block_type: str = ""
    page_start: int | None = None
    page_end: int | None = None
    bboxes: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    assets: list[dict] = field(default_factory=list)
    file_type: str = ""


class SearchChannel(ABC):
    """Abstract search channel (vector, keyword, graph, etc)."""

    @abstractmethod
    async def search(
        self, query: str, collection_name: str, top_k: int = 10
    ) -> list[SearchResult]:
        """Search *query* against *collection_name*, returning top-K results."""
        ...
