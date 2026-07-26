"""Integration-contract tests for both PDF ingestion entry points."""

from __future__ import annotations

import pytest

from app.ingestion.nodes.base import IngestionContext
from app.ingestion.nodes.parser_node import ParserNode
from app.ingestion.nodes.chunker_node import ChunkerNode
from app.ingestion.parser import DocumentParser
from app.ingestion.pdf.models import (
    PdfAsset,
    PdfBlock,
    PdfBoundingBox,
    StructuredPdfDocument,
)
from app.ingestion.pdf.asset_storage import (
    S3PdfAssetStorage,
    materialize_asset_urls,
)


def sample_document() -> StructuredPdfDocument:
    box = PdfBoundingBox(
        page_no=1,
        x0=36,
        top=650,
        x1=576,
        bottom=780,
        page_width=612,
        page_height=792,
    )
    asset = PdfAsset.from_bytes(
        page_no=1,
        bbox=box,
        filename="chart.png",
        mime_type="image/png",
        data=b"image-bytes",
        description="折线图显示收入持续增长。",
    )
    return StructuredPdfDocument(
        document_id="doc-1",
        source_file="report.pdf",
        page_count=1,
        blocks=[
            PdfBlock.table(
                block_id="table-1",
                page_no=1,
                bbox=box,
                headers=["季度", "收入"],
                rows=[["Q1", "100"], ["Q2", "120"]],
            ),
            PdfBlock.image(
                block_id="image-1",
                page_no=1,
                bbox=box,
                asset=asset,
                caption="收入趋势",
            ),
        ],
        assets=[asset],
    )


class FakeStructuredPdfParser:
    async def parse_bytes(self, content: bytes, *, source_file: str, document_id: str):
        assert content == b"%PDF-fake"
        assert source_file == "report.pdf"
        assert document_id == "doc-1"
        return sample_document()

    async def parse_file(self, file_path: str, *, source_file: str, document_id: str):
        assert file_path.endswith(".pdf")
        assert source_file == "report.pdf"
        assert document_id == "doc-1"
        return sample_document()


class TestDagPdfPath:
    @pytest.mark.asyncio
    async def test_parser_and_chunker_nodes_keep_structured_document(self):
        ctx = IngestionContext(
            raw_content=b"%PDF-fake",
            source_file_name="report.pdf",
            doc_id="doc-1",
            settings={"strategy": "FIXED_WINDOW", "table_max_rows": 20},
        )

        parse_result = await ParserNode(
            structured_pdf_parser=FakeStructuredPdfParser()
        )(ctx)
        chunk_result = await ChunkerNode()(ctx)

        assert parse_result.status == "success"
        assert parse_result.output["structured"] is True
        assert parse_result.output["table_count"] == 1
        assert parse_result.output["image_count"] == 1
        assert ctx.parsed_document is not None
        assert len(ctx.assets) == 1
        assert chunk_result.status == "success"
        assert {chunk["block_type"] for chunk in ctx.chunks} == {"TABLE", "IMAGE"}
        table_chunk = next(chunk for chunk in ctx.chunks if chunk["block_type"] == "TABLE")
        assert "季度: Q1" in table_chunk["embedding_content"]


class TestLegacyPdfPath:
    @pytest.mark.asyncio
    async def test_document_parser_returns_structured_pdf(self, tmp_path):
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-fake")
        parser = DocumentParser(structured_pdf_parser=FakeStructuredPdfParser())

        parsed = await parser.parse_document(
            str(pdf_path),
            document_id="doc-1",
            source_file="report.pdf",
        )

        assert isinstance(parsed, StructuredPdfDocument)
        assert parsed.page_count == 1
        assert len(parsed.assets) == 1


class FakeS3Client:
    def __init__(self):
        self.created = []
        self.uploaded = []

    def head_bucket(self, *, Bucket):
        raise RuntimeError("missing")

    def create_bucket(self, *, Bucket):
        self.created.append(Bucket)

    def put_object(self, **kwargs):
        self.uploaded.append(kwargs)


class TestPdfAssetStorage:
    @pytest.mark.asyncio
    async def test_asset_upload_is_content_addressed_and_materialized(self):
        asset = sample_document().assets[0]
        fake = FakeS3Client()
        storage = S3PdfAssetStorage(client=fake)

        stored = await storage.upload(asset, kb_id="kb-1", doc_id="doc-1")
        chunks = [{
            "content": f"![收入趋势](asset://{asset.asset_id})",
            "asset_ids": [asset.asset_id],
        }]
        materialize_asset_urls(chunks, {asset.asset_id: stored.storage_url})

        assert fake.created == [storage.bucket]
        assert len(fake.uploaded) == 1
        assert asset.content_hash[:32] in stored.storage_key
        assert stored.storage_key.startswith("assets/kb-1/doc-1/")
        assert stored.storage_url in chunks[0]["content"]
        assert "asset://" not in chunks[0]["content"]
