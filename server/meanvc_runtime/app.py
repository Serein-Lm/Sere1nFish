"""Private authenticated WebSocket service for streaming MeanVC sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket
from starlette.websockets import WebSocketDisconnect

from .engine import (
    CHUNK_SAMPLES,
    OUTPUT_SAMPLE_WIDTH,
    SAMPLE_RATE,
    MeanVCEngine,
    MeanVCStreamState,
    decode_reference_audio,
    pcm16_to_float,
)

TOKEN_FILE = Path(os.getenv("MEANVC_API_TOKEN_FILE", "/run/secrets/meanvc_api_token"))
MODEL_DIR = Path(os.getenv("MEANVC_MODEL_DIR", "/models"))
MAX_REFERENCE_BYTES = min(
    30 * 1024 * 1024,
    max(1024 * 1024, int(os.getenv("MEANVC_MAX_REFERENCE_BYTES", str(20 * 1024 * 1024)))),
)
MIN_REFERENCE_SECONDS = max(1.0, float(os.getenv("MEANVC_MIN_REFERENCE_SECONDS", "3")))
MAX_REFERENCE_SECONDS = min(30.0, max(5.0, float(os.getenv("MEANVC_MAX_REFERENCE_SECONDS", "15"))))
SESSION_TTL_SECONDS = max(60, int(os.getenv("MEANVC_SESSION_TTL_SECONDS", "900")))
MAX_SESSIONS = max(1, min(8, int(os.getenv("MEANVC_MAX_SESSIONS", "2"))))


def _load_token() -> str:
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Unable to read MeanVC token file: {TOKEN_FILE}") from exc
    if len(token) < 32:
        raise RuntimeError("MeanVC API token must contain at least 32 characters")
    return token


API_TOKEN = _load_token()
logger = logging.getLogger(__name__)
engine = MeanVCEngine(
    model_dir=MODEL_DIR,
    device=os.getenv("MEANVC_DEVICE", "auto").strip().lower() or "auto",
    cpu_threads=max(1, int(os.getenv("MEANVC_CPU_THREADS", "2"))),
)


@dataclass(slots=True)
class SessionStats:
    input_bytes: int = 0
    output_bytes: int = 0
    chunks: int = 0
    last_inference_ms: float = 0.0
    average_inference_ms: float = 0.0


@dataclass(slots=True)
class VoiceSession:
    session_id: str
    token_hash: str
    stream: MeanVCStreamState
    created_monotonic: float = field(default_factory=time.monotonic)
    last_used_monotonic: float = field(default_factory=time.monotonic)
    connected: bool = False
    stats: SessionStats = field(default_factory=SessionStats)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "connected": self.connected,
            "sample_rate": SAMPLE_RATE,
            "chunk_samples": CHUNK_SAMPLES,
            "chunk_ms": round(CHUNK_SAMPLES * 1000 / SAMPLE_RATE),
            "steps": self.stream.steps,
            "stats": asdict(self.stats),
        }


sessions: dict[str, VoiceSession] = {}
sessions_lock = asyncio.Lock()
cleanup_task: asyncio.Task[None] | None = None


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    prefix = "Bearer "
    candidate = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not candidate or not secrets.compare_digest(candidate, API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid MeanVC API token")


def _websocket_authorized(websocket: WebSocket, session: VoiceSession) -> bool:
    authorization = websocket.headers.get("authorization") or ""
    candidate = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not candidate:
        return False
    return secrets.compare_digest(
        hashlib.sha256(candidate.encode()).hexdigest(),
        session.token_hash,
    )


async def _cleanup_sessions() -> None:
    while True:
        await asyncio.sleep(30)
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        async with sessions_lock:
            expired = [
                session_id
                for session_id, session in sessions.items()
                if not session.connected and session.last_used_monotonic < cutoff
            ]
            for session_id in expired:
                sessions.pop(session_id, None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global cleanup_task
    await asyncio.to_thread(engine.initialize)
    cleanup_task = asyncio.create_task(_cleanup_sessions(), name="meanvc-session-cleanup")
    yield
    if cleanup_task:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)


app = FastAPI(
    title="Sere1nFish MeanVC Runtime",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": engine.initialized,
        "service": "meanvc-runtime",
        "device": str(engine.device),
        "warmup_ms": round(engine.warmup_ms, 2),
    }


@app.get("/v1/status", dependencies=[Depends(require_api_token)])
async def status() -> dict[str, Any]:
    return {
        "ok": engine.initialized,
        "provider": "meanvc",
        "device": str(engine.device),
        "warmup_ms": round(engine.warmup_ms, 2),
        "sample_rate": SAMPLE_RATE,
        "chunk_samples": CHUNK_SAMPLES,
        "active_sessions": sum(1 for session in sessions.values() if session.connected),
        "session_count": len(sessions),
        "max_sessions": MAX_SESSIONS,
        "reference_seconds": {
            "minimum": MIN_REFERENCE_SECONDS,
            "maximum": MAX_REFERENCE_SECONDS,
        },
    }


@app.post("/v1/sessions", dependencies=[Depends(require_api_token)])
async def create_session(
    reference: UploadFile = File(...),
    authorized_use: bool = Form(...),
    steps: int = Form(default=2, ge=1, le=2),
) -> dict[str, Any]:
    if not authorized_use:
        raise HTTPException(status_code=403, detail="Explicit voice authorization is required")
    payload = await reference.read(MAX_REFERENCE_BYTES + 1)
    if len(payload) > MAX_REFERENCE_BYTES:
        raise HTTPException(status_code=413, detail="Target voice sample exceeds the size limit")
    try:
        waveform = await asyncio.to_thread(
            decode_reference_audio,
            payload,
            minimum_seconds=MIN_REFERENCE_SECONDS,
            maximum_seconds=MAX_REFERENCE_SECONDS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async with sessions_lock:
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(status_code=429, detail="MeanVC session capacity has been reached")
    try:
        stream = await asyncio.to_thread(engine.create_stream, waveform, steps=steps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    session = VoiceSession(
        session_id=session_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        stream=stream,
    )
    async with sessions_lock:
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(status_code=429, detail="MeanVC session capacity has been reached")
        sessions[session_id] = session
    return {
        **session.as_dict(),
        "token": token,
        "websocket_path": f"/v1/stream/{session_id}",
        "expires_in": SESSION_TTL_SECONDS,
    }


@app.get("/v1/sessions/{session_id}", dependencies=[Depends(require_api_token)])
async def get_session(session_id: str) -> dict[str, Any]:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="MeanVC session not found")
    session.last_used_monotonic = time.monotonic()
    return session.as_dict()


@app.delete("/v1/sessions/{session_id}", dependencies=[Depends(require_api_token)])
async def delete_session(session_id: str) -> dict[str, bool]:
    async with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return {"deleted": False}
        if session.connected:
            raise HTTPException(status_code=409, detail="Disconnect the MeanVC stream before deleting it")
        sessions.pop(session_id, None)
    return {"deleted": True}


@app.websocket("/v1/stream/{session_id}")
async def stream_audio(websocket: WebSocket, session_id: str) -> None:
    session = sessions.get(session_id)
    if not session or not _websocket_authorized(websocket, session):
        await websocket.close(code=4401)
        return
    async with sessions_lock:
        current = sessions.get(session_id)
        if current is not session or session.connected:
            await websocket.close(code=4429)
            return
        session.connected = True
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "session.ready", **session.as_dict()}))
    buffered = bytearray()
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            payload = message.get("bytes")
            if payload is None:
                continue
            buffered.extend(payload)
            session.stats.input_bytes += len(payload)
            while True:
                required_bytes = session.stream.required_samples * OUTPUT_SAMPLE_WIDTH
                if len(buffered) < required_bytes:
                    break
                chunk = bytes(buffered[:required_bytes])
                del buffered[:required_bytes]
                output = await asyncio.to_thread(
                    engine.process,
                    session.stream,
                    pcm16_to_float(chunk),
                )
                await websocket.send_bytes(output)
                session.stats.output_bytes += len(output)
                session.stats.chunks = session.stream.chunks
                session.stats.last_inference_ms = round(session.stream.last_inference_ms, 2)
                session.stats.average_inference_ms = round(
                    session.stream.total_inference_ms / max(1, session.stream.chunks),
                    2,
                )
                session.last_used_monotonic = time.monotonic()
    except asyncio.CancelledError:
        raise
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("MeanVC stream failed for session %s", session_id)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(exc)[:300]}))
        except Exception:
            pass
    finally:
        async with sessions_lock:
            session.connected = False
            session.last_used_monotonic = time.monotonic()
