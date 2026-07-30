"""Tests for the shared non-PDF structured-document pipeline."""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

from app.ingestion.parser import DocumentParser
from app.ingestion.structured import (
    BlockType,
    GenericStructuredChunker,
    StructuredDocument,
    parse_clipboard_document,
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


@pytest.mark.asyncio
async def test_clipboard_document_preserves_images_as_searchable_assets(monkeypatch):
    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color="red").save(image_buffer, format="PNG")

    class FakeDescriber:
        async def describe(self, image_bytes, mime_type, *, context=""):
            assert mime_type == "image/png"
            return "一张红色的示意图"

    monkeypatch.setattr(
        "app.ingestion.pdf.vlm.get_image_describer",
        lambda: FakeDescriber(),
    )
    bundle = json.dumps({
        "version": 1,
        "content": (
            "# 架构\n\n图片前面的任务规划说明。\n\n"
            "![架构图](clipboard-image://img-1)\n\n"
            "图片后面的执行流程说明。"
        ),
        "images": [{
            "id": "img-1",
            "filename": "architecture.png",
            "mimeType": "image/png",
            "alt": "架构图",
            "data": base64.b64encode(image_buffer.getvalue()).decode("ascii"),
        }],
    }).encode("utf-8")

    document = await parse_clipboard_document(bundle, "architecture.clipdoc")
    image_blocks = [
        block for block in document.blocks if block.block_type == BlockType.IMAGE
    ]

    assert len(document.assets) == 1
    assert len(image_blocks) == 1
    assert "图片前文: # 架构 图片前面的任务规划说明。" in image_blocks[0].embedding_text
    assert "图片内容: 一张红色的示意图" in image_blocks[0].embedding_text
    assert "图片后文: 图片后面的执行流程说明。" in image_blocks[0].embedding_text
    assert image_blocks[0].outline_path == ["架构"]
    assert "asset://" in image_blocks[0].content
    assert "clipboard-image://" not in document.to_markdown()

    chunks = GenericStructuredChunker().chunk(document)
    assert [chunk["block_type"] for chunk in chunks] == [
        "PARAGRAPH",
        "IMAGE",
        "PARAGRAPH",
    ]
    assert chunks[1]["asset_ids"]
    assert "图片前面的任务规划说明" in chunks[1]["embedding_content"]
    assert "图片后面的执行流程说明" in chunks[1]["embedding_content"]


@pytest.mark.asyncio
async def test_clipboard_document_preserves_table_structure_and_position():
    bundle = json.dumps({
        "version": 1,
        "content": (
            "# Agent评估\n\n以下是核心评估维度。\n\n"
            "| 评估维度 | 核心指标 | 说明 |\n"
            "| --- | --- | --- |\n"
            "| 任务完成率 | Success Rate | 是否达成目标 |\n"
            "| 过程正确性 | Tool Call Accuracy | 参数是否正确 |\n\n"
            "表格之后是抽检策略。"
        ),
        "images": [],
    }).encode("utf-8")

    document = await parse_clipboard_document(bundle, "evaluation.clipdoc")
    block_types = [block.block_type for block in document.blocks]
    assert block_types == [
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.TABLE,
        BlockType.PARAGRAPH,
    ]

    table = document.blocks[2]
    assert table.table_headers == ["评估维度", "核心指标", "说明"]
    assert table.table_rows[0] == ["任务完成率", "Success Rate", "是否达成目标"]
    assert table.outline_path == ["Agent评估"]

    chunks = GenericStructuredChunker().chunk(document)
    assert [chunk["block_type"] for chunk in chunks] == [
        "PARAGRAPH",
        "TABLE",
        "PARAGRAPH",
    ]
    assert "评估维度: 任务完成率" in chunks[1]["embedding_content"]
    assert "核心指标: Success Rate" in chunks[1]["embedding_content"]
    assert "表格之后是抽检策略" in chunks[2]["content"]


def test_term_normalization_is_idempotent():
    mappings = [{"source": "RAG", "target": "检索增强生成", "type": "EXACT"}]
    normalized, applied = normalize_query("RAG 怎么做", mappings)
    normalized_again, applied_again = normalize_query(normalized, mappings)

    assert normalized == "检索增强生成 怎么做"
    assert applied[0]["count"] == 1
    assert normalized_again == normalized
    assert applied_again == []
