"""Document parser — extracts text from TXT/MD/DOCX/PDF files."""
from __future__ import annotations

import os
from pathlib import Path


class DocumentParser:
    """Parse documents by extension while preserving native structure."""

    EXT_PARSERS = {
        ".txt": "_parse_text",
        ".md": "_parse_text",
        ".markdown": "_parse_text",
        ".docx": "_parse_docx",
        ".xlsx": "_parse_xlsx",
        ".csv": "_parse_csv",
        ".pptx": "_parse_pptx",
        ".html": "_parse_html",
        ".htm": "_parse_html",
        ".png": "_parse_image",
        ".jpg": "_parse_image",
        ".jpeg": "_parse_image",
        ".webp": "_parse_image",
        ".clipdoc": "_parse_clipboard",
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
        """Return the shared structured representation for every native format."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await self.structured_pdf_parser.parse_file(
                file_path,
                document_id=document_id,
                source_file=source_file or os.path.basename(file_path),
            )
        parser_method = self.EXT_PARSERS.get(ext)
        if parser_method is None:
            raise ValueError(
                f"Unsupported file type: {ext}. "
                f"Supported: {', '.join(sorted(self.EXT_PARSERS))}"
            )
        return await getattr(self, parser_method)(
            file_path,
            source_file=source_file or os.path.basename(file_path),
        )

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
        parsed = await self.parse_document(file_path)
        if hasattr(parsed, "to_markdown"):
            return parsed.to_markdown()
        return parsed

    # ---- TXT / MD ----

    async def _parse_text(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_text_document

        content = Path(file_path).read_bytes()
        text = self._decode_text(content)
        file_type = os.path.splitext(file_path)[1].lower().lstrip(".") or "txt"
        return parse_text_document(text, source_file or os.path.basename(file_path), file_type)

    # ---- DOCX ----

    async def _parse_docx(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_docx_document

        return parse_docx_document(
            Path(file_path).read_bytes(),
            source_file or os.path.basename(file_path),
        )

    async def _parse_xlsx(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_xlsx_document

        return parse_xlsx_document(
            Path(file_path).read_bytes(),
            source_file or os.path.basename(file_path),
        )

    async def _parse_csv(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_csv_document

        return parse_csv_document(
            Path(file_path).read_bytes(),
            source_file or os.path.basename(file_path),
        )

    async def _parse_pptx(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_pptx_document

        return parse_pptx_document(
            Path(file_path).read_bytes(),
            source_file or os.path.basename(file_path),
        )

    async def _parse_html(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_html_document

        return parse_html_document(
            Path(file_path).read_bytes(),
            source_file or os.path.basename(file_path),
        )

    async def _parse_image(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_image_document

        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        return await parse_image_document(
            Path(file_path).read_bytes(),
            source_file or os.path.basename(file_path),
            ext,
        )

    async def _parse_clipboard(self, file_path: str, *, source_file: str = ""):
        from app.ingestion.structured import parse_clipboard_document

        return await parse_clipboard_document(
            Path(file_path).read_bytes(),
            source_file or os.path.basename(file_path),
        )

    # ---- PDF ----

    async def _parse_pdf(self, file_path: str, *, source_file: str = ""):
        return await self.structured_pdf_parser.parse_file(
            file_path,
            source_file=source_file or os.path.basename(file_path),
        )

    @staticmethod
    def _decode_text(content: bytes) -> str:
        for encoding in ("utf-8", "gb18030", "gbk", "latin-1"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="replace")
