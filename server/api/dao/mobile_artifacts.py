"""Mobile operation logs and screenshot metadata.

Screenshots are stored as PNG files on disk; MongoDB stores searchable
screenshot metadata and the authenticated read URL.

Operation logs are stored as local JSONL files under ``logs/mobile_operations``.
They are intentionally not written to MongoDB.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import MOBILE_SCREENSHOTS_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_root() -> Path:
    configured = os.getenv("MOBILE_SCREENSHOT_DIR")
    root = Path(configured) if configured else Path.cwd() / "data" / "mobile_screenshots"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


class OperationLogStore(Protocol):
    """Persistence interface for mobile operation logs."""

    def append(self, row: dict[str, Any]) -> None:
        """Persist one operation row."""

    def query(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent operation rows, newest first."""

    def delete_by_project(self, project_id: str) -> int:
        """Delete operation rows for a project."""


class LocalJsonlOperationLogStore:
    """Local JSONL store for mobile operation logs."""

    def __init__(self, root: Path | None = None) -> None:
        configured = os.getenv("MOBILE_OPERATION_LOG_DIR")
        if root is not None:
            self.root = root.resolve()
        elif configured:
            self.root = Path(configured).resolve()
        else:
            self.root = (Path.cwd() / "logs" / "mobile_operations").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for_created_at(self, created_at: str) -> Path:
        day = (created_at or _now())[:10]
        return self.root / f"{day}.jsonl"

    def append(self, row: dict[str, Any]) -> None:
        path = self._path_for_created_at(str(row.get("created_at") or ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str))
            fh.write("\n")

    def query(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 5000))
        rows: deque[dict[str, Any]] = deque(maxlen=safe_limit)
        for path in sorted(self.root.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(value, dict):
                            rows.append(value)
            except FileNotFoundError:
                continue
        return sorted(rows, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:safe_limit]

    def delete_by_project(self, project_id: str) -> int:
        if not project_id:
            return 0
        deleted = 0
        for path in sorted(self.root.glob("*.jsonl")):
            kept: list[str] = []
            changed = False
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                continue
            for line in lines:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue
                if isinstance(value, dict) and value.get("project_id") == project_id:
                    deleted += 1
                    changed = True
                    continue
                kept.append(line)
            if changed:
                if kept:
                    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
                else:
                    path.unlink(missing_ok=True)
        return deleted


_operation_store: OperationLogStore | None = None


def _get_operation_store() -> OperationLogStore:
    global _operation_store
    if _operation_store is None:
        _operation_store = LocalJsonlOperationLogStore()
    return _operation_store


def _resolve_stored_file(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    root = _storage_root()
    path = Path(path_value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _decode_png(image_base64: str) -> bytes:
    data = image_base64.strip()
    if "," in data and data.split(",", 1)[0].startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    screenshots = db[MOBILE_SCREENSHOTS_COLLECTION]
    try:
        await screenshots.create_index("screenshot_id", unique=True)
    except Exception:
        pass
    await screenshots.create_index([("project_id", 1), ("created_at", -1)])
    await screenshots.create_index([("device_id", 1), ("created_at", -1)])
    await screenshots.create_index("task_id")
    await screenshots.create_index("contact_id")


async def save_screenshot(
    db: AsyncIOMotorDatabase,
    *,
    image_base64: str,
    project_id: str | None = None,
    task_id: str | None = None,
    device_id: str | None = None,
    contact_id: str | None = None,
    source: str = "unknown",
    width: int | None = None,
    height: int | None = None,
    operation_id: str | None = None,
    note: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    screenshot_id = "ms_" + uuid.uuid4().hex
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    root = _storage_root()
    directory = root / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{screenshot_id}.png"
    path.write_bytes(_decode_png(image_base64))

    doc = {
        "screenshot_id": screenshot_id,
        "project_id": project_id,
        "task_id": task_id,
        "device_id": device_id,
        "contact_id": contact_id,
        "source": source,
        "file_path": str(path),
        "url": f"/api/v1/mobile/screenshots/{screenshot_id}/image",
        "width": width,
        "height": height,
        "operation_id": operation_id,
        "note": note,
        "meta": meta or {},
        "created_at": _now(),
    }
    try:
        await db[MOBILE_SCREENSHOTS_COLLECTION].insert_one(doc)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    doc.pop("_id", None)
    return doc


async def get_screenshot(
    db: AsyncIOMotorDatabase, screenshot_id: str
) -> dict[str, Any] | None:
    return await db[MOBILE_SCREENSHOTS_COLLECTION].find_one(
        {"screenshot_id": screenshot_id}, {"_id": 0}
    )


def resolve_screenshot_file(doc: dict[str, Any]) -> Path | None:
    return _resolve_stored_file(doc.get("file_path"))


async def list_screenshots(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str | None = None,
    device_id: str | None = None,
    task_id: str | None = None,
    contact_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    if device_id:
        query["device_id"] = device_id
    if task_id:
        query["task_id"] = task_id
    if contact_id:
        query["contact_id"] = contact_id
    cursor = (
        db[MOBILE_SCREENSHOTS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(max(1, min(limit, 500)))
    )
    return [doc async for doc in cursor]


async def log_operation(
    db: AsyncIOMotorDatabase,
    *,
    operation_type: str,
    device_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    contact_id: str | None = None,
    action: str = "",
    status: str = "ok",
    message: str = "",
    data: dict[str, Any] | None = None,
    screenshot_id: str | None = None,
) -> dict[str, Any]:
    _ = db  # kept for DAO interface compatibility; operation logs are local.
    operation_id = "mo_" + uuid.uuid4().hex
    doc = {
        "operation_id": operation_id,
        "operation_type": operation_type,
        "device_id": device_id,
        "project_id": project_id,
        "task_id": task_id,
        "contact_id": contact_id,
        "action": action,
        "status": status,
        "message": message,
        "data": data or {},
        "screenshot_id": screenshot_id,
        "created_at": _now(),
    }
    _get_operation_store().append(doc)
    try:
        from core.observability import obs_log

        level = "warning" if status in {"failed", "error", "cancelled"} else "info"
        obs_log(
            message or action or operation_type,
            project_id=project_id or "",
            task_id=task_id or "",
            source="mobile_operation",
            level=level,
            event=operation_type,
            phase="mobile",
            agent=device_id or "",
            data={
                "operation_id": operation_id,
                "device_id": device_id,
                "contact_id": contact_id,
                "action": action,
                "status": status,
                "screenshot_id": screenshot_id,
                "payload": data or {},
            },
        )
    except Exception:
        pass
    return doc


async def list_operations(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str | None = None,
    device_id: str | None = None,
    task_id: str | None = None,
    contact_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ = db  # kept for DAO interface compatibility; operation logs are local.
    rows = _get_operation_store().query(limit=max(1, min(limit * 5, 5000)))

    def _keep(doc: dict[str, Any]) -> bool:
        if project_id and doc.get("project_id") != project_id:
            return False
        if device_id and doc.get("device_id") != device_id:
            return False
        if task_id and doc.get("task_id") != task_id:
            return False
        if contact_id and doc.get("contact_id") != contact_id:
            return False
        return True

    return [doc for doc in rows if _keep(doc)][: max(1, min(limit, 500))]


async def delete_project_artifacts(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> dict[str, Any]:
    """Delete a project's mobile screenshots, local operation logs, and files."""
    screenshots_coll = db[MOBILE_SCREENSHOTS_COLLECTION]

    cursor = screenshots_coll.find({"project_id": project_id}, {"file_path": 1})
    paths: list[Path] = []
    file_errors: list[str] = []
    async for doc in cursor:
        path = _resolve_stored_file(doc.get("file_path"))
        if path is None:
            if doc.get("file_path"):
                file_errors.append(f"{doc['file_path']}: path outside screenshot storage")
            continue
        paths.append(path)

    screenshot_result = await screenshots_coll.delete_many({"project_id": project_id})
    operations_deleted = _get_operation_store().delete_by_project(project_id)

    files_deleted = 0
    for path in paths:
        try:
            existed = path.exists()
            path.unlink(missing_ok=True)
            if existed:
                files_deleted += 1
        except Exception as exc:  # noqa: BLE001
            file_errors.append(f"{path}: {exc}")

    return {
        "screenshots_deleted": screenshot_result.deleted_count,
        "operations_deleted": operations_deleted,
        "files_deleted": files_deleted,
        "file_errors": file_errors,
    }
