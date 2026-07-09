"""Socket.IO server for Scrcpy video streaming."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs
from typing import Any

from typing import NotRequired
from typing_extensions import TypedDict

import socketio

from AutoGLM_GUI.adb_plus import touch_down_async, touch_move_async, touch_up_async
from AutoGLM_GUI.logger import logger
from AutoGLM_GUI.scrcpy_protocol import ScrcpyMediaStreamPacket
from AutoGLM_GUI.scrcpy_stream import ScrcpyStreamer, is_port_available


class VideoPacketPayload(TypedDict):
    type: str
    data: bytes
    timestamp: int
    keyframe: NotRequired[bool | None]
    pts: NotRequired[int | None]


sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    server_kwargs={"socketio_path": "/socket.io"},
)

_socket_streamers: dict[str, ScrcpyStreamer] = {}
_stream_tasks: dict[str, asyncio.Task[None]] = {}
_socket_devices: dict[str, str] = {}
_socket_users: dict[str, str] = {}
_socket_admins: dict[str, bool] = {}
_device_locks: dict[
    str, asyncio.Lock
] = {}  # Lock per device to prevent concurrent connections
_port_lock = asyncio.Lock()
_next_stream_port = 27183


def _resolve_adb_device_id(device_id: str) -> str:
    """Resolve stable project device IDs to the active ADB endpoint."""
    try:
        from core.mobile.manager import MobileDeviceManager

        resolved = MobileDeviceManager().resolve_adb_device_id(device_id)
        if resolved != device_id:
            logger.info("Resolved device %s to ADB endpoint %s", device_id, resolved)
        return resolved
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to resolve ADB endpoint for %s: %s", device_id, exc)
        return device_id


def _bearer_value(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith("bearer "):
        return value.split(None, 1)[1].strip()
    return value


def _extract_token(environ: dict[str, Any], auth: Any | None) -> str | None:
    if isinstance(auth, dict):
        for key in ("token", "access_token", "authorization", "Authorization"):
            token = _bearer_value(str(auth.get(key) or ""))
            if token:
                return token

    token = _bearer_value(str(environ.get("HTTP_AUTHORIZATION") or ""))
    if token:
        return token

    query = parse_qs(str(environ.get("QUERY_STRING") or ""))
    for key in ("token", "access_token"):
        values = query.get(key)
        if values:
            token = _bearer_value(values[0])
            if token:
                return token
    return None


async def _authenticate_socket(
    environ: dict[str, Any], auth: Any | None
) -> tuple[str, bool] | None:
    token = _extract_token(environ, auth)
    if not token:
        return None
    try:
        from api.auth_store import TOKEN_STORE
        from api.db.mongodb import get_db
        from api.dao import users as users_dao

        username = TOKEN_STORE.get_username(token)
        if not username:
            return None
        user_doc = await users_dao.get_user(get_db(), username)
        if not user_doc or user_doc.get("disabled"):
            return None
        return username, user_doc.get("role") == "admin"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Socket.IO authentication failed: %s", exc)
        return None


async def _ensure_socket_device_allowed(sid: str, device_id: str) -> None:
    username = _socket_users.get(sid)
    if not username:
        raise PermissionError("unauthorized")
    if _socket_admins.get(sid):
        return
    from core.mobile.identity import resolve_device_key
    from core.mobile.pool import DevicePool

    device_key = await asyncio.to_thread(resolve_device_key, device_id)
    await asyncio.to_thread(
        DevicePool.get_instance().ensure_owner, device_key, username
    )


async def _stop_stream_for_sid(sid: str) -> None:
    task = _stream_tasks.pop(sid, None)
    if task:
        task.cancel()

    streamer = _socket_streamers.pop(sid, None)
    if streamer:
        streamer.stop()
    _socket_devices.pop(sid, None)


async def _allocate_stream_port(start: int = 27183, attempts: int = 128) -> int:
    """Pick an available local TCP port for one scrcpy stream."""
    global _next_stream_port
    async with _port_lock:
        base = max(start, _next_stream_port)
        for offset in range(attempts):
            port = base + offset
            if await is_port_available(port):
                _next_stream_port = port + 1
                return port

    raise RuntimeError("No available local ports for scrcpy stream")


def _classify_error(exc: Exception) -> dict[str, Any]:
    """Classify error and return user-friendly message."""
    error_str = str(exc)

    if "Address already in use" in error_str or (
        "Port" in error_str and "occupied" in error_str
    ):
        return {
            "message": "端口冲突，视频流端口仍被占用。通常会自动解决，如果持续出现请重启应用。",
            "type": "port_conflict",
            "technical_details": error_str,
        }
    elif "Device" in error_str and (
        "not available" in error_str or "not found" in error_str
    ):
        return {
            "message": "设备无响应，请检查 USB/WiFi 连接。",
            "type": "device_offline",
            "technical_details": error_str,
        }
    elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
        return {
            "message": "连接超时，请检查设备连接后重试。",
            "type": "timeout",
            "technical_details": error_str,
        }
    elif "Failed to connect" in error_str:
        return {
            "message": "无法连接到 scrcpy 服务器，请检查设备连接。",
            "type": "connection_failed",
            "technical_details": error_str,
        }
    else:
        return {
            "message": error_str,
            "type": "unknown",
            "technical_details": error_str,
        }


def stop_streamers(device_id: str | None = None) -> None:
    """Stop active scrcpy streamers (all or by device)."""
    resolved_device_id = _resolve_adb_device_id(device_id) if device_id else None
    sids = list(_socket_streamers.keys())
    for sid in sids:
        streamer = _socket_streamers.get(sid)
        if not streamer:
            continue
        logical_device_id = _socket_devices.get(sid)
        if (
            device_id
            and logical_device_id != device_id
            and streamer.device_id not in {device_id, resolved_device_id}
        ):
            continue
        task = _stream_tasks.pop(sid, None)
        if task:
            task.cancel()
        streamer.stop()
        _socket_streamers.pop(sid, None)
        _socket_devices.pop(sid, None)


async def _stream_packets(sid: str, streamer: ScrcpyStreamer) -> None:
    try:
        async for packet in streamer.iter_packets():
            payload = _packet_to_payload(packet)
            await sio.emit("video-data", payload, to=sid)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Video streaming failed: %s", exc)
        try:
            await sio.emit("error", {"message": str(exc)}, to=sid)
        except Exception as emit_exc:
            logger.debug(
                "Failed to emit Socket.IO stream error to %s: %s", sid, emit_exc
            )
    finally:
        await _stop_stream_for_sid(sid)


def _packet_to_payload(packet: ScrcpyMediaStreamPacket) -> VideoPacketPayload:
    payload: VideoPacketPayload = {
        "type": packet.type,
        "data": packet.data,
        "timestamp": int(time.time() * 1000),
    }
    if packet.type == "data":
        payload["keyframe"] = packet.keyframe
        payload["pts"] = packet.pts
    return payload


@sio.event
async def connect(sid: str, environ: dict[str, Any], auth: Any | None = None) -> None:
    user = await _authenticate_socket(environ, auth)
    if not user:
        logger.warning("Socket.IO client rejected: %s", sid)
        raise ConnectionRefusedError("unauthorized")
    username, is_admin = user
    _socket_users[sid] = username
    _socket_admins[sid] = is_admin
    logger.info("Socket.IO client connected: %s user=%s", sid, username)


@sio.event
async def disconnect(sid: str) -> None:
    logger.info("Socket.IO client disconnected: %s", sid)
    await _stop_stream_for_sid(sid)
    _socket_users.pop(sid, None)
    _socket_admins.pop(sid, None)


@sio.on("connect-device")  # type: ignore[misc]
async def connect_device(sid: str, data: dict[str, Any] | None) -> None:
    payload = data or {}
    device_id = payload.get("device_id") or payload.get("deviceId")
    if not device_id:
        await sio.emit(
            "error",
            {"message": "Device ID is required", "type": "invalid_request"},
            to=sid,
        )
        return
    requested_device_id = str(device_id)
    try:
        await _ensure_socket_device_allowed(sid, requested_device_id)
    except Exception as exc:  # noqa: BLE001
        await sio.emit(
            "error",
            {"message": f"无权访问该设备: {exc}", "type": "forbidden"},
            to=sid,
        )
        return

    max_size = int(payload.get("maxSize") or 1920)
    bit_rate = int(payload.get("bitRate") or 8_000_000)
    max_fps = max(1, min(int(payload.get("maxFps") or 60), 120))
    downsize_on_error = bool(payload.get("downsizeOnError", False))

    # Stop any existing stream for this sid
    await _stop_stream_for_sid(sid)

    # Get or create a lock for this device
    if requested_device_id not in _device_locks:
        _device_locks[requested_device_id] = asyncio.Lock()

    device_lock = _device_locks[requested_device_id]

    # Acquire lock to prevent concurrent connections to the same device
    async with device_lock:
        logger.debug(f"Acquired device lock for {requested_device_id}, sid: {sid}")
        adb_device_id = await asyncio.to_thread(
            _resolve_adb_device_id, requested_device_id
        )

        # Stop any existing streams for the same device (from other sids)
        sids_to_stop = [
            s
            for s, streamer in _socket_streamers.items()
            if s != sid
            and (
                _socket_devices.get(s) == requested_device_id
                or streamer.device_id in {requested_device_id, adb_device_id}
            )
        ]
        for s in sids_to_stop:
            logger.info(
                "Stopping existing stream for device %s from sid %s",
                requested_device_id,
                s,
            )
            await _stop_stream_for_sid(s)

        port = int(payload.get("port") or await _allocate_stream_port())
        streamer = ScrcpyStreamer(
            device_id=adb_device_id,
            max_size=max_size,
            bit_rate=bit_rate,
            max_fps=max_fps,
            downsize_on_error=downsize_on_error,
            port=port,
        )

        try:
            await streamer.start()  # ScrcpyStreamer has built-in retry logic
            metadata = await streamer.read_video_metadata()
            await sio.emit(
                "video-metadata",
                {
                    "deviceName": metadata.device_name,
                    "width": metadata.width,
                    "height": metadata.height,
                    "codec": metadata.codec,
                },
                to=sid,
            )

            _socket_streamers[sid] = streamer
            _socket_devices[sid] = requested_device_id
            _stream_tasks[sid] = asyncio.create_task(_stream_packets(sid, streamer))

        except Exception as exc:
            streamer.stop()
            logger.exception("Failed to start scrcpy stream: %s", exc)
            # Use unified error classification
            error_info = _classify_error(exc)
            await sio.emit("error", error_info, to=sid)


@sio.on("control-touch")  # type: ignore[misc]
async def control_touch(sid: str, data: dict[str, Any] | None) -> dict[str, Any]:
    """Handle low-latency manual touch events over the active Socket.IO channel."""
    payload = data or {}
    device_id = payload.get("device_id") or payload.get("deviceId") or _socket_devices.get(sid)
    action = str(payload.get("action") or "").lower()

    if not device_id:
        return {"success": False, "error": "device_id is required"}
    requested_device_id = str(device_id)
    try:
        await _ensure_socket_device_allowed(sid, requested_device_id)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"forbidden: {exc}"}

    try:
        x = int(payload.get("x"))
        y = int(payload.get("y"))
    except (TypeError, ValueError):
        return {"success": False, "error": "x and y are required"}

    action_map = {
        "down": touch_down_async,
        "move": touch_move_async,
        "up": touch_up_async,
        "cancel": touch_up_async,
    }
    handler = action_map.get(action)
    if handler is None:
        return {"success": False, "error": f"unsupported action: {action}"}

    try:
        adb_device_id = await asyncio.to_thread(
            _resolve_adb_device_id, requested_device_id
        )
        await handler(x=x, y=y, device_id=adb_device_id, delay=0.0)
        return {"success": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Socket.IO touch %s failed for %s: %s", action, device_id, exc
        )
        return {"success": False, "error": str(exc)}
