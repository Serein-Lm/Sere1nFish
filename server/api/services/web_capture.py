"""统一网页截图运行时：复用扫描 Chrome 的 CDP 会话并写入对象存储。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from api.storage import get_object_storage


def _select_page_target(
    targets: list[dict[str, Any]],
    preferred_url: str,
) -> dict[str, Any] | None:
    pages = [
        item
        for item in targets
        if item.get("type") == "page"
        and str(item.get("url") or "").startswith(("http://", "https://"))
    ]
    if not pages:
        return None
    preferred = urlsplit(preferred_url)

    def _score(item: dict[str, Any]) -> tuple[int, int]:
        candidate_url = str(item.get("url") or "")
        candidate = urlsplit(candidate_url)
        score = 0
        if candidate_url.rstrip("/") == preferred_url.rstrip("/"):
            score += 100
        same_host = bool(preferred.hostname and candidate.hostname == preferred.hostname)
        if same_host:
            score += 50
            if preferred.path and candidate.path == preferred.path:
                score += 20
        return score, len(candidate_url)

    selected = max(pages, key=_score)
    return selected if _score(selected)[0] > 0 else None


async def _cdp_command(
    websocket: Any,
    command_id: int,
    method: str,
    *,
    params: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": command_id, "method": method}
    if params:
        payload["params"] = params
    if session_id:
        payload["sessionId"] = session_id
    await websocket.send(json.dumps(payload))
    while True:
        message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        if message.get("id") != command_id:
            continue
        if message.get("error"):
            raise RuntimeError(str(message["error"].get("message") or message["error"]))
        return message.get("result") or {}


async def capture_cdp_page_screenshot(
    cdp_url: str,
    preferred_url: str,
    *,
    project_id: str,
    target_id: str = "",
    task_id: str = "",
    source: str = "web_tagging",
) -> dict[str, Any]:
    """截取 Agent 当前页面并返回稳定的鉴权 OSS 引用。"""
    import websockets

    async with websockets.connect(
        cdp_url,
        open_timeout=5,
        close_timeout=2,
        max_size=16 * 1024 * 1024,
    ) as websocket:
        command_id = 0

        async def _command(
            method: str,
            *,
            params: dict[str, Any] | None = None,
            session_id: str = "",
        ) -> dict[str, Any]:
            nonlocal command_id
            command_id += 1
            return await _cdp_command(
                websocket,
                command_id,
                method,
                params=params,
                session_id=session_id,
            )

        targets_result = await _command("Target.getTargets")
        target = _select_page_target(targets_result.get("targetInfos") or [], preferred_url)
        created_target_id = ""
        if not target:
            created = await _command(
                "Target.createTarget",
                params={"url": "about:blank"},
            )
            created_target_id = str(created.get("targetId") or "")
            if not created_target_id:
                raise RuntimeError("无法创建截图页面")
            target = {"targetId": created_target_id, "url": preferred_url}
        attached = await _command(
            "Target.attachToTarget",
            params={"targetId": target["targetId"], "flatten": True},
        )
        session_id = str(attached.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("无法附加到浏览器页面")
        captured_url = str(target.get("url") or preferred_url)
        try:
            await _command("Page.enable", session_id=session_id)
            if created_target_id:
                navigated = await _command(
                    "Page.navigate",
                    params={"url": preferred_url},
                    session_id=session_id,
                )
                if navigated.get("errorText"):
                    raise RuntimeError(str(navigated["errorText"]))
                for _ in range(16):
                    await asyncio.sleep(0.5)
                    state = await _command(
                        "Runtime.evaluate",
                        params={"expression": "document.readyState", "returnByValue": True},
                        session_id=session_id,
                    )
                    ready_state = str(
                        ((state.get("result") or {}).get("value") or "")
                    )
                    if ready_state in {"interactive", "complete"}:
                        break
            await _command("Page.bringToFront", session_id=session_id)
            try:
                location = await _command(
                    "Runtime.evaluate",
                    params={"expression": "location.href", "returnByValue": True},
                    session_id=session_id,
                )
                captured_url = str(
                    ((location.get("result") or {}).get("value") or captured_url)
                )
            except Exception:
                pass
            metrics = await _command("Page.getLayoutMetrics", session_id=session_id)
            captured = await _command(
                "Page.captureScreenshot",
                params={
                    "format": "png",
                    "fromSurface": True,
                    "captureBeyondViewport": False,
                },
                session_id=session_id,
            )
            screenshot = base64.b64decode(str(captured.get("data") or ""), validate=True)
            if not screenshot.startswith(b"\x89PNG") or len(screenshot) < 1024:
                raise RuntimeError("浏览器返回的截图无效")
        finally:
            if created_target_id:
                try:
                    await _command(
                        "Target.closeTarget",
                        params={"targetId": created_target_id},
                    )
                except Exception:
                    pass

    digest = hashlib.sha256(screenshot).hexdigest()
    identity = hashlib.sha256(
        f"{source}:{project_id}:{target_id}:{preferred_url}:{digest}".encode("utf-8")
    ).hexdigest()[:24]
    object_id = f"wss_{identity}"
    viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
    captured_at = datetime.now(timezone.utc).isoformat()
    storage = await get_object_storage()
    stored = await storage.store_bytes(
        screenshot,
        kind="web_page_screenshot",
        filename=f"{object_id}.png",
        object_id=object_id,
        content_type="image/png",
        project_id=project_id,
        subject_id=target_id,
        source=source,
        source_id=task_id,
        meta={
            "url": preferred_url,
            "captured_url": captured_url,
            "target_id": target_id,
            "task_id": task_id,
            "width": int(float(viewport.get("clientWidth") or 0)),
            "height": int(float(viewport.get("clientHeight") or 0)),
            "captured_at": captured_at,
        },
    )
    return {
        "screenshot_object_id": stored["object_id"],
        "screenshot_url": f"/api/v1/storage/objects/{stored['object_id']}/content",
        "screenshot_captured_url": captured_url,
        "screenshot_captured_at": captured_at,
        "screenshot_width": int(float(viewport.get("clientWidth") or 0)),
        "screenshot_height": int(float(viewport.get("clientHeight") or 0)),
    }
