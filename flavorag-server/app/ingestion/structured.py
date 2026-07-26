"""Shared structured-document IR for non-PDF enterprise formats."""

from __future__ import annotations

import csv
import io
import mimetypes
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.ingestion.pdf.models import PdfAsset, render_markdown_table


class BlockType(str, Enum):
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST = "LIST"
    TABLE = "TABLE"
    CODE = "CODE"
    IMAGE = "IMAGE"


@dataclass
class StructuredBlock:
    block_id: str
    block_type: BlockType
    content: str = ""
    embedding_text: str = ""
    outline_path: list[str] = field(default_factory=list)
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)
    location: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredDocument:
    source_file: str
    file_type: str
    blocks: list[StructuredBlock] = field(default_factory=list)
    assets: list[PdfAsset] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        parts: list[str] = []
        for block in self.blocks:
            if block.block_type == BlockType.TABLE:
                parts.append(render_markdown_table(block.table_headers, block.table_rows))
            elif block.block_type == BlockType.HEADING:
                parts.append(f"# {block.content}")
            elif block.block_type == BlockType.CODE:
                parts.append(f"```\n{block.content}\n```")
            else:
                parts.append(block.content)
        return "\n\n".join(part for part in parts if part.strip())


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())


def parse_text_document(text: str, source_file: str, file_type: str) -> StructuredDocument:
    blocks: list[StructuredBlock] = []
    outline: list[str] = []
    in_code = False
    code_lines: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph():
        if not paragraph:
            return
        value = "\n".join(paragraph).strip()
        paragraph.clear()
        if value:
            blocks.append(
                StructuredBlock(
                    _id("paragraph"),
                    BlockType.PARAGRAPH,
                    value,
                    value,
                    list(outline),
                )
            )

    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                blocks.append(
                    StructuredBlock(
                        _id("code"),
                        BlockType.CODE,
                        "\n".join(code_lines).strip(),
                        "\n".join(code_lines).strip(),
                        list(outline),
                    )
                )
                code_lines.clear()
            else:
                flush_paragraph()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            outline[:] = outline[: level - 1]
            outline.append(heading.group(2).strip())
            blocks.append(
                StructuredBlock(
                    _id("heading"),
                    BlockType.HEADING,
                    outline[-1],
                    outline[-1],
                    list(outline),
                    metadata={"level": level},
                )
            )
            continue
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line):
            flush_paragraph()
            value = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
            blocks.append(
                StructuredBlock(
                    _id("list"),
                    BlockType.LIST,
                    f"- {value}",
                    value,
                    list(outline),
                )
            )
            continue
        if not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line)
    flush_paragraph()
    if code_lines:
        blocks.append(
            StructuredBlock(
                _id("code"),
                BlockType.CODE,
                "\n".join(code_lines),
                "\n".join(code_lines),
                list(outline),
            )
        )
    return StructuredDocument(source_file, file_type, blocks)


def parse_csv_document(content: bytes, source_file: str) -> StructuredDocument:
    text = content.decode("utf-8-sig", errors="replace")
    rows = [list(row) for row in csv.reader(io.StringIO(text))]
    headers = [_clean(item) for item in (rows[0] if rows else [])]
    data = [[_clean(item) for item in row] for row in rows[1:]]
    block = StructuredBlock(
        _id("table"),
        BlockType.TABLE,
        table_headers=headers,
        table_rows=data,
        location={"rowStart": 2, "rowEnd": len(rows)},
        metadata={"sheet": source_file, "format": "csv"},
    )
    return StructuredDocument(source_file, "csv", [block])


def parse_docx_document(content: bytes, source_file: str) -> StructuredDocument:
    from docx import Document

    doc = Document(io.BytesIO(content))
    blocks: list[StructuredBlock] = []
    outline: list[str] = []
    paragraph_index = 0
    for paragraph in doc.paragraphs:
        value = paragraph.text.strip()
        if not value:
            continue
        paragraph_index += 1
        style = (paragraph.style.name if paragraph.style else "").lower()
        heading = re.search(r"heading\s*(\d+)|标题\s*(\d+)", style)
        if heading:
            level = int(next(group for group in heading.groups() if group) or 1)
            outline[:] = outline[: level - 1]
            outline.append(value)
            block_type = BlockType.HEADING
        elif "list" in style or "列表" in style:
            block_type = BlockType.LIST
        else:
            block_type = BlockType.PARAGRAPH
        blocks.append(
            StructuredBlock(
                _id(block_type.value.lower()),
                block_type,
                f"- {value}" if block_type == BlockType.LIST else value,
                value,
                list(outline),
                location={"paragraph": paragraph_index},
                metadata={"style": paragraph.style.name if paragraph.style else ""},
            )
        )
    for table_index, table in enumerate(doc.tables, start=1):
        rows = [[_clean(cell.text) for cell in row.cells] for row in table.rows]
        if not rows:
            continue
        blocks.append(
            StructuredBlock(
                _id("table"),
                BlockType.TABLE,
                outline_path=list(outline),
                table_headers=rows[0],
                table_rows=rows[1:],
                location={"table": table_index},
                metadata={"format": "docx"},
            )
        )
    return StructuredDocument(
        source_file,
        "docx",
        blocks,
        metadata={"paragraphCount": len(doc.paragraphs), "tableCount": len(doc.tables)},
    )


def parse_xlsx_document(content: bytes, source_file: str) -> StructuredDocument:
    import openpyxl

    values_wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    formula_wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
    blocks: list[StructuredBlock] = []
    for sheet_name in values_wb.sheetnames:
        ws = values_wb[sheet_name]
        formula_ws = formula_wb[sheet_name]
        blocks.append(
            StructuredBlock(
                _id("heading"),
                BlockType.HEADING,
                sheet_name,
                sheet_name,
                [sheet_name],
                location={"sheet": sheet_name},
                metadata={"level": 1},
            )
        )
        rows = [
            [_clean(cell.value) for cell in row]
            for row in ws.iter_rows()
            if any(cell.value is not None for cell in row)
        ]
        if not rows:
            continue
        hyperlinks: dict[str, str] = {}
        formulas: dict[str, str] = {}
        for row in ws.iter_rows():
            for cell in row:
                if cell.hyperlink and cell.hyperlink.target:
                    hyperlinks[cell.coordinate] = cell.hyperlink.target
                raw = formula_ws[cell.coordinate].value
                if isinstance(raw, str) and raw.startswith("="):
                    formulas[cell.coordinate] = raw
        blocks.append(
            StructuredBlock(
                _id("table"),
                BlockType.TABLE,
                outline_path=[sheet_name],
                table_headers=rows[0],
                table_rows=rows[1:],
                location={
                    "sheet": sheet_name,
                    "rowStart": 1,
                    "rowEnd": ws.max_row,
                },
                metadata={
                    "format": "xlsx",
                    "mergedCells": [str(item) for item in ws.merged_cells.ranges],
                    "hyperlinks": hyperlinks,
                    "formulas": formulas,
                },
            )
        )
    return StructuredDocument(
        source_file,
        "xlsx",
        blocks,
        metadata={"sheetCount": len(values_wb.sheetnames)},
    )


def parse_pptx_document(content: bytes, source_file: str) -> StructuredDocument:
    from pptx import Presentation

    presentation = Presentation(io.BytesIO(content))
    blocks: list[StructuredBlock] = []
    for slide_no, slide in enumerate(presentation.slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title else f"第 {slide_no} 页"
        blocks.append(
            StructuredBlock(
                _id("heading"),
                BlockType.HEADING,
                title,
                title,
                [title],
                location={"slide": slide_no},
                metadata={"level": 1},
            )
        )
        for shape_index, shape in enumerate(slide.shapes, start=1):
            if shape == slide.shapes.title:
                continue
            if getattr(shape, "has_table", False):
                rows = [
                    [_clean(cell.text) for cell in row.cells]
                    for row in shape.table.rows
                ]
                if rows:
                    blocks.append(
                        StructuredBlock(
                            _id("table"),
                            BlockType.TABLE,
                            outline_path=[title],
                            table_headers=rows[0],
                            table_rows=rows[1:],
                            location={"slide": slide_no, "shape": shape_index},
                            metadata={"format": "pptx"},
                        )
                    )
            elif getattr(shape, "has_text_frame", False):
                value = shape.text_frame.text.strip()
                if value:
                    blocks.append(
                        StructuredBlock(
                            _id("paragraph"),
                            BlockType.PARAGRAPH,
                            value,
                            value,
                            [title],
                            location={"slide": slide_no, "shape": shape_index},
                        )
                    )
    return StructuredDocument(
        source_file,
        "pptx",
        blocks,
        metadata={"slideCount": len(presentation.slides)},
    )


def parse_html_document(content: bytes, source_file: str) -> StructuredDocument:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    blocks: list[StructuredBlock] = []
    outline: list[str] = []
    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "table", "img"]):
        if element.find_parent(["table", "pre"]) and element.name not in {"table", "pre"}:
            continue
        value = element.get_text(" ", strip=True)
        if element.name.startswith("h"):
            level = int(element.name[1])
            outline[:] = outline[: level - 1]
            outline.append(value)
            blocks.append(
                StructuredBlock(
                    _id("heading"), BlockType.HEADING, value, value,
                    list(outline), metadata={"level": level},
                )
            )
        elif element.name == "table":
            rows = [
                [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
                for row in element.find_all("tr")
            ]
            if rows:
                blocks.append(
                    StructuredBlock(
                        _id("table"), BlockType.TABLE,
                        outline_path=list(outline),
                        table_headers=rows[0], table_rows=rows[1:],
                        metadata={"format": "html"},
                    )
                )
        elif element.name == "img":
            alt = element.get("alt") or element.get("src") or "网页图片"
            blocks.append(
                StructuredBlock(
                    _id("image"), BlockType.IMAGE, alt, alt,
                    list(outline), metadata={"src": element.get("src", "")},
                )
            )
        elif value:
            block_type = {
                "li": BlockType.LIST,
                "pre": BlockType.CODE,
            }.get(element.name, BlockType.PARAGRAPH)
            blocks.append(
                StructuredBlock(
                    _id(block_type.value.lower()),
                    block_type,
                    f"- {value}" if block_type == BlockType.LIST else value,
                    value,
                    list(outline),
                )
            )
    return StructuredDocument(source_file, "html", blocks)


async def parse_image_document(
    content: bytes,
    source_file: str,
    file_type: str,
) -> StructuredDocument:
    from app.ingestion.pdf.vlm import get_image_describer

    mime_type = mimetypes.guess_type(source_file)[0] or f"image/{file_type}"
    description = await get_image_describer().describe(
        content,
        mime_type,
        context=source_file,
    )
    asset = PdfAsset.from_bytes(
        page_no=1,
        bbox=None,
        filename=source_file,
        mime_type=mime_type,
        data=content,
        description=description,
    )
    visible = description or source_file
    block = StructuredBlock(
        _id("image"),
        BlockType.IMAGE,
        f"{description}\n\n![{source_file}](asset://{asset.asset_id})".strip(),
        visible,
        asset_ids=[asset.asset_id],
        location={"image": 1},
        metadata={
            "descriptionStatus": "enriched" if description else "missing",
            "mimeType": mime_type,
        },
    )
    return StructuredDocument(source_file, file_type, [block], [asset])


class GenericStructuredChunker:
    """Block-aware chunker retaining format-specific provenance."""

    def __init__(self, *, target_chars: int = 800, table_max_rows: int = 20):
        self.target_chars = max(128, target_chars)
        self.table_max_rows = max(1, table_max_rows)

    def chunk(self, document: StructuredDocument) -> list[dict]:
        chunks: list[dict] = []
        outline: list[str] = []
        text_buffer: list[StructuredBlock] = []

        def flush_text():
            nonlocal text_buffer
            if not text_buffer:
                return
            content = "\n\n".join(block.content for block in text_buffer if block.content)
            embedding = "\n".join(
                [f"章节: {' > '.join(outline)}"] if outline else []
            ) + ("\n" if outline else "") + content
            chunks.append(
                self._record(
                    chunks,
                    content,
                    embedding,
                    "PARAGRAPH",
                    {
                        "sourceBlockIds": [block.block_id for block in text_buffer],
                        "outlinePath": list(outline),
                        "locations": [block.location for block in text_buffer],
                        "sourceFormat": document.file_type,
                    },
                )
            )
            text_buffer = []

        for block in document.blocks:
            if block.block_type == BlockType.HEADING:
                flush_text()
                outline = list(block.outline_path or [block.content])
                continue
            if block.block_type == BlockType.TABLE:
                flush_text()
                for start in range(0, len(block.table_rows) or 1, self.table_max_rows):
                    rows = block.table_rows[start : start + self.table_max_rows]
                    content = render_markdown_table(block.table_headers, rows)
                    section = " > ".join(block.outline_path or outline)
                    embedding_lines = [f"章节: {section}"] if section else []
                    for row in rows:
                        pairs = [
                            f"{header}: {row[index] if index < len(row) else ''}"
                            for index, header in enumerate(block.table_headers)
                        ]
                        embedding_lines.append("；".join(pairs))
                    chunks.append(
                        self._record(
                            chunks,
                            content,
                            "\n".join(embedding_lines),
                            "TABLE",
                            {
                                **block.metadata,
                                "sourceBlockIds": [block.block_id],
                                "outlinePath": block.outline_path or outline,
                                "location": block.location,
                                "rowStart": start + 1,
                                "rowEnd": start + len(rows),
                                "sourceFormat": document.file_type,
                            },
                        )
                    )
                continue
            if block.block_type in {BlockType.IMAGE, BlockType.CODE}:
                flush_text()
                chunks.append(
                    self._record(
                        chunks,
                        block.content,
                        block.embedding_text or block.content,
                        block.block_type.value,
                        {
                            **block.metadata,
                            "sourceBlockIds": [block.block_id],
                            "outlinePath": block.outline_path or outline,
                            "location": block.location,
                            "sourceFormat": document.file_type,
                        },
                        asset_ids=block.asset_ids,
                    )
                )
                continue
            prospective = sum(len(item.content) for item in text_buffer) + len(block.content)
            if text_buffer and prospective > self.target_chars:
                flush_text()
            text_buffer.append(block)
        flush_text()
        return chunks

    @staticmethod
    def _record(
        chunks: list[dict],
        content: str,
        embedding: str,
        block_type: str,
        metadata: dict,
        *,
        asset_ids: list[str] | None = None,
    ) -> dict:
        return {
            "chunk_index": len(chunks),
            "content": content,
            "embedding_content": embedding,
            "char_count": len(content),
            "block_type": block_type,
            "page_start": None,
            "page_end": None,
            "bbox_json": [],
            "metadata_json": metadata,
            "asset_ids": list(asset_ids or []),
        }
