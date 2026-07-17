"""
多格式 AI 产物下载路由 — 受登录鉴权。

AI 中枢工具生成的文件通过稳定 artifact_id 下载；文件路径来自元信息，
校验落在产物目录内，避免路径穿越。
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse

from api.auth import User, get_current_active_user
from api.dao import artifacts as artifacts_dao
from api.db.mongodb import get_db

router = APIRouter(dependencies=[Depends(get_current_active_user)])

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_CONTENT_TYPE_RE = re.compile(
    r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+"
    r"(?:\s*;\s*charset=[A-Za-z0-9._-]+)?$"
)


def _check_owner(doc: dict, user: User) -> None:
    artifact_owner = str(doc.get("owner") or "")
    username = getattr(user, "username", "") or ""
    if artifact_owner and artifact_owner != username and not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="无权访问该产物")


def _public_meta(doc: dict, *, include_content: bool = False) -> dict:
    result = dict(doc)
    result.pop("file_path", None)
    if not include_content and isinstance(result.get("meta"), dict):
        result["meta"] = dict(result["meta"])
        result["meta"].pop("content", None)
    return result


def _artifact_content_type(doc: dict) -> str:
    configured = str(doc.get("content_type") or "").strip()
    if configured and _CONTENT_TYPE_RE.fullmatch(configured):
        return configured
    filename = str(doc.get("filename") or "")
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or (_DOCX_MEDIA if filename.lower().endswith(".docx") else "application/octet-stream")


@router.get("")
async def list_artifacts(
    current_user: User = Depends(get_current_active_user),
    kind: str = Query(default=""),
    conversation_id: str = Query(default=""),
    project_id: str = Query(default=""),
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    """列出当前用户的 AI 产物，可按会话/项目/类型过滤。"""
    owner = getattr(current_user, "username", "") or ""
    if scope == "all" and getattr(current_user, "is_admin", False):
        owner = ""
    items = await artifacts_dao.list_artifacts(
        get_db(),
        owner=owner,
        kind=kind.strip(),
        conversation_id=conversation_id.strip(),
        project_id=project_id.strip(),
        limit=limit,
    )
    return {"items": [_public_meta(item) for item in items], "total": len(items)}


@router.get("/{artifact_id}")
async def get_artifact_meta(
    artifact_id: str,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """获取产物元信息。"""
    doc = await artifacts_dao.get_artifact(get_db(), artifact_id)
    if not doc:
        raise HTTPException(status_code=404, detail="产物不存在")
    _check_owner(doc, current_user)
    return _public_meta(doc, include_content=True)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    direct: bool = Query(default=False),
    current_user: User = Depends(get_current_active_user),
):
    """下载产物文件（仅限登录用户）。"""
    doc = await artifacts_dao.get_artifact(get_db(), artifact_id)
    if not doc:
        raise HTTPException(status_code=404, detail="产物不存在")
    _check_owner(doc, current_user)

    filename = str(doc.get("filename") or artifact_id)
    suffix = re.sub(r"[^A-Za-z0-9.]", "", Path(filename).suffix)[:12]
    ascii_fallback = f"{artifact_id}{suffix}"
    content_type = _artifact_content_type(doc)
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    storage_object_id = str(doc.get("storage_object_id") or "")
    if storage_object_id:
        from api.storage import get_object_storage

        try:
            access = await (await get_object_storage()).read_access(
                storage_object_id,
                filename=filename,
                content_type=content_type,
            )
        except Exception as exc:
            raise HTTPException(status_code=404, detail="文件不存在") from exc
        if access.mode == "redirect":
            if direct:
                return {"url": access.url, "filename": filename}
            return RedirectResponse(
                access.url,
                status_code=307,
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            )
        if access.path and access.path.is_file():
            return FileResponse(
                access.path,
                media_type=content_type,
                headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff"},
            )

    file_path = Path(doc.get("file_path", "")).resolve()
    root = artifacts_dao.artifacts_dir()
    try:
        file_path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(file_path, media_type=content_type, headers={
        "Content-Disposition": disposition,
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    })
