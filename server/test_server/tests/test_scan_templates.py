"""扫描任务模板服务的行为测试（含参数白名单与默认模板唯一性）。"""
from __future__ import annotations

import pytest

from api.services import scan_templates


class _FakeConfigDAO:
    """config_dao 的内存实现：get/set 只针对 scan_templates 段。"""

    def __init__(self) -> None:
        self.sections: dict[str, dict] = {}

    async def get_config(self, db, category):
        return (
            {"config": self.sections[category]}
            if category in self.sections
            else None
        )

    async def set_config(self, db, category, config):
        self.sections[category] = dict(config)
        return {"config": dict(config)}


@pytest.fixture()
def dao(monkeypatch):
    fake = _FakeConfigDAO()
    monkeypatch.setattr(scan_templates.config_dao, "get_config", fake.get_config)
    monkeypatch.setattr(scan_templates.config_dao, "set_config", fake.set_config)
    return fake


@pytest.mark.asyncio
async def test_upsert_and_list_orders_default_first(dao):
    db = object()
    await scan_templates.upsert_scan_template(
        db, {"name": "标准扫描", "params": {"enable_url_scan": True}}
    )
    await scan_templates.upsert_scan_template(
        db, {"name": "默认模板", "is_default": True, "params": {}}
    )
    templates = await scan_templates.list_scan_templates(db)
    assert [item["name"] for item in templates] == ["默认模板", "标准扫描"]
    assert templates[0]["is_default"] is True
    assert templates[0]["id"].startswith("tpl_")


@pytest.mark.asyncio
async def test_default_is_unique_after_upsert(dao):
    db = object()
    first = await scan_templates.upsert_scan_template(
        db, {"name": "A", "is_default": True, "params": {}}
    )
    second = await scan_templates.upsert_scan_template(
        db, {"name": "B", "is_default": True, "params": {}}
    )
    templates = await scan_templates.list_scan_templates(db)
    defaults = [item for item in templates if item.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == second["id"]
    assert first["id"] != second["id"]


@pytest.mark.asyncio
async def test_rejects_unknown_params(dao):
    with pytest.raises(scan_templates.ScanTemplateError, match="不支持的模板参数"):
        await scan_templates.upsert_scan_template(
            object(),
            {"name": "X", "params": {"company_names": "不该出现在模板里"}},
        )


@pytest.mark.asyncio
async def test_rejects_empty_name(dao):
    with pytest.raises(scan_templates.ScanTemplateError):
        await scan_templates.upsert_scan_template(object(), {"name": "   "})


@pytest.mark.asyncio
async def test_update_existing_template_keeps_id(dao):
    db = object()
    created = await scan_templates.upsert_scan_template(
        db, {"name": "旧名", "params": {"enable_bidding": True}}
    )
    updated = await scan_templates.upsert_scan_template(
        db,
        {"name": "新名", "params": {"enable_bidding": False}},
        template_id=created["id"],
    )
    assert updated["id"] == created["id"]
    assert updated["name"] == "新名"
    templates = await scan_templates.list_scan_templates(db)
    assert len(templates) == 1


@pytest.mark.asyncio
async def test_delete_template(dao):
    db = object()
    created = await scan_templates.upsert_scan_template(db, {"name": "T", "params": {}})
    assert await scan_templates.delete_scan_template(db, created["id"]) is True
    assert await scan_templates.delete_scan_template(db, created["id"]) is False
    assert await scan_templates.list_scan_templates(db) == []


@pytest.mark.asyncio
async def test_get_default_returns_none_when_empty(dao):
    assert await scan_templates.get_default_scan_template(object()) is None
