"""小红书原始响应归档服务。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from api.storage import get_object_storage


def _object_url(object_id: str) -> str:
    return f"/api/v1/storage/objects/{object_id}/content"


class XhsArchiveService:
    """将平台原始 JSON 写入统一私有对象存储。"""

    def __init__(self, *, storage_factory: Any = get_object_storage) -> None:
        self._storage_factory = storage_factory
        self._storage: Any = None

    async def _get_storage(self) -> Any:
        if self._storage is None:
            self._storage = await self._storage_factory()
        return self._storage

    async def archive_json(
        self,
        payload: Any,
        *,
        kind: str,
        project_id: str,
        task_id: str,
        source_id: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        payload_hash = hashlib.sha256(data).hexdigest()
        scoped_hash = hashlib.sha256(
            f"{project_id}:{task_id}:{kind}:{source_id}:{payload_hash}".encode("utf-8")
        ).hexdigest()
        object_id = f"obj_xhs_{kind}_{scoped_hash[:24]}"
        storage = await self._get_storage()
        stored = await storage.store_bytes(
            data,
            kind=f"xhs_{kind}",
            filename=f"xhs-{kind}-{scoped_hash[:16]}.json",
            object_id=object_id,
            content_type="application/json; charset=utf-8",
            project_id=project_id,
            subject_id=source_id,
            source="xhs",
            source_id=source_id,
            meta={
                "task_id": task_id,
                "payload_sha256": payload_hash,
                **(meta or {}),
            },
        )
        stored_id = str(stored.get("object_id") or object_id)
        return {
            "storage_object_id": stored_id,
            "url": _object_url(stored_id),
        }
