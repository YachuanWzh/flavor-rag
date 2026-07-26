"""Document parser — extracts text from TXT/MD/DOCX/PDF files."""
from __future__ import annotations

import os


class DocumentParser:
    """Parse documents by file extension. Supports TXT, MD, DOCX, PDF."""

    EXT_PARSERS = {
        ".txt": "_parse_text",
        ".md": "_parse_text",
        ".markdown": "_parse_text",
        ".docx": "_parse_docx",
        ".pdf": "_parse_pdf",
    }

    def __init__(self, structured_pdf_parser=None):
        if structured_pdf_parser is None:
            from app.ingestion.pdf.parser import StructuredPdfParser
            structured_pdf_parser = StructuredPdfParser()
        self.structured_pdf_parser = structured_pdf_parser

    async def parse_document(
        self,
        file_path: str,
        *,
        document_id: str = "",
        source_file: str = "",
    ):
        """Return a StructuredPdfDocument for PDFs, plain text otherwise."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await self.structured_pdf_parser.parse_file(
                file_path,
                document_id=document_id,
                source_file=source_file or os.path.basename(file_path),
            )
        return await self.parse(file_path)

    async def parse(self, file_path: str) -> str:
        """Parse *file_path* and return plain text content.

        Raises ValueError if the extension is not supported.
        """
        ext = os.path.splitext(file_path)[1].lower()
        parser_method = self.EXT_PARSERS.get(ext)
        if parser_method is None:
            raise ValueError(
                f"Unsupported file type: {ext}. "
                f"Supported: {', '.join(sorted(self.EXT_PARSERS))}"
            )
        parsed = await getattr(self, parser_method)(file_path)
        if hasattr(parsed, "to_markdown"):
            return parsed.to_markdown()
        return parsed

    # ---- TXT / MD ----

    async def _parse_text(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    # ---- DOCX ----

    async def _parse_docx(self, file_path: str) -> str:
        try:
            from docx import Document
        except ImportError:
            raise ImportError("python-docx is required to parse .docx files")
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)

    # ---- PDF ----

    async def _parse_pdf(self, file_path: str) -> str:
        return await self.structured_pdf_parser.parse_file(
            file_path,
            source_file=os.path.basename(file_path),
        )
