"""Content-addressed storage and persistence for extracted PDF assets."""

from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import dataclass

from app.config.logging_config import get_logger
from app.ingestion.pdf.models import PdfAsset

_log = get_logger("flavorag.ingestion.pdf.assets")


@dataclass(frozen=True)
class StoredPdfAsset:
    asset_id: str
    storage_key: str
    storage_url: str


class S3PdfAssetStorage:
    def __init__(self, client=None):
        from app.config.settings import settings

        self.settings = settings
        if client is None:
            import boto3
            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
            )
        self.client = client
        self.bucket = settings.s3_bucket
        self._bucket_ready = False

    async def upload(self, asset: PdfAsset, *, kb_id: str, doc_id: str) -> StoredPdfAsset:
        return await asyncio.to_thread(self._upload_sync, asset, kb_id, doc_id)

    async def delete_keys(self, storage_keys: list[str]) -> None:
        if not storage_keys:
            return
        await asyncio.to_thread(self._delete_keys_sync, storage_keys)

    def _upload_sync(self, asset: PdfAsset, kb_id: str, doc_id: str) -> StoredPdfAsset:
        self._ensure_bucket()
        extension = _extension_for(asset.filename, asset.mime_type)
        key = f"assets/{kb_id}/{doc_id}/{asset.content_hash[:32]}{extension}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=asset.data,
            ContentType=asset.mime_type,
            Metadata={
                "asset-id": asset.asset_id,
                "page-no": str(asset.page_no),
                "sha256": asset.content_hash,
            },
        )
        endpoint = self.settings.s3_endpoint.rstrip("/")
        url = f"{endpoint}/{self.bucket}/{key}"
        asset.storage_key = key
        asset.storage_url = url
        return StoredPdfAsset(asset.asset_id, key, url)

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_ready = True

    def _delete_keys_sync(self, storage_keys: list[str]) -> None:
        for start in range(0, len(storage_keys), 1000):
            batch = storage_keys[start:start + 1000]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )


async def persist_pdf_assets(
    assets: list[PdfAsset],
    *,
    kb_id: str,
    doc_id: str,
    created_by: str,
    session,
    storage: S3PdfAssetStorage | None = None,
) -> dict[str, str]:
    """Upload assets and add KnowledgeAsset rows to an existing transaction."""
    if not assets:
        return {}
    storage = storage or S3PdfAssetStorage()
    from app.models import KnowledgeAsset, KnowledgeDocument
    from sqlalchemy import select

    asset_ids = [asset.asset_id for asset in assets]
    existing_result = await session.execute(
        select(KnowledgeAsset).where(KnowledgeAsset.id.in_(asset_ids))
    )
    existing_by_id = {
        record.id: record for record in existing_result.scalars().all()
    }
    scope_result = await session.execute(
        select(
            KnowledgeDocument.tenant_id,
            KnowledgeDocument.department_id,
        ).where(KnowledgeDocument.id == doc_id)
    )
    scope = scope_result.first()
    tenant_id = scope.tenant_id if scope else "default"
    department_id = scope.department_id if scope else None

    mapping: dict[str, str] = {}
    uploaded_bytes = 0
    for asset in assets:
        stored = await storage.upload(asset, kb_id=kb_id, doc_id=doc_id)
        mapping[asset.asset_id] = stored.storage_url
        uploaded_bytes += len(asset.data)
        record = existing_by_id.get(asset.asset_id)
        if record is None:
            record = KnowledgeAsset(
                id=asset.asset_id,
                kb_id=kb_id,
                doc_id=doc_id,
                tenant_id=tenant_id,
                department_id=department_id,
                created_by=created_by,
            )
            session.add(record)
        record.asset_type = "IMAGE"
        record.mime_type = asset.mime_type
        record.file_name = asset.filename
        record.file_size = len(asset.data)
        record.content_hash = asset.content_hash
        record.storage_key = stored.storage_key
        record.storage_url = stored.storage_url
        record.page_no = asset.page_no
        record.bbox_json = asset.bbox.to_dict() if asset.bbox else None
        record.description = asset.description or None
        record.metadata_json = asset.metadata
        record.deleted = 0
    _log.info(
        "pdf_assets_persisted",
        doc_id=doc_id,
        asset_count=len(assets),
        uploaded_bytes=uploaded_bytes,
    )
    return mapping


def materialize_asset_urls(chunks: list[dict], asset_urls: dict[str, str]) -> None:
    for chunk in chunks:
        content = chunk.get("content", "")
        for asset_id in chunk.get("asset_ids", []):
            url = asset_urls.get(asset_id)
            if url:
                content = content.replace(f"asset://{asset_id}", url)
        chunk["content"] = content


def _extension_for(filename: str, mime_type: str) -> str:
    if "." in filename:
        suffix = "." + filename.rsplit(".", 1)[-1].lower()
        if 1 < len(suffix) <= 10:
            return suffix
    return mimetypes.guess_extension(mime_type) or ".bin"
