"""User profile management API — admin-only endpoints for viewing and managing
user profiles (7 dimensions) + mem0 memory facts.

All endpoints require admin role.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db, async_session_factory
from app.auth.dependencies import get_admin_user
from app.config.settings import settings
from app.models import User, UserProfile
from app.memory.mem0_client import Mem0Manager
from app.memory.profile_builder import build_or_update_profile
from app.time_utils import utc_isoformat

router = APIRouter(prefix="/api/admin/profiles", tags=["admin-profiles"])


# ─── Schemas ───


class ProfileConfigUpdate(BaseModel):
    profile_update_mode: str | None = None  # incremental | daily
    profile_daily_cron: str | None = None
    mem0_enabled: bool | None = None


# ─── List endpoint ───


@router.get("")
async def list_profiles(
    search: str | None = Query(None, description="按用户名搜索"),
    tenant_id: str | None = Query(None, description="租户过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """List all user profiles with pagination and search."""
    stmt = (
        select(
            UserProfile,
            User.username,
            User.role,
        )
        .join(User, User.id == UserProfile.user_id, isouter=True)
    )

    if tenant_id:
        stmt = stmt.where(UserProfile.tenant_id == tenant_id)

    if search:
        stmt = stmt.where(User.username.ilike(f"%{search}%"))

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    stmt = stmt.order_by(desc(UserProfile.updated_at)).offset(offset).limit(page_size)
    rows = (await db.execute(stmt)).all()

    return {
        "code": "0",
        "data": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "items": [
                {
                    "id": p.id,
                    "userId": p.user_id,
                    "username": username or "unknown",
                    "userRole": role or "user",
                    "tenantId": p.tenant_id,
                    "domains": p.domains or [],
                    "expertiseLevel": p.expertise_level,
                    "totalQueries": p.total_queries or 0,
                    "totalConversations": p.total_conversations or 0,
                    "thumbsUp": p.thumbs_up_count or 0,
                    "thumbsDown": p.thumbs_down_count or 0,
                    "mem0FactsCount": p.mem0_facts_count or 0,
                    "profileVersion": p.profile_version or 1,
                    "lastActiveTime": utc_isoformat(p.last_active_time),
                    "updatedAt": utc_isoformat(p.updated_at),
                }
                for p, username, role in rows
            ],
        },
    }


# ─── Detail endpoint ───


@router.get("/{user_id}")
async def get_profile_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """Get full profile detail for a user (all 7 dimensions)."""
    stmt = (
        select(
            UserProfile,
            User.username,
            User.role,
            User.tenant_id,
            User.department_id,
            User.create_time,
        )
        .join(User, User.id == UserProfile.user_id, isouter=True)
        .where(UserProfile.user_id == user_id)
    )
    row = (await db.execute(stmt)).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户画像不存在")

    p, username, user_role, tenant_id, dept_id, user_created = row

    return {
        "code": "0",
        "data": {
            # Dimension 1: Basic info
            "userId": p.user_id,
            "username": username or "unknown",
            "userRole": user_role or "user",
            "tenantId": tenant_id or p.tenant_id,
            "departmentId": dept_id,
            "userCreateTime": utc_isoformat(user_created),

            # Dimension 2: Professional domain
            "domains": p.domains or [],
            "expertiseLevel": p.expertise_level,
            "domainSummary": p.domain_summary,

            # Dimension 3: Intent distribution
            "intentDistribution": p.intent_distribution or {},

            # Dimension 4: KB preference
            "preferredKbs": p.preferred_kbs or [],
            "preferredDocTypes": p.preferred_doc_types or {},

            # Dimension 5: Query style
            "avgQueryLength": p.avg_query_length,
            "deepThinkingRate": p.deep_thinking_rate,
            "graphRagRate": p.graph_rag_rate,
            "hydeRate": p.hyde_rate,

            # Dimension 6: Feedback signals
            "thumbsUpCount": p.thumbs_up_count or 0,
            "thumbsDownCount": p.thumbs_down_count or 0,
            "followUpRate": p.follow_up_rate,
            "satisfactionTopics": p.satisfaction_topics or [],

            # Dimension 7: mem0
            "mem0FactsCount": p.mem0_facts_count or 0,
            "mem0LastSync": utc_isoformat(p.mem0_last_sync),

            # Metadata
            "totalQueries": p.total_queries or 0,
            "totalConversations": p.total_conversations or 0,
            "lastActiveTime": utc_isoformat(p.last_active_time),
            "profileVersion": p.profile_version or 1,
            "createdAt": utc_isoformat(p.created_at),
            "updatedAt": utc_isoformat(p.updated_at),
        },
    }


# ─── Rebuild endpoint ───


@router.post("/{user_id}/rebuild")
async def rebuild_profile(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """Manually trigger profile rebuild for a user."""
    # Verify user exists
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    profile = await build_or_update_profile(db, user_id, target.tenant_id or "default")
    if profile is None:
        raise HTTPException(status_code=400, detail="该用户尚无提问记录，无法构建画像")

    await db.commit()
    return {"code": "0", "message": "画像重建完成", "data": {"profileVersion": profile.profile_version}}


@router.get("/{user_id}/interview")
async def get_interview_profile(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """Get the interview capability radar and recent interview trend."""
    from app.api.interview import _profile_payload

    return {"code": "0", "data": await _profile_payload(db, user_id)}


# ─── Memory facts management ───


@router.get("/{user_id}/memories")
async def list_user_memories(
    user_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_admin_user),
):
    """List all mem0 memory facts for a user."""
    memories = await Mem0Manager.get_instance().get_all(user_id, limit=limit)
    return {
        "code": "0",
        "data": {
            "userId": user_id,
            "count": len(memories),
            "memories": memories,
        },
    }


@router.delete("/{user_id}/memories/{memory_id}")
async def delete_memory(
    user_id: str,
    memory_id: str,
    user: User = Depends(get_admin_user),
):
    """Delete a single memory fact."""
    success = await Mem0Manager.get_instance().delete(memory_id)
    if not success:
        raise HTTPException(status_code=400, detail="删除失败，记忆不存在或已删除")

    # Update mem0_facts_count in profile
    async with async_session_factory() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if profile:
            count = await Mem0Manager.get_instance().count(user_id)
            profile.mem0_facts_count = count
            await session.commit()

    return {"code": "0", "message": "记忆已删除"}


# ─── Config endpoint ───


@router.get("/config")
async def get_profile_config(
    user: User = Depends(get_admin_user),
):
    """Get current profile/mem0 configuration (runtime values)."""
    return {
        "code": "0",
        "data": {
            "mem0Enabled": settings.mem0_enabled,
            "mem0CollectionName": settings.mem0_collection_name,
            "mem0Model": settings.mem0_model,
            "profileUpdateMode": settings.profile_update_mode,
            "profileDailyCron": settings.profile_daily_cron,
            "profileLlmModel": settings.profile_llm_model,
            "profileMinQueriesForBuild": settings.profile_min_queries_for_build,
            "mem0SearchTopK": settings.mem0_search_top_k,
        },
    }
