from __future__ import annotations

from typing import Any

import pytest

from api.services import tianyancha_runtime


def test_runtime_policy_disables_provider_and_bounds_bidding_window() -> None:
    policy = tianyancha_runtime.parse_tianyancha_runtime_policy(
        {
            "tianyancha": {
                "enabled": False,
                "disabled_reason": "quota_insufficient",
            },
            "bidding": {"lookback_days": 90, "max_records_per_type": 2000},
        }
    )

    assert policy.enabled is False
    assert policy.disabled_reason == "quota_insufficient"
    assert policy.bidding_lookback_days == 30
    assert policy.bidding_max_records_per_type == 20


@pytest.mark.asyncio
async def test_runtime_policy_update_preserves_other_collection_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, Any] = {}

    async def _get_config(_db: Any, _category: str) -> dict[str, Any]:
        return {
            "config": {
                "browser_workers": 88,
                "tianyancha": {"enabled": True},
                "bidding": {"lookback_days": 60, "max_records_per_type": 10},
            }
        }

    async def _set_config(
        _db: Any,
        _category: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        stored.update(config)
        return {"config": config}

    monkeypatch.setattr(tianyancha_runtime.config_dao, "get_config", _get_config)
    monkeypatch.setattr(tianyancha_runtime.config_dao, "set_config", _set_config)

    class _Database:
        def __bool__(self) -> bool:
            raise NotImplementedError("Mongo Database objects have no truth value")

    policy = await tianyancha_runtime.set_tianyancha_runtime_policy(
        db=_Database(),
        enabled=False,
        disabled_reason="quota_insufficient",
        bidding_lookback_days=30,
        bidding_max_records_per_type=20,
    )

    assert stored["browser_workers"] == 88
    assert stored["tianyancha"]["enabled"] is False
    assert stored["bidding"]["lookback_days"] == 30
    assert stored["bidding"]["max_records_per_type"] == 20
    assert policy.enabled is False
    assert policy.bidding_lookback_days == 30
    assert policy.bidding_max_records_per_type == 20
