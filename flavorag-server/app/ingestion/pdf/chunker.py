"""Block-aware chunking for structured PDF documents."""

from __future__ import annotations

from app.ingestion.pdf.models import (
    PdfBlock,
    PdfBlockType,
    StructuredPdfDocument,
    render_markdown_table,
)


class StructuredPdfChunker:
    def __init__(self, *, target_chars: int = 800, table_max_rows: int = 20):
        self.target_chars = max(100, target_chars)
        self.table_max_rows = max(1, table_max_rows)

    def chunk(self, document: StructuredPdfDocument) -> list[dict]:
        chunks: list[dict] = []
        outline: list[str] = []
        paragraph_buffer: list[PdfBlock] = []

        def flush_paragraphs():
            if not paragraph_buffer:
                return
            chunks.append(self._paragraph_chunk(paragraph_buffer, outline, len(chunks)))
            paragraph_buffer.clear()

        for block in document.blocks:
            if block.block_type == PdfBlockType.HEADING:
                flush_paragraphs()
                title = block.content.strip()
                if title:
                    level = int(block.metadata.get("heading_level", 1))
                    outline[:] = outline[: max(0, level - 1)]
                    outline.append(title)
                continue

            if block.block_type == PdfBlockType.PARAGRAPH:
                projected = sum(len(item.content) for item in paragraph_buffer) + len(block.content)
                if paragraph_buffer and projected > self.target_chars:
                    flush_paragraphs()
                paragraph_buffer.append(block)
                continue

            flush_paragraphs()
            if block.block_type == PdfBlockType.TABLE:
                for table_chunk in self._table_chunks(block, outline):
                    table_chunk["chunk_index"] = len(chunks)
                    chunks.append(table_chunk)
            elif block.block_type == PdfBlockType.IMAGE:
                chunks.append(self._image_chunk(block, outline, len(chunks)))

        flush_paragraphs()
        for index, item in enumerate(chunks):
            item["chunk_index"] = index
        return chunks

    def _paragraph_chunk(
        self, blocks: list[PdfBlock], outline: list[str], chunk_index: int
    ) -> dict:
        body = "\n\n".join(block.content for block in blocks if block.content.strip())
        section = " > ".join(outline)
        display = f"## {section}\n\n{body}" if section else body
        pages = [page for block in blocks for page in range(block.page_start, block.page_end + 1)]
        bboxes = [box.to_dict() for block in blocks for box in block.bboxes]
        return {
            "content": display,
            "embedding_content": display,
            "chunk_index": chunk_index,
            "char_count": len(display),
            "block_type": "PARAGRAPH",
            "page_start": min(pages) if pages else 0,
            "page_end": max(pages) if pages else 0,
            "bbox_json": bboxes,
            "metadata_json": {
                "outline_path": list(outline),
                "source_block_ids": [block.block_id for block in blocks],
            },
            "asset_ids": [],
        }

    def _table_chunks(self, block: PdfBlock, outline: list[str]) -> list[dict]:
        output: list[dict] = []
        rows = block.table_rows
        if not rows:
            rows = []
        for start in range(0, max(1, len(rows)), self.table_max_rows):
            selected = rows[start : start + self.table_max_rows]
            selected_pages = block.table_row_pages[start : start + len(selected)]
            if not selected and start > 0:
                continue
            markdown = render_markdown_table(block.table_headers, selected)
            section = " > ".join(outline)
            content = f"### {section}\n\n{markdown}" if section else markdown
            embedding = _table_embedding_text(block.table_headers, selected, section)
            page_start = min(selected_pages) if selected_pages else block.page_start
            page_end = max(selected_pages) if selected_pages else block.page_end
            relevant_boxes = [
                box.to_dict()
                for box in block.bboxes
                if page_start <= box.page_no <= page_end
            ]
            output.append({
                "content": content,
                "embedding_content": embedding,
                "chunk_index": 0,
                "char_count": len(content),
                "block_type": "TABLE",
                "page_start": page_start,
                "page_end": page_end,
                "bbox_json": relevant_boxes,
                "metadata_json": {
                    "outline_path": list(outline),
                    "source_block_ids": list(block.metadata.get("source_table_ids", [block.block_id])),
                    "logical_table_id": block.block_id,
                    "logical_page_start": block.page_start,
                    "logical_page_end": block.page_end,
                    "row_start": start,
                    "row_end": start + len(selected),
                    "cross_page": bool(block.metadata.get("cross_page")),
                    "merge_confidence": block.metadata.get("merge_confidence"),
                    "headers": list(block.table_headers),
                },
                "asset_ids": [],
            })
            if not rows:
                break
        return output

    def _image_chunk(
        self, block: PdfBlock, outline: list[str], chunk_index: int
    ) -> dict:
        return {
            "content": block.content,
            "embedding_content": block.embedding_text,
            "chunk_index": chunk_index,
            "char_count": len(block.content),
            "block_type": "IMAGE",
            "page_start": block.page_start,
            "page_end": block.page_end,
            "bbox_json": [box.to_dict() for box in block.bboxes],
            "metadata_json": {
                **block.metadata,
                "outline_path": list(outline),
                "source_block_ids": [block.block_id],
            },
            "asset_ids": list(block.asset_ids),
        }


def _table_embedding_text(headers: list[str], rows: list[list[str]], section: str) -> str:
    lines: list[str] = []
    if section:
        lines.append(f"section: {section}")
    if headers:
        lines.append("headers: " + ", ".join(headers))
    for row in rows:
        pairs = []
        for index, value in enumerate(row):
            if not value:
                continue
            key = headers[index] if index < len(headers) and headers[index] else f"column_{index + 1}"
            pairs.append(f"{key}: {value}")
        if pairs:
            lines.append("; ".join(pairs))
    return "\n".join(lines)
