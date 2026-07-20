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

    @staticmethod
    def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            if key == "$or":
                if not any(_Collection._matches(doc, branch) for branch in expected):
                    return False
                continue
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$exists" in expected and (key in doc) != bool(expected["$exists"]):
                    return False
                continue
            if actual != expected and str(actual or "") != str(expected or ""):
                return False
        return True

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for doc in self.docs if self._matches(doc, query))

    def find(
        self,
        query: dict[str, Any],
        _projection: dict[str, Any] | None = None,
    ) -> _Cursor:
        docs = [doc for doc in self.docs if self._matches(doc, query)]
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
        return [], 0

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

    assert total == 1
    assert len(items) == 1
    assert items[0]["_id"] == scan_id
    assert items[0]["data"]["intro"]["site_name"] == "示例站点"
    assert items[0]["data"]["has_findings"] is True
    assert items[0]["data"]["findings"][0]["finding_id"] == "finding-1"


def _legacy_record(
    record_id: ObjectId,
    url: str,
    score: int | None,
    target_id: str,
    created_at: Any,
) -> dict[str, Any]:
    findings = []
    if score is not None:
        findings = [{"finding_id": f"legacy-{score}", "attention_score": score}]
    return {
        "_id": record_id,
        "project_id": ObjectId("6a5a1f59518d9e71e1887ab0"),
        "url": url,
        "target_id": target_id,
        "source": "web_tagging",
        "created_at": created_at,
        "data": {"has_findings": bool(findings), "findings": findings},
    }


@pytest.mark.asyncio
async def test_website_records_sort_sources_together_before_pagination_and_filter_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import website_records

    project_id = "project-1"
    scans = [
        {
            "_id": ObjectId(),
            "project_id": project_id,
            "task_id": "low-task",
            "target_id": "target-a",
            "source": "web_tagging",
            "url": "https://low.example",
            "success": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "_id": ObjectId(),
            "project_id": project_id,
            "task_id": "high-task",
            "target_id": "target-a",
            "source": "web_tagging",
            "url": "https://high.example",
            "success": True,
            "created_at": "2026-01-02T00:00:00+00:00",
        },
        {
            "_id": ObjectId(),
            "project_id": project_id,
            "task_id": "other-task",
            "target_id": "target-b",
            "source": "web_tagging",
            "url": "https://other.example",
            "success": True,
            "created_at": "2026-01-03T00:00:00+00:00",
        },
    ]
    findings = [
        {
            "project_id": project_id,
            "source": "web_tagging",
            "task_id": "low-task",
            "source_url": "https://low.example",
            "attention_score": 40,
        },
        {
            "project_id": project_id,
            "source": "web_tagging",
            "task_id": "high-task",
            "source_url": "https://high.example",
            "attention_score": 95,
        },
        {
            "project_id": project_id,
            "source": "web_tagging",
            "task_id": "other-task",
            "source_url": "https://other.example",
            "attention_score": 10,
        },
    ]
    legacy = [
        _legacy_record(
            ObjectId(),
            "https://legacy.example",
            80,
            "target-a",
            "2026-01-04T00:00:00+00:00",
        ),
        _legacy_record(
            ObjectId(),
            "https://safe.example",
            None,
            "target-a",
            "2026-01-05T00:00:00+00:00",
        ),
        _legacy_record(
            ObjectId(),
            "https://legacy-b.example",
            5,
            "target-b",
            "2026-01-06T00:00:00+00:00",
        ),
    ]
    db = _Db(scans, findings)
    calls: list[str] = []

    async def list_legacy(_db: Any, **kwargs: Any):
        target_id = kwargs.get("target_id") or ""
        calls.append(target_id)
        selected = [item for item in legacy if not target_id or item.get("target_id") == target_id]
        return selected[: kwargs.get("limit", len(selected))], len(selected)

    monkeypatch.setattr(
        website_records.web_tagging_dao,
        "list_web_tagging_results",
        list_legacy,
    )

    page, total = await website_records.list_website_records(
        db,
        project_id=project_id,
        skip=1,
        limit=2,
    )

    assert total == 6
    assert [item["url"] for item in page] == [
        "https://legacy.example",
        "https://low.example",
    ]

    first_page, first_page_total = await website_records.list_website_records(
        db,
        project_id=project_id,
        limit=1,
    )
    assert len(first_page) == 1
    assert first_page_total == total

    filtered, filtered_total = await website_records.list_website_records(
        db,
        project_id=project_id,
        target_id="target-b",
        limit=10,
    )

    assert filtered_total == 2
    assert {item["target_id"] for item in filtered} == {"target-b"}
    assert {item["url"] for item in filtered} == {
        "https://other.example",
        "https://legacy-b.example",
    }
    assert calls[-1] == "target-b"


@pytest.mark.asyncio
async def test_website_target_counts_use_lightweight_deduplicated_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import website_records

    db = _Db(
        [
            {
                "project_id": "project-1",
                "target_id": "target-a",
                "source": "web_tagging",
                "url": "http://example.com/login",
            },
            {
                "project_id": "project-1",
                "target_id": "target-a",
                "source": "web_tagging",
                "url": "https://example.com/login",
            },
            {
                "project_id": "project-1",
                "target_id": "target-a",
                "source": "web_tagging",
                "url": "https://example.com/kkfileview/index",
            },
            {
                "project_id": "project-1",
                "target_id": "target-b",
                "source": "web_tagging",
                "url": "https://b.example.com",
            },
        ],
        [],
    )

    async def list_legacy_identities(*_args: Any, **_kwargs: Any):
        return [
            {
                "target_id": "target-a",
                "url": "https://example.com/login",
                "data": {"intro": {}},
            }
        ]

    monkeypatch.setattr(
        website_records.web_tagging_dao,
        "list_web_tagging_identities",
        list_legacy_identities,
    )

    counts = await website_records.count_project_website_records_by_target(
        db,
        project_id="project-1",
        target_ids=["target-a", "target-b"],
    )

    assert counts == {"target-a": 1, "target-b": 1}
