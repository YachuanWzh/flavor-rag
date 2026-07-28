"""Bounded, signature-aware validation for uploaded knowledge sources."""

from __future__ import annotations

import os
import zipfile


class UploadValidationError(ValueError):
    pass


_MIME_BY_EXTENSION = {
    "pdf": {"application/pdf"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    },
    "pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
    },
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "webp": {"image/webp"},
}


def _signature_matches(extension: str, header: bytes) -> bool:
    if extension == "pdf":
        return header.startswith(b"%PDF-")
    if extension in {"docx", "xlsx", "pptx"}:
        return header.startswith(b"PK\x03\x04")
    if extension == "png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {"jpg", "jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == "webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return True


def validate_upload(
    *,
    filename: str,
    content_type: str | None,
    header: bytes,
    size: int,
    max_bytes: int,
) -> str:
    extension = os.path.splitext(filename or "")[1].lower().lstrip(".")
    if not extension:
        raise UploadValidationError("file extension is required")
    if size < 0 or size > max_bytes:
        raise UploadValidationError(
            f"file size {size} exceeds configured maximum {max_bytes}"
        )
    allowed_mimes = _MIME_BY_EXTENSION.get(extension)
    normalized_mime = (content_type or "").split(";", 1)[0].strip().lower()
    if allowed_mimes and normalized_mime and normalized_mime not in allowed_mimes:
        raise UploadValidationError(
            f"MIME type {normalized_mime} does not match .{extension}"
        )
    if not _signature_matches(extension, header):
        raise UploadValidationError(f"file signature does not match .{extension}")
    return extension


def save_upload_bounded(
    upload,
    destination: str,
    *,
    max_bytes: int,
    max_pdf_pages: int,
    max_uncompressed_bytes: int,
    max_archive_entries: int = 10000,
    max_compression_ratio: float = 200.0,
    max_image_pixels: int = 100_000_000,
) -> int:
    """Stream an UploadFile to disk with hard limits and structural checks."""
    written = 0
    header = b""
    try:
        with open(destination, "wb") as target:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                if not header:
                    header = chunk[:16]
                written += len(chunk)
                if written > max_bytes:
                    raise UploadValidationError(
                        f"file size exceeds configured maximum {max_bytes}"
                    )
                target.write(chunk)
        extension = validate_upload(
            filename=upload.filename or "",
            content_type=getattr(upload, "content_type", None),
            header=header,
            size=written,
            max_bytes=max_bytes,
        )
        if extension in {"docx", "xlsx", "pptx"}:
            with zipfile.ZipFile(destination) as archive:
                entries = archive.infolist()
                if len(entries) > max_archive_entries:
                    raise UploadValidationError(
                        "archive entry count exceeds configured maximum"
                    )
                if any(item.flag_bits & 0x1 for item in entries):
                    raise UploadValidationError(
                        "encrypted archives are not supported"
                    )
                expanded = sum(item.file_size for item in entries)
                if expanded > max_uncompressed_bytes:
                    raise UploadValidationError(
                        "archive uncompressed size exceeds configured maximum"
                    )
                compressed = sum(item.compress_size for item in entries)
                if expanded / max(1, compressed) > max_compression_ratio:
                    raise UploadValidationError(
                        "archive compression ratio exceeds configured maximum"
                    )
        if extension == "pdf":
            from pypdf import PdfReader

            if len(PdfReader(destination).pages) > max_pdf_pages:
                raise UploadValidationError(
                    "PDF page count exceeds configured maximum"
                )
        if extension in {"png", "jpg", "jpeg", "webp"}:
            from PIL import Image

            with Image.open(destination) as image:
                if image.width * image.height > max_image_pixels:
                    raise UploadValidationError(
                        "image pixel count exceeds configured maximum"
                    )
                image.verify()
        return written
    except Exception:
        try:
            if os.path.exists(destination):
                os.remove(destination)
        except OSError:
            pass
        raise
