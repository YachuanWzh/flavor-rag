"""SDD acceptance tests for complex PDF multimodal ingestion."""

from __future__ import annotations

from io import BytesIO

import pytest

from app.ingestion.pdf.models import (
    PdfAsset,
    PdfBlock,
    PdfBlockType,
    PdfBoundingBox,
    StructuredPdfDocument,
)
from app.ingestion.pdf.table_merger import CrossPageTableMerger
from app.ingestion.pdf.chunker import StructuredPdfChunker
from app.ingestion.pdf.parser import StructuredPdfParser


PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0


def bbox(page: int, top: float, bottom: float, x0: float = 36, x1: float = 576):
    return PdfBoundingBox(
        page_no=page,
        x0=x0,
        top=top,
        x1=x1,
        bottom=bottom,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
    )


def table(
    block_id: str,
    page: int,
    *,
    top: float,
    bottom: float,
    headers: list[str],
    rows: list[list[str]],
    x0: float = 36,
    x1: float = 576,
) -> PdfBlock:
    return PdfBlock.table(
        block_id=block_id,
        page_no=page,
        bbox=bbox(page, top, bottom, x0=x0, x1=x1),
        headers=headers,
        rows=rows,
    )


class TestCrossPageTableMerger:
    def test_repeated_header_continuation_merges_and_drops_repeat(self):
        first = table(
            "t1", 1, top=620, bottom=780,
            headers=["ID", "姓名", "分数"],
            rows=[["1", "张三", "91"], ["2", "李四", "88"]],
        )
        second = table(
            "t2", 2, top=28, bottom=180,
            headers=["ID", "姓名", "分数"],
            rows=[["3", "王五", "95"], ["4", "赵六", "89"]],
        )

        merged = CrossPageTableMerger().merge([first, second])

        assert len(merged) == 1
        logical = merged[0]
        assert logical.page_start == 1
        assert logical.page_end == 2
        assert logical.table_headers == ["ID", "姓名", "分数"]
        assert logical.table_rows == [
            ["1", "张三", "91"],
            ["2", "李四", "88"],
            ["3", "王五", "95"],
            ["4", "赵六", "89"],
        ]
        assert logical.table_row_pages == [1, 1, 2, 2]
        assert logical.metadata["cross_page"] is True
        assert logical.metadata["continuation_pages"] == [1, 2]
        assert logical.metadata["header_mode"] == "repeated"

    def test_three_page_table_becomes_one_logical_table(self):
        fragments = [
            table(
                "p1", 1, top=650, bottom=785,
                headers=["序号", "金额"],
                rows=[["1", "100"]],
            ),
            table(
                "p2", 2, top=20, bottom=785,
                headers=["序号", "金额"],
                rows=[["2", "200"]],
            ),
            table(
                "p3", 3, top=18, bottom=160,
                headers=["序号", "金额"],
                rows=[["3", "300"]],
            ),
        ]

        merged = CrossPageTableMerger().merge(fragments)

        assert len(merged) == 1
        assert merged[0].page_start == 1
        assert merged[0].page_end == 3
        assert merged[0].table_rows == [["1", "100"], ["2", "200"], ["3", "300"]]
        assert merged[0].table_row_pages == [1, 2, 3]

    def test_headerless_numeric_continuation_restores_first_row(self):
        first = table(
            "t1", 1, top=620, bottom=780,
            headers=["ID", "姓名", "分数"],
            rows=[["1", "张三", "91"], ["2", "李四", "88"]],
        )
        # PDF table extractors commonly treat the first continuation data row
        # as a provisional header when the repeated header is absent.
        second = table(
            "t2", 2, top=25, bottom=160,
            headers=["3", "王五", "95"],
            rows=[["4", "赵六", "89"]],
        )

        merged = CrossPageTableMerger().merge([first, second])

        assert len(merged) == 1
        assert merged[0].table_rows[-2:] == [["3", "王五", "95"], ["4", "赵六", "89"]]
        assert merged[0].table_row_pages[-2:] == [2, 2]
        assert merged[0].metadata["header_mode"] == "headerless"

    def test_ambiguous_text_only_headerless_fragments_are_not_merged(self):
        first = table(
            "t1", 1, top=620, bottom=780,
            headers=["区域", "负责人"],
            rows=[["华东", "张三"], ["华南", "李四"]],
        )
        second = table(
            "t2", 2, top=25, bottom=160,
            headers=["华北", "王五"],
            rows=[["西南", "赵六"]],
        )

        merged = CrossPageTableMerger().merge([first, second])

        assert len(merged) == 2
        assert merged[0].metadata.get("cross_page") is not True

    def test_table_away_from_page_boundary_is_not_merged(self):
        first = table(
            "t1", 1, top=200, bottom=500,
            headers=["ID", "金额"],
            rows=[["1", "100"]],
        )
        second = table(
            "t2", 2, top=25, bottom=160,
            headers=["ID", "金额"],
            rows=[["2", "200"]],
        )

        assert len(CrossPageTableMerger().merge([first, second])) == 2

    def test_different_geometry_is_not_merged(self):
        first = table(
            "t1", 1, top=620, bottom=780,
            headers=["ID", "金额"],
            rows=[["1", "100"]],
        )
        second = table(
            "t2", 2, top=25, bottom=160,
            headers=["ID", "金额"],
            rows=[["2", "200"]],
            x0=160,
            x1=450,
        )

        assert len(CrossPageTableMerger().merge([first, second])) == 2

    def test_new_table_caption_prevents_false_merge(self):
        first = table(
            "t1", 1, top=620, bottom=780,
            headers=["ID", "金额"],
            rows=[["1", "100"]],
        )
        second = table(
            "t2", 2, top=55, bottom=180,
            headers=["ID", "金额"],
            rows=[["2", "200"]],
        )
        second.metadata["preceding_text"] = "表 2：其他项目预算"

        assert len(CrossPageTableMerger().merge([first, second])) == 2


class TestStructuredPdfChunker:
    def test_table_uses_markdown_for_display_and_key_values_for_embedding(self):
        logical = table(
            "table-1", 1, top=620, bottom=780,
            headers=["ID", "姓名", "分数"],
            rows=[["1", "张三", "91"]],
        )
        continuation = table(
            "table-2", 2, top=25, bottom=160,
            headers=["ID", "姓名", "分数"],
            rows=[["2", "李四", "88"], ["3", "王五", "95"]],
        )
        logical = CrossPageTableMerger().merge([logical, continuation])[0]
        document = StructuredPdfDocument(
            document_id="doc-1",
            source_file="scores.pdf",
            page_count=2,
            blocks=[logical],
        )

        chunks = StructuredPdfChunker(table_max_rows=2).chunk(document)

        assert len(chunks) == 2
        assert "| ID | 姓名 | 分数 |" in chunks[0]["content"]
        assert "ID: 1" in chunks[0]["embedding_content"]
        assert "姓名: 张三" in chunks[0]["embedding_content"]
        assert chunks[0]["block_type"] == "TABLE"
        assert chunks[0]["metadata_json"]["logical_table_id"] == "table-1"
        assert chunks[0]["metadata_json"]["cross_page"] is True
        assert chunks[0]["metadata_json"]["row_start"] == 0
        assert chunks[0]["metadata_json"]["row_end"] == 2
        assert chunks[0]["page_start"] == 1
        assert chunks[0]["page_end"] == 2
        assert chunks[1]["metadata_json"]["row_start"] == 2
        assert chunks[1]["metadata_json"]["row_end"] == 3
        assert chunks[1]["page_start"] == 2
        assert chunks[1]["page_end"] == 2

    def test_image_is_atomic_and_description_is_embedding_text(self):
        image_bbox = bbox(2, 220, 520, x0=100, x1=500)
        asset = PdfAsset.from_bytes(
            page_no=2,
            bbox=image_bbox,
            filename="chart.png",
            mime_type="image/png",
            data=b"fake-png",
            description="柱状图显示 2025 年销售额同比增长 18%。",
        )
        block = PdfBlock.image(
            block_id="image-1",
            page_no=2,
            bbox=image_bbox,
            asset=asset,
            caption="年度销售图",
        )
        document = StructuredPdfDocument(
            document_id="doc-1",
            source_file="report.pdf",
            page_count=2,
            blocks=[block],
            assets=[asset],
        )

        chunks = StructuredPdfChunker().chunk(document)

        assert len(chunks) == 1
        assert chunks[0]["block_type"] == "IMAGE"
        assert chunks[0]["embedding_content"] == "柱状图显示 2025 年销售额同比增长 18%。"
        assert chunks[0]["asset_ids"] == [asset.asset_id]
        assert f"asset://{asset.asset_id}" in chunks[0]["content"]
        assert chunks[0]["metadata_json"]["description_status"] == "enriched"


class FakeImageDescriber:
    async def describe(self, image_bytes: bytes, mime_type: str, *, context: str = "") -> str:
        assert image_bytes
        assert mime_type.startswith("image/")
        return "测试图片：蓝色矩形，包含跨页表格附件标识。"


def _build_two_page_pdf_with_table_and_image() -> bytes:
    pytest.importorskip("reportlab")
    pytest.importorskip("PIL")

    from PIL import Image
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    style = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ])

    first = Table(
        [["ID", "Name", "Score"], ["1", "Alice", "91"], ["2", "Bob", "88"]],
        colWidths=[120, 180, 120],
        rowHeights=24,
    )
    first.setStyle(style)
    first.wrapOn(pdf, 540, 200)
    first.drawOn(pdf, 72, 18)
    pdf.showPage()

    second = Table(
        [["ID", "Name", "Score"], ["3", "Carol", "95"], ["4", "David", "89"]],
        colWidths=[120, 180, 120],
        rowHeights=24,
    )
    second.setStyle(style)
    second.wrapOn(pdf, 540, 200)
    second.drawOn(pdf, 72, 700)

    image_buffer = BytesIO()
    image = Image.new("RGB", (80, 40), color=(40, 120, 220))
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    pdf.drawImage(ImageReader(image_buffer), 72, 500, width=160, height=80)
    pdf.save()
    return output.getvalue()


class TestStructuredPdfParserIntegration:
    @pytest.mark.asyncio
    async def test_generated_pdf_merges_table_and_enriches_image(self):
        content = _build_two_page_pdf_with_table_and_image()
        parser = StructuredPdfParser(image_describer=FakeImageDescriber())

        document = await parser.parse_bytes(
            content,
            source_file="two-page-report.pdf",
            document_id="doc-test",
        )

        tables = [b for b in document.blocks if b.block_type == PdfBlockType.TABLE]
        images = [b for b in document.blocks if b.block_type == PdfBlockType.IMAGE]

        assert document.page_count == 2
        assert len(tables) == 1
        assert tables[0].page_start == 1
        assert tables[0].page_end == 2
        assert [row[0] for row in tables[0].table_rows] == ["1", "2", "3", "4"]
        assert len(images) >= 1
        assert any("蓝色矩形" in image.embedding_text for image in images)
        assert document.metadata["cross_page_table_count"] == 1
