"""Tests for F7: Document version diff."""
from __future__ import annotations

# ─── F7.2 Diff computation ───


def test_diff_pure_addition():
    from app.services.document_diff import compute_chunk_diff

    old_hashes = ["aaa", "bbb"]
    new_hashes = ["aaa", "bbb", "ccc"]
    result = compute_chunk_diff(old_hashes, new_hashes)
    assert result.added == ["ccc"]
    assert result.removed == []
    assert result.modified == []


def test_diff_pure_removal():
    from app.services.document_diff import compute_chunk_diff

    old_hashes = ["aaa", "bbb", "ccc"]
    new_hashes = ["aaa"]
    result = compute_chunk_diff(old_hashes, new_hashes)
    assert result.removed == ["bbb", "ccc"]
    assert result.added == []


def test_diff_modification():
    from app.services.document_diff import compute_chunk_diff

    old_hashes = ["aaa", "bbb"]
    new_hashes = ["aaa", "xxx"]
    result = compute_chunk_diff(old_hashes, new_hashes)
    # bbb removed, xxx added — without content similarity they are not "modified"
    assert "bbb" in result.removed
    assert "xxx" in result.added


def test_diff_identical():
    from app.services.document_diff import compute_chunk_diff

    hashes = ["aaa", "bbb", "ccc"]
    result = compute_chunk_diff(hashes, hashes)
    assert result.added == []
    assert result.removed == []
    assert result.modified == []
    assert result.total_changes == 0


def test_diff_empty_to_content():
    from app.services.document_diff import compute_chunk_diff

    result = compute_chunk_diff([], ["x", "y"])
    assert result.added == ["x", "y"]
    assert result.total_changes == 2


def test_diff_content_to_empty():
    from app.services.document_diff import compute_chunk_diff

    result = compute_chunk_diff(["x", "y"], [])
    assert result.removed == ["x", "y"]
    assert result.total_changes == 2


# ─── F7.1 Version record ───


def test_version_record_dataclass():
    from app.services.document_diff import VersionRecord

    record = VersionRecord(
        doc_id="doc1",
        version_no=2,
        content_hash="abc123",
        chunk_count=15,
    )
    assert record.version_no == 2
    assert record.diff_summary is None


def test_version_numbering_sequential():
    from app.services.document_diff import next_version_no

    assert next_version_no([]) == 1
    assert next_version_no([1, 2, 3]) == 4
    assert next_version_no([1, 3]) == 4  # max + 1
