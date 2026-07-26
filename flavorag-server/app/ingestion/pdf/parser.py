"""Layout-aware PDF parser using pdfplumber and pypdf.

Tables and text are extracted with page coordinates. Embedded raster images are
extracted with pypdf and optionally enriched by a write-time VLM.
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
import statistics
from io import BytesIO
from typing import Any

from app.config.logging_config import get_logger
from app.ingestion.pdf.models import (
    PdfAsset,
    PdfBlock,
    PdfBlockType,
    PdfBoundingBox,
    StructuredPdfDocument,
    new_block_id,
)
from app.ingestion.pdf.table_merger import CrossPageTableMerger
from app.ingestion.pdf.vlm import ImageDescriber, get_image_describer
from app.ingestion.pdf.ocr import OCRProvider, get_ocr_provider

_log = get_logger("flavorag.ingestion.pdf")


class StructuredPdfParser:
    PARSER_VERSION = "pdfplumber-pypdf-v1"

    def __init__(
        self,
        *,
        image_describer: ImageDescriber | None = None,
        ocr_provider: OCRProvider | None = None,
        ocr_min_native_chars: int | None = None,
        vlm_max_concurrency: int | None = None,
        image_min_area_ratio: float = 0.001,
    ):
        self.image_describer = image_describer or get_image_describer()
        self.ocr_provider = ocr_provider or get_ocr_provider()
        if vlm_max_concurrency is None:
            from app.config.settings import settings
            vlm_max_concurrency = settings.vlm_max_concurrency
            if ocr_min_native_chars is None:
                ocr_min_native_chars = settings.pdf_ocr_min_native_chars
        self.vlm_max_concurrency = max(1, vlm_max_concurrency)
        self.ocr_min_native_chars = max(0, ocr_min_native_chars or 0)
        self.image_min_area_ratio = max(0.0, image_min_area_ratio)
        self.table_merger = CrossPageTableMerger()

    async def parse_file(
        self, file_path: str, *, document_id: str = "", source_file: str = ""
    ) -> StructuredPdfDocument:
        with open(file_path, "rb") as handle:
            content = handle.read()
        return await self.parse_bytes(
            content,
            document_id=document_id,
            source_file=source_file or file_path.rsplit("\\", 1)[-1],
        )

    async def parse_bytes(
        self,
        content: bytes,
        *,
        source_file: str = "",
        document_id: str = "",
    ) -> StructuredPdfDocument:
        if not content:
            raise ValueError("PDF content is empty")

        try:
            import pdfplumber
            from pypdf import PdfReader
        except ImportError as exc:
            raise ImportError(
                "pdfplumber and pypdf are required for structured PDF parsing"
            ) from exc

        table_blocks: list[PdfBlock] = []
        text_blocks: list[PdfBlock] = []
        assets: list[PdfAsset] = []
        image_boxes_by_page: dict[int, list[PdfBoundingBox]] = {}
        page_dimensions: dict[int, tuple[float, float]] = {}
        native_chars_by_page: dict[int, int] = {}

        with pdfplumber.open(BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            for page_index, page in enumerate(pdf.pages, start=1):
                page_tables = self._extract_tables(page, page_index)
                table_blocks.extend(page_tables)
                table_boxes = [block.first_bbox for block in page_tables if block.first_bbox]
                page_text_blocks = self._extract_text_blocks(page, page_index, table_boxes)
                page_dimensions[page_index] = (float(page.width), float(page.height))
                native_chars_by_page[page_index] = sum(
                    len(block.content) for block in page_text_blocks
                ) + sum(
                    len(cell)
                    for block in page_tables
                    for row in [block.table_headers, *block.table_rows]
                    for cell in row
                )
                self._annotate_table_context(page_tables, page_text_blocks)
                text_blocks.extend(page_text_blocks)
                image_boxes_by_page[page_index] = self._extract_image_boxes(page, page_index)

        ocr_blocks = await self._extract_ocr_blocks(
            content,
            page_dimensions=page_dimensions,
            native_chars_by_page=native_chars_by_page,
        )
        text_blocks.extend(ocr_blocks)

        reader = PdfReader(BytesIO(content))
        assets = self._extract_image_assets(reader, image_boxes_by_page)
        asset_scope = (document_id or "unscoped").replace("-", "")[:8]
        for asset in assets:
            asset.asset_id = f"asset_{asset_scope}_{asset.content_hash[:16]}"
        await self._enrich_assets(assets, source_file)

        image_blocks = [
            PdfBlock.image(
                block_id=new_block_id("image"),
                page_no=asset.page_no,
                bbox=asset.bbox,
                asset=asset,
                caption=_caption_from_filename(asset.filename),
            )
            for asset in assets
        ]

        merged_tables = self.table_merger.merge(table_blocks)
        cross_page_count = sum(
            1 for block in merged_tables if block.metadata.get("cross_page")
        )
        blocks = sorted(
            [*text_blocks, *merged_tables, *image_blocks],
            key=_reading_order_key,
        )
        document = StructuredPdfDocument(
            document_id=document_id,
            source_file=source_file,
            page_count=page_count,
            blocks=blocks,
            assets=assets,
            metadata={
                "parser": self.PARSER_VERSION,
                "page_count": page_count,
                "block_count": len(blocks),
                "table_count": len(merged_tables),
                "cross_page_table_count": cross_page_count,
                "image_count": len(assets),
                "vlm_enriched_image_count": sum(bool(asset.description) for asset in assets),
                "ocr_block_count": len(ocr_blocks),
                "ocr_page_count": len({block.page_start for block in ocr_blocks}),
            },
        )
        _log.info(
            "structured_pdf_parsed",
            source_file=source_file,
            pages=page_count,
            blocks=len(blocks),
            tables=len(merged_tables),
            cross_page_tables=cross_page_count,
            images=len(assets),
        )
        return document

    async def _extract_ocr_blocks(
        self,
        content: bytes,
        *,
        page_dimensions: dict[int, tuple[float, float]],
        native_chars_by_page: dict[int, int],
    ) -> list[PdfBlock]:
        if not self.ocr_provider.enabled:
            return []
        pages = [
            page_no
            for page_no in sorted(page_dimensions)
            if native_chars_by_page.get(page_no, 0) < self.ocr_min_native_chars
        ]
        if not pages:
            return []

        async def process(page_no: int) -> list[PdfBlock]:
            page_width, page_height = page_dimensions[page_no]
            try:
                image = await asyncio.to_thread(
                    _render_pdf_page_png,
                    content,
                    page_no,
                )
                recognized = await self.ocr_provider.recognize(
                    image,
                    page_no=page_no,
                    page_width=page_width,
                    page_height=page_height,
                )
            except Exception as exc:
                _log.warning("pdf_ocr_failed", page=page_no, error=str(exc))
                return []

            blocks: list[PdfBlock] = []
            for index, item in enumerate(recognized):
                if item.normalized:
                    x0 = item.x0 * page_width
                    top = item.top * page_height
                    x1 = item.x1 * page_width
                    bottom = item.bottom * page_height
                else:
                    x0, top, x1, bottom = item.x0, item.top, item.x1, item.bottom
                box = PdfBoundingBox(
                    page_no=page_no,
                    x0=max(0.0, min(page_width, x0)),
                    top=max(0.0, min(page_height, top)),
                    x1=max(0.0, min(page_width, x1)),
                    bottom=max(0.0, min(page_height, bottom)),
                    page_width=page_width,
                    page_height=page_height,
                )
                blocks.append(
                    PdfBlock.text(
                        block_id=f"ocr_p{page_no}_{index + 1}",
                        block_type=PdfBlockType.PARAGRAPH,
                        page_no=page_no,
                        bbox=box,
                        content=item.text,
                        metadata={
                            "extraction_method": "ocr",
                            "confidence": item.confidence,
                        },
                    )
                )
            return blocks

        grouped = await asyncio.gather(*(process(page_no) for page_no in pages))
        return [block for page_blocks in grouped for block in page_blocks]

    def _extract_tables(self, page, page_no: int) -> list[PdfBlock]:
        try:
            found = page.find_tables(table_settings={
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "intersection_tolerance": 4,
            })
            tables = list(getattr(found, "tables", found))
            if not tables:
                found = page.find_tables()
                tables = list(getattr(found, "tables", found))
        except Exception as exc:
            _log.warning("pdf_table_detection_failed", page=page_no, error=str(exc))
            return []

        output: list[PdfBlock] = []
        for index, table in enumerate(tables):
            try:
                raw_rows = table.extract() or []
            except Exception as exc:
                _log.warning(
                    "pdf_table_extract_failed", page=page_no, table=index, error=str(exc)
                )
                continue
            rows = [_normalize_row(row) for row in raw_rows]
            rows = [row for row in rows if any(cell for cell in row)]
            if not rows:
                continue
            width = max(len(row) for row in rows)
            rows = [_pad(row, width) for row in rows]
            headers = rows[0]
            data_rows = rows[1:]
            if not any(headers):
                headers = [f"column_{i + 1}" for i in range(width)]
            x0, top, x1, bottom = (float(value) for value in table.bbox)
            box = PdfBoundingBox(
                page_no=page_no,
                x0=x0,
                top=top,
                x1=x1,
                bottom=bottom,
                page_width=float(page.width),
                page_height=float(page.height),
            )
            output.append(PdfBlock.table(
                block_id=f"table_p{page_no}_{index + 1}",
                page_no=page_no,
                bbox=box,
                headers=headers,
                rows=data_rows,
                metadata={
                    "parser": "pdfplumber",
                    "extraction_method": "native",
                    "confidence": 1.0,
                    "table_index_on_page": index,
                },
            ))
        return output

    def _extract_text_blocks(
        self, page, page_no: int, table_boxes: list[PdfBoundingBox]
    ) -> list[PdfBlock]:
        try:
            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=True,
                extra_attrs=["size"],
            )
        except Exception:
            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=True,
            )
        words = [
            word for word in words
            if not any(_word_inside_box(word, box) for box in table_boxes)
        ]
        if not words:
            return []

        sizes = [float(word.get("size", 10.0) or 10.0) for word in words]
        median_size = statistics.median(sizes) if sizes else 10.0
        line_groups: list[list[dict[str, Any]]] = []
        for word in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
            if (
                not line_groups
                or abs(float(word["top"]) - _line_top(line_groups[-1])) > 3.0
            ):
                line_groups.append([word])
            else:
                line_groups[-1].append(word)

        output: list[PdfBlock] = []
        for line_index, line in enumerate(line_groups):
            line.sort(key=lambda word: float(word["x0"]))
            text = " ".join(str(word.get("text", "")).strip() for word in line).strip()
            if not text:
                continue
            max_size = max(float(word.get("size", median_size) or median_size) for word in line)
            is_heading = max_size >= median_size * 1.25 and len(text) <= 160
            box = PdfBoundingBox(
                page_no=page_no,
                x0=min(float(word["x0"]) for word in line),
                top=min(float(word["top"]) for word in line),
                x1=max(float(word["x1"]) for word in line),
                bottom=max(float(word["bottom"]) for word in line),
                page_width=float(page.width),
                page_height=float(page.height),
            )
            if box.top <= 24 or box.bottom >= float(page.height) - 24:
                block_type = PdfBlockType.HEADER_FOOTER
            elif is_heading:
                block_type = PdfBlockType.HEADING
            elif re.match(r"^(?:[-*•·]|\d+[.)、])\s*", text):
                block_type = PdfBlockType.LIST
            elif _looks_like_formula(text):
                block_type = PdfBlockType.FORMULA
            else:
                block_type = PdfBlockType.PARAGRAPH
            output.append(PdfBlock.text(
                block_id=f"text_p{page_no}_{line_index + 1}",
                block_type=block_type,
                page_no=page_no,
                bbox=box,
                content=text,
                metadata={
                    "font_size": round(max_size, 2),
                    "heading_level": 1 if is_heading else None,
                    "extraction_method": "native",
                    "confidence": 1.0,
                },
            ))
        return output

    def _extract_image_boxes(self, page, page_no: int) -> list[PdfBoundingBox]:
        output: list[PdfBoundingBox] = []
        for image in page.images:
            try:
                box = PdfBoundingBox(
                    page_no=page_no,
                    x0=float(image["x0"]),
                    top=float(image["top"]),
                    x1=float(image["x1"]),
                    bottom=float(image["bottom"]),
                    page_width=float(page.width),
                    page_height=float(page.height),
                )
            except (KeyError, TypeError, ValueError):
                continue
            page_area = box.page_width * box.page_height
            if page_area and (box.width * box.height) / page_area >= self.image_min_area_ratio:
                output.append(box)
        return output

    def _annotate_table_context(
        self, tables: list[PdfBlock], text_blocks: list[PdfBlock]
    ) -> None:
        for table in tables:
            table_box = table.first_bbox
            if not table_box:
                continue
            immediately_above = [
                block for block in text_blocks
                if block.last_bbox
                and block.last_bbox.bottom <= table_box.top
                and table_box.top - block.last_bbox.bottom <= 72
            ]
            if immediately_above:
                immediately_above.sort(key=lambda block: block.last_bbox.bottom)
                table.metadata["preceding_text"] = " ".join(
                    block.content for block in immediately_above[-2:]
                )

    def _extract_image_assets(
        self, reader, image_boxes_by_page: dict[int, list[PdfBoundingBox]]
    ) -> list[PdfAsset]:
        output: list[PdfAsset] = []
        seen_hashes: set[str] = set()
        for page_no, page in enumerate(reader.pages, start=1):
            boxes = image_boxes_by_page.get(page_no, [])
            try:
                images = list(page.images)
            except Exception as exc:
                _log.warning("pdf_image_extract_failed", page=page_no, error=str(exc))
                continue
            for index, image in enumerate(images):
                try:
                    data = bytes(image.data)
                except Exception as exc:
                    _log.warning(
                        "pdf_image_bytes_failed", page=page_no, image=index, error=str(exc)
                    )
                    continue
                if not data:
                    continue
                filename = getattr(image, "name", None) or f"page-{page_no}-image-{index + 1}.bin"
                mime_type = _guess_image_mime(filename, data)
                box = boxes[index] if index < len(boxes) else None
                asset = PdfAsset.from_bytes(
                    page_no=page_no,
                    bbox=box,
                    filename=filename,
                    mime_type=mime_type,
                    data=data,
                    metadata={"image_index_on_page": index},
                )
                if asset.content_hash in seen_hashes:
                    continue
                seen_hashes.add(asset.content_hash)
                output.append(asset)
        return output

    async def _enrich_assets(self, assets: list[PdfAsset], source_file: str) -> None:
        semaphore = asyncio.Semaphore(self.vlm_max_concurrency)

        async def enrich(asset: PdfAsset):
            async with semaphore:
                try:
                    asset.description = (
                        await self.image_describer.describe(
                            asset.data,
                            asset.mime_type,
                            context=f"文件={source_file}; 页码={asset.page_no}",
                        )
                    ).strip()
                    asset.metadata["description_status"] = (
                        "enriched" if asset.description else "missing"
                    )
                except Exception as exc:
                    asset.metadata["description_status"] = "failed"
                    asset.metadata["description_error"] = str(exc)[:500]
                    _log.warning(
                        "pdf_image_vlm_failed",
                        page=asset.page_no,
                        filename=asset.filename,
                        error=str(exc),
                    )

        await asyncio.gather(*(enrich(asset) for asset in assets))


def _reading_order_key(block: PdfBlock):
    box = block.first_bbox
    return (
        block.page_start,
        box.top if box else 0.0,
        box.x0 if box else 0.0,
        block.block_type.value,
    )


def _word_inside_box(word: dict[str, Any], box: PdfBoundingBox) -> bool:
    center_x = (float(word["x0"]) + float(word["x1"])) / 2
    center_y = (float(word["top"]) + float(word["bottom"])) / 2
    return box.x0 <= center_x <= box.x1 and box.top <= center_y <= box.bottom


def _line_top(line: list[dict[str, Any]]) -> float:
    return statistics.mean(float(word["top"]) for word in line)


def _normalize_row(row) -> list[str]:
    return [" ".join(str(value or "").replace("\r", "\n").split()) for value in row]


def _pad(row: list[str], width: int) -> list[str]:
    return list(row[:width]) + [""] * max(0, width - len(row))


def _looks_like_formula(text: str) -> bool:
    if len(text) > 240:
        return False
    math_symbols = sum(text.count(symbol) for symbol in ("=", "∑", "√", "≤", "≥", "±", "∫"))
    return math_symbols >= 1 and len(text.split()) <= 24


def _caption_from_filename(filename: str) -> str:
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def _render_pdf_page_png(content: bytes, page_no: int) -> bytes:
    """Render one page for OCR without a system binary dependency."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(content)
    try:
        page = document[page_no - 1]
        try:
            bitmap = page.render(scale=2.5)
            image = bitmap.to_pil()
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
        finally:
            page.close()
    finally:
        document.close()


def _guess_image_mime(filename: str, data: bytes) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and guessed.startswith("image/"):
        return guessed
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF8", "image/gif"),
        (b"BM", "image/bmp"),
    )
    for prefix, mime in signatures:
        if data.startswith(prefix):
            return mime
    return "application/octet-stream"
