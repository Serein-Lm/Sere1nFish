"""Ephemeral server-side media bus for protected OBS browser sources."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.observability import obs_log


MEDIA_PACKET_VIDEO = 1
MEDIA_PACKET_AUDIO = 2
MEDIA_PACKET_AUDIO_16K = 3
MAX_VIDEO_PACKET_BYTES = 8 * 1024 * 1024
MAX_AUDIO_PACKET_BYTES = 512 * 1024


class MediaOutputError(RuntimeError):
    pass


class MediaOutputNotFound(MediaOutputError):
    pass


class MediaOutputPermissionError(MediaOutputError):
    pass


@dataclass(slots=True)
class MediaOutputSubscription:
    subscription_id: str
    session_id: str
    queue: asyncio.Queue[bytes | None]


@dataclass(slots=True)
class _MediaOutputSession:
    session_id: str
    owner: str
    viewer_token_digest: str
    created_at: float
    expires_at: float
    subscribers: dict[str, asyncio.Queue[bytes | None]] = field(default_factory=dict)
    video_frames: int = 0
    audio_chunks: int = 0
    last_published_at: float | None = None


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class MediaOutputService:
    """Owns short-lived A/V sessions and fans packets out to OBS viewers."""

    def __init__(
        self,
        *,
        max_sessions: int = 32,
        max_sessions_per_owner: int = 4,
        max_subscribers_per_session: int = 4,
        subscriber_queue_size: int = 128,
    ) -> None:
        self._max_sessions = max_sessions
        self._max_sessions_per_owner = max_sessions_per_owner
        self._max_subscribers_per_session = max_subscribers_per_session
        self._subscriber_queue_size = subscriber_queue_size
        self._sessions: dict[str, _MediaOutputSession] = {}
        self._token_sessions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _metadata(session: _MediaOutputSession) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "viewer_path": "/api/v1/media-output/view",
            "viewer_count": len(session.subscribers),
            "video_frames": session.video_frames,
            "audio_chunks": session.audio_chunks,
            "last_published_at": session.last_published_at,
        }

    @staticmethod
    def _terminate(session: _MediaOutputSession) -> None:
        for queue in session.subscribers.values():
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            queue.put_nowait(None)
        session.subscribers.clear()

    def _remove_locked(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        self._token_sessions.pop(session.viewer_token_digest, None)
        self._terminate(session)

    def _purge_expired_locked(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            self._remove_locked(session_id)

    def _owned_session_locked(
        self,
        session_id: str,
        owner: str,
    ) -> _MediaOutputSession:
        session = self._sessions.get(session_id)
        if not session:
            raise MediaOutputNotFound("远端 OBS 输出会话不存在或已过期")
        if session.owner != owner:
            raise MediaOutputPermissionError("远端 OBS 输出会话不存在")
        return session

    async def create(self, owner: str, *, ttl_seconds: int = 8 * 3600) -> dict[str, Any]:
        now = time.time()
        ttl = min(12 * 3600, max(15 * 60, int(ttl_seconds)))
        token = secrets.token_urlsafe(32)
        digest = _token_digest(token)
        session = _MediaOutputSession(
            session_id=f"media-{uuid.uuid4().hex[:20]}",
            owner=owner,
            viewer_token_digest=digest,
            created_at=now,
            expires_at=now + ttl,
        )
        async with self._lock:
            self._purge_expired_locked(now)
            if len(self._sessions) >= self._max_sessions:
                raise MediaOutputError("远端 OBS 输出会话已达到系统上限")
            owned_count = sum(
                item.owner == owner for item in self._sessions.values()
            )
            if owned_count >= self._max_sessions_per_owner:
                raise MediaOutputError("当前用户的远端 OBS 输出会话已达到上限")
            self._sessions[session.session_id] = session
            self._token_sessions[digest] = session.session_id
        obs_log(
            "远端 OBS 输出会话已创建",
            task_id=session.session_id,
            source="media_output",
            level="info",
            event="media_output_created",
            data={"owner": owner, "expires_at": session.expires_at},
        )
        return {**self._metadata(session), "viewer_token": token}

    async def get(self, session_id: str, owner: str) -> dict[str, Any]:
        async with self._lock:
            self._purge_expired_locked(time.time())
            return self._metadata(self._owned_session_locked(session_id, owner))

    async def require_owner(self, session_id: str, owner: str) -> None:
        async with self._lock:
            self._purge_expired_locked(time.time())
            self._owned_session_locked(session_id, owner)

    async def delete(self, session_id: str, owner: str) -> None:
        async with self._lock:
            self._purge_expired_locked(time.time())
            self._owned_session_locked(session_id, owner)
            self._remove_locked(session_id)
        obs_log(
            "远端 OBS 输出会话已关闭",
            task_id=session_id,
            source="media_output",
            level="info",
            event="media_output_deleted",
            data={"owner": owner},
        )

    async def subscribe(self, viewer_token: str) -> MediaOutputSubscription:
        digest = _token_digest(viewer_token)
        now = time.time()
        async with self._lock:
            self._purge_expired_locked(now)
            session_id = self._token_sessions.get(digest)
            session = self._sessions.get(session_id or "")
            if not session or not secrets.compare_digest(
                session.viewer_token_digest,
                digest,
            ):
                raise MediaOutputNotFound("远端 OBS 输出地址无效或已过期")
            if len(session.subscribers) >= self._max_subscribers_per_session:
                raise MediaOutputError("远端 OBS 输出观看端已达到上限")
            subscription_id = uuid.uuid4().hex
            queue: asyncio.Queue[bytes | None] = asyncio.Queue(
                maxsize=self._subscriber_queue_size
            )
            session.subscribers[subscription_id] = queue
            return MediaOutputSubscription(subscription_id, session.session_id, queue)

    async def unsubscribe(self, subscription: MediaOutputSubscription) -> None:
        async with self._lock:
            session = self._sessions.get(subscription.session_id)
            if session:
                session.subscribers.pop(subscription.subscription_id, None)

    async def publish_video(self, session_id: str, owner: str, data: bytes) -> None:
        if not data or len(data) > MAX_VIDEO_PACKET_BYTES:
            raise MediaOutputError("远端 OBS 视频帧大小无效")
        await self._publish(session_id, owner, MEDIA_PACKET_VIDEO, data)

    async def publish_audio(
        self,
        session_id: str,
        owner: str,
        data: bytes,
        *,
        sample_rate: int = 24000,
    ) -> None:
        if not data or len(data) > MAX_AUDIO_PACKET_BYTES or len(data) % 2:
            raise MediaOutputError("远端 OBS PCM 音频分片大小无效")
        packet_type = {
            16000: MEDIA_PACKET_AUDIO_16K,
            24000: MEDIA_PACKET_AUDIO,
        }.get(sample_rate)
        if packet_type is None:
            raise MediaOutputError("远端 OBS PCM 音频采样率不受支持")
        await self._publish(session_id, owner, packet_type, data)

    async def _publish(
        self,
        session_id: str,
        owner: str,
        media_kind: int,
        data: bytes,
    ) -> None:
        packet = bytes((media_kind,)) + data
        now = time.time()
        async with self._lock:
            self._purge_expired_locked(now)
            session = self._owned_session_locked(session_id, owner)
            session.last_published_at = now
            if media_kind == MEDIA_PACKET_VIDEO:
                session.video_frames += 1
            else:
                session.audio_chunks += 1
            for queue in session.subscribers.values():
                if queue.full():
                    while True:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                queue.put_nowait(packet)

    async def close(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._token_sessions.clear()
            for session in sessions:
                self._terminate(session)


_media_output_service = MediaOutputService()


def get_media_output_service() -> MediaOutputService:
    return _media_output_service


async def shutdown_media_output_service() -> None:
    await _media_output_service.close()
