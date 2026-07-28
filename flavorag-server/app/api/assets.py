"""Asset proxy API — serves PDF-extracted images via presigned S3 URLs.

The RustFS/S3 bucket is private by default, so the browser cannot fetch
``http://localhost:9000/flavorag-sources/...`` directly.  This endpoint
authenticates the user, checks KB-level read permission, then redirects
to a short-lived presigned URL.

Authentication supports both the standard ``Authorization: Bearer`` header
and a ``?token=`` query parameter, because ``<img src="...">`` tags cannot
set custom headers.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_access_token
from app.config.settings import settings
from app.database.session import get_db
from app.models import KnowledgeAsset, User
from app.security.access import Permission
from app.security.service import principal_from_user, require_kb

router = APIRouter(prefix="/api/assets", tags=["assets"])


async def _resolve_user(
    request: Request,
    token: str | None,
    db: AsyncSession,
) -> User:
    """Resolve a User from JWT token in header or query parameter."""
    # Try query parameter first (for <img> tags)
    jwt = token
    # Fall back to Authorization header
    if not jwt:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            jwt = auth_header[7:]

    if not jwt:
        raise HTTPException(status_code=401, detail="未提供认证Token")

    payload = decode_access_token(jwt)
    if not payload:
        raise HTTPException(status_code=401, detail="Token无效或已过期")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token格式错误")

    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted == 0)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


@router.get("/{asset_id}")
async def serve_asset(
    asset_id: str,
    request: Request,
    token: str | None = Query(None, description="JWT token (for <img> tags)"),
    db: AsyncSession = Depends(get_db),
):
    """Redirect to a presigned S3 URL for the given asset.

    Returns 404 if the asset does not exist, is deleted, or the user
    lacks read access to the owning knowledge base.
    """
    user = await _resolve_user(request, token, db)

    result = await db.execute(
        select(KnowledgeAsset).where(
            KnowledgeAsset.id == asset_id,
            KnowledgeAsset.deleted == 0,
            KnowledgeAsset.index_status == "ACTIVE",
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    # Permission check: user must have READ on the asset's KB.
    await require_kb(
        db,
        principal_from_user(user),
        asset.kb_id,
        Permission.READ,
    )

    # Generate presigned URL
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": asset.storage_key},
            ExpiresIn=3600,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="生成下载链接失败")

    return RedirectResponse(url=url)
