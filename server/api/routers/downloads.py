"""Authenticated runtime artifact downloads."""

from __future__ import annotations

import mimetypes
import os
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from api.auth import get_current_active_user


router = APIRouter(dependencies=[Depends(get_current_active_user)])

def _downloads_root() -> Path:
    return Path(os.getenv("DOWNLOADS_ROOT", "/srv/downloads")).resolve()


def _extra_allowed_patterns() -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.getenv("DOWNLOADS_ALLOWED_PATTERNS", "").split(",")
        if item.strip()
    )


def _is_allowed_download(normalized: str) -> bool:
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if parts == ("mobile-agent.apk",):
        return True
    if len(parts) == 4 and parts[:2] == ("mobile", "easytier"):
        filename = parts[3]
        if filename.startswith("easytier-v") and (
            filename.endswith(".apk") or filename.endswith(".apk.sha256")
        ):
            return True
    return any(fnmatchcase(normalized, pattern) for pattern in _extra_allowed_patterns())


def _normalize_download_path(relative_path: str) -> str:
    if "\\" in relative_path:
        raise HTTPException(status_code=404, detail="文件不存在")
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise HTTPException(status_code=404, detail="文件不存在")
    normalized = pure.as_posix()
    if not _is_allowed_download(normalized):
        raise HTTPException(status_code=404, detail="文件不存在")
    return normalized


def _resolve_download_path(relative_path: str) -> Path:
    root = _downloads_root()
    normalized = _normalize_download_path(relative_path)
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


@router.get("/{relative_path:path}")
async def download_file(relative_path: str, direct: bool = Query(default=False)):
    """Download a runtime file only for authenticated users."""
    normalized = _normalize_download_path(relative_path)
    from api.dao import storage_objects as storage_dao
    from api.db.mongodb import get_db
    from api.storage import get_object_storage

    stored = await storage_dao.get_by_relative_path(get_db(), normalized)
    if stored:
        storage = await get_object_storage()
        filename = Path(normalized).name
        if filename.lower().endswith(".apk"):
            fallback = "download.apk"
            disposition = (
                f"attachment; filename=\"{fallback}\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
            return StreamingResponse(
                storage.iter_bytes(stored["object_id"]),
                media_type="application/vnd.android.package-archive",
                headers={
                    "Content-Disposition": disposition,
                    "Content-Length": str(int(stored.get("size") or 0)),
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        access = await storage.read_access(
            stored["object_id"],
            filename=filename,
            content_type=stored.get("content_type") or "application/octet-stream",
        )
        if access.mode == "redirect":
            if direct:
                return {"url": access.url, "filename": filename}
            return RedirectResponse(
                access.url,
                status_code=307,
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            )
        if access.path and access.path.is_file():
            return FileResponse(access.path, filename=Path(normalized).name)

    path = _resolve_download_path(relative_path)
    media_type = mimetypes.guess_type(path.name)[0]
    if path.suffix == ".apk":
        media_type = "application/vnd.android.package-archive"
    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        filename=path.name,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )
