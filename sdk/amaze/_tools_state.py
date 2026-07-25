"""Push-driven current-tools state.

The orchestrator posts `POST /_amaze/tools_changed` to the agent's chat
port whenever `policy.allowed_tools` diffs. The SDK's inbound handler
calls `_apply_push` to store the new payload and set a dirty flag.
Author code reads the state via two public functions:

    amaze.is_tools_changed() -> bool
        Atomic read-and-clear. First caller after a push sees True;
        concurrent callers see False. Author decides when and how
        often to check — typically at the start of their message
        handler.

    amaze.current_tools() -> list[dict]
        Snapshot of the currently-allowed tool set with schemas:
        [{name, description, inputSchema}, ...]. Empty list until
        the first push. Author owns the copy — safe to mutate the
        return value.

Both functions are plain sync — they use a threading.Lock so they
can be called from either sync or async handlers. Lock hold time is
sub-microsecond (list slice + boolean toggle), so briefly blocking
an event loop thread is fine.

Author never calls `_apply_push` directly. It's SDK-internal, invoked
by the FastAPI endpoint on the chat port.
"""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_current_tools: list[dict[str, Any]] = []
_dirty_flag: bool = False


def _apply_push(payload: dict[str, Any]) -> None:
    """SDK-internal. Called by the /_amaze/tools_changed endpoint when a
    push arrives. Replaces the stored tool set and raises the dirty
    flag. Idempotent — repeat pushes with the same body still set the
    flag (author decides whether to no-op on their side).
    """
    global _current_tools, _dirty_flag
    tools = payload.get("allowed_tools")
    if not isinstance(tools, list):
        tools = []
    with _lock:
        _current_tools = [t for t in tools if isinstance(t, dict)]
        _dirty_flag = True


def is_tools_changed() -> bool:
    """Return True iff a push arrived since the last call, then reset
    the flag atomically. Two concurrent callers will see True on at
    most one of them.
    """
    global _dirty_flag
    with _lock:
        was_dirty = _dirty_flag
        _dirty_flag = False
        return was_dirty


def current_tools() -> list[dict[str, Any]]:
    """Return a copy of the current authoritative tool set. Each entry
    is `{name, description, inputSchema}` (whatever the orchestrator
    pushed). Empty list until the first push arrives.

    Copy is defensive: mutating the returned list can't affect SDK
    state. The individual dicts are shared, though — if you plan to
    mutate them, deep-copy first.
    """
    with _lock:
        return list(_current_tools)
