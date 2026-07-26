"""Structured PDF intermediate representation.

The parser, chunker, and persistence layers share these types so layout and
asset provenance are not lost by converting a PDF to one plain string.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PdfBlockType(str, Enum):
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    TABLE = "TABLE"
    IMAGE = "IMAGE"


@dataclass(frozen=True)
class PdfBoundingBox:
    """One block location in one-based PDF page coordinates."""

    page_no: int
    x0: float
    top: float
    x1: float
    bottom: float
    page_width: float
    page_height: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def normalized_x0(self) -> float:
        return self.x0 / self.page_width if self.page_width else 0.0

    @property
    def normalized_x1(self) -> float:
        return self.x1 / self.page_width if self.page_width else 0.0

    @property
    def normalized_top(self) -> float:
        return self.top / self.page_height if self.page_height else 0.0

    @property
    def normalized_bottom(self) -> float:
        return self.bottom / self.page_height if self.page_height else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "pageNo": self.page_no,
            "x0": round(self.x0, 3),
            "top": round(self.top, 3),
            "x1": round(self.x1, 3),
            "bottom": round(self.bottom, 3),
            "pageWidth": round(self.page_width, 3),
            "pageHeight": round(self.page_height, 3),
        }


@dataclass
class PdfAsset:
    asset_id: str
    page_no: int
    bbox: PdfBoundingBox | None
    filename: str
    mime_type: str
    data: bytes = field(repr=False)
    content_hash: str = ""
    description: str = ""
    storage_key: str = ""
    storage_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_bytes(
        cls,
        *,
        page_no: int,
        bbox: PdfBoundingBox | None,
        filename: str,
        mime_type: str,
        data: bytes,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "PdfAsset":
        digest = hashlib.sha256(data).hexdigest()
        return cls(
            asset_id=f"asset_{digest[:20]}",
            page_no=page_no,
            bbox=bbox,
            filename=filename,
            mime_type=mime_type,
            data=data,
            content_hash=digest,
            description=description.strip(),
            metadata=dict(metadata or {}),
        )


@dataclass
class PdfBlock:
    block_id: str
    block_type: PdfBlockType
    page_start: int
    page_end: int
    bboxes: list[PdfBoundingBox] = field(default_factory=list)
    content: str = ""
    embedding_text: str = ""
    outline_path: list[str] = field(default_factory=list)
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    table_row_pages: list[int] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def table(
        cls,
        *,
        block_id: str,
        page_no: int,
        bbox: PdfBoundingBox,
        headers: list[str],
        rows: list[list[str]],
        row_pages: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "PdfBlock":
        clean_headers = [_clean_cell(value) for value in headers]
        clean_rows = [[_clean_cell(value) for value in row] for row in rows]
        pages = list(row_pages or [page_no] * len(clean_rows))
        if len(pages) != len(clean_rows):
            raise ValueError("row_pages must align with table rows")
        return cls(
            block_id=block_id,
            block_type=PdfBlockType.TABLE,
            page_start=page_no,
            page_end=page_no,
            bboxes=[bbox],
            table_headers=clean_headers,
            table_rows=clean_rows,
            table_row_pages=pages,
            metadata={"provisional_header": True, **(metadata or {})},
        )

    @classmethod
    def text(
        cls,
        *,
        block_id: str,
        block_type: PdfBlockType,
        page_no: int,
        bbox: PdfBoundingBox,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> "PdfBlock":
        if block_type not in (PdfBlockType.HEADING, PdfBlockType.PARAGRAPH):
            raise ValueError("text block type must be HEADING or PARAGRAPH")
        clean = content.strip()
        return cls(
            block_id=block_id,
            block_type=block_type,
            page_start=page_no,
            page_end=page_no,
            bboxes=[bbox],
            content=clean,
            embedding_text=clean,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def image(
        cls,
        *,
        block_id: str,
        page_no: int,
        bbox: PdfBoundingBox | None,
        asset: PdfAsset,
        caption: str = "",
    ) -> "PdfBlock":
        description = asset.description.strip()
        visible = caption.strip() or asset.filename
        display = f"![{visible}](asset://{asset.asset_id})"
        if description:
            display = f"{description}\n\n{display}"
        fallback = visible if not description else description
        return cls(
            block_id=block_id,
            block_type=PdfBlockType.IMAGE,
            page_start=page_no,
            page_end=page_no,
            bboxes=[bbox] if bbox else [],
            content=display,
            embedding_text=fallback,
            asset_ids=[asset.asset_id],
            metadata={
                "caption": visible,
                "description_status": "enriched" if description else "missing",
            },
        )

    @property
    def first_bbox(self) -> PdfBoundingBox | None:
        return self.bboxes[0] if self.bboxes else None

    @property
    def last_bbox(self) -> PdfBoundingBox | None:
        return self.bboxes[-1] if self.bboxes else None


@dataclass
class StructuredPdfDocument:
    document_id: str
    source_file: str
    page_count: int
    blocks: list[PdfBlock] = field(default_factory=list)
    assets: list[PdfAsset] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        parts: list[str] = []
        for block in self.blocks:
            if block.block_type == PdfBlockType.TABLE:
                parts.append(render_markdown_table(block.table_headers, block.table_rows))
            elif block.content:
                prefix = "# " if block.block_type == PdfBlockType.HEADING else ""
                parts.append(prefix + block.content)
        return "\n\n".join(part for part in parts if part.strip())


def render_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    width = max(len(headers), max((len(row) for row in rows), default=0))
    if width == 0:
        return ""
    normalized_headers = _pad_row(headers, width)
    rendered = [
        "| " + " | ".join(_escape_markdown_cell(v) for v in normalized_headers) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    rendered.extend(
        "| " + " | ".join(_escape_markdown_cell(v) for v in _pad_row(row, width)) + " |"
        for row in rows
    )
    return "\n".join(rendered)


def new_block_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _pad_row(row: list[str], width: int) -> list[str]:
    return list(row[:width]) + [""] * max(0, width - len(row))


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", "\n").split())


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
