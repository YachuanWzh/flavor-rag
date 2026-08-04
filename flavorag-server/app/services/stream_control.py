"""Server-side cancellation registry for streaming chat generations.

The registry maps conversation IDs to cancellation flags.  ``POST /chat/stop``
sets the flag; the SSE generator checks it on every token, breaks out of the
LLM stream and closes the upstream HTTP connection so the provider stops
billing tokens immediately.

Scope: single-process (uvicorn worker).  In a multi-worker deployment the
stop request must be routed to the worker serving the stream; clients still
benefit because closing the browser connection is detected separately via
``request.is_disconnected()``.
"""
from __future__ import annotations

import asyncio

_active_streams: dict[str, asyncio.Event] = {}


def register_stream(stream_key: str) -> None:
    """Register an active stream for the given key (conversation id)."""
    if not stream_key:
        return
    _active_streams[stream_key] = asyncio.Event()


def unregister_stream(stream_key: str) -> None:
    """Remove the stream entry; safe to call multiple times."""
    if not stream_key:
        return
    _active_streams.pop(stream_key, None)


def request_stop(stream_key: str) -> bool:
    """Signal the stream to stop.  Returns False when no stream is registered."""
    event = _active_streams.get(stream_key)
    if event is None:
        return False
    event.set()
    return True


def is_stop_requested(stream_key: str) -> bool:
    """Return True when a stop has been requested for the given key."""
    if not stream_key:
        return False
    event = _active_streams.get(stream_key)
    return event is not None and event.is_set()
