"""Tests for the shared non-PDF structured-document pipeline."""

from __future__ import annotations

import pytest

from app.ingestion.parser import DocumentParser
from app.ingestion.structured import (
    BlockType,
    GenericStructuredChunker,
    StructuredDocument,
    parse_csv_document,
)
from app.rag.rewrite import normalize_query


def test_csv_preserves_table_semantics_and_location():
    document = parse_csv_document(
        "产品,价格\n红茶,18\n绿茶,20\n".encode("utf-8"),
        "menu.csv",
    )
    assert isinstance(document, StructuredDocument)
    assert document.blocks[0].block_type == BlockType.TABLE
    assert document.blocks[0].table_headers == ["产品", "价格"]

    chunks = GenericStructuredChunker(table_max_rows=1).chunk(document)
    assert len(chunks) == 2
    assert chunks[0]["block_type"] == "TABLE"
    assert "产品: 红茶" in chunks[0]["embedding_content"]
    assert chunks[0]["metadata_json"]["sourceFormat"] == "csv"
    assert chunks[0]["metadata_json"]["rowStart"] == 1


@pytest.mark.asyncio
async def test_legacy_parser_entry_also_returns_structured_document(tmp_path):
    file_path = tmp_path / "guide.md"
    file_path.write_text("# 安装\n\n第一步配置环境。\n\n- 启动服务", encoding="utf-8")

    document = await DocumentParser().parse_document(str(file_path))

    assert isinstance(document, StructuredDocument)
    assert any(block.block_type == BlockType.HEADING for block in document.blocks)
    assert "第一步配置环境" in document.to_markdown()


def test_term_normalization_is_idempotent():
    mappings = [{"source": "RAG", "target": "检索增强生成", "type": "EXACT"}]
    normalized, applied = normalize_query("RAG 怎么做", mappings)
    normalized_again, applied_again = normalize_query(normalized, mappings)

    assert normalized == "检索增强生成 怎么做"
    assert applied[0]["count"] == 1
    assert normalized_again == normalized
    assert applied_again == []
