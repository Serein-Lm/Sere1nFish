"""Session manager for mobile preview and AI control workloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from typing import Any


class SessionState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class SessionRecord:
    session_id: str
    device_id: str
    kind: str
    state: SessionState = SessionState.CREATED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def touch(self) -> None:
        self.updated_at = time.time()


class MobileSessionManager:
    """In-memory session manager with device-level exclusivity controls."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._device_locks: dict[str, str] = {}

    def create(self, *, device_id: str, kind: str, metadata: dict[str, Any] | None = None) -> SessionRecord:
        existing = self._device_locks.get(device_id)
        if existing and existing in self._sessions and self._sessions[existing].state in {SessionState.CREATED, SessionState.RUNNING, SessionState.PAUSED}:
            raise RuntimeError(f"device '{device_id}' already has an active session")

        session = SessionRecord(
            session_id=uuid.uuid4().hex[:12],
            device_id=device_id,
            kind=kind,
            metadata=dict(metadata or {}),
        )
        self._sessions[session.session_id] = session
        self._device_locks[device_id] = session.session_id
        return session

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def update_state(self, session_id: str, state: SessionState, error: str | None = None) -> None:
        session = self._sessions[session_id]
        session.state = state
        session.error = error
        session.touch()

    def release(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session and self._device_locks.get(session.device_id) == session_id:
            self._device_locks.pop(session.device_id, None)

    def active_for_device(self, device_id: str) -> SessionRecord | None:
        session_id = self._device_locks.get(device_id)
        if not session_id:
            return None
        return self._sessions.get(session_id)
