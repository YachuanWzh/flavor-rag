"""Tests for F4: Document approval workflow."""
from __future__ import annotations

import pytest

# ─── F4.1 Approval state machine ───


def test_approval_config_defaults():
    from app.config.settings import settings

    assert settings.approval_enabled is False


def test_valid_transitions():
    from app.services.approval import ApprovalStateMachine

    sm = ApprovalStateMachine()
    assert sm.can_transition("UPLOADED", "PENDING_REVIEW") is True
    assert sm.can_transition("PENDING_REVIEW", "APPROVED") is True
    assert sm.can_transition("PENDING_REVIEW", "REJECTED") is True
    assert sm.can_transition("APPROVED", "QUEUED") is True


def test_invalid_transitions():
    from app.services.approval import ApprovalStateMachine

    sm = ApprovalStateMachine()
    assert sm.can_transition("UPLOADED", "APPROVED") is False
    assert sm.can_transition("REJECTED", "APPROVED") is False
    assert sm.can_transition("APPROVED", "REJECTED") is False
    assert sm.can_transition("SUCCESS", "PENDING_REVIEW") is False


def test_transition_raises_on_invalid():
    from app.services.approval import ApprovalStateMachine, InvalidTransition

    sm = ApprovalStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition("UPLOADED", "APPROVED")


def test_bypass_when_disabled():
    from app.services.approval import ApprovalStateMachine

    sm = ApprovalStateMachine()
    # When approval is disabled, UPLOADED goes directly to QUEUED
    assert sm.can_transition("UPLOADED", "QUEUED") is True


# ─── F4.2 Approval record model ───


def test_approval_record_dataclass():
    from app.services.approval import ApprovalRecord

    record = ApprovalRecord(
        doc_id="doc1",
        kb_id="kb1",
        tenant_id="default",
        status="pending",
        submitted_by="user1",
    )
    assert record.status == "pending"
    assert record.reviewer_id is None
    assert record.review_comment is None


# ─── F4.3 Permission check ───


def test_reviewer_must_have_write_permission():
    from app.services.approval import can_review

    # Admin role can review
    assert can_review(role="admin", is_kb_owner=False, has_write_acl=False) is True
    # KB owner can review
    assert can_review(role="user", is_kb_owner=True, has_write_acl=False) is True
    # User with WRITE ACL can review
    assert can_review(role="user", is_kb_owner=False, has_write_acl=True) is True
    # Plain user without any permission cannot review
    assert can_review(role="user", is_kb_owner=False, has_write_acl=False) is False


# ─── F4.4 Approval-disabled backward compatibility ───


def test_effective_status_no_approval():
    from app.services.approval import effective_initial_status

    # When approval is disabled, documents go straight to queued
    assert effective_initial_status(approval_enabled=False) == "queued"
    # When enabled, documents wait for review
    assert effective_initial_status(approval_enabled=True) == "PENDING_REVIEW"
