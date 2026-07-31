"""Authenticated FaceFusion image and low-latency streaming gateway.

This process runs on the GPU node. It intentionally exposes a small API instead
of the FaceFusion Gradio application so callers do not depend on UI internals.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

import cv2
import numpy
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import Response

from .media_diagnostics import WhipPublishDiagnostics
from .media_pipeline import MediaPipeline
from .profiles import QUALITY_PROFILES, QualityProfile
from .voice_conversion import MeanVCVoiceConverter, VoiceConversionSession

MAX_IMAGE_BYTES = int(os.getenv("DEEPFAKE_MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))
MAX_FRAME_BYTES = int(os.getenv("DEEPFAKE_MAX_FRAME_BYTES", str(4 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("DEEPFAKE_MAX_IMAGE_PIXELS", str(3840 * 2160)))
MAX_SOURCE_IMAGES = min(8, max(1, int(os.getenv("DEEPFAKE_MAX_SOURCE_IMAGES", "4"))))
MAX_SOURCE_WIDTH = min(2560, max(512, int(os.getenv("DEEPFAKE_MAX_SOURCE_WIDTH", "1600"))))
MAX_VOICE_REFERENCE_BYTES = min(
    30 * 1024 * 1024,
    max(
        1024 * 1024,
        int(os.getenv("DEEPFAKE_MAX_VOICE_REFERENCE_BYTES", str(20 * 1024 * 1024))),
    ),
)
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
MEDIA_ENABLED = os.getenv("DEEPFAKE_MEDIA_ENABLED", "0").lower() in {"1", "true", "yes"}
MEDIA_PUBLIC_BASE_URL = os.getenv("DEEPFAKE_MEDIA_PUBLIC_BASE_URL", "").strip().rstrip("/")
MEDIA_RTSP_BASE_URL = os.getenv("DEEPFAKE_MEDIA_RTSP_BASE_URL", "rtsp://127.0.0.1:8554").strip().rstrip("/")
MEDIA_OUTPUT_FPS = min(30, max(5, int(os.getenv("DEEPFAKE_MEDIA_OUTPUT_FPS", "15"))))
MEDIA_SHUTDOWN_TIMEOUT_SECONDS = max(
    2.0,
    min(15.0, float(os.getenv("DEEPFAKE_MEDIA_SHUTDOWN_TIMEOUT_SECONDS", "6"))),
)
logger = logging.getLogger("uvicorn.error")

if MEDIA_ENABLED:
    media_public_url = urlsplit(MEDIA_PUBLIC_BASE_URL)
    if media_public_url.scheme != "https" or not media_public_url.hostname:
        raise RuntimeError("DEEPFAKE_MEDIA_PUBLIC_BASE_URL must be a valid HTTPS URL")
    if urlsplit(MEDIA_RTSP_BASE_URL).scheme != "rtsp":
        raise RuntimeError("DEEPFAKE_MEDIA_RTSP_BASE_URL must be a valid RTSP URL")

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
        self.pixel_boost = QUALITY_PROFILES.get(DEFAULT_IMAGE_PROFILE).face_swapper_pixel_boost
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
        try:
            ok, content_target = capture.read()
            if source is None or not ok or content_target is None:
                return

            from facefusion.content_analyser import analyse_frame

            analyse_frame(content_target)
            for profile in QUALITY_PROFILES.all():
                ok, target = capture.read()
                if not ok or target is None:
                    break
                _, source_face_sets = self.validate_source_sync([source], profile=profile)
                self.process_sync([source], source_face_sets, target, profile)
        finally:
            capture.release()

    def validate_source_sync(
        self,
        source_frames: list[numpy.ndarray[Any, Any]],
        *,
        profile: QualityProfile | None = None,
    ) -> tuple[dict[str, Any], list[list[Any]]]:
        from facefusion import face_store
        from facefusion.face_creator import get_static_faces
        from facefusion.face_selector import sort_faces_by_order

        if profile:
            self._apply_profile(profile)
        face_store.clear_faces()
        try:
            if not 1 <= len(source_frames) <= MAX_SOURCE_IMAGES:
                raise GatewayError(f"Provide between 1 and {MAX_SOURCE_IMAGES} source images")

            primary_faces = []
            source_face_sets = []
            source_details = []
            for index, source_frame in enumerate(source_frames, start=1):
                faces = sort_faces_by_order(get_static_faces([source_frame]), "large-small")
                if not faces:
                    raise GatewayError(f"No face was detected in source image {index}")
                source_face_sets.append(faces)
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

            return (
                {
                    "count": len(source_frames),
                    "identity_consistency": round(consistency, 4),
                    "sources": source_details,
                },
                source_face_sets,
            )
        finally:
            face_store.clear_faces()

    async def validate_source(
        self,
        source_frames: list[numpy.ndarray[Any, Any]],
        *,
        profile: QualityProfile | None = None,
    ) -> tuple[dict[str, Any], list[list[Any]]]:
        async with self._inference_lock:
            return await asyncio.to_thread(
                self.validate_source_sync,
                source_frames,
                profile=profile,
            )

    def _apply_profile(self, profile: QualityProfile) -> None:
        from facefusion import state_manager

        state_manager.set_item("processors", list(profile.processors))
        state_manager.set_item("face_mask_types", list(profile.face_mask_types))
        state_manager.set_item("face_swapper_weight", profile.face_swapper_weight)
        state_manager.set_item("face_swapper_pixel_boost", profile.face_swapper_pixel_boost)
        state_manager.set_item("face_detector_model", profile.face_detector_model)
        state_manager.set_item("face_detector_size", profile.face_detector_size)
        state_manager.set_item("face_landmarker_model", profile.face_landmarker_model)
        if profile.face_enhancer_model:
            state_manager.set_item("face_enhancer_model", profile.face_enhancer_model)
            state_manager.set_item("face_enhancer_blend", profile.face_enhancer_blend)
            state_manager.set_item("face_enhancer_weight", profile.face_enhancer_weight)

    def process_sync(
        self,
        source_frames: list[numpy.ndarray[Any, Any]],
        source_face_sets: list[list[Any]],
        target_frame: numpy.ndarray[Any, Any],
        profile: QualityProfile,
    ) -> numpy.ndarray[Any, Any]:
        from facefusion import face_store
        from facefusion.audio import create_empty_audio_frame
        from facefusion.vision import extract_vision_mask

        self._apply_profile(profile)
        face_store.clear_faces()
        for source_frame, source_faces in zip(source_frames, source_face_sets, strict=True):
            face_store.set_faces(source_frame, source_faces)
        source_audio = create_empty_audio_frame()
        source_voice = create_empty_audio_frame()
        output = target_frame.copy()
        output_mask = extract_vision_mask(output)
        try:
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
        finally:
            face_store.clear_faces()

    async def process(
        self,
        source_frames: list[numpy.ndarray[Any, Any]],
        source_face_sets: list[list[Any]],
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
            output = await asyncio.to_thread(
                self.process_sync,
                source_frames,
                source_face_sets,
                target_frame,
                profile,
            )
        elapsed = time.perf_counter() - started
        self.total_frames += 1
        self.total_inference_seconds += elapsed
        return output, elapsed * 1000

    @property
    def average_fps(self) -> float:
        if not self.total_inference_seconds:
            return 0.0
        return self.total_frames / self.total_inference_seconds

    @property
    def face_cache_entries(self) -> int:
        from facefusion import face_store

        return len(face_store.FACE_STORE)


@dataclass(slots=True)
class StreamSession:
    session_id: str
    source_frames: list[numpy.ndarray[Any, Any]]
    source_face_sets: list[list[Any]]
    source_analysis: dict[str, Any]
    ticket_hash: str
    max_width: int
    profile_id: str
    transport: str = "frame_ws"
    media_publish_hash: str = ""
    media_publish_diagnostics: WhipPublishDiagnostics = field(default_factory=WhipPublishDiagnostics)
    media_read_hash: str = ""
    media_internal_token: str = field(default="", repr=False)
    voice_conversion: VoiceConversionSession | None = field(default=None, repr=False)
    voice_connected: bool = False
    voice_input_bytes: int = 0
    voice_output_bytes: int = 0
    voice_chunks: int = 0
    voice_last_error: str = ""
    media_pipeline: MediaPipeline | None = field(default=None, repr=False)
    media_task: asyncio.Task[None] | None = field(default=None, repr=False)
    created_at: str = field(default_factory=_now_iso)
    last_used_monotonic: float = field(default_factory=time.monotonic)
    frame_count: int = 0
    total_inference_ms: float = 0.0
    recent_inference_ms: deque[float] = field(default_factory=lambda: deque(maxlen=30))
    connected: bool = False

    @property
    def active(self) -> bool:
        return self.connected or self.voice_connected or bool(
            self.media_pipeline
            and self.media_pipeline.stats.state in {"starting", "live", "reconnecting"}
        )

    @property
    def reserves_gpu_slot(self) -> bool:
        return self.connected or self.voice_connected or self.transport == "obs_whip"

    @property
    def measured_fps(self) -> float:
        if not self.recent_inference_ms:
            return 0.0
        return 1000.0 / (sum(self.recent_inference_ms) / len(self.recent_inference_ms))

    def as_dict(self) -> dict[str, Any]:
        media = self.media_pipeline.stats.as_dict() if self.media_pipeline else None
        if self.transport == "obs_whip":
            media = media or {"state": "starting"}
            media["publish"] = self.media_publish_diagnostics.as_dict()
        payload = {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "connected": self.connected,
            "frame_count": self.frame_count,
            "average_inference_ms": round(self.total_inference_ms / self.frame_count, 2) if self.frame_count else 0,
            "measured_fps": round(self.measured_fps, 2),
            "max_width": self.max_width,
            "profile": self.profile_id,
            "transport": self.transport,
            "media": media,
            "source_analysis": self.source_analysis,
        }
        if self.voice_conversion:
            payload["voice_conversion"] = {
                "enabled": True,
                "provider": self.voice_conversion.provider,
                "connected": self.voice_connected,
                "sample_rate": self.voice_conversion.sample_rate,
                "chunk_ms": self.voice_conversion.chunk_ms,
                "input_bytes": self.voice_input_bytes,
                "output_bytes": self.voice_output_bytes,
                "chunks": self.voice_chunks,
                "last_error": self.voice_last_error,
            }
        return payload


runtime = FaceFusionRuntime()
voice_converter = MeanVCVoiceConverter.from_environment()
sessions: dict[str, StreamSession] = {}
sessions_lock = asyncio.Lock()
cleanup_task: asyncio.Task[None] | None = None


def _rtsp_session_url(path: str, token: str) -> str:
    parsed = urlsplit(MEDIA_RTSP_BASE_URL)
    host = parsed.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"worker:{quote(token, safe='')}@{host}{port}"
    base_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, f"{base_path}/{path}", "", ""))


async def _run_media_session(session: StreamSession) -> None:
    async def process_frame(target: numpy.ndarray[Any, Any]) -> tuple[numpy.ndarray[Any, Any], float]:
        try:
            output, inference_ms = await runtime.process(
                session.source_frames,
                session.source_face_sets,
                target,
                profile=QUALITY_PROFILES.get(session.profile_id),
                analyse_content=session.frame_count % 15 == 0,
            )
        except UnsafeContentError:
            return target, 0.0
        session.frame_count += 1
        session.total_inference_ms += inference_ms
        session.recent_inference_ms.append(inference_ms)
        session.last_used_monotonic = time.monotonic()
        return output, inference_ms

    conversion = session.voice_conversion
    pipeline = MediaPipeline(
        input_url=_rtsp_session_url(f"media/input/{session.session_id}", session.media_internal_token),
        output_url=_rtsp_session_url(f"media/output/{session.session_id}", session.media_internal_token),
        max_width=session.max_width,
        output_fps=MEDIA_OUTPUT_FPS,
        processor=process_frame,
        voice_bridge_url=conversion.websocket_url if conversion else "",
        voice_bridge_token=conversion.token if conversion else "",
        voice_bridge_ca_file="",
        voice_bridge_provider=conversion.provider if conversion else "",
        voice_bridge_sample_rate=conversion.sample_rate if conversion else 16000,
    )
    session.media_pipeline = pipeline
    try:
        await pipeline.run()
    except asyncio.CancelledError:
        await pipeline.stop()
        raise


async def _stop_media_session(session: StreamSession) -> None:
    pipeline = session.media_pipeline
    session.media_pipeline = None
    task = session.media_task
    session.media_task = None
    conversion = session.voice_conversion
    session.voice_conversion = None
    try:
        if task:
            if not task.done():
                task.cancel()
                done, _ = await asyncio.wait(
                    {task},
                    timeout=MEDIA_SHUTDOWN_TIMEOUT_SECONDS,
                )
                if task not in done:
                    logger.warning(
                        "Media task cancellation timed out session=%s timeout_seconds=%.1f",
                        session.session_id,
                        MEDIA_SHUTDOWN_TIMEOUT_SECONDS,
                    )
            if task.done():
                await asyncio.gather(task, return_exceptions=True)
        if pipeline:
            stop_task = asyncio.create_task(
                pipeline.stop(),
                name=f"deepfake-media-stop-{session.session_id}",
            )
            done, _ = await asyncio.wait(
                {stop_task},
                timeout=MEDIA_SHUTDOWN_TIMEOUT_SECONDS,
            )
            if stop_task in done:
                await asyncio.gather(stop_task, return_exceptions=True)
            else:
                stop_task.cancel()
                logger.warning(
                    "Media pipeline stop timed out session=%s timeout_seconds=%.1f",
                    session.session_id,
                    MEDIA_SHUTDOWN_TIMEOUT_SECONDS,
                )
    finally:
        if conversion and voice_converter:
            await voice_converter.delete_session(conversion.session_id)


async def _cleanup_sessions() -> None:
    while True:
        await asyncio.sleep(30)
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        removed: list[StreamSession] = []
        async with sessions_lock:
            expired = [key for key, value in sessions.items() if not value.active and value.last_used_monotonic < cutoff]
            for key in expired:
                removed_session = sessions.pop(key, None)
                if removed_session:
                    removed.append(removed_session)
        for session in removed:
            await _stop_media_session(session)


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
    remaining = list(sessions.values())
    await asyncio.gather(*(_stop_media_session(session) for session in remaining))


app = FastAPI(title="Sere1nFish Deepfake Gateway", version="1.5.1", lifespan=lifespan)


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
    voice_status = (
        await voice_converter.status()
        if voice_converter
        else {"ok": False, "provider": "meanvc", "last_error": "MeanVC runtime is not configured"}
    )
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
        "face_cache_entries": runtime.face_cache_entries,
        "active_sessions": sum(1 for session in sessions.values() if session.reserves_gpu_slot),
        "session_count": len(sessions),
        "max_sessions": MAX_SESSIONS,
        "media_transport": {
            "enabled": MEDIA_ENABLED,
            "protocol": "whip_whep" if MEDIA_ENABLED else "",
            "public_base_url": MEDIA_PUBLIC_BASE_URL if MEDIA_ENABLED else "",
            "output_fps": MEDIA_OUTPUT_FPS if MEDIA_ENABLED else 0,
            "audio_supported": bool(MEDIA_ENABLED and voice_status.get("ok")),
            "audio_codec": "Opus" if MEDIA_ENABLED and voice_status.get("ok") else "",
            "voice_conversion": voice_status,
        },
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
    effective_max_width = min(max_width, quality_profile.max_width)
    target_frame = _fit_frame(
        _decode_image(await target.read(MAX_IMAGE_BYTES + 1), label="target"),
        effective_max_width,
    )
    try:
        source_analysis, source_face_sets = await runtime.validate_source(
            source_frames,
            profile=quality_profile,
        )
        output, inference_ms = await runtime.process(
            source_frames,
            source_face_sets,
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
            "X-Max-Width": str(effective_max_width),
            "X-Synthetic-Media": "true",
        },
    )


@app.post("/v1/sessions", dependencies=[Depends(require_api_token)])
async def create_session(
    source: list[UploadFile] = File(...),
    voice_reference: UploadFile | None = File(default=None),
    authorized_use: bool = Form(...),
    max_width: int = Form(default=960, ge=320, le=1280),
    profile: str = Form(default=DEFAULT_REALTIME_PROFILE),
    transport: str = Form(default="frame_ws"),
    voice_enabled: bool = Form(default=False),
    voice_provider: str = Form(default="meanvc"),
    voice_steps: int = Form(default=2, ge=1, le=2),
) -> dict[str, Any]:
    if not authorized_use:
        raise HTTPException(status_code=403, detail="Explicit authorization is required")
    try:
        quality_profile = QUALITY_PROFILES.get(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    transport = transport.strip().lower()
    if transport not in {"frame_ws", "obs_whip"}:
        raise HTTPException(status_code=422, detail="Unknown realtime transport")
    if transport == "obs_whip" and not MEDIA_ENABLED:
        raise HTTPException(status_code=503, detail="OBS direct media transport is not configured")
    voice_provider = voice_provider.strip().lower()
    if voice_enabled:
        if voice_provider != "meanvc":
            raise HTTPException(status_code=422, detail="Unknown voice conversion provider")
        if not voice_converter:
            raise HTTPException(status_code=503, detail="MeanVC runtime is not configured")
        if voice_reference is None:
            raise HTTPException(status_code=422, detail="Target voice sample is required")
    source_frames = await _decode_source_uploads(source)
    try:
        source_analysis, source_face_sets = await runtime.validate_source(
            source_frames,
            profile=quality_profile,
        )
    except GatewayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    conversion: VoiceConversionSession | None = None
    if voice_enabled and voice_reference and voice_converter:
        reference = await voice_reference.read(MAX_VOICE_REFERENCE_BYTES + 1)
        if len(reference) > MAX_VOICE_REFERENCE_BYTES:
            raise HTTPException(status_code=413, detail="Target voice sample exceeds the size limit")
        try:
            conversion = await voice_converter.create_session(
                reference=reference,
                filename=voice_reference.filename or "target-voice.wav",
                content_type=voice_reference.content_type or "application/octet-stream",
                steps=voice_steps,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    removed_sessions: list[StreamSession] = []
    session: StreamSession | None = None
    capacity_error = ""
    async with sessions_lock:
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        expired = [
            key
            for key, value in sessions.items()
            if not value.active and value.last_used_monotonic < cutoff
        ]
        for key in expired:
            removed = sessions.pop(key, None)
            if removed:
                removed_sessions.append(removed)
        reserved_count = sum(1 for value in sessions.values() if value.reserves_gpu_slot)
        if len(sessions) >= MAX_STORED_SESSIONS:
            capacity_error = "The GPU pending session limit has been reached"
        elif transport == "obs_whip" and reserved_count >= MAX_SESSIONS:
            capacity_error = "The GPU realtime session limit has been reached"
        else:
            session_id = uuid.uuid4().hex
            ticket = secrets.token_urlsafe(32)
            media_publish_token = secrets.token_urlsafe(32) if transport == "obs_whip" else ""
            media_read_token = secrets.token_urlsafe(32) if transport == "obs_whip" else ""
            media_internal_token = secrets.token_urlsafe(32) if transport == "obs_whip" else ""
            effective_max_width = min(max_width, quality_profile.max_width)
            session = StreamSession(
                session_id=session_id,
                source_frames=source_frames,
                source_face_sets=source_face_sets,
                source_analysis=source_analysis,
                ticket_hash=hashlib.sha256(ticket.encode()).hexdigest(),
                max_width=effective_max_width,
                profile_id=quality_profile.profile_id,
                transport=transport,
                media_publish_hash=hashlib.sha256(media_publish_token.encode()).hexdigest() if media_publish_token else "",
                media_read_hash=hashlib.sha256(media_read_token.encode()).hexdigest() if media_read_token else "",
                media_internal_token=media_internal_token,
                voice_conversion=conversion,
            )
            sessions[session_id] = session
            if transport == "obs_whip":
                session.media_task = asyncio.create_task(
                    _run_media_session(session),
                    name=f"deepfake-media-{session_id}",
                )
    if removed_sessions:
        await asyncio.gather(*(_stop_media_session(value) for value in removed_sessions))
    if capacity_error:
        if conversion and voice_converter:
            await voice_converter.delete_session(conversion.session_id)
        raise HTTPException(status_code=429, detail=capacity_error)
    if session is None:
        if conversion and voice_converter:
            await voice_converter.delete_session(conversion.session_id)
        raise HTTPException(status_code=500, detail="Unable to create the GPU session")
    payload: dict[str, Any] = {
        "session_id": session_id,
        "ticket": ticket,
        "websocket_path": f"/v1/realtime/{session_id}",
        "expires_in": SESSION_TTL_SECONDS,
        "model": runtime.model,
        "max_width": effective_max_width,
        "profile": quality_profile.profile_id,
        "transport": transport,
        "source_analysis": source_analysis,
    }
    if conversion:
        payload["voice_conversion"] = {
            "enabled": True,
            "provider": conversion.provider,
            "sample_rate": conversion.sample_rate,
            "chunk_ms": conversion.chunk_ms,
        }
        if transport == "frame_ws":
            payload["voice_websocket_path"] = f"/v1/realtime/{session_id}/voice"
    if transport == "obs_whip":
        input_path = f"media/input/{session_id}"
        output_path = f"media/output/{session_id}"
        payload["media"] = {
            "publish_url": f"{MEDIA_PUBLIC_BASE_URL}/{input_path}/whip",
            "publish_token": media_publish_token,
            "viewer_url": (
                f"{MEDIA_PUBLIC_BASE_URL}/{output_path}/?"
                f"token={quote(media_read_token, safe='')}&controls=false&muted=false&autoplay=true&playsInline=true"
            ),
            "whep_url": f"{MEDIA_PUBLIC_BASE_URL}/{output_path}/whep",
            "read_token": media_read_token,
            "expires_in": SESSION_TTL_SECONDS,
            "recommended": {
                "width": effective_max_width,
                "fps": MEDIA_OUTPUT_FPS,
                "video_codec": "H264",
                "keyframe_interval_seconds": 1,
                "audio_codec": "Opus" if conversion else "",
                "audio_sample_rate": 48000 if conversion else 0,
            },
            "audio": {
                "enabled": bool(conversion),
                "input": "OBS microphone",
                "output": "MeanVC converted voice",
                "provider": conversion.provider if conversion else "",
                "processing_sample_rate": conversion.sample_rate if conversion else 0,
                "chunk_ms": conversion.chunk_ms if conversion else 0,
            },
        }
    return payload


def _media_auth_token(payload: dict[str, Any]) -> str:
    token = str(payload.get("token") or "").strip()
    if token:
        return token
    query = parse_qs(str(payload.get("query") or ""), keep_blank_values=False)
    for key in ("token", "jwt"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    return str(payload.get("password") or "").strip()


def _media_session_for_path(path: str) -> tuple[StreamSession | None, str]:
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "media" or parts[1] not in {"input", "output"}:
        return None, ""
    return sessions.get(parts[2]), parts[1]


def _media_request_authorized(payload: dict[str, Any]) -> bool:
    session, direction = _media_session_for_path(str(payload.get("path") or ""))
    if not session or session.transport != "obs_whip":
        return False
    action = str(payload.get("action") or "")
    user = str(payload.get("user") or "")
    token = _media_auth_token(payload)
    authorized = False
    if user == "worker":
        allowed = (direction, action) in {("input", "read"), ("output", "publish")}
        authorized = bool(token) and allowed and secrets.compare_digest(token, session.media_internal_token)
    elif token:
        expected_hash = ""
        if direction == "input" and action == "publish":
            expected_hash = session.media_publish_hash
        elif direction == "output" and action == "read":
            expected_hash = session.media_read_hash
        authorized = bool(expected_hash) and secrets.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(),
            expected_hash,
        )
    if direction == "input" and action == "publish" and user != "worker":
        session.media_publish_diagnostics.record(payload, authorized=authorized)
        if authorized:
            session.last_used_monotonic = time.monotonic()
        logger.log(
            logging.INFO if authorized else logging.WARNING,
            "WHIP publish authorization %s session=%s ip=%s protocol=%s attempt=%s",
            "accepted" if authorized else "rejected",
            session.session_id,
            session.media_publish_diagnostics.last_ip or "unknown",
            session.media_publish_diagnostics.last_protocol or "unknown",
            session.media_publish_diagnostics.attempts,
        )
    return authorized


@app.post("/internal/mediamtx/auth", include_in_schema=False)
async def mediamtx_auth(request: Request) -> Response:
    if not MEDIA_ENABLED or not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        return Response(status_code=401)
    try:
        payload = await request.json()
    except (ValueError, json.JSONDecodeError):
        return Response(status_code=401)
    if not isinstance(payload, dict) or not _media_request_authorized(payload):
        return Response(status_code=401)
    return Response(status_code=204)


@app.get("/v1/sessions/{session_id}", dependencies=[Depends(require_api_token)])
async def get_session(session_id: str) -> dict[str, Any]:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.last_used_monotonic = time.monotonic()
    return session.as_dict()


@app.delete("/v1/sessions/{session_id}", dependencies=[Depends(require_api_token)])
async def delete_session(session_id: str) -> dict[str, bool]:
    removed: StreamSession | None = None
    async with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return {"deleted": False}
        if session.connected or session.voice_connected:
            raise HTTPException(status_code=409, detail="Disconnect the realtime stream before deleting the session")
        removed = sessions.pop(session_id, None)
    if removed:
        await _stop_media_session(removed)
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
    if not session or session.transport != "frame_ws" or not _websocket_authorized(websocket, session):
        await websocket.close(code=4401)
        return
    async with sessions_lock:
        current = sessions.get(session_id)
        other_active_count = sum(
            1
            for value in sessions.values()
            if value is not session and value.reserves_gpu_slot
        )
        if current is not session or session.connected or other_active_count >= MAX_SESSIONS:
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
                    session.source_face_sets,
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


@app.websocket("/v1/realtime/{session_id}/voice")
async def realtime_voice_stream(websocket: WebSocket, session_id: str) -> None:
    session = sessions.get(session_id)
    if (
        not session
        or session.transport != "frame_ws"
        or not session.voice_conversion
        or not voice_converter
        or not _websocket_authorized(websocket, session)
    ):
        await websocket.close(code=4401)
        return
    async with sessions_lock:
        current = sessions.get(session_id)
        other_active_count = sum(
            1
            for value in sessions.values()
            if value is not session and value.reserves_gpu_slot
        )
        if (
            current is not session
            or session.voice_connected
            or other_active_count >= MAX_SESSIONS
        ):
            rejected = True
        else:
            session.voice_connected = True
            session.voice_last_error = ""
            rejected = False
    if rejected:
        await websocket.close(code=4429)
        return

    conversion = session.voice_conversion
    await websocket.accept(subprotocol="sere1nfish")

    async def browser_to_converter(remote: Any) -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            payload = message.get("bytes")
            if payload is None:
                if message.get("text") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                continue
            if len(payload) > 128 * 1024 or len(payload) % 2:
                raise GatewayError("PCM audio chunk is invalid")
            session.voice_input_bytes += len(payload)
            session.last_used_monotonic = time.monotonic()
            await remote.send(payload)

    async def converter_to_browser(remote: Any) -> None:
        while True:
            payload = await remote.recv()
            if isinstance(payload, bytes):
                session.voice_output_bytes += len(payload)
                session.voice_chunks += 1
                session.last_used_monotonic = time.monotonic()
                await websocket.send_bytes(payload)
            else:
                await websocket.send_text(payload)

    try:
        async with voice_converter.open_stream(conversion) as remote:
            tasks = {
                asyncio.create_task(
                    browser_to_converter(remote),
                    name=f"deepfake-browser-voice-upload-{session_id}",
                ),
                asyncio.create_task(
                    converter_to_browser(remote),
                    name=f"deepfake-browser-voice-download-{session_id}",
                ),
            }
            try:
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        session.voice_last_error = str(exc)[:300]
        logger.warning("Browser MeanVC stream failed session=%s error=%s", session_id, exc)
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "message": session.voice_last_error})
            )
        except Exception:
            pass
    finally:
        async with sessions_lock:
            session.voice_connected = False
            session.last_used_monotonic = time.monotonic()
