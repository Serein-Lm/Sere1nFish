"""Shared mobile streaming session primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class MobileStreamSession:
    session_id: str
    device_id: str
    state: StreamState = StreamState.IDLE
    latest_frame: bytes | None = None
    latest_metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def update_frame(self, frame: bytes, metadata: dict[str, Any] | None = None) -> None:
        self.latest_frame = frame
        if metadata:
            self.latest_metadata.update(metadata)

    def mark_running(self) -> None:
        self.state = StreamState.RUNNING
        self.error = None

    def mark_failed(self, error: str) -> None:
        self.state = StreamState.FAILED
        self.error = error

    def mark_stopping(self) -> None:
        self.state = StreamState.STOPPING
