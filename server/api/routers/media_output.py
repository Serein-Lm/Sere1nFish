"""Secure transient media output endpoints for OBS Browser Source."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from api.auth import User, get_current_active_user
from api.services.media_output import (
    MediaOutputError,
    MediaOutputNotFound,
    MediaOutputPermissionError,
    MediaOutputSubscription,
    get_media_output_service,
)


router = APIRouter()

VIEWER_PUBLIC_SUBPROTOCOL = "sere1nfish-output"
VIEWER_AUTH_SUBPROTOCOL_PREFIX = "sere1nfish.output."


class MediaOutputCreateRequest(BaseModel):
    ttl_seconds: int = Field(default=8 * 3600, ge=15 * 60, le=12 * 3600)


def _viewer_token(websocket: WebSocket) -> str:
    protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
    for protocol in protocols:
        value = protocol.strip()
        if value.startswith(VIEWER_AUTH_SUBPROTOCOL_PREFIX):
            return value[len(VIEWER_AUTH_SUBPROTOCOL_PREFIX) :]
    return ""


def _http_error(exc: MediaOutputError) -> HTTPException:
    if isinstance(exc, (MediaOutputNotFound, MediaOutputPermissionError)):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/sessions")
async def create_media_output_session(
    body: MediaOutputCreateRequest,
    user: User = Depends(get_current_active_user),
):
    try:
        return await get_media_output_service().create(
            user.username,
            ttl_seconds=body.ttl_seconds,
        )
    except MediaOutputError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}")
async def get_media_output_session(
    session_id: str,
    user: User = Depends(get_current_active_user),
):
    try:
        return await get_media_output_service().get(session_id, user.username)
    except MediaOutputError as exc:
        raise _http_error(exc) from exc


@router.delete("/sessions/{session_id}")
async def delete_media_output_session(
    session_id: str,
    user: User = Depends(get_current_active_user),
):
    try:
        await get_media_output_service().delete(session_id, user.username)
        return {"ok": True, "session_id": session_id}
    except MediaOutputError as exc:
        raise _http_error(exc) from exc


async def _watch_sender(
    websocket: WebSocket,
    subscription: MediaOutputSubscription,
) -> None:
    while True:
        packet = await subscription.queue.get()
        if packet is None:
            return
        await websocket.send_bytes(packet)


async def _watch_receiver(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return


@router.websocket("/watch")
async def watch_media_output(websocket: WebSocket) -> None:
    token = _viewer_token(websocket)
    if not token:
        await websocket.accept(subprotocol=VIEWER_PUBLIC_SUBPROTOCOL)
        await websocket.close(code=4401)
        return
    service = get_media_output_service()
    try:
        subscription = await service.subscribe(token)
    except MediaOutputError:
        await websocket.accept(subprotocol=VIEWER_PUBLIC_SUBPROTOCOL)
        await websocket.close(code=4404)
        return

    tasks: set[asyncio.Task[None]] = set()
    try:
        await websocket.accept(subprotocol=VIEWER_PUBLIC_SUBPROTOCOL)
        await websocket.send_text(
            json.dumps(
                {
                    "type": "output.ready",
                    "session_id": subscription.session_id,
                    "video": "encoded-image",
                    "audio": "pcm_s16le/24000/mono",
                },
                separators=(",", ":"),
            )
        )
        tasks = {
            asyncio.create_task(_watch_sender(websocket, subscription)),
            asyncio.create_task(_watch_receiver(websocket)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.unsubscribe(subscription)
        with suppress(Exception):
            await websocket.close(code=1000)


_OBS_VIEW_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Sere1nFish Remote Output</title>
  <style>
    html,body{width:100%;height:100%;margin:0;overflow:hidden;background:#000}
    body{display:grid;place-items:center;font-family:system-ui,sans-serif}
    #frame{display:block;width:100%;height:100%;object-fit:contain;background:#000}
    #status{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);max-width:80%;
      color:#fff;background:rgba(0,0,0,.76);padding:10px 14px;border-radius:4px;text-align:center}
    #status:empty{display:none}
  </style>
</head>
<body>
  <img id="frame" alt="">
  <div id="status">正在连接远端输出...</div>
  <script>
  (() => {
    const VIDEO = 1;
    const AUDIO = 2;
    const token = location.hash.slice(1);
    const frame = document.getElementById('frame');
    const status = document.getElementById('status');
    let socket = null;
    let reconnectTimer = 0;
    let reconnectDelay = 500;
    let imageUrl = '';
    let audioContext = null;
    let nextAudioAt = 0;
    const audioSources = new Set();

    const setStatus = text => { status.textContent = text; };
    const ensureAudio = async () => {
      if (!audioContext) audioContext = new AudioContext({latencyHint:'interactive'});
      if (audioContext.state === 'suspended') await audioContext.resume().catch(() => {});
      return audioContext;
    };
    const playPcm = async bytes => {
      if (!bytes.byteLength || bytes.byteLength % 2) return;
      const context = await ensureAudio();
      if (context.state !== 'running') {
        setStatus('请在 OBS 浏览器源属性中启用音频，或点击画面恢复音频');
        return;
      }
      const count = bytes.byteLength / 2;
      const buffer = context.createBuffer(1, count, 24000);
      const channel = buffer.getChannelData(0);
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      for (let i = 0; i < count; i += 1) channel[i] = view.getInt16(i * 2, true) / 32768;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      source.onended = () => audioSources.delete(source);
      audioSources.add(source);
      nextAudioAt = Math.max(nextAudioAt, context.currentTime + 0.025);
      source.start(nextAudioAt);
      nextAudioAt += buffer.duration;
    };
    const showFrame = bytes => {
      const previous = imageUrl;
      imageUrl = URL.createObjectURL(new Blob([bytes]));
      frame.onload = () => {
        if (previous) URL.revokeObjectURL(previous);
        setStatus('');
      };
      frame.src = imageUrl;
    };
    const scheduleReconnect = () => {
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(5000, reconnectDelay * 1.7);
    };
    const connect = () => {
      if (!token) {
        setStatus('OBS 输出地址缺少访问凭据');
        return;
      }
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(protocol + '//' + location.host + '/api/v1/media-output/watch',
        ['sere1nfish-output', 'sere1nfish.output.' + token]);
      socket.binaryType = 'arraybuffer';
      socket.onopen = () => {
        reconnectDelay = 500;
        setStatus('等待远端画面...');
      };
      socket.onmessage = event => {
        if (typeof event.data === 'string') return;
        const packet = new Uint8Array(event.data);
        if (packet.byteLength < 2) return;
        const payload = packet.subarray(1);
        if (packet[0] === VIDEO) showFrame(payload);
        else if (packet[0] === AUDIO) void playPcm(payload);
      };
      socket.onerror = () => socket && socket.close();
      socket.onclose = event => {
        socket = null;
        const expired = event.code === 4401 || event.code === 4404;
        setStatus(expired ? 'OBS 输出地址已失效' : '远端输出已断开，正在重连...');
        if (!expired) scheduleReconnect();
      };
    };
    document.addEventListener('pointerdown', () => void ensureAudio(), {passive:true});
    addEventListener('beforeunload', () => {
      clearTimeout(reconnectTimer);
      if (socket) socket.close();
      if (imageUrl) URL.revokeObjectURL(imageUrl);
      audioSources.forEach(source => { try { source.stop(); } catch {} });
      if (audioContext) void audioContext.close();
    });
    connect();
  })();
  </script>
</body>
</html>"""


@router.get("/view", response_class=HTMLResponse, include_in_schema=False)
async def media_output_view() -> HTMLResponse:
    return HTMLResponse(
        _OBS_VIEW_HTML,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; img-src blob:; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self' wss: ws:"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
