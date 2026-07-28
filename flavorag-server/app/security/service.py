from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    ResourceACL,
    User,
)
from app.rag.search.base import SearchResult
from app.security.access import Permission, Principal


def principal_from_user(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id or "default",
        department_id=user.department_id or "",
        role=user.role or "user",
    )


def _permission_names(required: Permission) -> list[str]:
    if required == Permission.ADMIN:
        return ["ADMIN"]
    if required == Permission.WRITE:
        return ["WRITE", "ADMIN"]
    return ["READ", "WRITE", "ADMIN"]


def _acl_exists(
    principal: Principal,
    resource_type: str,
    resource_id_column,
    required: Permission,
):
    subject_predicates = [
        and_(ResourceACL.subject_type == "USER", ResourceACL.subject_id == principal.user_id),
        and_(ResourceACL.subject_type == "ROLE", ResourceACL.subject_id == principal.role),
    ]
    if principal.department_id:
        subject_predicates.append(
            and_(
                ResourceACL.subject_type == "DEPARTMENT",
                ResourceACL.subject_id == principal.department_id,
            )
        )
    return exists(
        select(ResourceACL.id).where(
            ResourceACL.tenant_id == principal.tenant_id,
            ResourceACL.resource_type == resource_type,
            ResourceACL.resource_id == resource_id_column,
            ResourceACL.permission.in_(_permission_names(required)),
            ResourceACL.deleted == 0,
            or_(*subject_predicates),
        )
    )


def kb_access_predicate(principal: Principal, required: Permission = Permission.READ):
    if principal.role == "system_admin":
        return KnowledgeBase.deleted == 0
    tenant = KnowledgeBase.tenant_id == principal.tenant_id
    if principal.role in {"admin", "tenant_admin"}:
        return and_(tenant, KnowledgeBase.deleted == 0)
    access = [
        KnowledgeBase.created_by == principal.user_id,
        _acl_exists(
            principal,
            "KNOWLEDGE_BASE",
            KnowledgeBase.id,
            required,
        ),
    ]
    if required == Permission.READ:
        access.append(KnowledgeBase.visibility == "TENANT")
        if principal.department_id:
            access.append(
                and_(
                    KnowledgeBase.visibility == "DEPARTMENT",
                    KnowledgeBase.department_id == principal.department_id,
                )
            )
    return and_(tenant, KnowledgeBase.deleted == 0, or_(*access))


def document_access_predicate(
    principal: Principal,
    required: Permission = Permission.READ,
):
    if principal.role == "system_admin":
        return KnowledgeDocument.deleted == 0
    tenant = KnowledgeDocument.tenant_id == principal.tenant_id
    parent_allowed = exists(
        select(KnowledgeBase.id).where(
            KnowledgeBase.id == KnowledgeDocument.kb_id,
            kb_access_predicate(principal, required),
        )
    )
    if principal.role in {"admin", "tenant_admin"}:
        return and_(tenant, KnowledgeDocument.deleted == 0, parent_allowed)
    access = [
        KnowledgeDocument.created_by == principal.user_id,
        KnowledgeDocument.visibility == "INHERIT",
        _acl_exists(principal, "DOCUMENT", KnowledgeDocument.id, required),
    ]
    if required == Permission.READ:
        access.append(KnowledgeDocument.visibility == "TENANT")
        if principal.department_id:
            access.append(
                and_(
                    KnowledgeDocument.visibility == "DEPARTMENT",
                    KnowledgeDocument.department_id == principal.department_id,
                )
            )
    return and_(
        tenant,
        KnowledgeDocument.deleted == 0,
        parent_allowed,
        or_(*access),
    )


async def require_kb(
    session: AsyncSession,
    principal: Principal,
    kb_id: str,
    required: Permission = Permission.READ,
) -> KnowledgeBase:
    result = await session.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            kb_access_predicate(principal, required),
        )
    )
    resource = result.scalar_one_or_none()
    if resource is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return resource


async def require_document(
    session: AsyncSession,
    principal: Principal,
    doc_id: str,
    required: Permission = Permission.READ,
) -> KnowledgeDocument:
    result = await session.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id,
            document_access_predicate(principal, required),
        )
    )
    resource = result.scalar_one_or_none()
    if resource is None:
        raise HTTPException(status_code=404, detail="document not found")
    return resource


async def filter_authorized_results(
    session: AsyncSession,
    principal: Principal,
    results: list[SearchResult],
    *,
    kb_id: str | None = None,
    kb_ids: list[str] | None = None,
) -> list[SearchResult]:
    """Fail-closed post-filter for every external retrieval channel."""
    chunk_ids = [item.chunk_id for item in results if item.chunk_id]
    allowed_kb_ids = list(dict.fromkeys(kb_ids or ([kb_id] if kb_id else [])))
    if not chunk_ids or not allowed_kb_ids:
        return []
    rows = await session.execute(
        select(KnowledgeChunk.id).join(
            KnowledgeDocument,
            KnowledgeDocument.id == KnowledgeChunk.doc_id,
        ).where(
            KnowledgeChunk.id.in_(chunk_ids),
            KnowledgeChunk.kb_id.in_(allowed_kb_ids),
            KnowledgeChunk.tenant_id == principal.tenant_id,
            KnowledgeChunk.deleted == 0,
            KnowledgeChunk.enabled == 1,
            document_access_predicate(principal, Permission.READ),
        )
    )
    allowed = set(rows.scalars().all())
    return [item for item in results if item.chunk_id in allowed]
