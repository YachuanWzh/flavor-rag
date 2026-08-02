"""Document version diff — hash-based chunk comparison and version tracking."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiffSummary:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)


@dataclass
class VersionRecord:
    doc_id: str
    version_no: int
    content_hash: str
    chunk_count: int = 0
    file_size: int = 0
    uploaded_by: str = ""
    diff_summary: DiffSummary | None = None


def compute_chunk_diff(
    old_hashes: list[str], new_hashes: list[str]
) -> DiffSummary:
    """Compare two generations of chunk content hashes.

    Uses set difference on hashes. Without raw content, true "modified"
    detection requires content-similarity comparison at a higher layer;
    here we report pure additions and removals.
    """
    old_set = set(old_hashes)
    new_set = set(new_hashes)

    added = [h for h in new_hashes if h not in old_set]
    removed = [h for h in old_hashes if h not in new_set]

    return DiffSummary(added=added, removed=removed, modified=[])


def next_version_no(existing_versions: list[int]) -> int:
    """Return the next sequential version number."""
    if not existing_versions:
        return 1
    return max(existing_versions) + 1
