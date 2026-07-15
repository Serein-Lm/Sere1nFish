"""CosyVoice 声音复刻 API — 音色管理 / 复刻输出 / 进度回顾"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService, SpeechSynthesizer
from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.auth import get_current_active_user, require_admin, User
from api.db.mongodb import get_db
from api.dao import voice as voice_dao
from api.services.runtime_config import get_runtime_app_config, get_runtime_config_section
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.voice")

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads" / "voice"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma"}
MAX_AUDIO_SIZE = 50 * 1024 * 1024  # 50MB


def _public_url(request: Request, path: str) -> str:
    """Build a URL that external Aliyun services can fetch through nginx."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return str(request.url_for("get_uploaded_file", filename=Path(path).name))
    return f"{proto}://{host}{path}"


# ==================== 配置 ====================

async def _cfg() -> dict:
    app_config = await get_runtime_app_config()
    cv = await get_runtime_config_section("cosyvoice")
    bailian = await get_runtime_config_section("bailian")
    rt = app_config.runtime
    api_key = cv.get("api_key") or bailian.get("api_key") or rt.api_key
    if not api_key:
        raise HTTPException(500, "数据库 bailian.api_key/runtime.api_key 未配置")

    region = (cv.get("region") or bailian.get("region") or "beijing").strip().lower()
    workspace_id = cv.get("workspace_id") or bailian.get("workspace_id")
    base_http = cv.get("base_http") or bailian.get("base_http")
    base_ws = cv.get("base_ws") or bailian.get("base_ws")

    if not base_http or not base_ws:
        if workspace_id and region == "singapore":
            base_http = base_http or f"https://{workspace_id}.ap-southeast-1.maas.aliyuncs.com/api/v1"
            base_ws = base_ws or f"wss://{workspace_id}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference"
        elif workspace_id:
            base_http = base_http or f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1"
            base_ws = base_ws or f"wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        elif region == "singapore":
            base_http = base_http or "https://dashscope-intl.aliyuncs.com/api/v1"
            base_ws = base_ws or "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
        else:
            base_http = base_http or "https://dashscope.aliyuncs.com/api/v1"
            base_ws = base_ws or "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

    return {
        "api_key": api_key,
        "model": cv.get("model", "cosyvoice-v3.5-plus"),
        "region": region,
        "workspace_id": workspace_id,
        "prefix": cv.get("prefix", "sere1nfish"),
        "language_hints": cv.get("language_hints", ["zh"]),
        "max_prompt_audio_length": cv.get("max_prompt_audio_length", 10.0),
        "enable_preprocess": cv.get("enable_preprocess", False),
        "base_http": base_http,
        "base_ws": base_ws,
    }


async def _init_sdk() -> dict:
    c = await _cfg()
    dashscope.api_key = c["api_key"]
    dashscope.base_http_api_url = c["base_http"]
    dashscope.base_websocket_api_url = c["base_ws"]
    return c


# ==================== 请求/响应模型 ====================

class VoiceCreateReq(BaseModel):
    url: str = Field(..., description="音频文件公网 URL（wav/mp3/flac/m4a，3-30 秒）")
    prefix: str | None = Field(None, max_length=10, pattern=r"^[a-zA-Z0-9]*$",
                                description="音色名前缀（仅字母数字，≤10 字符）")
    language_hints: list[str] | None = Field(None, description="语种提示 zh/en/ja/ko/…")
    max_prompt_audio_length: float | None = Field(None, ge=3.0, le=30.0, description="参考音频最大时长(秒)")
    enable_preprocess: bool | None = Field(None, description="开启降噪/增强/音量规整")

    model_config = {"json_schema_extra": {"examples": [{
        "url": "https://oss.example.com/audio/sample.wav",
        "prefix": "user01",
    }]}}


class VoiceCreateResp(BaseModel):
    voice_id: str
    model: str
    request_id: str | None = None

    model_config = {"json_schema_extra": {"examples": [{
        "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
        "model": "cosyvoice-v3.5-plus",
        "request_id": "req-xxxx",
    }]}}


class VoiceUpdateReq(BaseModel):
    url: str = Field(..., description="新的音频文件 URL")
    language_hints: list[str] | None = None
    max_prompt_audio_length: float | None = Field(None, ge=3.0, le=30.0)
    enable_preprocess: bool | None = None


class SynthesizeReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="待合成文本")
    voice_id: str = Field(..., description="音色 ID")
    model: str | None = Field(None, description="合成模型（需与创建音色时一致，不传用配置默认值）")

    model_config = {"json_schema_extra": {"examples": [{
        "text": "你好，这是一段测试语音",
        "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
    }]}}


class PageResp(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


# ==================== 零、文件上传 ====================

@router.post("/upload", summary="上传音频文件")
async def upload_audio(
    request: Request,
    file: UploadFile = File(..., description="音频文件（wav/mp3/flac/m4a/ogg，≤50MB）"),
    _: User = Depends(get_current_active_user),
):
    """上传本地音频文件，返回可用于创建音色的 URL。

    支持格式: wav, mp3, flac, m4a, ogg, aac, wma（≤50MB）
    """
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_AUDIO_EXTS:
        raise HTTPException(400, f"不支持的音频格式 {ext}，允许: {', '.join(ALLOWED_AUDIO_EXTS)}")

    content = await file.read()
    if len(content) > MAX_AUDIO_SIZE:
        raise HTTPException(400, f"文件超过 {MAX_AUDIO_SIZE // 1024 // 1024}MB 限制")

    object_id = "voice_" + uuid.uuid4().hex
    filename = f"{object_id}{ext}"
    from api.storage import get_object_storage

    storage = await get_object_storage()
    stored = await storage.store_bytes(
        content,
        kind="voice_upload",
        filename=filename,
        object_id=object_id,
        content_type=file.content_type or "application/octet-stream",
        source="voice_upload",
        source_id=filename,
        meta={"original_name": file.filename},
    )
    access = await storage.read_access(
        stored["object_id"],
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        expires_seconds=1800,
    )
    relative_url = f"/api/v1/storage/objects/{stored['object_id']}/content"
    voice_callback_url = f"/api/v1/voice/files/{filename}"
    public_url = access.url if access.mode == "redirect" else _public_url(request, voice_callback_url)
    logger.info(f"音频上传: {file.filename} -> {filename} ({len(content)} bytes)")

    return {
        "filename": filename,
        "original_name": file.filename,
        "size": len(content),
        "url": public_url,
        "relative_url": relative_url,
        "storage_object_id": stored["object_id"],
    }


@router.get("/files/{filename}", summary="获取已上传的音频文件")
async def get_uploaded_file(filename: str):
    """返回已上传的音频文件（供 CosyVoice API 访问）。"""
    from api.dao import storage_objects as storage_dao
    from api.storage import get_object_storage

    stored = await storage_dao.get_by_source(
        get_db(),
        source="voice_upload",
        source_id=filename,
    )
    if stored:
        access = await (await get_object_storage()).read_access(
            stored["object_id"],
            filename=filename,
            content_type=stored.get("content_type") or "application/octet-stream",
            expires_seconds=300,
        )
        if access.mode == "redirect":
            return RedirectResponse(access.url, status_code=307)
        if access.path and access.path.is_file():
            return Response(content=access.path.read_bytes(), media_type=stored.get("content_type"))

    filepath = UPLOAD_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(404, "文件不存在")

    ext = filepath.suffix.lower()
    mime_map = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
        ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".aac": "audio/aac",
        ".wma": "audio/x-ms-wma",
    }
    media_type = mime_map.get(ext, "application/octet-stream")

    data = filepath.read_bytes()
    return Response(content=data, media_type=media_type)


# ==================== 一、音色管理 ====================

@router.post("/voices", response_model=VoiceCreateResp, summary="创建复刻音色")
async def create_voice(body: VoiceCreateReq, _: User = Depends(get_current_active_user)):
    """传入音频公网 URL 创建专属音色，返回 voice_id。

    注意: 每次调用创建新音色，达到配额上限后不可再创建。
    """
    c = await _init_sdk()

    prefix = body.prefix or c["prefix"]
    hints = body.language_hints or c["language_hints"]
    max_len = body.max_prompt_audio_length or c["max_prompt_audio_length"]
    preprocess = body.enable_preprocess if body.enable_preprocess is not None else c["enable_preprocess"]

    try:
        svc = VoiceEnrollmentService()
        voice_id = await asyncio.to_thread(
            svc.create_voice,
            target_model=c["model"], prefix=prefix, url=body.url,
            language_hints=hints, max_prompt_audio_length=max_len,
            enable_preprocess=preprocess,
        )
        req_id = svc.get_last_request_id()
    except Exception as e:
        logger.error(f"创建音色失败: {e}")
        raise HTTPException(500, f"创建音色失败: {e}")

    db = get_db()
    await voice_dao.save_clone(
        db, voice_id=voice_id, model=c["model"], prefix=prefix,
        url=body.url, language_hints=hints, request_id=req_id,
    )
    logger.info(f"音色创建: voice_id={voice_id}")

    return VoiceCreateResp(voice_id=voice_id, model=c["model"], request_id=req_id)


@router.get("/voices", response_model=PageResp, summary="音色列表")
async def list_voices(
    prefix: str | None = Query(None, description="按前缀筛选"),
    status: str | None = Query(None, description="按状态筛选 active/deleted"),
    page: int = Query(0, ge=0, description="页码（从 0 开始）"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    _: User = Depends(get_current_active_user),
):
    """分页查询已创建的音色记录。"""
    db = get_db()
    items, total = await voice_dao.list_clones(
        db, prefix=prefix, status=status, skip=page * page_size, limit=page_size,
    )
    return PageResp(items=items, total=total, page=page, page_size=page_size)


@router.get("/voices/{voice_id}", summary="音色详情")
async def get_voice(voice_id: str, _: User = Depends(get_current_active_user)):
    """查询指定音色的本地记录 + DashScope 远端详情。"""
    db = get_db()
    local = await voice_dao.get_clone(db, voice_id)

    await _init_sdk()
    try:
        svc = VoiceEnrollmentService()
        remote = await asyncio.to_thread(svc.query_voice, voice_id=voice_id)
    except Exception:
        remote = None

    if not local and not remote:
        raise HTTPException(404, f"音色 {voice_id} 不存在")

    return {"local": local, "remote": remote}


@router.put("/voices/{voice_id}", summary="更新音色音频")
async def update_voice(
    voice_id: str, body: VoiceUpdateReq,
    _: User = Depends(get_current_active_user),
):
    """用新音频替换已有音色。voice_id 不变。"""
    c = await _init_sdk()

    hints = body.language_hints or c["language_hints"]
    max_len = body.max_prompt_audio_length or c["max_prompt_audio_length"]
    preprocess = body.enable_preprocess if body.enable_preprocess is not None else c["enable_preprocess"]

    try:
        svc = VoiceEnrollmentService()
        await asyncio.to_thread(
            svc.update_voice, voice_id=voice_id, url=body.url,
            language_hints=hints, max_prompt_audio_length=max_len,
            enable_preprocess=preprocess,
        )
        req_id = svc.get_last_request_id()
    except Exception as e:
        err = str(e)
        if "not found" in err.lower() or "not exist" in err.lower():
            raise HTTPException(404, f"音色 {voice_id} 不存在")
        raise HTTPException(500, f"更新音色失败: {err}")

    logger.info(f"音色更新: voice_id={voice_id}")
    return {"ok": True, "voice_id": voice_id, "request_id": req_id}


@router.delete("/voices/{voice_id}", summary="删除音色")
async def delete_voice(voice_id: str, _: User = Depends(require_admin)):
    """永久删除音色（管理员权限）。同步删除 DashScope 远端和本地记录。"""
    await _init_sdk()

    try:
        svc = VoiceEnrollmentService()
        await asyncio.to_thread(svc.delete_voice, voice_id=voice_id)
    except Exception as e:
        err = str(e)
        if "not found" not in err.lower() and "not exist" not in err.lower():
            raise HTTPException(500, f"删除音色失败: {err}")

    db = get_db()
    await voice_dao.delete_clone(db, voice_id)
    logger.info(f"音色删除: voice_id={voice_id}")
    return {"ok": True, "voice_id": voice_id}


# ==================== 二、复刻输出（语音合成） ====================

@router.post(
    "/synthesize",
    summary="语音合成",
    responses={200: {"content": {"audio/mpeg": {}}, "description": "MP3 音频二进制流"}},
)
async def synthesize(body: SynthesizeReq, _: User = Depends(get_current_active_user)):
    """使用复刻音色将文本合成为 MP3 音频。

    返回 audio/mpeg 二进制流，前端可直接用 `<audio>` 播放。
    响应 Header 包含 X-Request-Id 和 X-Record-Id。
    """
    c = await _init_sdk()
    model = body.model or c["model"]

    db = get_db()
    record_id = await voice_dao.create_synthesis_record(
        db, voice_id=body.voice_id, text=body.text, model=model,
    )

    try:
        synth = SpeechSynthesizer(model=model, voice=body.voice_id)
        audio = await asyncio.to_thread(synth.call, body.text)

        if not audio:
            await voice_dao.fail_synthesis_record(db, record_id, "合成返回空数据")
            raise HTTPException(500, "语音合成返回空数据")

        req_id = synth.get_last_request_id()
        delay = synth.get_first_package_delay()

        await voice_dao.complete_synthesis_record(
            db, record_id,
            audio_bytes=len(audio),
            first_pkg_delay_ms=delay or 0,
            request_id=req_id,
        )

        logger.info(f"合成完成: record={record_id}, bytes={len(audio)}, delay={delay}ms")

        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "X-Request-Id": req_id or "",
                "X-Record-Id": record_id,
                "X-First-Package-Delay-Ms": str(delay or 0),
                "Content-Disposition": 'inline; filename="speech.mp3"',
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        await voice_dao.fail_synthesis_record(db, record_id, str(e))
        logger.error(f"合成失败: {e}")
        raise HTTPException(500, f"语音合成失败: {e}")


# ==================== 三、进度回顾（合成历史） ====================

@router.get("/records", response_model=PageResp, summary="合成记录列表")
async def list_records(
    voice_id: str | None = Query(None, description="按音色 ID 筛选"),
    status: str | None = Query(None, description="按状态筛选 processing/completed/failed"),
    page: int = Query(0, ge=0),
    page_size: int = Query(20, ge=1, le=100),
    _: User = Depends(get_current_active_user),
):
    """分页查询语音合成历史记录。支持按音色和状态筛选。"""
    db = get_db()
    items, total = await voice_dao.list_synthesis_records(
        db, voice_id=voice_id, status=status,
        skip=page * page_size, limit=page_size,
    )
    return PageResp(items=items, total=total, page=page, page_size=page_size)


@router.get("/records/{record_id}", summary="合成记录详情")
async def get_record(record_id: str, _: User = Depends(get_current_active_user)):
    """查询单条合成记录的详细信息（状态、耗时、文本等）。"""
    db = get_db()
    rec = await voice_dao.get_synthesis_record(db, record_id)
    if not rec:
        raise HTTPException(404, f"合成记录 {record_id} 不存在")
    return rec
