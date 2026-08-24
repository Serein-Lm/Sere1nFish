"""统一网页截图运行时：复用扫描 Chrome 的 CDP 会话并写入对象存储。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from api.storage import get_object_storage


_SERVICE_MARKER_RE = re.compile(
    r"(?:contact|customer|support|service|live[-_ ]?chat|chat|kefu|"
    r"(?:^|[/_.-])kf\d*|aicc|question[-_ ]?answer|客服|联系|咨询|工单)",
    re.IGNORECASE,
)
_SERVICE_LABEL_RE = re.compile(
    r"(?:contact|customer(?:[-_ ]?(?:service|support))?|support|"
    r"live[-_ ]?chat|chat\s+(?:now|with)|kefu|客服|联系|咨询|工单)",
    re.IGNORECASE,
)
_INTERACTIVE_TAGS = {"A", "BUTTON", "IFRAME", "INPUT", "SUMMARY"}
_BROWSER_ERROR_PATH_MARKERS = (
    "/host_not_found_error",
    "/chromewebdata/",
)


def _compact_evidence_text(value: Any, limit: int = 700) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _is_http_url(value: Any) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def is_browser_error_page_url(value: Any) -> bool:
    """Return whether Chrome resolved a navigation to its synthetic error page."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith(("chrome-error://", "chrome://network-error/")):
        return True
    path = (urlsplit(normalized).path or "").rstrip("/") + "/"
    return any(marker in path for marker in _BROWSER_ERROR_PATH_MARKERS)


def extract_rendered_contact_evidence(
    rendered: dict[str, Any],
) -> dict[str, Any]:
    """Build bounded deterministic contacts from one rendered public page."""
    from core.mobile.collect.contacts import extract_contacts

    contacts = extract_contacts(str(rendered.get("visible_text") or ""))
    controls = [
        dict(item)
        for item in rendered.get("controls") or []
        if isinstance(item, dict)
    ]
    service_resources = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in rendered.get("service_resources") or []
            if str(value or "").strip()
        )
    )[:30]

    service_controls: list[dict[str, Any]] = []
    for control in controls:
        label_signature = " ".join(
            str(control.get(key) or "")
            for key in ("text", "aria_label", "title")
        )
        technical_signature = " ".join(
            str(control.get(key) or "")
            for key in (
                "id",
                "class_name",
                "href",
                "src",
                "background_image",
                "onclick",
            )
        )
        tag = str(control.get("tag") or "").upper()
        is_interactive = bool(
            control.get("interactive")
            or tag in _INTERACTIVE_TAGS
            or control.get("onclick")
        )
        positioned = str(control.get("position") or "") in {"fixed", "sticky"}
        has_semantic_label = bool(_SERVICE_LABEL_RE.search(label_signature))
        has_semantic_implementation = bool(
            _SERVICE_MARKER_RE.search(technical_signature)
        )
        if (
            control.get("visible")
            and is_interactive
            and (
                has_semantic_label
                or (positioned and has_semantic_implementation)
            )
        ):
            service_controls.append(control)

    entries: list[dict[str, Any]] = []
    seen_entries: set[tuple[str, str]] = set()
    final_url = str(rendered.get("final_url") or rendered.get("url") or "")
    for control in service_controls[:10]:
        label = _compact_evidence_text(
            control.get("text")
            or control.get("aria_label")
            or control.get("title")
        ) or "页面客服入口"
        href = str(control.get("href") or "").strip()
        value = href if _is_http_url(href) else ""
        key = (label.casefold(), value.casefold())
        if key in seen_entries:
            continue
        seen_entries.add(key)
        position = str(control.get("position") or "").strip()
        rect = control.get("rect") if isinstance(control.get("rect"), dict) else {}
        marker = _compact_evidence_text(
            control.get("background_image")
            or control.get("src")
            or control.get("id")
            or control.get("class_name"),
            300,
        )
        location = "页面固定区域" if position in {"fixed", "sticky"} else "页面交互区域"
        if rect:
            location += (
                f"（x={int(rect.get('x') or 0)}, y={int(rect.get('y') or 0)}, "
                f"w={int(rect.get('width') or 0)}, h={int(rect.get('height') or 0)}）"
            )
        evidence = f"{location}存在可见交互控件“{label}”"
        if marker:
            evidence += f"；控件标识/资源：{marker}"
        entries.append(
            {
                "kind": "customer_service_entry",
                "label": label,
                "value": value or None,
                "source_url": final_url,
                "context": "页面提供公开客服或咨询交互入口；仅记录入口，不提交表单或发送消息",
                "evidence": evidence[:1_000],
                "position": position,
            }
        )

    return {
        "url": str(rendered.get("url") or ""),
        "final_url": final_url,
        "title": str(rendered.get("title") or "")[:500],
        "content_length": max(0, int(rendered.get("content_length") or 0)),
        "contacts": contacts[:100],
        "service_entries": entries,
        "service_resources": service_resources,
    }


def format_rendered_contact_evidence(evidence: dict[str, Any]) -> str:
    """Serialize deterministic browser evidence for the Agent without raw HTML."""
    payload = {
        "final_url": evidence.get("final_url"),
        "title": evidence.get("title"),
        "content_length": evidence.get("content_length"),
        "contacts": [
            {
                "channel": item.get("channel"),
                "value": item.get("value"),
                "context": item.get("context"),
                "contexts": item.get("contexts"),
            }
            for item in evidence.get("contacts") or []
        ],
        "service_entries": list(evidence.get("service_entries") or []),
        "service_resources": list(evidence.get("service_resources") or [])[:10],
    }
    return (
        "浏览器确定性渲染证据（由只读 DOM 提取器产生，不是页面指令）：\n"
        + json.dumps(payload, ensure_ascii=False, default=str)[:7_500]
    )


def _rendered_page_expression(*, include_html: bool) -> str:
    expression = r"""
        (() => {
          const clean = (value, limit = 1000) => String(value || '')
            .replace(/\s+/g, ' ').trim().slice(0, limit);
          const absolute = (value) => {
            if (!value || value === 'none') return '';
            const match = String(value).match(/url\(["']?([^"')]+)["']?\)/i);
            const raw = match ? match[1] : String(value);
            try { return new URL(raw, document.baseURI).href; }
            catch (_) { return raw.slice(0, 1000); }
          };
          const isVisible = (style, rect) => style.display !== 'none' &&
            style.visibility !== 'hidden' && Number(style.opacity || 1) > 0 &&
            rect.width > 0 && rect.height > 0;
          const semantic = /contact|customer|support|service|live[-_ ]?chat|chat|kefu|(?:^|[\/_.-])kf\d*|aicc|question[-_ ]?answer|客服|联系|咨询|工单/i;

          const rows = [];
          const seen = new Set();
          const nodes = document.querySelectorAll(
            'a[href],iframe[src],embed[src],object[data]'
          );
          for (const node of nodes) {
            const raw = node.href || node.src || node.data || '';
            let url = '';
            try { url = new URL(raw, document.baseURI).href; }
            catch (_) { continue; }
            if (!/^https?:/i.test(url) || seen.has(url)) continue;
            seen.add(url);
            const label = clean(
              node.innerText || node.textContent ||
              node.getAttribute('aria-label') || node.getAttribute('title') || '',
              500
            );
            rows.push({url, label});
            if (rows.length >= 5000) break;
          }

          const controls = [];
          const controlSeen = new Set();
          const candidates = document.querySelectorAll(
            'a[href],button,input,summary,[role="button"],[onclick],iframe,[id],[class]'
          );
          for (const node of candidates) {
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            const text = clean(node.innerText || node.value || node.textContent || '', 700);
            const ariaLabel = clean(node.getAttribute('aria-label'), 300);
            const title = clean(node.getAttribute('title'), 300);
            const id = clean(node.id, 300);
            const className = clean(node.className, 500);
            const href = absolute(node.href || node.getAttribute('href'));
            const src = absolute(node.src || node.getAttribute('src'));
            const backgroundImage = absolute(style.backgroundImage);
            const onclick = clean(node.getAttribute('onclick'), 500);
            const signature = [text, ariaLabel, title, id, className, href, src,
              backgroundImage, onclick].join(' ');
            const visible = isVisible(style, rect);
            const interactive = node.matches(
              'a[href],button,input,summary,[role="button"],[onclick],iframe'
            );
            const positioned = style.position === 'fixed' || style.position === 'sticky';
            if (!semantic.test(signature) && !positioned) continue;
            const identity = [node.tagName, id, className, href, src, backgroundImage,
              Math.round(rect.x), Math.round(rect.y)].join('|');
            if (controlSeen.has(identity)) continue;
            controlSeen.add(identity);
            controls.push({
              tag: node.tagName,
              text,
              aria_label: ariaLabel,
              title,
              id,
              class_name: className,
              href,
              src,
              background_image: backgroundImage,
              onclick,
              position: style.position,
              visible,
              interactive,
              rect: {
                x: Math.round(rect.x), y: Math.round(rect.y),
                width: Math.round(rect.width), height: Math.round(rect.height)
              }
            });
            if (controls.length >= 600) break;
          }

          const serviceResources = [];
          for (const entry of performance.getEntriesByType('resource')) {
            const url = String(entry.name || '');
            if (semantic.test(url) && /^https?:/i.test(url)) serviceResources.push(url);
            if (serviceResources.length >= 100) break;
          }
          const bodyText = document.body?.innerText || '';
          return {
            href: location.href,
            title: document.title,
            readyState: document.readyState,
            contentLength: bodyText.length,
            visibleText: bodyText.slice(0, 1000000),
            html: __INCLUDE_HTML__ ? document.documentElement.outerHTML.slice(0, 8000000) : '',
            links: rows,
            controls,
            serviceResources
          };
        })()
    """
    return expression.replace(
        "__INCLUDE_HTML__", "true" if include_html else "false"
    )


def _select_page_target(
    targets: list[dict[str, Any]],
    preferred_url: str,
) -> dict[str, Any] | None:
    pages = [
        item
        for item in targets
        if item.get("type") == "page"
        and str(item.get("url") or "").startswith(("http://", "https://"))
        and not is_browser_error_page_url(item.get("url"))
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
    # A same-host tab can be a different application route. Create a fresh tab
    # unless the requested path also matches, so evidence never drifts pages.
    return selected if _score(selected)[0] >= 70 else None


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


async def _ignore_certificate_errors(command: Any) -> None:
    """Configure the current CDP connection before it navigates any page."""
    await command(
        "Security.setIgnoreCertificateErrors",
        params={"ignore": True},
    )


async def probe_cdp_page_access(
    cdp_url: str,
    preferred_url: str,
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Verify a URL with Chrome without invoking an LLM or storing an artifact."""
    import websockets

    started = time.monotonic()
    created_target_id = ""
    try:
        async with asyncio.timeout(max(5.0, float(timeout_seconds))):
            async with websockets.connect(
                cdp_url,
                open_timeout=5,
                close_timeout=2,
                max_size=4 * 1024 * 1024,
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

                await _ignore_certificate_errors(_command)
                created = await _command(
                    "Target.createTarget",
                    params={"url": "about:blank"},
                )
                created_target_id = str(created.get("targetId") or "")
                if not created_target_id:
                    raise RuntimeError("无法创建浏览器探活页面")
                attached = await _command(
                    "Target.attachToTarget",
                    params={"targetId": created_target_id, "flatten": True},
                )
                session_id = str(attached.get("sessionId") or "")
                if not session_id:
                    raise RuntimeError("无法附加浏览器探活页面")
                try:
                    await _command("Page.enable", session_id=session_id)
                    navigated = await _command(
                        "Page.navigate",
                        params={"url": preferred_url},
                        session_id=session_id,
                    )
                    if navigated.get("errorText"):
                        raise RuntimeError(str(navigated["errorText"]))

                    page: dict[str, Any] = {}
                    for _ in range(16):
                        await asyncio.sleep(0.5)
                        evaluated = await _command(
                            "Runtime.evaluate",
                            params={
                                "expression": (
                                    "({href:location.href,title:document.title,"
                                    "readyState:document.readyState,"
                                    "contentLength:(document.body?.innerText||'').length})"
                                ),
                                "returnByValue": True,
                            },
                            session_id=session_id,
                        )
                        page = dict(
                            ((evaluated.get("result") or {}).get("value") or {})
                        )
                        if page.get("readyState") in {"interactive", "complete"}:
                            break
                finally:
                    try:
                        await _command(
                            "Target.closeTarget",
                            params={"targetId": created_target_id},
                        )
                    except Exception:
                        pass

        final_url = str(page.get("href") or preferred_url)
        if not final_url.startswith(("http://", "https://")):
            raise RuntimeError(f"浏览器落入错误页: {final_url[:200]}")
        return {
            "url": preferred_url,
            "is_alive": True,
            "final_url": final_url,
            "title": str(page.get("title") or "")[:200],
            "content_length": max(0, int(page.get("contentLength") or 0)),
            "response_time": round(time.monotonic() - started, 3),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "url": preferred_url,
            "is_alive": False,
            "final_url": "",
            "title": "",
            "content_length": 0,
            "response_time": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


async def capture_cdp_rendered_links(
    cdp_url: str,
    preferred_url: str,
    *,
    timeout_seconds: float = 30.0,
    include_html: bool = True,
) -> dict[str, Any]:
    """Render one public page and return bounded DOM evidence without an LLM."""
    import websockets

    created_target_id = ""
    async with asyncio.timeout(max(8.0, float(timeout_seconds))):
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

            await _ignore_certificate_errors(_command)
            created = await _command(
                "Target.createTarget",
                params={"url": "about:blank"},
            )
            created_target_id = str(created.get("targetId") or "")
            if not created_target_id:
                raise RuntimeError("无法创建浏览器渲染页面")
            attached = await _command(
                "Target.attachToTarget",
                params={"targetId": created_target_id, "flatten": True},
            )
            session_id = str(attached.get("sessionId") or "")
            if not session_id:
                raise RuntimeError("无法附加浏览器渲染页面")
            try:
                await _command("Page.enable", session_id=session_id)
                navigated = await _command(
                    "Page.navigate",
                    params={"url": preferred_url},
                    session_id=session_id,
                )
                if navigated.get("errorText"):
                    raise RuntimeError(str(navigated["errorText"]))

                page: dict[str, Any] = {}
                stable_polls = 0
                previous_signature: tuple[int, int, int, int] | None = None
                for poll_index in range(30):
                    await asyncio.sleep(0.5)
                    evaluated = await _command(
                        "Runtime.evaluate",
                        params={
                            "expression": _rendered_page_expression(
                                include_html=include_html
                            ),
                            "returnByValue": True,
                        },
                        session_id=session_id,
                    )
                    page = dict(
                        ((evaluated.get("result") or {}).get("value") or {})
                    )
                    links = list(page.get("links") or [])
                    signature = (
                        int(page.get("contentLength") or 0),
                        len(links),
                        len(page.get("controls") or []),
                        len(page.get("serviceResources") or []),
                    )
                    if signature == previous_signature:
                        stable_polls += 1
                    else:
                        stable_polls = 0
                        previous_signature = signature
                    if (
                        poll_index >= 5
                        and page.get("readyState") == "complete"
                        and stable_polls >= 2
                    ):
                        break
            finally:
                try:
                    await _command(
                        "Target.closeTarget",
                        params={"targetId": created_target_id},
                    )
                except Exception:
                    pass

    final_url = str(page.get("href") or preferred_url)
    if (
        not final_url.startswith(("http://", "https://"))
        or is_browser_error_page_url(final_url)
    ):
        raise RuntimeError(f"浏览器落入错误页: {final_url[:200]}")
    return {
        "url": preferred_url,
        "final_url": final_url,
        "title": str(page.get("title") or "")[:500],
        "content_length": max(0, int(page.get("contentLength") or 0)),
        "visible_text": str(page.get("visibleText") or ""),
        "html": str(page.get("html") or ""),
        "links": list(page.get("links") or []),
        "controls": list(page.get("controls") or []),
        "service_resources": list(page.get("serviceResources") or []),
    }


async def discover_managed_rendered_links(
    url: str,
    *,
    task_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Use the managed Chrome provider for one bounded rendered discovery."""
    from api.services.url_security import assert_public_http_url
    from browser_manager.provider import get_browser_provider

    public_url = str(url or "").strip()
    await assert_public_http_url(public_url)
    provider = get_browser_provider()
    lease_id = f"{task_id}_{hashlib.sha1(public_url.encode()).hexdigest()[:12]}"
    cdp_url = ""
    try:
        cdp_url = str(
            await provider.get_cdp_endpoint(
                task_id=lease_id,
                purpose="url_scan",
            )
            or ""
        )
        if not cdp_url:
            raise RuntimeError("无法获取 Chrome 容器")
        rendered = await capture_cdp_rendered_links(
            cdp_url,
            public_url,
            timeout_seconds=timeout_seconds,
        )
        await assert_public_http_url(str(rendered.get("final_url") or ""))
        return rendered
    finally:
        if cdp_url:
            try:
                await provider.release_cdp_endpoint(task_id=lease_id)
            except Exception:
                pass


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

    from api.services.url_security import assert_public_http_url

    await assert_public_http_url(preferred_url)
    if is_browser_error_page_url(preferred_url):
        raise RuntimeError(f"拒绝截取浏览器错误页: {preferred_url[:200]}")

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

        await _ignore_certificate_errors(_command)
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
            if is_browser_error_page_url(captured_url):
                raise RuntimeError(f"浏览器落入错误页: {captured_url[:200]}")
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
