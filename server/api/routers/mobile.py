"""
手机可视化与控制 - 最小路由 (列设备 / 截图 / 健康 / 视频流reset)

- 列设备 / 健康 / 截图:走 core.mobile,复用 AutoGLM 设备内核 (ADB)
- 实时视频画面:走 Socket.IO (AutoGLM scrcpy_stream)。
  前端连接 /socket.io 后 emit "connect-device" {device_id},
  服务端回推 "video-metadata" 和 "video-data" (H264) 帧。
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from api.auth import get_current_active_user, require_admin, User
from api.services.runtime_config import get_runtime_app_config
from core.mobile import MobileDeviceManager
from core.mobile.coordinates import CoordSpace, resolve_swipe, resolve_tap
from core.observability import obs_log, observation_context

router = APIRouter(dependencies=[Depends(get_current_active_user)])

_manager: MobileDeviceManager | None = None


def _get_manager() -> MobileDeviceManager:
    """懒加载设备管理器单例,并启动后台轮询。"""
    global _manager
    if _manager is None:
        _manager = MobileDeviceManager()
        _manager.start_polling()
    return _manager


# 设备阻塞操作的统一超时(秒)。底层 adb 子进程对 tap/swipe/key 等有 30s 超时,
# 但 input.py 的 type_text/clear_text 无子进程超时,这里用 wait_for 兜底,
# 防止设备无响应时请求与线程池 worker 被无限挂起。
def _default_adb_timeout() -> float:
    return 35.0


async def _load_adb_timeout() -> float:
    try:
        return float((await get_runtime_app_config()).mobile.adb_timeout) + 5.0
    except Exception:
        return _default_adb_timeout()


_ADB_OP_TIMEOUT = _default_adb_timeout()


async def _device_op(fn, *args, op: str, timeout: float | None = None):
    """在线程池执行阻塞设备调用,施加超时与统一错误映射(504 超时 / 502 失败)。"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args), timeout or await _load_adb_timeout()
        )
    except HTTPException:
        raise
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"{op}超时(设备无响应)") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{op}失败: {exc}") from exc


async def _ensure_device_access(device_id: str, current_user: User) -> None:
    """Block access to devices reserved by another user; admins bypass."""
    if current_user.is_admin:
        return
    from core.mobile.identity import resolve_device_key
    from core.mobile.pool import DevicePool, PoolError

    key = await asyncio.to_thread(resolve_device_key, device_id)
    try:
        await asyncio.to_thread(
            DevicePool.get_instance().ensure_owner, key, current_user.username
        )
    except PoolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# 供手机子路由复用同一设备权限语义，避免各能力各自实现预约校验。
ensure_device_access = _ensure_device_access


def _refresh_and_list(mgr: MobileDeviceManager) -> list:
    """单次线程内刷新并列设备,避免两次 to_thread 往返。"""
    mgr.refresh()
    return mgr.list_devices()


# ============ 数据模型 ============

class DeviceItem(BaseModel):
    device_id: str
    status: str
    model: str | None = None
    connection_type: str | None = None


class DeviceListResponse(BaseModel):
    devices: list[DeviceItem]


class TapRequest(BaseModel):
    x: int
    y: int
    coord_space: CoordSpace = "pixel"
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None


class SwipeRequest(BaseModel):
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    duration_ms: int | None = None
    coord_space: CoordSpace = "pixel"
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None


class ScreenshotJsonResponse(BaseModel):
    success: bool
    capture_failed: bool = False
    save_failed: bool = False
    base64: str | None = None
    width: int | None = None
    height: int | None = None
    screenshot_id: str | None = None
    screenshot_url: str | None = None
    error: str | None = None
    save_error: str | None = None


class TextRequest(BaseModel):
    text: str
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None


class KeyRequest(BaseModel):
    key: str
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None


class LaunchRequest(BaseModel):
    app_name: str
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None


class TaskRequest(BaseModel):
    device_id: str
    task: str
    agent_type: str = "general-vision"
    max_steps: int | None = None
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None


class CancelRequest(BaseModel):
    task_id: str


class ChatSuggestRequest(BaseModel):
    device_id: str
    project_id: str | None = None
    task_id: str | None = None
    my_background: str = ""
    contact_profile: str = ""
    contact_id: str | None = None  # 传了则从画像库自动注入对方画像


class ChatSendRequest(BaseModel):
    device_id: str
    text: str
    send_button: dict[str, int] | None = None
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None


class AcquireRequest(BaseModel):
    device_id: str
    note: str = ""


class ReleaseRequest(BaseModel):
    device_id: str


class WifiConnectRequest(BaseModel):
    ip: str
    port: int = 5555


class AdbPairCodeRequest(BaseModel):
    ip: str
    pairing_port: int
    pairing_code: str
    connect_port: int | None = None


class AdbConnectRequest(BaseModel):
    ip: str
    port: int


class AdbPairQrCompleteRequest(BaseModel):
    service_name: str
    password: str
    timeout_seconds: float = 60.0
    connect_after_pair: bool = True


class UsbToWifiRequest(BaseModel):
    device_id: str
    port: int = 5555


class DisconnectRequest(BaseModel):
    device_id: str


class RemoteDiscoverRequest(BaseModel):
    base_url: str


class RemoteAddRequest(BaseModel):
    base_url: str
    device_id: str


class RemoteRemoveRequest(BaseModel):
    serial: str


class PlanRequest(BaseModel):
    goal: str


class RunPlannedRequest(BaseModel):
    device_id: str
    goal: str
    project_id: str | None = None
    task_id: str | None = None
    contact_id: str | None = None
    max_steps_per_subtask: int | None = None
    screen_aware: bool = True
    max_replans: int = 2


class ProfileUpsertRequest(BaseModel):
    project_id: str | None = None
    name: str | None = None
    platform: str | None = None
    persona: dict | None = None


class ProfileAnalyzeRequest(BaseModel):
    device_id: str
    contact_id: str
    project_id: str | None = None
    task_id: str | None = None
    name: str | None = None
    platform: str | None = None


class AutoChatStartRequest(BaseModel):
    device_id: str
    project_id: str | None = None
    contact_id: str | None = None  # 留空则从屏幕识别对方身份推导
    contact_name: str | None = None
    my_background: str = ""
    goal: str = ""  # 诱导目标(如: 引导对方点击我发送的伪装文件); 空则普通聊天
    platform: str | None = None
    interval: float = 8.0
    auto_send: bool = False
    ensure_chat: bool = False  # 不在对话界面时是否自动导航
    send_button: dict[str, int] | None = None


class AutoChatStopRequest(BaseModel):
    task_id: str


class WatchStartRequest(BaseModel):
    device_id: str
    project_id: str | None = None
    platform: str = "微信"
    my_background: str = ""
    auto_accept: bool = True
    auto_send: bool = False
    interval: float = 20.0
    send_button: dict[str, int] | None = None


class WatchStopRequest(BaseModel):
    watch_id: str


class WakeRequest(BaseModel):
    device_id: str
    stay_on: bool = False


class StayAwakeRequest(BaseModel):
    device_id: str
    on: bool = True


class WakeUnlockRequest(BaseModel):
    device_id: str
    pin: str | None = None
    stay_on: bool = True


class GroupCreateRequest(BaseModel):
    name: str
    color: str | None = None


class GroupUpdateRequest(BaseModel):
    name: str | None = None
    color: str | None = None
    order: int | None = None


class DeviceMetaUpdateRequest(BaseModel):
    """设备元数据部分更新；仅写入显式提供的字段（`group_id: null` 表示移出分组）。"""

    display_name: str | None = None
    note: str | None = None
    tags: list[str] | None = None
    group_id: str | None = None


class EasyTierAccessResponse(BaseModel):
    enabled: bool
    public_host: str
    network_name: str
    network_secret: str
    hostname: str
    virtual_cidr: str
    adb_port: int
    backend_peer_hostname: str
    backend_peer_ipv4: str
    phone_ipv4_cidr: str
    auto_scan_enabled: bool
    listeners: list[str]
    peers: list[str]
    agent_download_url: str
    android_download_url: str
    docs_url: str
    server_command: str
    phone_command: str
    config_filename: str
    config_toml: str
    config_payload: dict
    qr_payload: dict
    warnings: list[str]


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _log_mobile_operation(
    *,
    operation_type: str,
    device_id: str,
    project_id: str | None = None,
    task_id: str | None = None,
    contact_id: str | None = None,
    action: str = "",
    status: str = "ok",
    message: str = "",
    data: dict | None = None,
    screenshot_id: str | None = None,
) -> None:
    if not project_id:
        return
    try:
        from api.db.mongodb import get_db
        from api.dao import mobile_artifacts as ma_dao

        await ma_dao.log_operation(
            get_db(),
            operation_type=operation_type,
            device_id=device_id,
            project_id=project_id,
            task_id=task_id,
            contact_id=contact_id,
            action=action,
            status=status,
            message=message,
            data=data,
            screenshot_id=screenshot_id,
        )
    except Exception:
        pass


async def _save_mobile_screenshot(
    *,
    image_base64: str,
    project_id: str | None,
    task_id: str | None,
    device_id: str,
    contact_id: str | None = None,
    source: str,
    width: int | None = None,
    height: int | None = None,
    note: str = "",
    strict: bool = False,
) -> dict | None:
    try:
        from api.db.mongodb import get_db
        from api.dao import mobile_artifacts as ma_dao

        return await ma_dao.save_screenshot(
            get_db(),
            image_base64=image_base64,
            project_id=project_id,
            task_id=task_id,
            device_id=device_id,
            contact_id=contact_id,
            source=source,
            width=width,
            height=height,
            note=note,
        )
    except Exception:
        if strict:
            raise
        return None


# ============ 路由 ============

@router.get("/devices", response_model=DeviceListResponse)
async def list_devices() -> DeviceListResponse:
    """列出所有已连接的手机设备。"""
    mgr = _get_manager()
    devices = await asyncio.to_thread(_refresh_and_list, mgr)
    return DeviceListResponse(
        devices=[
            DeviceItem(
                device_id=d.device_id,
                status=d.status,
                model=d.model,
                connection_type=d.connection_type,
            )
            for d in devices
        ]
    )


@router.get("/devices/{device_id}/health")
async def device_health(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """探测设备健康(截图 / 输入 / 当前应用是否就绪)。"""
    await _ensure_device_access(device_id, current_user)
    try:
        timeout = await _load_adb_timeout()
        health = await asyncio.wait_for(
            asyncio.to_thread(_get_manager().health, device_id),
            timeout + 30,
        )
    except asyncio.TimeoutError:
        return {
            "device_id": device_id,
            "online": False,
            "screenshot_ready": False,
            "input_ready": False,
            "current_app_ready": False,
            "capture_failed": True,
            "error": "health probe timeout",
        }
    return {
        "device_id": health.device_id,
        "online": health.online,
        "screenshot_ready": health.screenshot_ready,
        "input_ready": health.input_ready,
        "current_app_ready": health.current_app_ready,
        "capture_failed": health.capture_failed,
        "error": health.error,
    }


@router.get(
    "/devices/{device_id}/screenshot",
    response_model=None,  # 返回类型为 Response | ScreenshotJsonResponse 联合，禁用响应模型推断
    responses={
        200: {
            "content": {
                "image/png": {},
                "application/json": {"schema": ScreenshotJsonResponse.model_json_schema()},
            }
        }
    },
)
async def device_screenshot(
    device_id: str,
    *,
    format: Literal["png", "json"] = Query(
        "png",
        description="png=二进制图片；json=结构化结果(含 success/capture_failed)",
    ),
    project_id: str | None = Query(None, description="传入后保存截图并关联项目"),
    task_id: str | None = Query(None, description="可选任务 ID"),
    contact_id: str | None = Query(None, description="可选联系人 ID"),
    save: bool = Query(False, description="是否强制保存；保存仍建议传 project_id"),
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> Response | ScreenshotJsonResponse:
    """抓取一帧屏幕截图。实时画面请用 Socket.IO connect-device。"""
    await _ensure_device_access(device_id, current_user)
    if save and not project_id:
        raise HTTPException(status_code=400, detail="保存截图需要传 project_id")
    try:
        timeout = await _load_adb_timeout()
        shot = await asyncio.wait_for(
            asyncio.to_thread(_get_manager().capture, device_id), timeout
        )
    except Exception as exc:
        if format == "json":
            return ScreenshotJsonResponse(
                success=False,
                capture_failed=True,
                error=str(exc),
            )
        status = 504 if isinstance(exc, asyncio.TimeoutError) else 502
        raise HTTPException(status_code=status, detail=f"截图失败: {exc}") from exc

    saved = None
    if project_id or save:
        try:
            saved = await _save_mobile_screenshot(
                image_base64=shot.base64_data,
                project_id=project_id,
                task_id=task_id,
                device_id=device_id,
                contact_id=contact_id,
                source="manual_screenshot",
                width=shot.width,
                height=shot.height,
                note="manual screenshot",
                strict=True,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"截图已获取但保存失败: {exc}"
            if format == "json":
                return ScreenshotJsonResponse(
                    success=False,
                    capture_failed=False,
                    save_failed=True,
                    base64=shot.base64_data,
                    width=shot.width,
                    height=shot.height,
                    error=message,
                    save_error=str(exc),
                )
            raise HTTPException(status_code=502, detail=message) from exc
        await _log_mobile_operation(
            operation_type="screenshot",
            device_id=device_id,
            project_id=project_id,
            task_id=task_id,
            contact_id=contact_id,
            action="capture",
            screenshot_id=(saved or {}).get("screenshot_id"),
        )

    if format == "json":
        return ScreenshotJsonResponse(
            success=True,
            capture_failed=False,
            base64=shot.base64_data,
            width=shot.width,
            height=shot.height,
            screenshot_id=(saved or {}).get("screenshot_id"),
            screenshot_url=(saved or {}).get("url"),
        )

    png = base64.b64decode(shot.base64_data)
    headers = {}
    if saved:
        headers["X-Screenshot-Id"] = saved["screenshot_id"]
        headers["X-Screenshot-Url"] = saved["url"]
    return Response(content=png, media_type="image/png", headers=headers)


@router.post("/video/reset")
async def reset_video(
    current_user: Annotated[User, Depends(get_current_active_user)],
    device_id: str | None = None,
) -> dict:
    """重置(停止)scrcpy 视频流;前端重连 /socket.io 即可重新开流。"""
    from AutoGLM_GUI.socketio_server import stop_streamers

    if device_id:
        await _ensure_device_access(device_id, current_user)
    elif not current_user.is_admin:
        raise HTTPException(status_code=403, detail="重置全部视频流需要管理员权限")
    await asyncio.to_thread(stop_streamers, device_id)
    return {"ok": True, "device_id": device_id}


# ============ 交互控制 (前端点击画面 → 操作手机) ============
# coord_space 默认 pixel（设备像素）。也可用 normalized_1000 / normalized_10000 / auto。

@router.post("/devices/{device_id}/tap")
async def tap(
    device_id: str,
    req: TapRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """点击指定坐标。"""
    await _ensure_device_access(device_id, current_user)
    mgr = _get_manager()
    dev = mgr.get_device(device_id)

    def _do() -> None:
        px, py = resolve_tap(
            req.x,
            req.y,
            device_id=mgr.resolve_adb_device_id(device_id),
            coord_space=req.coord_space,
        )
        dev.tap(px, py)

    await _device_op(_do, op="tap")
    await _log_mobile_operation(
        operation_type="tap",
        device_id=device_id,
        project_id=req.project_id,
        task_id=req.task_id,
        contact_id=req.contact_id,
        action="tap",
        data={"x": req.x, "y": req.y, "coord_space": req.coord_space},
    )
    return {"ok": True}


@router.post("/devices/{device_id}/swipe")
async def swipe(
    device_id: str,
    req: SwipeRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """滑动 (拖拽 / 翻页)。"""
    await _ensure_device_access(device_id, current_user)
    mgr = _get_manager()
    dev = mgr.get_device(device_id)

    def _do() -> None:
        sx, sy, ex, ey = resolve_swipe(
            req.start_x,
            req.start_y,
            req.end_x,
            req.end_y,
            device_id=mgr.resolve_adb_device_id(device_id),
            coord_space=req.coord_space,
        )
        dev.swipe(sx, sy, ex, ey, req.duration_ms)

    await _device_op(_do, op="swipe")
    await _log_mobile_operation(
        operation_type="swipe",
        device_id=device_id,
        project_id=req.project_id,
        task_id=req.task_id,
        contact_id=req.contact_id,
        action="swipe",
        data={
            "start_x": req.start_x,
            "start_y": req.start_y,
            "end_x": req.end_x,
            "end_y": req.end_y,
            "duration_ms": req.duration_ms,
            "coord_space": req.coord_space,
        },
    )
    return {"ok": True}


@router.post("/devices/{device_id}/text")
async def input_text(
    device_id: str,
    req: TextRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """向当前聚焦输入框输入文本。"""
    await _ensure_device_access(device_id, current_user)
    dev = _get_manager().get_device(device_id)

    def _do() -> None:
        original_ime = dev.detect_and_set_adb_keyboard()
        need_restore = "com.android.adbkeyboard/.AdbIME" not in original_ime
        try:
            dev.type_text(req.text)
        finally:
            if need_restore and original_ime:
                dev.restore_keyboard(original_ime)

    await _device_op(_do, op="输入")
    await _log_mobile_operation(
        operation_type="text",
        device_id=device_id,
        project_id=req.project_id,
        task_id=req.task_id,
        contact_id=req.contact_id,
        action="type_text",
        data={"text": req.text},
    )
    return {"ok": True}


@router.post("/devices/{device_id}/key")
async def press_key(
    device_id: str,
    req: KeyRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """系统按键: back / home / enter / search / dpad_* 等。"""
    await _ensure_device_access(device_id, current_user)
    dev = _get_manager().get_device(device_id)
    key = req.key.strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="按键不能为空")
    ok = await _device_op(dev.press_key, key, op="按键")
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的按键: {req.key} "
                "(支持 back/home/enter/search/delete/tab/menu/escape/space/dpad_* 等)"
            ),
        )
    await _log_mobile_operation(
        operation_type="key",
        device_id=device_id,
        project_id=req.project_id,
        task_id=req.task_id,
        contact_id=req.contact_id,
        action=key,
    )
    return {"ok": True}


@router.post("/devices/{device_id}/launch")
async def launch_app(
    device_id: str,
    req: LaunchRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """启动指定应用。"""
    await _ensure_device_access(device_id, current_user)
    dev = _get_manager().get_device(device_id)
    ok = await _device_op(dev.launch_app, req.app_name, op="启动")
    await _log_mobile_operation(
        operation_type="launch",
        device_id=device_id,
        project_id=req.project_id,
        task_id=req.task_id,
        contact_id=req.contact_id,
        action="launch_app",
        status="ok" if ok else "failed",
        data={"app_name": req.app_name},
    )
    return {"ok": bool(ok), "app_name": req.app_name}


@router.get("/devices/{device_id}/current_app")
async def current_app(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """读取当前前台应用 (聊天 hook / 状态判断用)。"""
    await _ensure_device_access(device_id, current_user)
    dev = _get_manager().get_device(device_id)
    name = await _device_op(dev.get_current_app, op="读取当前应用")
    return {"device_id": device_id, "current_app": name}


# ============ AI 自助任务 (执行层:复用 AutoGLM 视觉 agent,配置用我们的) ============

@router.post("/agent/task")
async def agent_task(
    req: TaskRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> StreamingResponse:
    """给 AI 下达一个手机任务,流式返回 思考/动作/截图/完成 事件。

    首个事件 task_start 含 task_id,可用于 POST /agent/cancel 中途停止。
    """
    from core.mobile.executor import run_task_stream

    await _ensure_device_access(req.device_id, current_user)
    task_id = req.task_id or uuid.uuid4().hex[:12]

    async def gen():
        last_type = ""
        try:
            with observation_context(
                project_id=req.project_id,
                task_id=task_id,
                turn_id=task_id,
                phase="mobile_agent",
                agent=req.agent_type,
            ):
                obs_log(
                    "手机 AI 单步任务启动",
                    project_id=req.project_id or "",
                    task_id=task_id,
                    source="mobile_agent",
                    level="notice",
                    event="task_start",
                    phase="mobile_agent",
                    agent=req.agent_type,
                    data={"device_id": req.device_id, "task": req.task},
                )
                async for event in run_task_stream(
                    req.device_id,
                    req.task,
                    agent_type=req.agent_type,
                    max_steps=req.max_steps,
                    task_id=task_id,
                    project_id=req.project_id,
                    contact_id=req.contact_id,
                    owner=current_user.username,
                ):
                    last_type = str(event.get("type") or "")
                    yield _sse(event)
                obs_log(
                    "手机 AI 单步任务结束",
                    project_id=req.project_id or "",
                    task_id=task_id,
                    source="mobile_agent",
                    level="notice" if last_type != "error" else "error",
                    event="task_done" if last_type != "error" else "task_error",
                    phase="mobile_agent",
                    agent=req.agent_type,
                    data={"device_id": req.device_id, "last_type": last_type},
                )
        except Exception as exc:  # noqa: BLE001
            obs_log(
                f"手机 AI 单步任务异常: {exc}",
                project_id=req.project_id or "",
                task_id=task_id,
                source="mobile_agent",
                level="error",
                event="task_error",
                phase="mobile_agent",
                agent=req.agent_type,
                data={"device_id": req.device_id, "error": str(exc)},
            )
            yield _sse({"type": "error", "data": {"message": str(exc)}})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/agent/cancel")
async def agent_cancel(
    req: CancelRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """取消正在运行的执行层任务。"""
    from core.mobile.executor import cancel_task

    ok = await cancel_task(
        req.task_id, owner=current_user.username, is_admin=current_user.is_admin
    )
    return {"ok": ok, "task_id": req.task_id}


# ============ 辅助聊天 (读屏→话术→建议→发送) ============

@router.post("/chat-assist/suggest")
async def chat_assist_suggest(
    req: ChatSuggestRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> StreamingResponse:
    """读屏(视觉) → copywriting skills 生成候选话术,流式返回。不自动发送。

    传 contact_id 时,自动从画像库读取该联系人画像注入(系甶4:建议结合画像)。
    """
    from core.mobile.chat_assist import suggest_stream

    await _ensure_device_access(req.device_id, current_user)
    task_id = req.task_id or uuid.uuid4().hex[:12]

    contact_profile = req.contact_profile
    if req.contact_id and not contact_profile:
        from api.db.mongodb import get_db
        from api.dao import contact_profiles as cp_dao
        from core.mobile.profiling import format_profile_for_prompt

        doc = await cp_dao.get_profile(get_db(), req.contact_id)
        contact_profile = format_profile_for_prompt(doc)

    async def gen():
        try:
            with observation_context(
                project_id=req.project_id,
                task_id=task_id,
                turn_id=task_id,
                phase="mobile_chat_assist",
                agent="chat_assist",
            ):
                obs_log(
                    "手机话术辅助启动",
                    project_id=req.project_id or "",
                    task_id=task_id,
                    source="mobile_chat_assist",
                    level="notice",
                    event="suggest_start",
                    phase="mobile_chat_assist",
                    agent="chat_assist",
                    data={"device_id": req.device_id, "contact_id": req.contact_id},
                )
                async for event in suggest_stream(
                    req.device_id,
                    my_background=req.my_background,
                    contact_profile=contact_profile,
                    contact_id=req.contact_id,
                    project_id=req.project_id,
                    task_id=task_id,
                ):
                    yield _sse(event)
                obs_log(
                    "手机话术辅助结束",
                    project_id=req.project_id or "",
                    task_id=task_id,
                    source="mobile_chat_assist",
                    level="notice",
                    event="suggest_done",
                    phase="mobile_chat_assist",
                    agent="chat_assist",
                    data={"device_id": req.device_id, "contact_id": req.contact_id},
                )
        except Exception as exc:  # noqa: BLE001
            obs_log(
                f"手机话术辅助异常: {exc}",
                project_id=req.project_id or "",
                task_id=task_id,
                source="mobile_chat_assist",
                level="error",
                event="suggest_error",
                phase="mobile_chat_assist",
                agent="chat_assist",
                data={"device_id": req.device_id, "contact_id": req.contact_id, "error": str(exc)},
            )
            yield _sse({"stage": "error", "data": {"message": str(exc)}})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/chat-assist/send")
async def chat_assist_send(
    req: ChatSendRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """把选定话术输入聊天框;若传 send_button{x,y} 则点一下发送。"""
    from core.mobile.chat_assist import send_reply

    await _ensure_device_access(req.device_id, current_user)
    try:
        return await send_reply(
            req.device_id,
            req.text,
            send_button=req.send_button,
            project_id=req.project_id,
            task_id=req.task_id,
            contact_id=req.contact_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"发送失败: {exc}") from exc


# ============ 概览 ============

@router.get("/overview")
async def overview() -> dict:
    """汇总:设备(在线/发现) + 配置状态 + 能力 + 运行中任务。"""
    from core.mobile.executor import running_task_ids

    mgr = _get_manager()
    # 设备刷新列举 与 配置加载 相互独立
    devices, app_cfg = await asyncio.gather(
        asyncio.to_thread(_refresh_and_list, mgr),
        get_runtime_app_config(),
    )
    online = [d for d in devices if d.status == "device"]

    rt = app_cfg.runtime
    llm_ready = bool(rt.base_url)
    video = app_cfg.mobile.video
    return {
        "devices": {
            "total": len(devices),
            "online": len(online),
            "items": [
                {
                    "device_id": d.device_id,
                    "status": d.status,
                    "model": d.model,
                    "connection_type": d.connection_type,
                }
                for d in devices
            ],
        },
        "config": {
            "llm_configured": llm_ready,
            "models": {
                "default": rt.models.default,
                "vision": rt.models.vision,
                "mobile_planner": rt.models.mobile_planner_model,
                "mobile_executor": rt.models.mobile_executor_model,
                "mobile_screen": rt.models.mobile_screen_model,
                "mobile_chat": rt.models.mobile_chat_model,
            },
            "sampling": {
                "max_tokens": rt.max_tokens,
                "temperature": rt.temperature,
                "top_p": rt.top_p,
                "frequency_penalty": rt.frequency_penalty,
            },
        },
        "executor_max_tokens": app_cfg.mobile.executor_max_tokens,
        "video_defaults": {
            "maxSize": video.max_size,
            "bitRate": video.bit_rate,
            "maxFps": video.max_fps,
            "downsizeOnError": video.downsize_on_error,
        },
        "coordinate_scales": {"agent": 1000, "api": 10000},
        "socketio": {
            "path": "/socket.io",
            "connect_device": "connect-device",
            "video_events": ["video-metadata", "video-data"],
            "control_event": "control-touch",  # 低延迟手动控制(down/move/up/cancel)
            "video_payload_keys": [
                "device_id",
                "port",
                "maxSize",
                "bitRate",
                "maxFps",
                "downsizeOnError",
            ],
        },
        "running_tasks": running_task_ids(),
        "capabilities": {
            "visualization": True,
            "control": True,
            "ai_task": llm_ready,
            "chat_assist": llm_ready,
        },
    }


# ============ 系统1:资源池 + 网络组网 ============

@router.get("/network/easytier/access", response_model=EasyTierAccessResponse)
async def easytier_access(
    request: Request,
    _user: Annotated[User, Depends(get_current_active_user)],
) -> EasyTierAccessResponse:
    """返回远程手机加入 EasyTier 网络所需的接入配置。

    该接口只对登录用户开放。返回值包含网络名、网络密钥、公网 peer、
    EasyTier Android TOML 配置文件、下载链接和结构化配置。手机导入配置入网后，
    后端会通过 mDNS 或 EasyTier 虚拟网段 ADB 扫描自动纳入资源池；
    `/pool/connect/wifi` 仅作为自动发现不可用时的兜底入口。
    """
    from api.services.runtime_config import get_runtime_config_section
    from core.mobile.easytier import build_easytier_access_profile, set_easytier_runtime_config

    config = await get_runtime_config_section("easytier")
    set_easytier_runtime_config(config)
    profile = build_easytier_access_profile(request.headers.get("host"), config=config)
    return EasyTierAccessResponse(**profile.__dict__)

@router.get("/pool")
async def pool_overview(
    group_id: str | None = Query(
        None, description="按分组过滤;传 'ungrouped' 取未分组,省略取全部"
    ),
) -> dict:
    """资源池全景:设备(含 mDNS 自动发现) + 稳定 key + 占用 + 分组/备注/标签元数据。

    可选 `group_id` 过滤。每个设备附带 `device_key`(稳定硬件 key)与 `meta`。
    """
    from core.mobile.pool import DevicePool
    from api.db.mongodb import get_db
    from api.dao import device_metadata as dm_dao

    items = await asyncio.to_thread(DevicePool.get_instance().list_pool)  # 已含 device_key
    keys = [it.get("device_key") or it["device_id"] for it in items]
    metas = await dm_dao.get_metadata_map(get_db(), set(keys))
    for it, key in zip(items, keys):
        m = metas.get(key) or {}
        it.setdefault("device_key", key)
        it["meta"] = {
            "display_name": m.get("display_name"),
            "note": m.get("note", ""),
            "tags": m.get("tags", []),
            "group_id": m.get("group_id"),
        }
    if group_id is not None:
        if group_id == "ungrouped":
            items = [it for it in items if not it["meta"].get("group_id")]
        else:
            items = [it for it in items if it["meta"].get("group_id") == group_id]
    return {"devices": items, "total": len(items)}


# ============ 系统1b:设备分组 + 元数据(备注/标签/显示名,Mongo,重连稳定 key) ============
# 与 AutoGLM 文件版管理器完全解耦:分组/元数据存 Mongo,元数据按 ro.serialno 稳定 key 存。

@router.post("/groups")
async def create_device_group(req: GroupCreateRequest) -> dict:
    """新建设备分组。"""
    from api.db.mongodb import get_db
    from api.dao import device_metadata as dm_dao

    if not req.name.strip():
        raise HTTPException(status_code=400, detail="分组名不能为空")
    return await dm_dao.create_group(get_db(), req.name.strip(), color=req.color)


@router.get("/groups")
async def list_device_groups() -> dict:
    """列出全部分组(含每组设备数)。"""
    from api.db.mongodb import get_db
    from api.dao import device_metadata as dm_dao

    db = get_db()
    groups, metas = await asyncio.gather(
        dm_dao.list_groups(db),
        dm_dao.list_metadata(db),
    )
    counts: dict[str, int] = {}
    for m in metas:
        gid = m.get("group_id")
        if gid:
            counts[gid] = counts.get(gid, 0) + 1
    for g in groups:
        g["device_count"] = counts.get(g["group_id"], 0)
    return {"groups": groups, "total": len(groups)}


@router.patch("/groups/{group_id}")
async def update_device_group(group_id: str, req: GroupUpdateRequest) -> dict:
    """更新分组(名称/颜色/排序)。"""
    from api.db.mongodb import get_db
    from api.dao import device_metadata as dm_dao

    provided = req.model_fields_set
    kwargs: dict = {}
    for f in ("name", "color", "order"):
        if f in provided:
            kwargs[f] = getattr(req, f)
    updated = await dm_dao.update_group(get_db(), group_id, **kwargs)
    if not updated:
        raise HTTPException(status_code=404, detail="分组不存在")
    return updated


@router.delete("/groups/{group_id}")
async def delete_device_group(group_id: str) -> dict:
    """删除分组(组内设备自动解绑,不删设备元数据)。"""
    from api.db.mongodb import get_db
    from api.dao import device_metadata as dm_dao

    ok = await dm_dao.delete_group(get_db(), group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="分组不存在")
    return {"ok": True, "group_id": group_id}


@router.get("/devices/{device_id}/meta")
async def get_device_meta(device_id: str) -> dict:
    """读取设备元数据(按稳定 key);无记录返回空模板。"""
    from api.db.mongodb import get_db
    from api.dao import device_metadata as dm_dao
    from core.mobile.identity import resolve_device_key

    key = await asyncio.to_thread(resolve_device_key, device_id)
    meta = await dm_dao.get_metadata(get_db(), key) or {
        "device_key": key,
        "display_name": None,
        "note": "",
        "tags": [],
        "group_id": None,
    }
    meta["device_id"] = device_id
    return meta


@router.put("/devices/{device_id}/meta")
async def put_device_meta(device_id: str, req: DeviceMetaUpdateRequest) -> dict:
    """部分更新设备元数据(备注/标签/显示名/分组);按稳定 key 存,重连不丢。"""
    from api.db.mongodb import get_db
    from api.dao import device_metadata as dm_dao
    from core.mobile.identity import resolve_device_key

    db = get_db()
    key = await asyncio.to_thread(resolve_device_key, device_id)

    provided = req.model_fields_set
    kwargs: dict = {"last_device_id": device_id}
    for f in ("display_name", "note", "tags", "group_id"):
        if f in provided:
            kwargs[f] = getattr(req, f)

    if kwargs.get("group_id"):
        if not await dm_dao.get_group(db, kwargs["group_id"]):
            raise HTTPException(status_code=404, detail="目标分组不存在")

    meta = await dm_dao.upsert_metadata(db, key, **kwargs)
    if meta is not None:
        meta["device_id"] = device_id
    return meta or {}


@router.post("/pool/acquire")
async def pool_acquire(
    req: AcquireRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """申请独占一台设备(手机作为可申请资源)。按稳定 key 占用,重连不丢。"""
    from core.mobile.pool import DevicePool, PoolError
    from core.mobile.identity import resolve_device_key

    key = await asyncio.to_thread(resolve_device_key, req.device_id)
    try:
        res = await asyncio.to_thread(
            DevicePool.get_instance().acquire,
            key,
            current_user.username,
            req.note,
            device_id=req.device_id,
        )
    except PoolError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        from api.db.mongodb import get_db
        from api.dao import device_reservations as dr_dao

        await dr_dao.upsert_reservation(
            get_db(), res.device_key, res.owner,
            note=req.note, since=res.since, device_id=req.device_id,
        )
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "device_id": req.device_id,
        "device_key": res.device_key,
        "owner": res.owner,
        "since": res.since,
    }


@router.post("/pool/release")
async def pool_release(
    req: ReleaseRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """释放自己占用的设备(按稳定 key)。"""
    from core.mobile.pool import DevicePool, PoolError
    from core.mobile.identity import resolve_device_key

    key = await asyncio.to_thread(resolve_device_key, req.device_id)
    try:
        ok = await asyncio.to_thread(
            DevicePool.get_instance().release, key, current_user.username
        )
    except PoolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if ok:
        try:
            from api.db.mongodb import get_db
            from api.dao import device_reservations as dr_dao

            await dr_dao.delete_reservation(get_db(), key)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": ok, "device_id": req.device_id, "device_key": key}


@router.post("/pool/connect/wifi")
async def pool_connect_wifi(
    req: WifiConnectRequest,
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """以 ip:port 接入(easytier 组网后的远程手机走这里)。"""
    from core.mobile.pool import DevicePool

    return await asyncio.to_thread(
        DevicePool.get_instance().connect_wifi_manual, req.ip, req.port
    )


@router.post("/pool/connect/usb-to-wifi")
async def pool_usb_to_wifi(
    req: UsbToWifiRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """把已连 USB 的设备切到 WiFi(便于拔线远程)。"""
    from core.mobile.pool import DevicePool

    await _ensure_device_access(req.device_id, current_user)
    return await asyncio.to_thread(
        DevicePool.get_instance().connect_wifi_from_usb, req.device_id, req.port
    )


@router.post("/pool/disconnect")
async def pool_disconnect(
    req: DisconnectRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    from core.mobile.pool import DevicePool

    await _ensure_device_access(req.device_id, current_user)
    return await asyncio.to_thread(DevicePool.get_instance().disconnect, req.device_id)


@router.post("/pool/remote/discover")
async def pool_remote_discover(
    req: RemoteDiscoverRequest,
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """从远程 Device Agent Server 发现设备。"""
    from core.mobile.pool import DevicePool

    return await asyncio.to_thread(
        DevicePool.get_instance().discover_remote, req.base_url
    )


@router.post("/pool/remote/add")
async def pool_remote_add(
    req: RemoteAddRequest,
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    from core.mobile.pool import DevicePool

    return await asyncio.to_thread(
        DevicePool.get_instance().add_remote, req.base_url, req.device_id
    )


@router.post("/pool/remote/remove")
async def pool_remote_remove(
    req: RemoteRemoveRequest,
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    from core.mobile.pool import DevicePool

    return await asyncio.to_thread(DevicePool.get_instance().remove_remote, req.serial)


# ============ 系统2:规划层(拆任务 → 执行层) ============

@router.post("/agent/plan")
async def agent_plan(req: PlanRequest) -> dict:
    """只规划:把高层目标拆成有序子任务。"""
    from core.mobile.planner import plan_task

    try:
        subtasks = await plan_task(req.goal)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"规划失败: {exc}") from exc
    return {"goal": req.goal, "subtasks": subtasks}


@router.post("/agent/run-planned")
async def agent_run_planned(
    req: RunPlannedRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> StreamingResponse:
    """规划 + 执行(SSE):规划层拆任务,逐个交执行层完成。"""
    from core.mobile.planner import run_planned_task

    await _ensure_device_access(req.device_id, current_user)
    task_id = req.task_id or uuid.uuid4().hex[:12]

    async def gen():
        final_stage = ""
        try:
            with observation_context(
                project_id=req.project_id,
                task_id=task_id,
                turn_id=task_id,
                phase="mobile_planned",
                agent="mobile_planner",
            ):
                obs_log(
                    "手机 AI 规划任务启动",
                    project_id=req.project_id or "",
                    task_id=task_id,
                    source="mobile_planner",
                    level="notice",
                    event="planned_start",
                    phase="mobile_planned",
                    agent="mobile_planner",
                    data={"device_id": req.device_id, "goal": req.goal},
                )
                async for event in run_planned_task(
                    req.device_id,
                    req.goal,
                    max_steps_per_subtask=req.max_steps_per_subtask,
                    screen_aware=req.screen_aware,
                    max_replans=req.max_replans,
                    project_id=req.project_id,
                    contact_id=req.contact_id,
                    owner=current_user.username,
                    plan_id=task_id,
                ):
                    final_stage = str(event.get("stage") or "")
                    yield _sse(event)
                obs_log(
                    "手机 AI 规划任务结束",
                    project_id=req.project_id or "",
                    task_id=task_id,
                    source="mobile_planner",
                    level="notice" if final_stage not in {"error", "aborted"} else "error",
                    event="planned_done" if final_stage not in {"error", "aborted"} else "planned_error",
                    phase="mobile_planned",
                    agent="mobile_planner",
                    data={"device_id": req.device_id, "final_stage": final_stage},
                )
        except Exception as exc:  # noqa: BLE001
            obs_log(
                f"手机 AI 规划任务异常: {exc}",
                project_id=req.project_id or "",
                task_id=task_id,
                source="mobile_planner",
                level="error",
                event="planned_error",
                phase="mobile_planned",
                agent="mobile_planner",
                data={"device_id": req.device_id, "error": str(exc)},
            )
            yield _sse({"stage": "error", "data": {"message": str(exc)}})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ============ 系统3:人物画像 ============

@router.get("/profiles")
async def list_profiles(
    device_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> dict:
    """列出人物画像(可按 device_id 过滤)。"""
    from api.db.mongodb import get_db
    from api.dao import contact_profiles as cp_dao

    items = await cp_dao.list_profiles(
        get_db(), device_id=device_id, project_id=project_id, limit=limit
    )
    return {"profiles": items, "total": len(items)}


@router.get("/projects/{project_id}/profiles")
async def list_project_profiles(project_id: str, limit: int = 100) -> dict:
    """项目维度读取手机聊天沉淀的人物画像。"""
    from api.db.mongodb import get_db
    from api.dao import contact_profiles as cp_dao

    items = await cp_dao.list_profiles(get_db(), project_id=project_id, limit=limit)
    return {"profiles": items, "total": len(items)}


@router.get("/projects/{project_id}/profile-observations")
async def list_project_profile_observations(
    project_id: str,
    contact_id: str | None = None,
    finding_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> dict:
    """项目维度读取手机画像观察明细，用于后续聚合分析。"""
    from api.db.mongodb import get_db
    from api.dao import mobile_profile_observations as mpo_dao

    items = await mpo_dao.list_observations(
        get_db(),
        project_id=project_id,
        contact_id=contact_id,
        finding_id=finding_id,
        task_id=task_id,
        limit=limit,
    )
    return {"observations": items, "total": len(items)}


@router.get("/projects/{project_id}/screenshots")
async def list_project_screenshots(project_id: str, limit: int = 100) -> dict:
    """项目维度读取已保存的真实手机截图元数据。"""
    from api.db.mongodb import get_db
    from api.dao import mobile_artifacts as ma_dao

    items = await ma_dao.list_screenshots(get_db(), project_id=project_id, limit=limit)
    return {"screenshots": items, "total": len(items)}


@router.get("/projects/{project_id}/operations")
async def list_project_operations(project_id: str, limit: int = 100) -> dict:
    """项目维度读取手机操作日志。"""
    from api.db.mongodb import get_db
    from api.dao import mobile_artifacts as ma_dao

    items = await ma_dao.list_operations(get_db(), project_id=project_id, limit=limit)
    return {"operations": items, "total": len(items)}


@router.get("/projects/{project_id}/auto-chat/sessions")
async def list_project_auto_chat_sessions(project_id: str, limit: int = 100) -> dict:
    """项目维度读取自动聊天 session 快照。"""
    from api.db.mongodb import get_db
    from api.dao import auto_chat_sessions as acs_dao

    items = await acs_dao.list_sessions(get_db(), project_id=project_id, limit=limit)
    return {"sessions": items, "total": len(items)}


@router.get("/screenshots/{screenshot_id}/image")
async def get_mobile_screenshot_image(screenshot_id: str):
    """读取已保存手机截图图片。路由受登录鉴权保护。"""
    from api.db.mongodb import get_db
    from api.dao import mobile_artifacts as ma_dao

    doc = await ma_dao.get_screenshot(get_db(), screenshot_id)
    if not doc:
        raise HTTPException(status_code=404, detail="截图不存在")
    storage_object_id = str(doc.get("storage_object_id") or "")
    if storage_object_id:
        from api.storage import get_object_storage

        try:
            storage = await get_object_storage()
            access = await storage.read_access(
                storage_object_id,
                filename=f"{screenshot_id}.png",
                content_type="image/png",
            )
        except Exception as exc:
            raise HTTPException(status_code=404, detail="截图文件不存在") from exc
        if access.mode == "redirect":
            try:
                content = await storage.get_bytes(storage_object_id)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="截图文件暂时不可读取") from exc
            return Response(content=content, media_type="image/png", headers={"Cache-Control": "private, max-age=60"})
        if access.path and access.path.is_file():
            return FileResponse(str(access.path), media_type="image/png")
    path = ma_dao.resolve_screenshot_file(doc)
    if path is None:
        raise HTTPException(status_code=404, detail="截图文件不存在")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="截图文件不存在")
    return FileResponse(str(path), media_type="image/png")


@router.post("/profiles/analyze")
async def analyze_profile(
    req: ProfileAnalyzeRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """从设备读屏 → 提取并沉淀画像(系统3:实时识别 + 沉淀)。"""
    from core.mobile.profiling import analyze_and_update

    await _ensure_device_access(req.device_id, current_user)
    try:
        profile = await analyze_and_update(
            req.device_id,
            req.contact_id,
            name=req.name,
            platform=req.platform,
            project_id=req.project_id,
            task_id=req.task_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"画像分析失败: {exc}") from exc
    return profile


@router.get("/profiles/{contact_id}")
async def get_profile(contact_id: str) -> dict:
    """查看单个画像(前端点击实时查看)。"""
    from api.db.mongodb import get_db
    from api.dao import contact_profiles as cp_dao

    doc = await cp_dao.get_profile(get_db(), contact_id)
    if not doc:
        raise HTTPException(status_code=404, detail="画像不存在")
    return doc


@router.get("/profiles/{contact_id}/observations")
async def list_profile_observations(
    contact_id: str,
    project_id: str | None = None,
    finding_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> dict:
    """读取某个联系人的画像观察明细。"""
    from api.db.mongodb import get_db
    from api.dao import mobile_profile_observations as mpo_dao

    items = await mpo_dao.list_observations(
        get_db(),
        project_id=project_id,
        contact_id=contact_id,
        finding_id=finding_id,
        task_id=task_id,
        limit=limit,
    )
    return {"observations": items, "total": len(items)}


@router.put("/profiles/{contact_id}")
async def put_profile(contact_id: str, req: ProfileUpsertRequest) -> dict:
    """手动新建/更新画像。"""
    from api.db.mongodb import get_db
    from api.dao import contact_profiles as cp_dao

    data: dict = {}
    if req.name is not None:
        data["name"] = req.name
    if req.platform is not None:
        data["platform"] = req.platform
    if req.persona is not None:
        data["persona"] = req.persona
    return await cp_dao.upsert_profile(
        get_db(), contact_id, data, project_id=req.project_id
    ) or {}


@router.delete("/profiles/{contact_id}")
async def delete_profile(contact_id: str) -> dict:
    from api.db.mongodb import get_db
    from api.dao import contact_profiles as cp_dao

    ok = await cp_dao.delete_profile(get_db(), contact_id)
    return {"ok": ok, "contact_id": contact_id}


# ============ 系统5:自动聊天(加人后自动聊) ============

@router.post("/auto-chat/start")
async def auto_chat_start(
    req: AutoChatStartRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """启动自动聊天:定时读屏→沉淀画像→(auto_send 时)基于画像自动回复。"""
    from core.mobile.auto_chat import AutoChatManager

    await _ensure_device_access(req.device_id, current_user)
    task_id = await AutoChatManager.get_instance().start(
        req.device_id,
        req.contact_id,
        project_id=req.project_id,
        contact_name=req.contact_name,
        my_background=req.my_background,
        goal=req.goal,
        platform=req.platform,
        owner=current_user.username,
        interval=req.interval,
        auto_send=req.auto_send,
        ensure_chat=req.ensure_chat,
        send_button=req.send_button,
    )
    return {"ok": True, "task_id": task_id}


@router.post("/auto-chat/stop")
async def auto_chat_stop(
    req: AutoChatStopRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    from core.mobile.auto_chat import AutoChatManager

    ok = AutoChatManager.get_instance().stop(
        req.task_id, owner=current_user.username, is_admin=current_user.is_admin
    )
    return {"ok": ok, "task_id": req.task_id}


@router.get("/auto-chat/status")
async def auto_chat_status(
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    from core.mobile.auto_chat import AutoChatManager

    sessions = AutoChatManager.get_instance().status(task_id)
    if not current_user.is_admin:
        if isinstance(sessions, list):
            sessions = [s for s in sessions if s.get("owner") in (None, current_user.username)]
        elif sessions and sessions.get("owner") not in (None, current_user.username):
            sessions = None
    if project_id and isinstance(sessions, list):
        sessions = [s for s in sessions if s.get("project_id") == project_id]
    return {"sessions": sessions}


@router.post("/auto-chat/watch/start")
async def auto_chat_watch_start(
    req: WatchStartRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """启动新好友 watcher:检测新好友→(可)自动通过→进对话→为其起自动聊天。"""
    from core.mobile.auto_chat import AutoChatManager

    await _ensure_device_access(req.device_id, current_user)
    watch_id = await AutoChatManager.get_instance().start_watch(
        req.device_id,
        project_id=req.project_id,
        platform=req.platform,
        my_background=req.my_background,
        auto_accept=req.auto_accept,
        auto_send=req.auto_send,
        interval=req.interval,
        send_button=req.send_button,
        owner=current_user.username,
    )
    return {"ok": True, "watch_id": watch_id}


@router.post("/auto-chat/watch/stop")
async def auto_chat_watch_stop(
    req: WatchStopRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    from core.mobile.auto_chat import AutoChatManager

    ok = AutoChatManager.get_instance().stop_watch(
        req.watch_id, owner=current_user.username, is_admin=current_user.is_admin
    )
    return {"ok": ok, "watch_id": req.watch_id}


# ============ 系统1 增强:自动接入 / 唤醒 ============

@router.post("/pool/auto-connect")
async def pool_auto_connect(
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """把 mDNS/EasyTier 发现的可用设备自动 connect 接入资源池。"""
    from core.mobile.pool import DevicePool

    return await asyncio.to_thread(DevicePool.get_instance().auto_connect_discovered)


@router.get("/adb/wireless/capabilities")
async def adb_wireless_capabilities(
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """返回后端 ADB 是否支持无线配对能力。"""
    from core.mobile.adb_pairing import adb_capabilities

    return await asyncio.to_thread(adb_capabilities)


@router.post("/adb/wireless/pair-code")
async def adb_wireless_pair_code(
    req: AdbPairCodeRequest,
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """使用 Android 无线调试的配对码完成 ADB TLS 配对。"""
    from core.mobile.adb_pairing import adb_pair_with_code

    try:
        return await asyncio.to_thread(
            adb_pair_with_code,
            host=req.ip,
            pairing_port=req.pairing_port,
            pairing_code=req.pairing_code,
            connect_port=req.connect_port,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/adb/wireless/connect")
async def adb_wireless_connect(
    req: AdbConnectRequest,
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """连接已配对的 Android 无线调试 TLS 端口。"""
    from core.mobile.adb_pairing import adb_connect

    try:
        return await asyncio.to_thread(adb_connect, host=req.ip, port=req.port)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/adb/wireless/pair-qr/start")
async def adb_wireless_pair_qr_start(
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """生成 Android 无线调试二维码配对 payload。"""
    from core.mobile.adb_pairing import generate_qr_pairing_session

    return await asyncio.to_thread(generate_qr_pairing_session)


@router.post("/adb/wireless/pair-qr/complete")
async def adb_wireless_pair_qr_complete(
    req: AdbPairQrCompleteRequest,
    _admin: Annotated[User, Depends(require_admin)],
) -> dict:
    """等待二维码配对 mDNS 服务出现后完成 ADB TLS 配对。"""
    from core.mobile.adb_pairing import complete_qr_pairing

    try:
        return await asyncio.to_thread(
            complete_qr_pairing,
            service_name=req.service_name,
            password=req.password,
            timeout_seconds=req.timeout_seconds,
            connect_after_pair=req.connect_after_pair,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pool/wake")
async def pool_wake(
    req: WakeRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """唤醒亮屏(KEYCODE_WAKEUP);stay_on=true 充电时常亮,便于远程操作。"""
    from core.mobile.pool import DevicePool

    await _ensure_device_access(req.device_id, current_user)
    return await asyncio.to_thread(
        DevicePool.get_instance().wake, req.device_id, stay_on=req.stay_on
    )


@router.post("/pool/wake-unlock")
async def pool_wake_unlock(
    req: WakeUnlockRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """唤醒亮屏并尝试滑开锁屏;可选一次性 PIN,后端不保存。

    设备必须已开机且 ADB/Agent 在线。该接口不能绕过 Android 安全策略；
    强锁屏、生物识别或企业管控设备需要预授权 Agent/无障碍能力配合。
    """
    from core.mobile.pool import DevicePool

    await _ensure_device_access(req.device_id, current_user)
    return await asyncio.to_thread(
        DevicePool.get_instance().wake_and_unlock,
        req.device_id,
        pin=req.pin,
        stay_on=req.stay_on,
    )


@router.post("/pool/stay-awake")
async def pool_stay_awake(
    req: StayAwakeRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """设置充电时是否常亮(svc power stayon)。"""
    from core.mobile.pool import DevicePool

    await _ensure_device_access(req.device_id, current_user)
    return await asyncio.to_thread(
        DevicePool.get_instance().set_stay_awake, req.device_id, req.on
    )


@router.get("/pool/keepalive/status")
async def pool_keepalive_status(
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    """查询手机保活后台循环状态(心跳/重连/常亮统计)。"""
    from core.mobile.keepalive import MobileKeepAlive

    return MobileKeepAlive.get_instance().status()


# ============ 系统4:建议随时查看(落库读取) ============

@router.get("/suggestions/{key}")
async def get_latest_suggestions(key: str) -> dict:
    """读取最新建议。key = contact_id 或 device:<device_id>(自动聊天观察模式下落库)。"""
    from api.db.mongodb import get_db
    from api.dao import chat_suggestions as cs_dao

    doc = await cs_dao.get_suggestions(get_db(), key)
    if not doc:
        raise HTTPException(status_code=404, detail="暂无建议")
    return doc


# ============ 实时事件推送(系统3/4/5 前端实时查看) ============

def _parse_types(types: str | None) -> set[str] | None:
    if not types:
        return None
    return {t.strip() for t in types.split(",") if t.strip()} or None


@router.get("/events")
async def events_stream(
    device_id: str | None = None,
    contact_id: str | None = None,
    project_id: str | None = None,
    types: str | None = None,
) -> StreamingResponse:
    """SSE 实时事件流:profile_updated / suggestion / auto_chat / auto_chat_watch。

    可按 device_id / contact_id / types(逗号分隔)过滤。先补最近历史再实时跟。
    """
    from core.mobile.events import EventBus

    type_set = _parse_types(types)
    bus = EventBus.get_instance()

    async def gen():
        for ev in bus.recent(
            device_id=device_id,
            contact_id=contact_id,
            project_id=project_id,
            types=type_set,
            limit=30,
        ):
            yield _sse(ev)
        async for ev in bus.subscribe(
            device_id=device_id,
            contact_id=contact_id,
            project_id=project_id,
            types=type_set,
        ):
            if ev is None:
                yield ": keepalive\n\n"  # SSE 注释行:保活 + 触发断连检测,清理空闲订阅
            else:
                yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/events/recent")
async def events_recent(
    device_id: str | None = None,
    contact_id: str | None = None,
    project_id: str | None = None,
    types: str | None = None,
    limit: int = 50,
) -> dict:
    """拉取最近事件(前端首屏补齐用)。"""
    from core.mobile.events import EventBus

    return {
        "events": EventBus.get_instance().recent(
            device_id=device_id,
            contact_id=contact_id,
            project_id=project_id,
            types=_parse_types(types),
            limit=limit,
        )
    }
