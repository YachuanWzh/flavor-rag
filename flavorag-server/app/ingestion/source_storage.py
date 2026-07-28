"""Shared source-document storage with local-development fallback."""

from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from app.config.settings import settings


def is_object_source(location: str) -> bool:
    return location.startswith("s3://")


def _client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


async def persist_source(
    local_path: str,
    *,
    kb_id: str,
    doc_id: str,
    filename: str,
) -> str:
    """Persist a validated local source and return its durable location."""
    if is_object_source(local_path):
        return local_path
    if settings.source_storage_backend.lower() != "s3":
        return local_path
    suffix = Path(filename).suffix.lower()
    key = (
        f"{settings.source_storage_prefix.strip('/')}/{kb_id}/{doc_id}/"
        f"source{suffix}"
    )

    def upload() -> None:
        client = _client()
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
        except Exception:
            client.create_bucket(Bucket=settings.s3_bucket)
        client.upload_file(
            local_path,
            settings.s3_bucket,
            key,
            ExtraArgs={"ContentType": _content_type(suffix)},
        )

    await asyncio.to_thread(upload)
    try:
        os.remove(local_path)
    except OSError:
        pass
    return f"s3://{settings.s3_bucket}/{key}"


@asynccontextmanager
async def materialize_source(location: str):
    """Yield a local parser path for either local or object-store sources."""
    if not is_object_source(location):
        yield location
        return
    bucket, key = _parse_uri(location)
    suffix = Path(key).suffix
    handle = tempfile.NamedTemporaryFile(
        prefix="flavorag-source-",
        suffix=suffix,
        delete=False,
    )
    temp_path = handle.name
    handle.close()
    try:
        await asyncio.to_thread(_client().download_file, bucket, key, temp_path)
        yield temp_path
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


async def presign_source(location: str, *, expires_sec: int = 900) -> str:
    bucket, key = _parse_uri(location)
    return await asyncio.to_thread(
        _client().generate_presigned_url,
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_sec,
    )


async def delete_source(location: str | None) -> None:
    if not location:
        return
    if is_object_source(location):
        bucket, key = _parse_uri(location)
        await asyncio.to_thread(
            _client().delete_object, Bucket=bucket, Key=key
        )
        return
    try:
        os.remove(location)
    except FileNotFoundError:
        pass


def _parse_uri(location: str) -> tuple[str, str]:
    if not is_object_source(location):
        raise ValueError("not an s3 source URI")
    bucket, separator, key = location[5:].partition("/")
    if not bucket or not separator or not key:
        raise ValueError("invalid s3 source URI")
    return bucket, key


def _content_type(suffix: str) -> str:
    import mimetypes

    return mimetypes.guess_type(f"source{suffix}")[0] or "application/octet-stream"
