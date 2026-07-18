from __future__ import annotations

from typing import Any

import pytest
from bson import ObjectId


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = list(docs)

    def sort(self, *_args: Any) -> "_Cursor":
        return self

    def skip(self, value: int) -> "_Cursor":
        self.docs = self.docs[value:]
        return self

    def limit(self, value: int) -> "_Cursor":
        self.docs = self.docs[:value]
        return self

    async def to_list(self, value: int) -> list[dict[str, Any]]:
        return self.docs[:value]

    def __aiter__(self):
        async def iterate():
            for doc in self.docs:
                yield doc

        return iterate()


class _Collection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    async def count_documents(self, _query: dict[str, Any]) -> int:
        return len(self.docs)

    def find(
        self,
        query: dict[str, Any],
        _projection: dict[str, Any] | None = None,
    ) -> _Cursor:
        task_ids = set((query.get("task_id") or {}).get("$in") or [])
        docs = [
            doc
            for doc in self.docs
            if not task_ids or str(doc.get("task_id") or "") in task_ids
        ]
        return _Cursor(docs)


class _Db:
    def __init__(self, scans: list[dict[str, Any]], findings: list[dict[str, Any]]) -> None:
        self.collections = {
            "url_scan_results": _Collection(scans),
            "findings": _Collection(findings),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self.collections[name]


@pytest.mark.asyncio
async def test_website_records_join_url_scan_findings_and_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import website_records

    scan_id = ObjectId()
    db = _Db(
        [
            {
                "_id": scan_id,
                "project_id": "project-1",
                "task_id": "scan-1_url",
                "target_id": "target-1",
                "source": "web_tagging",
                "url": "https://example.com",
                "success": True,
            }
        ],
        [
            {
                "finding_id": "finding-1",
                "project_id": "project-1",
                "task_id": "scan-1_url",
                "source": "web_tagging",
                "source_url": "https://example.com/",
                "site_name": "示例站点",
                "entity_name": "示例公司",
                "summary": "站点摘要",
                "attention_score": 80,
            }
        ],
    )

    async def list_legacy(*_args: Any, **_kwargs: Any):
        return [], 2

    monkeypatch.setattr(
        website_records.web_tagging_dao,
        "list_web_tagging_results",
        list_legacy,
    )

    items, total = await website_records.list_website_records(
        db,
        project_id="project-1",
        limit=10,
    )

    assert total == 3
    assert len(items) == 1
    assert items[0]["_id"] == scan_id
    assert items[0]["data"]["intro"]["site_name"] == "示例站点"
    assert items[0]["data"]["has_findings"] is True
    assert items[0]["data"]["findings"][0]["finding_id"] == "finding-1"
