"""Document approval workflow — state machine, records, and permission checks."""
from __future__ import annotations

from dataclasses import dataclass


class InvalidTransition(RuntimeError):
    """Raised when a document status transition violates the approval FSM."""


# Valid transitions: source → set of allowed targets
_TRANSITIONS: dict[str, set[str]] = {
    "UPLOADED": {"PENDING_REVIEW", "QUEUED"},  # QUEUED for bypass when disabled
    "PENDING_REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"QUEUED"},
    "QUEUED": {"RUNNING"},
    "RUNNING": {"SUCCESS", "RETRY", "DEAD"},
    "RETRY": {"RUNNING", "DEAD"},
}


class ApprovalStateMachine:
    """Enforces the document approval lifecycle."""

    def can_transition(self, current: str, target: str) -> bool:
        allowed = _TRANSITIONS.get(current.upper(), set())
        return target.upper() in allowed

    def transition(self, current: str, target: str) -> str:
        if not self.can_transition(current, target):
            raise InvalidTransition(
                f"cannot transition from {current!r} to {target!r}"
            )
        return target.upper()


@dataclass
class ApprovalRecord:
    doc_id: str
    kb_id: str
    tenant_id: str
    status: str = "pending"
    submitted_by: str = ""
    reviewer_id: str | None = None
    review_comment: str | None = None


def can_review(*, role: str, is_kb_owner: bool, has_write_acl: bool) -> bool:
    """Determine whether a user may approve/reject a document."""
    if role in ("admin", "system_admin", "tenant_admin"):
        return True
    if is_kb_owner:
        return True
    return has_write_acl


def effective_initial_status(*, approval_enabled: bool) -> str:
    """Return the document status after upload, respecting the approval toggle."""
    if approval_enabled:
        return "PENDING_REVIEW"
    return "queued"
