"""百炼声音复刻 API — 音色管理 / 实时合成 / 进度回顾。"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from api.auth import get_current_active_user, require_admin, User
from api.db.mongodb import get_db
from api.dao import voice as voice_dao
from api.services.voice_runtime import (
    VoiceConfigurationError,
    VoiceModelMismatchError,
    VoiceProviderError,
    get_voice_runtime_service,
)
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.voice")

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads" / "voice"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".m4a"}
MAX_AUDIO_SIZE = 10 * 1024 * 1024


def _public_url(request: Request, path: str) -> str:
    """Build a URL that external Aliyun services can fetch through nginx."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return str(request.url_for("get_uploaded_file", filename=Path(path).name))
    return f"{proto}://{host}{path}"


# ==================== 请求/响应模型 ====================

class VoiceCreateReq(BaseModel):
    url: str = Field(..., description="音频文件公网 URL（wav/mp3/m4a，建议 10-20 秒）")
    prefix: str | None = Field(
        None,
        min_length=1,
        max_length=10,
        pattern=r"^[a-z0-9]+$",
        description="音色名前缀（仅小写字母和数字，1-10 字符）",
    )
    language_hints: list[str] | None = Field(None, description="语种提示 zh/en/ja/ko/…")
    max_prompt_audio_length: float | None = Field(None, ge=3.0, le=60.0, description="参考音频最大时长(秒)")
    enable_preprocess: bool | None = Field(None, description="开启降噪/增强/音量规整")
    authorized_use: bool = Field(
        ...,
        description="确认已获得该声音所有者授权，且仅用于合法合成",
    )

    model_config = {"json_schema_extra": {"examples": [{
        "url": "https://oss.example.com/audio/sample.wav",
        "prefix": "user01",
        "authorized_use": True,
    }]}}


class VoiceCreateResp(BaseModel):
    voice_id: str
    model: str
    request_id: str | None = None

    model_config = {"json_schema_extra": {"examples": [{
        "voice_id": "qwen-audio-3.0-tts-flash-user01-a1b2c3d4",
        "model": "qwen-audio-3.0-tts-flash",
        "request_id": "req-xxxx",
    }]}}


class VoiceUpdateReq(BaseModel):
    url: str = Field(..., description="新的音频文件 URL")
    language_hints: list[str] | None = None
    max_prompt_audio_length: float | None = Field(None, ge=3.0, le=60.0)
    enable_preprocess: bool | None = None


class SynthesizeReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="待合成文本")
    voice_id: str = Field(..., description="音色 ID")
    model: str | None = Field(None, description="合成模型（需与创建音色时一致，不传用配置默认值）")
    instruction: str | None = Field(
        None,
        max_length=128,
        description="语气、方言或表达方式指令",
    )

    model_config = {"json_schema_extra": {"examples": [{
        "text": "你好，这是一段测试语音",
        "voice_id": "qwen-audio-3.0-tts-flash-user01-a1b2c3d4",
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
    file: UploadFile = File(..., description="音频文件（wav/mp3/m4a，≤10MB）"),
    _: User = Depends(get_current_active_user),
):
    """上传本地音频文件，返回可用于创建音色的 URL。

    支持格式: wav, mp3, m4a（≤10MB）
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
    if not filename or Path(filename).name != filename:
        raise HTTPException(400, "文件名无效")

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
async def create_voice(
    body: VoiceCreateReq,
    user: User = Depends(get_current_active_user),
):
    """传入音频公网 URL 创建专属音色，返回 voice_id。

    注意: 每次调用创建新音色，达到配额上限后不可再创建。
    """
    if not body.authorized_use:
        raise HTTPException(403, "创建复刻音色前必须确认已获得声音所有者授权")
    try:
        result = await get_voice_runtime_service().create_voice(
            get_db(),
            url=body.url,
            prefix=body.prefix,
            language_hints=body.language_hints,
            max_prompt_audio_length=body.max_prompt_audio_length,
            enable_preprocess=body.enable_preprocess,
            authorized_by=user.username,
        )
    except VoiceConfigurationError as exc:
        logger.error("声音复刻配置不可用: %s", exc)
        raise HTTPException(500, str(exc)) from exc
    except VoiceProviderError as exc:
        logger.error("创建音色失败: %s", exc)
        raise HTTPException(502, str(exc)) from exc

    logger.info("音色创建: voice_id=%s model=%s", result.voice_id, result.model)
    return VoiceCreateResp(
        voice_id=result.voice_id,
        model=result.model,
        request_id=result.request_id,
    )


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
    try:
        detail = await get_voice_runtime_service().get_voice_detail(
            get_db(),
            voice_id,
        )
    except VoiceConfigurationError as exc:
        raise HTTPException(500, str(exc)) from exc
    if not detail:
        raise HTTPException(404, f"音色 {voice_id} 不存在")
    return detail


@router.put("/voices/{voice_id}", summary="更新音色音频")
async def update_voice(
    voice_id: str, body: VoiceUpdateReq,
    _: User = Depends(get_current_active_user),
):
    """用新音频替换已有音色。voice_id 不变。"""
    try:
        request_id = await get_voice_runtime_service().update_voice(
            get_db(),
            voice_id=voice_id,
            url=body.url,
        )
    except (VoiceConfigurationError, VoiceProviderError) as exc:
        err = str(exc)
        if "not found" in err.lower() or "not exist" in err.lower():
            raise HTTPException(404, f"音色 {voice_id} 不存在")
        raise HTTPException(502, f"更新音色失败: {err}") from exc

    logger.info(f"音色更新: voice_id={voice_id}")
    return {"ok": True, "voice_id": voice_id, "request_id": request_id}


@router.delete("/voices/{voice_id}", summary="删除音色")
async def delete_voice(voice_id: str, _: User = Depends(require_admin)):
    """永久删除音色（管理员权限）。同步删除 DashScope 远端和本地记录。"""
    try:
        await get_voice_runtime_service().delete_voice(get_db(), voice_id)
    except (VoiceConfigurationError, VoiceProviderError) as exc:
        err = str(exc)
        if "not found" not in err.lower() and "not exist" not in err.lower():
            raise HTTPException(502, f"删除音色失败: {err}") from exc

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
    try:
        record_id, result = await get_voice_runtime_service().synthesize(
            get_db(),
            text=body.text,
            voice_id=body.voice_id,
            requested_model=body.model,
            instruction=body.instruction,
        )
    except VoiceModelMismatchError as exc:
        raise HTTPException(409, str(exc)) from exc
    except VoiceConfigurationError as exc:
        raise HTTPException(500, str(exc)) from exc
    except VoiceProviderError as exc:
        logger.error("合成失败: %s", exc)
        raise HTTPException(502, str(exc)) from exc

    return Response(
        content=result.audio,
        media_type="audio/mpeg",
        headers={
            "X-Request-Id": result.request_id or "",
            "X-Record-Id": record_id,
            "X-First-Package-Delay-Ms": str(result.first_package_delay_ms),
            "X-Voice-Model": result.model,
            "X-Synthetic-Media": "true",
            "Content-Disposition": 'inline; filename="speech.mp3"',
        },
    )


@router.post(
    "/synthesize/stream",
    summary="实时流式语音合成",
    responses={
        200: {
            "content": {"audio/pcm": {}},
            "description": "24kHz 单声道 16-bit little-endian PCM 分片流",
        }
    },
)
async def synthesize_stream(
    body: SynthesizeReq,
    _: User = Depends(get_current_active_user),
):
    """使用预热 WebSocket 连接，将复刻音色 PCM 分片直接透传给客户端。"""
    try:
        handle = await get_voice_runtime_service().stream_synthesis(
            get_db(),
            text=body.text,
            voice_id=body.voice_id,
            requested_model=body.model,
            instruction=body.instruction,
        )
    except VoiceModelMismatchError as exc:
        raise HTTPException(409, str(exc)) from exc
    except VoiceConfigurationError as exc:
        raise HTTPException(500, str(exc)) from exc
    except VoiceProviderError as exc:
        raise HTTPException(502, str(exc)) from exc

    return StreamingResponse(
        handle.chunks,
        media_type=f"audio/pcm;rate={handle.sample_rate};channels=1",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Record-Id": handle.record_id,
            "X-Voice-Model": handle.model,
            "X-Audio-Encoding": "pcm_s16le",
            "X-Audio-Sample-Rate": str(handle.sample_rate),
            "X-Audio-Channels": "1",
            "X-Synthetic-Media": "true",
        },
    )


# ==================== 三、进度回顾（合成历史） ====================

@router.get("/records", response_model=PageResp, summary="合成记录列表")
async def list_records(
    voice_id: str | None = Query(None, description="按音色 ID 筛选"),
    status: str | None = Query(
        None,
        description="按状态筛选 processing/completed/failed/cancelled",
    ),
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
