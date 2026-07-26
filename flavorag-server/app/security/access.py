from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Permission(IntEnum):
    READ = 1
    WRITE = 2
    ADMIN = 3


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    department_id: str = ""
    role: str = "user"


@dataclass(frozen=True)
class Resource:
    resource_type: str
    resource_id: str
    tenant_id: str
    owner_id: str
    department_id: str = ""
    parent_id: str = ""


@dataclass(frozen=True)
class AccessGrant:
    subject_type: str
    subject_id: str
    resource_type: str
    resource_id: str
    permission: Permission


def is_allowed(
    principal: Principal,
    resource: Resource,
    required: Permission,
    grants: list[AccessGrant],
    *,
    parent_allowed: bool = True,
) -> bool:
    """Evaluate one resource without database or transport concerns."""
    if principal.role == "system_admin":
        return True
    if not principal.tenant_id or principal.tenant_id != resource.tenant_id:
        return False
    if resource.resource_type == "DOCUMENT" and not parent_allowed:
        return False
    if principal.role in {"admin", "tenant_admin"}:
        return True
    if resource.owner_id and resource.owner_id == principal.user_id:
        return True
    if (
        resource.department_id
        and principal.department_id
        and resource.department_id == principal.department_id
    ):
        return True

    subjects = {
        ("USER", principal.user_id),
        ("ROLE", principal.role),
    }
    if principal.department_id:
        subjects.add(("DEPARTMENT", principal.department_id))
    return any(
        (grant.subject_type, grant.subject_id) in subjects
        and grant.resource_type == resource.resource_type
        and grant.resource_id == resource.resource_id
        and grant.permission >= required
        for grant in grants
    )

