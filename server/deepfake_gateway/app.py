"""Authenticated FaceFusion image and low-latency streaming gateway.

This process runs on the GPU node. It intentionally exposes a small API instead
of the FaceFusion Gradio application so callers do not depend on UI internals.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket
from fastapi.responses import Response

from .profiles import QUALITY_PROFILES, QualityProfile

MAX_IMAGE_BYTES = int(os.getenv("DEEPFAKE_MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))
MAX_FRAME_BYTES = int(os.getenv("DEEPFAKE_MAX_FRAME_BYTES", str(4 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("DEEPFAKE_MAX_IMAGE_PIXELS", str(3840 * 2160)))
MAX_SOURCE_IMAGES = min(8, max(1, int(os.getenv("DEEPFAKE_MAX_SOURCE_IMAGES", "4"))))
MAX_SOURCE_WIDTH = min(2560, max(512, int(os.getenv("DEEPFAKE_MAX_SOURCE_WIDTH", "1600"))))
SESSION_TTL_SECONDS = max(60, int(os.getenv("DEEPFAKE_SESSION_TTL_SECONDS", "900")))
MAX_SESSIONS = max(1, int(os.getenv("DEEPFAKE_MAX_SESSIONS", "2")))
MAX_STORED_SESSIONS = max(
    MAX_SESSIONS,
    int(os.getenv("DEEPFAKE_MAX_STORED_SESSIONS", str(MAX_SESSIONS * 4))),
)
IMAGE_JPEG_QUALITY = min(98, max(75, int(os.getenv("DEEPFAKE_IMAGE_JPEG_QUALITY", "95"))))
REALTIME_JPEG_QUALITY = min(96, max(70, int(os.getenv("DEEPFAKE_REALTIME_JPEG_QUALITY", "92"))))
DEFAULT_IMAGE_PROFILE = os.getenv("DEEPFAKE_DEFAULT_IMAGE_PROFILE", "quality").strip()
DEFAULT_REALTIME_PROFILE = os.getenv("DEEPFAKE_DEFAULT_REALTIME_PROFILE", "quality").strip()
CONFIG_PATH = os.getenv("FACEFUSION_CONFIG_PATH", "/opt/facefusion/facefusion.ini")
TOKEN_FILE = Path(os.getenv("DEEPFAKE_API_TOKEN_FILE", "/run/secrets/deepfake_api_token"))

QUALITY_PROFILES.get(DEFAULT_IMAGE_PROFILE)
QUALITY_PROFILES.get(DEFAULT_REALTIME_PROFILE)


class GatewayError(RuntimeError):
    """Base gateway error."""


class UnsafeContentError(GatewayError):
    """Raised when FaceFusion rejects a frame as unsafe."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_api_token() -> str:
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Unable to read API token file: {TOKEN_FILE}") from exc
    if len(token) < 32:
        raise RuntimeError("Deepfake API token must contain at least 32 characters")
    return token


API_TOKEN = _load_api_token()


def _decode_image(data: bytes, *, label: str) -> numpy.ndarray[Any, Any]:
    if not data:
        raise HTTPException(status_code=400, detail=f"{label} image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} image exceeds the size limit")
    frame = cv2.imdecode(numpy.frombuffer(data, dtype=numpy.uint8), cv2.IMREAD_COLOR)
    if frame is None or not numpy.any(frame):
        raise HTTPException(status_code=415, detail=f"{label} is not a supported image")
    height, width = frame.shape[:2]
    if height < 64 or width < 64 or height * width > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=422, detail=f"{label} image dimensions are unsupported")
    return frame


def _encode_jpeg(frame: numpy.ndarray[Any, Any], *, quality: int) -> bytes:
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise GatewayError("Unable to encode inference output")
    return encoded.tobytes()


def _fit_frame(frame: numpy.ndarray[Any, Any], max_width: int) -> numpy.ndarray[Any, Any]:
    height, width = frame.shape[:2]
    if max_width <= 0 or width <= max_width:
        return frame
    ratio = max_width / width
    return cv2.resize(frame, (max_width, max(64, int(height * ratio))), interpolation=cv2.INTER_AREA)


class FaceFusionRuntime:
    """Owns FaceFusion global state and serializes GPU inference."""

    def __init__(self) -> None:
        self.ready = False
        self.model = ""
        self.pixel_boost = ""
        self._processor_modules: dict[str, Any] = {}
        self._inference_lock = asyncio.Lock()
        self.started_at = _now_iso()
        self.warmup_ms = 0.0
        self.total_frames = 0
        self.total_inference_seconds = 0.0

    async def initialize(self) -> None:
        started = time.perf_counter()
        await asyncio.to_thread(self._initialize_sync)
        self.warmup_ms = (time.perf_counter() - started) * 1000
        self.ready = True

    def _initialize_sync(self) -> None:
        from facefusion import core, logger, state_manager
        from facefusion.args import apply_args
        from facefusion.processors.core import get_processors_modules
        from facefusion.program import create_program

        program = create_program()
        args = vars(program.parse_args(["run", "--config-path", CONFIG_PATH]))
        apply_args(args, state_manager.init_item)
        logger.init(state_manager.get_item("log_level"))
        if not core.common_pre_check():
            raise RuntimeError("FaceFusion model pre-check failed")
        self.model = str(state_manager.get_item("face_swapper_model") or "")
        self.pixel_boost = str(state_manager.get_item("face_swapper_pixel_boost") or "")
        processor_names = list(QUALITY_PROFILES.processor_names())
        modules = get_processors_modules(processor_names)
        for processor_name, module in zip(processor_names, modules, strict=True):
            if not module.pre_check():
                raise RuntimeError(f"FaceFusion processor pre-check failed: {processor_name}")
            self._processor_modules[processor_name] = module
        self._warmup_sync()

    def _warmup_sync(self) -> None:
        example_source = Path("/opt/facefusion/.assets/examples/source.jpg")
        example_target = Path("/opt/facefusion/.assets/examples/target-360p.mp4")
        if not example_source.is_file() or not example_target.is_file():
            return
        source = cv2.imread(str(example_source), cv2.IMREAD_COLOR)
        capture = cv2.VideoCapture(str(example_target))
        ok, target = capture.read()
        capture.release()
        if source is None or not ok or target is None:
            return
        self.validate_source_sync([source])
        for profile in QUALITY_PROFILES.all():
            self.process_sync([source], target, profile)

    def validate_source_sync(self, source_frames: list[numpy.ndarray[Any, Any]]) -> dict[str, Any]:
        from facefusion.face_creator import get_static_faces
        from facefusion.face_selector import sort_faces_by_order

        if not 1 <= len(source_frames) <= MAX_SOURCE_IMAGES:
            raise GatewayError(f"Provide between 1 and {MAX_SOURCE_IMAGES} source images")

        primary_faces = []
        source_details = []
        for index, source_frame in enumerate(source_frames, start=1):
            faces = sort_faces_by_order(get_static_faces([source_frame]), "large-small")
            if not faces:
                raise GatewayError(f"No face was detected in source image {index}")
            primary_face = faces[0]
            primary_faces.append(primary_face)
            x1, y1, x2, y2 = [float(value) for value in primary_face.bounding_box]
            frame_height, frame_width = source_frame.shape[:2]
            face_ratio = max(0.0, (x2 - x1) * (y2 - y1)) / float(frame_width * frame_height)
            source_details.append(
                {
                    "index": index,
                    "face_count": len(faces),
                    "face_ratio": round(face_ratio, 4),
                }
            )

        consistency = 1.0
        if len(primary_faces) > 1:
            similarities = []
            for left_index, left_face in enumerate(primary_faces):
                for right_face in primary_faces[left_index + 1 :]:
                    similarities.append(float(numpy.dot(left_face.embedding_norm, right_face.embedding_norm)))
            consistency = min(similarities)
            if consistency < 0.15:
                raise GatewayError("Source images do not appear to show the same identity")

        return {
            "count": len(source_frames),
            "identity_consistency": round(consistency, 4),
            "sources": source_details,
        }

    async def validate_source(self, source_frames: list[numpy.ndarray[Any, Any]]) -> dict[str, Any]:
        async with self._inference_lock:
            return await asyncio.to_thread(self.validate_source_sync, source_frames)

    def _apply_profile(self, profile: QualityProfile) -> None:
        from facefusion import state_manager

        state_manager.set_item("processors", list(profile.processors))
        state_manager.set_item("face_mask_types", list(profile.face_mask_types))
        state_manager.set_item("face_swapper_weight", profile.face_swapper_weight)
        if profile.face_enhancer_model:
            state_manager.set_item("face_enhancer_model", profile.face_enhancer_model)
            state_manager.set_item("face_enhancer_blend", profile.face_enhancer_blend)
            state_manager.set_item("face_enhancer_weight", profile.face_enhancer_weight)

    def process_sync(
        self,
        source_frames: list[numpy.ndarray[Any, Any]],
        target_frame: numpy.ndarray[Any, Any],
        profile: QualityProfile,
    ) -> numpy.ndarray[Any, Any]:
        from facefusion.audio import create_empty_audio_frame
        from facefusion.vision import extract_vision_mask

        self._apply_profile(profile)
        source_audio = create_empty_audio_frame()
        source_voice = create_empty_audio_frame()
        output = target_frame.copy()
        output_mask = extract_vision_mask(output)
        for processor_name in profile.processors:
            processor = self._processor_modules[processor_name]
            output, output_mask = processor.process_frame(
                {
                    "source_vision_frames": source_frames,
                    "source_audio_frame": source_audio,
                    "source_voice_frame": source_voice,
                    "target_vision_frames": [target_frame],
                    "temp_vision_frame": output,
                    "temp_vision_mask": output_mask,
                }
            )
        return output

    async def process(
        self,
        source_frames: list[numpy.ndarray[Any, Any]],
        target_frame: numpy.ndarray[Any, Any],
        *,
        profile: QualityProfile,
        analyse_content: bool,
    ) -> tuple[numpy.ndarray[Any, Any], float]:
        if not self.ready:
            raise GatewayError("FaceFusion runtime is not ready")
        started = time.perf_counter()
        async with self._inference_lock:
            if analyse_content:
                from facefusion.content_analyser import analyse_frame

                if await asyncio.to_thread(analyse_frame, target_frame):
                    raise UnsafeContentError("Frame rejected by the content analyser")
            output = await asyncio.to_thread(self.process_sync, source_frames, target_frame, profile)
        elapsed = time.perf_counter() - started
        self.total_frames += 1
        self.total_inference_seconds += elapsed
        return output, elapsed * 1000

    @property
    def average_fps(self) -> float:
        if not self.total_inference_seconds:
            return 0.0
        return self.total_frames / self.total_inference_seconds


@dataclass(slots=True)
class StreamSession:
    session_id: str
    source_frames: list[numpy.ndarray[Any, Any]]
    source_analysis: dict[str, Any]
    ticket_hash: str
    max_width: int
    profile_id: str
    created_at: str = field(default_factory=_now_iso)
    last_used_monotonic: float = field(default_factory=time.monotonic)
    frame_count: int = 0
    total_inference_ms: float = 0.0
    recent_inference_ms: deque[float] = field(default_factory=lambda: deque(maxlen=30))
    connected: bool = False

    @property
    def measured_fps(self) -> float:
        if not self.recent_inference_ms:
            return 0.0
        return 1000.0 / (sum(self.recent_inference_ms) / len(self.recent_inference_ms))

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "connected": self.connected,
            "frame_count": self.frame_count,
            "average_inference_ms": round(self.total_inference_ms / self.frame_count, 2) if self.frame_count else 0,
            "measured_fps": round(self.measured_fps, 2),
            "max_width": self.max_width,
            "profile": self.profile_id,
            "source_analysis": self.source_analysis,
        }


runtime = FaceFusionRuntime()
sessions: dict[str, StreamSession] = {}
sessions_lock = asyncio.Lock()
cleanup_task: asyncio.Task[None] | None = None


async def _cleanup_sessions() -> None:
    while True:
        await asyncio.sleep(30)
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        async with sessions_lock:
            expired = [key for key, value in sessions.items() if not value.connected and value.last_used_monotonic < cutoff]
            for key in expired:
                sessions.pop(key, None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global cleanup_task
    await runtime.initialize()
    cleanup_task = asyncio.create_task(_cleanup_sessions(), name="deepfake-session-cleanup")
    yield
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Sere1nFish Deepfake Gateway", version="1.1.0", lifespan=lifespan)


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(value, API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid gateway credentials")


def _gpu_status() -> dict[str, Any]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2,
        ).strip()
        name, total, used, utilization = [part.strip() for part in output.splitlines()[0].split(",")]
        return {
            "name": name,
            "memory_total_mb": int(total),
            "memory_used_mb": int(used),
            "utilization_percent": int(utilization),
        }
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return {"name": "unknown"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": runtime.ready, "service": "deepfake-gateway", "version": app.version}


@app.get("/v1/status", dependencies=[Depends(require_api_token)])
async def status() -> dict[str, Any]:
    return {
        "ok": runtime.ready,
        "model": runtime.model,
        "pixel_boost": runtime.pixel_boost,
        "profiles": [profile.as_dict() for profile in QUALITY_PROFILES.all()],
        "default_image_profile": DEFAULT_IMAGE_PROFILE,
        "default_realtime_profile": DEFAULT_REALTIME_PROFILE,
        "max_source_images": MAX_SOURCE_IMAGES,
        "warmup_ms": round(runtime.warmup_ms, 2),
        "runtime_average_fps": round(runtime.average_fps, 2),
        "active_sessions": sum(1 for session in sessions.values() if session.connected),
        "session_count": len(sessions),
        "max_sessions": MAX_SESSIONS,
        "gpu": _gpu_status(),
        "model_use": "authorized_non_commercial",
    }


async def _decode_source_uploads(source: list[UploadFile]) -> list[numpy.ndarray[Any, Any]]:
    if not 1 <= len(source) <= MAX_SOURCE_IMAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Provide between 1 and {MAX_SOURCE_IMAGES} source images",
        )
    return [
        _fit_frame(
            _decode_image(await upload.read(MAX_IMAGE_BYTES + 1), label=f"source {index}"),
            MAX_SOURCE_WIDTH,
        )
        for index, upload in enumerate(source, start=1)
    ]


@app.post("/v1/swap/image", dependencies=[Depends(require_api_token)])
async def swap_image(
    source: list[UploadFile] = File(...),
    target: UploadFile = File(...),
    authorized_use: bool = Form(...),
    max_width: int = Form(default=1280, ge=320, le=1920),
    profile: str = Form(default=DEFAULT_IMAGE_PROFILE),
) -> Response:
    if not authorized_use:
        raise HTTPException(status_code=403, detail="Explicit authorization is required")
    try:
        quality_profile = QUALITY_PROFILES.get(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source_frames = await _decode_source_uploads(source)
    target_frame = _fit_frame(
        _decode_image(await target.read(MAX_IMAGE_BYTES + 1), label="target"),
        max_width,
    )
    try:
        source_analysis = await runtime.validate_source(source_frames)
        output, inference_ms = await runtime.process(
            source_frames,
            target_frame,
            profile=quality_profile,
            analyse_content=True,
        )
    except UnsafeContentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        _encode_jpeg(output, quality=IMAGE_JPEG_QUALITY),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Inference-Ms": f"{inference_ms:.2f}",
            "X-Quality-Profile": quality_profile.profile_id,
            "X-Source-Count": str(source_analysis["count"]),
            "X-Source-Consistency": str(source_analysis["identity_consistency"]),
            "X-Synthetic-Media": "true",
        },
    )


@app.post("/v1/sessions", dependencies=[Depends(require_api_token)])
async def create_session(
    source: list[UploadFile] = File(...),
    authorized_use: bool = Form(...),
    max_width: int = Form(default=960, ge=320, le=1280),
    profile: str = Form(default=DEFAULT_REALTIME_PROFILE),
) -> dict[str, Any]:
    if not authorized_use:
        raise HTTPException(status_code=403, detail="Explicit authorization is required")
    try:
        quality_profile = QUALITY_PROFILES.get(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source_frames = await _decode_source_uploads(source)
    try:
        source_analysis = await runtime.validate_source(source_frames)
    except GatewayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with sessions_lock:
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        expired = [key for key, value in sessions.items() if not value.connected and value.last_used_monotonic < cutoff]
        for key in expired:
            sessions.pop(key, None)
        if len(sessions) >= MAX_STORED_SESSIONS:
            raise HTTPException(status_code=429, detail="The GPU pending session limit has been reached")
        session_id = uuid.uuid4().hex
        ticket = secrets.token_urlsafe(32)
        sessions[session_id] = StreamSession(
            session_id=session_id,
            source_frames=source_frames,
            source_analysis=source_analysis,
            ticket_hash=hashlib.sha256(ticket.encode()).hexdigest(),
            max_width=max_width,
            profile_id=quality_profile.profile_id,
        )
    return {
        "session_id": session_id,
        "ticket": ticket,
        "websocket_path": f"/v1/realtime/{session_id}",
        "expires_in": SESSION_TTL_SECONDS,
        "model": runtime.model,
        "max_width": max_width,
        "profile": quality_profile.profile_id,
        "source_analysis": source_analysis,
    }


@app.get("/v1/sessions/{session_id}", dependencies=[Depends(require_api_token)])
async def get_session(session_id: str) -> dict[str, Any]:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.as_dict()


@app.delete("/v1/sessions/{session_id}", dependencies=[Depends(require_api_token)])
async def delete_session(session_id: str) -> dict[str, bool]:
    async with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return {"deleted": False}
        if session.connected:
            raise HTTPException(status_code=409, detail="Disconnect the realtime stream before deleting the session")
        sessions.pop(session_id, None)
    return {"deleted": True}


def _websocket_ticket(websocket: WebSocket) -> str:
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    prefix = "sere1nfish.ticket."
    for protocol in protocols.split(","):
        value = protocol.strip()
        if value.startswith(prefix):
            return value[len(prefix) :]
    return ""


def _websocket_authorized(websocket: WebSocket, session: StreamSession) -> bool:
    scheme, _, value = websocket.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and secrets.compare_digest(value, API_TOKEN):
        return True
    ticket = _websocket_ticket(websocket)
    return bool(ticket) and secrets.compare_digest(
        hashlib.sha256(ticket.encode()).hexdigest(),
        session.ticket_hash,
    )


@app.websocket("/v1/realtime/{session_id}")
async def realtime_stream(websocket: WebSocket, session_id: str) -> None:
    session = sessions.get(session_id)
    if not session or not _websocket_authorized(websocket, session):
        await websocket.close(code=4401)
        return
    async with sessions_lock:
        current = sessions.get(session_id)
        active_count = sum(1 for value in sessions.values() if value.connected)
        if current is not session or session.connected or active_count >= MAX_SESSIONS:
            rejected = True
        else:
            session.connected = True
            rejected = False
    if rejected:
        await websocket.close(code=4429)
        return
    try:
        await websocket.accept(subprotocol="sere1nfish")
        await websocket.send_text(json.dumps({"type": "ready", **session.as_dict()}))
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            frame_bytes = message.get("bytes")
            if frame_bytes is None:
                if message.get("text") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", **session.as_dict()}))
                continue
            if len(frame_bytes) > MAX_FRAME_BYTES:
                await websocket.send_text(json.dumps({"type": "error", "message": "Frame exceeds the size limit"}))
                continue
            try:
                target = _fit_frame(_decode_image(frame_bytes, label="frame"), session.max_width)
                analyse_content = session.frame_count % 15 == 0
                output, inference_ms = await runtime.process(
                    session.source_frames,
                    target,
                    profile=QUALITY_PROFILES.get(session.profile_id),
                    analyse_content=analyse_content,
                )
                session.frame_count += 1
                session.total_inference_ms += inference_ms
                session.recent_inference_ms.append(inference_ms)
                session.last_used_monotonic = time.monotonic()
                await websocket.send_bytes(_encode_jpeg(output, quality=REALTIME_JPEG_QUALITY))
            except UnsafeContentError:
                await websocket.send_text(json.dumps({"type": "blocked", "message": "Frame rejected by content analyser"}))
            except (GatewayError, HTTPException) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                await websocket.send_text(json.dumps({"type": "error", "message": str(detail)}))
    finally:
        async with sessions_lock:
            session.connected = False
            session.last_used_monotonic = time.monotonic()
