"""Device control routes (tap/swipe/touch)."""

import asyncio

from fastapi import APIRouter

from AutoGLM_GUI.control_coordinates import (
    resolve_control_point,
    resolve_control_segment,
)
from AutoGLM_GUI.device_protocol import DeviceProtocol
from AutoGLM_GUI.devices.adb_device import ADBDevice
from AutoGLM_GUI.schemas import (
    SwipeRequest,
    SwipeResponse,
    TapRequest,
    TapResponse,
    TouchDownRequest,
    TouchDownResponse,
    TouchMoveRequest,
    TouchMoveResponse,
    TouchUpRequest,
    TouchUpResponse,
)

router = APIRouter()


def _get_control_device(device_id: str) -> DeviceProtocol:
    """Resolve device through DeviceManager when it is known, with legacy fallback."""
    from AutoGLM_GUI.device_manager import DeviceManager

    device_manager = DeviceManager.get_instance()
    if device_manager.get_device_by_device_id(device_id):
        return device_manager.get_device_protocol(device_id)
    return ADBDevice(device_id)


def _local_adb_serial(device_id: str) -> str:
    """Map UI device_id to the ADB serial used for shell input."""
    from AutoGLM_GUI.device_manager import DeviceConnectionType, DeviceManager

    device_manager = DeviceManager.get_instance()
    managed = device_manager.get_device_by_device_id(device_id)
    if not managed:
        return device_id
    if managed.connection_type == DeviceConnectionType.REMOTE:
        raise ValueError("Touch control is not supported on remote devices")
    return managed.primary_device_id


@router.post("/api/control/tap", response_model=TapResponse)
async def control_tap(request: TapRequest) -> TapResponse:
    """Execute tap at normalized coordinates (0–1000 agent or 0–10000 API scale)."""
    try:
        if not request.device_id:
            return TapResponse(success=False, error="device_id is required")

        px, py = await asyncio.to_thread(
            resolve_control_point,
            request.x,
            request.y,
            device_id=request.device_id,
        )
        device = _get_control_device(request.device_id)
        await asyncio.to_thread(
            device.tap,
            x=px,
            y=py,
            delay=request.delay,
        )

        return TapResponse(success=True)
    except Exception as e:
        return TapResponse(success=False, error=str(e))


@router.post("/api/control/swipe", response_model=SwipeResponse)
async def control_swipe(request: SwipeRequest) -> SwipeResponse:
    """Execute swipe using normalized coordinates."""
    try:
        if not request.device_id:
            return SwipeResponse(success=False, error="device_id is required")

        start_x, start_y, end_x, end_y = await asyncio.to_thread(
            resolve_control_segment,
            request.start_x,
            request.start_y,
            request.end_x,
            request.end_y,
            device_id=request.device_id,
        )
        device = _get_control_device(request.device_id)
        await asyncio.to_thread(
            device.swipe,
            start_x=start_x,
            start_y=start_y,
            end_x=end_x,
            end_y=end_y,
            duration_ms=request.duration_ms,
            delay=request.delay,
        )

        return SwipeResponse(success=True)
    except Exception as e:
        return SwipeResponse(success=False, error=str(e))


@router.post("/api/control/touch/down", response_model=TouchDownResponse)
async def control_touch_down(request: TouchDownRequest) -> TouchDownResponse:
    """Send touch DOWN at normalized coordinates (local ADB devices only)."""
    try:
        if not request.device_id:
            return TouchDownResponse(success=False, error="device_id is required")

        from AutoGLM_GUI.adb_plus import touch_down_async

        adb_serial = await asyncio.to_thread(_local_adb_serial, request.device_id)
        px, py = await asyncio.to_thread(
            resolve_control_point,
            request.x,
            request.y,
            device_id=adb_serial,
        )
        await touch_down_async(
            x=px,
            y=py,
            device_id=adb_serial,
            delay=request.delay,
        )

        return TouchDownResponse(success=True)
    except Exception as e:
        return TouchDownResponse(success=False, error=str(e))


@router.post("/api/control/touch/move", response_model=TouchMoveResponse)
async def control_touch_move(request: TouchMoveRequest) -> TouchMoveResponse:
    """Send touch MOVE at normalized coordinates (local ADB devices only)."""
    try:
        if not request.device_id:
            return TouchMoveResponse(success=False, error="device_id is required")

        from AutoGLM_GUI.adb_plus import touch_move_async

        adb_serial = await asyncio.to_thread(_local_adb_serial, request.device_id)
        px, py = await asyncio.to_thread(
            resolve_control_point,
            request.x,
            request.y,
            device_id=adb_serial,
        )
        await touch_move_async(
            x=px,
            y=py,
            device_id=adb_serial,
            delay=request.delay,
        )

        return TouchMoveResponse(success=True)
    except Exception as e:
        return TouchMoveResponse(success=False, error=str(e))


@router.post("/api/control/touch/up", response_model=TouchUpResponse)
async def control_touch_up(request: TouchUpRequest) -> TouchUpResponse:
    """Send touch UP at normalized coordinates (local ADB devices only)."""
    try:
        if not request.device_id:
            return TouchUpResponse(success=False, error="device_id is required")

        from AutoGLM_GUI.adb_plus import touch_up_async

        adb_serial = await asyncio.to_thread(_local_adb_serial, request.device_id)
        px, py = await asyncio.to_thread(
            resolve_control_point,
            request.x,
            request.y,
            device_id=adb_serial,
        )
        await touch_up_async(
            x=px,
            y=py,
            device_id=adb_serial,
            delay=request.delay,
        )

        return TouchUpResponse(success=True)
    except Exception as e:
        return TouchUpResponse(success=False, error=str(e))
