"""Server-side stop-generation registry tests."""
from app.services.stream_control import (
    is_stop_requested,
    register_stream,
    request_stop,
    unregister_stream,
)


def test_stop_flag_lifecycle():
    register_stream("conv-1")
    try:
        assert is_stop_requested("conv-1") is False
        assert request_stop("conv-1") is True
        assert is_stop_requested("conv-1") is True
    finally:
        unregister_stream("conv-1")
    assert is_stop_requested("conv-1") is False


def test_stop_unknown_stream_returns_false():
    assert request_stop("missing-stream") is False
    assert is_stop_requested("missing-stream") is False


def test_empty_key_is_ignored():
    register_stream("")
    assert request_stop("") is False
    assert is_stop_requested("") is False


def test_unregister_is_idempotent():
    register_stream("conv-2")
    unregister_stream("conv-2")
    unregister_stream("conv-2")
    assert is_stop_requested("conv-2") is False
