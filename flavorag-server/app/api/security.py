from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_db
from app.models import Department, ResourceACL, Tenant, User, gen_id
from app.security.access import Permission
from app.security.service import principal_from_user, require_document, require_kb

router = APIRouter(prefix="/api/security", tags=["security"])


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: str | None = None


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ACLGrantRequest(BaseModel):
    subject_type: str
    subject_id: str
    resource_type: str
    resource_id: str
    permission: str = "READ"


async def _require_resource_admin(
    db: AsyncSession,
    user: User,
    resource_type: str,
    resource_id: str,
) -> None:
    principal = principal_from_user(user)
    if resource_type == "KNOWLEDGE_BASE":
        await require_kb(db, principal, resource_id, Permission.ADMIN)
    elif resource_type == "DOCUMENT":
        await require_document(db, principal, resource_id, Permission.ADMIN)
    else:
        raise HTTPException(status_code=400, detail="unsupported resource type")


@router.get("/tenants")
async def list_tenants(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.role != "system_admin":
        raise HTTPException(status_code=403, detail="system admin required")
    rows = await db.execute(select(Tenant).where(Tenant.enabled == 1))
    return {
        "code": "0",
        "data": [{"id": item.id, "name": item.name} for item in rows.scalars().all()],
    }


@router.post("/tenants")
async def create_tenant(
    request: TenantCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role != "system_admin":
        raise HTTPException(status_code=403, detail="system admin required")
    tenant = Tenant(id=gen_id(), name=request.name.strip(), enabled=1)
    db.add(tenant)
    await db.flush()
    return {"code": "0", "data": {"id": tenant.id, "name": tenant.name}}


@router.get("/departments")
async def list_departments(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = await db.execute(
        select(Department).where(
            Department.tenant_id == (user.tenant_id or "default"),
            Department.deleted == 0,
        )
    )
    return {
        "code": "0",
        "data": [
            {"id": item.id, "name": item.name, "parentId": item.parent_id}
            for item in rows.scalars().all()
        ],
    }


@router.post("/departments")
async def create_department(
    request: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in {"admin", "tenant_admin", "system_admin"}:
        raise HTTPException(status_code=403, detail="tenant admin required")
    department = Department(
        id=gen_id(),
        tenant_id=user.tenant_id or "default",
        parent_id=request.parent_id,
        name=request.name.strip(),
        created_by=user.id,
    )
    db.add(department)
    await db.flush()
    return {"code": "0", "data": {"id": department.id, "name": department.name}}


@router.post("/acl")
async def grant_acl(
    request: ACLGrantRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    subject_type = request.subject_type.upper()
    resource_type = request.resource_type.upper()
    permission = request.permission.upper()
    if subject_type not in {"USER", "DEPARTMENT", "ROLE"}:
        raise HTTPException(status_code=400, detail="unsupported subject type")
    if permission not in {"READ", "WRITE", "ADMIN"}:
        raise HTTPException(status_code=400, detail="unsupported permission")
    await _require_resource_admin(db, user, resource_type, request.resource_id)

    tenant_id = user.tenant_id or "default"
    if subject_type == "USER":
        subject = (
            await db.execute(
                select(User.id).where(
                    User.id == request.subject_id,
                    User.tenant_id == tenant_id,
                    User.deleted == 0,
                )
            )
        ).scalar_one_or_none()
        if subject is None:
            raise HTTPException(status_code=400, detail="subject user not in tenant")
    elif subject_type == "DEPARTMENT":
        subject = (
            await db.execute(
                select(Department.id).where(
                    Department.id == request.subject_id,
                    Department.tenant_id == tenant_id,
                    Department.deleted == 0,
                )
            )
        ).scalar_one_or_none()
        if subject is None:
            raise HTTPException(status_code=400, detail="subject department not in tenant")

    existing = (
        await db.execute(
            select(ResourceACL).where(
                ResourceACL.tenant_id == tenant_id,
                ResourceACL.subject_type == subject_type,
                ResourceACL.subject_id == request.subject_id,
                ResourceACL.resource_type == resource_type,
                ResourceACL.resource_id == request.resource_id,
                ResourceACL.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.permission = permission
        grant = existing
    else:
        grant = ResourceACL(
            id=gen_id(),
            tenant_id=tenant_id,
            subject_type=subject_type,
            subject_id=request.subject_id,
            resource_type=resource_type,
            resource_id=request.resource_id,
            permission=permission,
            created_by=user.id,
        )
        db.add(grant)
    await db.flush()
    return {"code": "0", "data": {"id": grant.id}}


@router.delete("/acl/{grant_id}")
async def revoke_acl(
    grant_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    grant = (
        await db.execute(
            select(ResourceACL).where(
                ResourceACL.id == grant_id,
                ResourceACL.tenant_id == (user.tenant_id or "default"),
                ResourceACL.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="ACL grant not found")
    await _require_resource_admin(db, user, grant.resource_type, grant.resource_id)
    grant.deleted = 1
    return {"code": "0", "data": None}
