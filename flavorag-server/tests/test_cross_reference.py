"""Tests for cross-element reference injection."""
import pytest
from app.ingestion.cross_reference import (
    inject_cross_references,
    _find_reference_sentences,
    _find_target_chunk,
)


def _chunk(content: str, block_type: str = "PARAGRAPH", embedding: str = "") -> dict:
    return {
        "content": content,
        "embedding_content": embedding or content,
        "block_type": block_type,
        "chunk_index": 0,
        "char_count": len(content),
    }


class TestFindReferenceSentences:
    def test_numbered_table_ref(self):
        refs = _find_reference_sentences("各型号参数如表3所示。")
        assert len(refs) == 1
        assert refs[0]["ref_type"] == "TABLE"
        assert refs[0]["ref_label"] == "3"
        assert refs[0]["positional"] is False

    def test_numbered_figure_ref(self):
        refs = _find_reference_sentences("系统架构见图5。")
        assert len(refs) == 1
        assert refs[0]["ref_type"] == "IMAGE"
        assert refs[0]["ref_label"] == "5"

    def test_chinese_numeral_ref(self):
        refs = _find_reference_sentences("详细数据见表一。")
        assert len(refs) == 1
        assert refs[0]["ref_label"] == "一"

    def test_positional_table_ref(self):
        refs = _find_reference_sentences("详细参数详见下表。")
        assert len(refs) == 1
        assert refs[0]["ref_type"] == "TABLE"
        assert refs[0]["positional"] is True

    def test_positional_figure_ref(self):
        refs = _find_reference_sentences("流程如下图所示。")
        assert len(refs) == 1
        assert refs[0]["ref_type"] == "IMAGE"
        assert refs[0]["positional"] is True

    def test_english_table_ref(self):
        refs = _find_reference_sentences("See Table 3 for details.")
        assert len(refs) == 1
        assert refs[0]["ref_type"] == "TABLE"
        assert refs[0]["ref_label"] == "3"

    def test_english_figure_ref(self):
        refs = _find_reference_sentences("As shown in Figure 2, the trend is clear.")
        assert len(refs) == 1
        assert refs[0]["ref_type"] == "IMAGE"
        assert refs[0]["ref_label"] == "2"

    def test_no_reference(self):
        refs = _find_reference_sentences("这是一段普通的文字描述。")
        assert refs == []

    def test_multiple_sentences_multiple_refs(self):
        text = "系统架构见图1。各模块参数如表2所示。"
        refs = _find_reference_sentences(text)
        assert len(refs) == 2
        types = {r["ref_type"] for r in refs}
        assert types == {"TABLE", "IMAGE"}

    def test_generic_forward_table(self):
        refs = _find_reference_sentences("具体配置参见表格。")
        assert len(refs) == 1
        assert refs[0]["ref_type"] == "TABLE"
        assert refs[0]["positional"] is True


class TestFindTargetChunk:
    def test_forward_table(self):
        chunks = [
            _chunk("如表1所示"),
            _chunk("| A | B |\n|---|---|\n| 1 | 2 |", "TABLE"),
        ]
        assert _find_target_chunk(chunks, 0, "TABLE", False) == 1

    def test_forward_image(self):
        chunks = [
            _chunk("见图3"),
            _chunk("![img](x.png)", "IMAGE"),
        ]
        assert _find_target_chunk(chunks, 0, "IMAGE", False) == 1

    def test_backward_table(self):
        chunks = [
            _chunk("| A | B |", "TABLE"),
            _chunk("如上表所示"),
        ]
        assert _find_target_chunk(chunks, 1, "TABLE", True) == 0

    def test_no_target_within_distance(self):
        chunks = [
            _chunk("见表1"),
            _chunk("无关段落1"),
            _chunk("无关段落2"),
            _chunk("无关段落3"),
            _chunk("无关段落4"),
            _chunk("| A |", "TABLE"),
        ]
        # Distance > 3, should not find
        assert _find_target_chunk(chunks, 0, "TABLE", False) is None

    def test_skips_non_matching_type(self):
        chunks = [
            _chunk("见图1"),
            _chunk("| A |", "TABLE"),  # not IMAGE
            _chunk("![img](x.png)", "IMAGE"),
        ]
        assert _find_target_chunk(chunks, 0, "IMAGE", False) == 2


class TestInjectCrossReferences:
    def test_basic_table_injection(self):
        chunks = [
            _chunk("各型号参数如表1所示。请仔细阅读。"),
            _chunk("| 型号 | 重量 |\n|---|---|\n| A | 5kg |", "TABLE", "型号: A；重量: 5kg"),
        ]
        result = inject_cross_references(chunks)
        assert "引用上下文" in result[1]["embedding_content"]
        assert "表1" in result[1]["embedding_content"]
        # Original embedding content preserved
        assert "型号: A" in result[1]["embedding_content"]
        # Metadata recorded
        assert "cross_references" in result[1]["metadata_json"]

    def test_basic_image_injection(self):
        chunks = [
            _chunk("系统整体架构如图2所示。"),
            _chunk("![架构图](arch.png)", "IMAGE", "[图片: 架构图]"),
        ]
        result = inject_cross_references(chunks)
        assert "引用上下文" in result[1]["embedding_content"]
        assert "图2" in result[1]["embedding_content"]
        assert "[图片: 架构图]" in result[1]["embedding_content"]

    def test_positional_reference(self):
        chunks = [
            _chunk("详细实验数据详见下表。"),
            _chunk("| 实验 | 结果 |\n|---|---|\n| 1 | OK |", "TABLE", "实验: 1；结果: OK"),
        ]
        result = inject_cross_references(chunks)
        assert "详见下表" in result[1]["embedding_content"]

    def test_no_injection_when_no_reference(self):
        chunks = [
            _chunk("这是一段普通文字。"),
            _chunk("| A | B |", "TABLE", "A: x"),
        ]
        result = inject_cross_references(chunks)
        assert result[1]["embedding_content"] == "A: x"

    def test_no_injection_for_distant_target(self):
        chunks = [
            _chunk("见表1。"),
            _chunk("段落A"),
            _chunk("段落B"),
            _chunk("段落C"),
            _chunk("段落D"),
            _chunk("| A |", "TABLE", "col: val"),
        ]
        result = inject_cross_references(chunks)
        # Too far away, no injection
        assert result[5]["embedding_content"] == "col: val"

    def test_multiple_refs_to_same_target(self):
        chunks = [
            _chunk("如表1所示，性能提升明显。"),
            _chunk("综合对比结果参见表1。"),
            _chunk("| 指标 | 值 |\n|---|---|\n| QPS | 1000 |", "TABLE", "指标: QPS；值: 1000"),
        ]
        result = inject_cross_references(chunks)
        # Both sentences should be injected
        embedding = result[2]["embedding_content"]
        assert "性能提升明显" in embedding
        assert "综合对比结果" in embedding

    def test_empty_chunks(self):
        assert inject_cross_references([]) == []

    def test_single_chunk(self):
        chunks = [_chunk("见表1")]
        result = inject_cross_references(chunks)
        assert result[0]["embedding_content"] == "见表1"

    def test_does_not_scan_table_chunks_for_refs(self):
        """TABLE chunks should not be scanned for references."""
        chunks = [
            _chunk("| 见表1 |", "TABLE", "col: 见表1"),
            _chunk("| A |", "TABLE", "col: A"),
        ]
        result = inject_cross_references(chunks)
        # No injection because source is TABLE type
        assert result[1]["embedding_content"] == "col: A"

    def test_english_reference(self):
        chunks = [
            _chunk("The results are shown in Table 2. Performance improved."),
            _chunk("| Metric | Value |\n|---|---|\n| Latency | 5ms |", "TABLE", "Metric: Latency; Value: 5ms"),
        ]
        result = inject_cross_references(chunks)
        assert "Table 2" in result[1]["embedding_content"]

    def test_chunk_without_embedding_content_field(self):
        """Chunks without embedding_content should use content as fallback."""
        chunks = [
            _chunk("数据如图1所示。"),
            {"content": "![chart](c.png)", "block_type": "IMAGE", "chunk_index": 1, "char_count": 18},
        ]
        result = inject_cross_references(chunks)
        assert "引用上下文" in result[1]["embedding_content"]
        assert "![chart](c.png)" in result[1]["embedding_content"]
