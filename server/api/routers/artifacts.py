"""
产物（Word 文档等）下载路由 — 受登录鉴权。

AI 中枢工具生成的 .docx 通过稳定 artifact_id 下载；文件路径来自元信息，
校验落在产物目录内，避免路径穿越。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.auth import get_current_active_user
from api.dao import artifacts as artifacts_dao
from api.db.mongodb import get_db

router = APIRouter(dependencies=[Depends(get_current_active_user)])

_DOCX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@router.get("/{artifact_id}")
async def get_artifact_meta(artifact_id: str) -> dict:
    """获取产物元信息。"""
    doc = await artifacts_dao.get_artifact(get_db(), artifact_id)
    if not doc:
        raise HTTPException(status_code=404, detail="产物不存在")
    return doc


@router.get("/{artifact_id}/download")
async def download_artifact(artifact_id: str) -> FileResponse:
    """下载产物文件（仅限登录用户）。"""
    doc = await artifacts_dao.get_artifact(get_db(), artifact_id)
    if not doc:
        raise HTTPException(status_code=404, detail="产物不存在")

    file_path = Path(doc.get("file_path", "")).resolve()
    root = artifacts_dao.artifacts_dir()
    try:
        file_path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    filename = doc.get("filename") or f"{artifact_id}.docx"
    ascii_fallback = f"{artifact_id}.docx"
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return FileResponse(
        file_path,
        media_type=_DOCX_MEDIA,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )
