"""Unit tests for RRF fusion and deduplication — no external deps."""
import pytest
from app.rag.search.base import SearchResult
from app.rag.postprocess.fusion import rrf_fusion, deduplicate


def make_result(chunk_id: str, content: str, score: float) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, content=content, score=score)


class TestRrfFusion:
    def test_single_list_returns_same(self):
        results = [make_result("a", "aaa", 0.9), make_result("b", "bbb", 0.5)]
        fused = rrf_fusion(results)
        assert len(fused) == 2
        assert fused[0].chunk_id == "a"

    def test_two_lists_merge(self):
        a = [make_result("x", "xxx", 0.9), make_result("y", "yyy", 0.5)]
        b = [make_result("y", "yyy", 0.8), make_result("z", "zzz", 0.3)]
        fused = rrf_fusion(a, b)
        ids = [r.chunk_id for r in fused]
        # y appears in both lists -> higher RRF score -> should be first
        assert ids[0] == "y"

    def test_empty_input(self):
        assert rrf_fusion() == []

    def test_empty_list(self):
        assert rrf_fusion([]) == []


class TestDeduplicate:
    def test_no_dupes(self):
        results = [
            make_result("a", "completely different content", 0.9),
            make_result("b", "another unrelated text", 0.5),
        ]
        assert len(deduplicate(results)) == 2

    def test_near_dupes_removed(self):
        results = [
            make_result("a", "this is almost the same content here", 0.9),
            make_result("b", "this is almost the same content here", 0.8),
            make_result("c", "unique text", 0.5),
        ]
        deduped = deduplicate(results)
        assert len(deduped) == 2
        assert deduped[1].chunk_id == "c"

    def test_empty(self):
        assert deduplicate([]) == []

    def test_threshold_sensitivity(self):
        results = [
            make_result("a", "abc def ghi", 0.9),
            make_result("b", "xyz uvw rst", 0.5),
        ]
        # These are very different -> both kept
        assert len(deduplicate(results, content_threshold=0.5)) == 2
