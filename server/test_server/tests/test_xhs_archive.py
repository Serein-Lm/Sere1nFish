from __future__ import annotations

import pytest

from api.services.xhs_archive import XhsArchiveService


@pytest.mark.asyncio
async def test_xhs_archive_uses_private_object_storage_reference() -> None:
    calls: list[dict] = []

    class _Storage:
        async def store_bytes(self, data: bytes, **kwargs):
            calls.append({"data": data, **kwargs})
            return {"object_id": kwargs["object_id"]}

    async def storage_factory():
        return _Storage()

    result = await XhsArchiveService(storage_factory=storage_factory).archive_json(
        {"items": [{"id": "note-1", "title": "原始标题"}]},
        kind="search",
        project_id="project-1",
        task_id="task-1",
        source_id="task-1:page:1",
        meta={"keyword": "目标公司", "page": 1},
    )

    assert result["storage_object_id"].startswith("obj_xhs_search_")
    assert result["url"].endswith(f"/{result['storage_object_id']}/content")
    assert calls[0]["kind"] == "xhs_search"
    assert calls[0]["project_id"] == "project-1"
    assert calls[0]["content_type"].startswith("application/json")
    assert "原始标题".encode() in calls[0]["data"]
