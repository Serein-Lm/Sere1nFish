from __future__ import annotations

import asyncio

import pytest

from api.dao import targets as targets_dao
from api.routers.project_api import _normalize_xhs_target_params
from api.services.targets import require_project_target


def test_require_project_target_returns_canonical_project_identity(monkeypatch) -> None:
    async def _project_target(*_args, **_kwargs):
        return {"target_id": "target-1", "target_name": "项目正式名称"}

    async def _target(*_args, **_kwargs):
        return {"target_id": "target-1", "canonical_name": "全局正式名称"}

    monkeypatch.setattr(targets_dao, "get_project_target", _project_target)
    monkeypatch.setattr(targets_dao, "get_target", _target)

    result = asyncio.run(
        require_project_target(None, project_id="project-1", target_id=" target-1 ")
    )

    assert result == {"target_id": "target-1", "target_name": "项目正式名称"}


def test_require_project_target_rejects_unlinked_target(monkeypatch) -> None:
    async def _project_target(*_args, **_kwargs):
        return None

    monkeypatch.setattr(targets_dao, "get_project_target", _project_target)

    with pytest.raises(ValueError, match="不属于当前项目"):
        asyncio.run(
            require_project_target(None, project_id="project-1", target_id="target-2")
        )


def test_xhs_target_params_replace_client_name_with_project_name(monkeypatch) -> None:
    async def _project_target(*_args, **_kwargs):
        return {"target_id": "target-1", "target_name": "项目正式名称"}

    async def _target(*_args, **_kwargs):
        return {"target_id": "target-1", "canonical_name": "全局正式名称"}

    monkeypatch.setattr(targets_dao, "get_project_target", _project_target)
    monkeypatch.setattr(targets_dao, "get_target", _target)
    params = {"target_id": "target-1", "target_name": "客户端名称", "keyword": "关键词"}

    asyncio.run(
        _normalize_xhs_target_params(None, project_id="project-1", params=params)
    )

    assert params == {
        "target_id": "target-1",
        "target_name": "项目正式名称",
        "keyword": "关键词",
    }


def test_xhs_target_params_drop_unscoped_name() -> None:
    params = {"target_name": "自由文本公司", "keyword": "关键词"}

    asyncio.run(
        _normalize_xhs_target_params(None, project_id="project-1", params=params)
    )

    assert params == {"keyword": "关键词"}
