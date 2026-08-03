"""天眼查供应商运行策略。

供应商总开关与招投标时间窗统一保存在 ``collection_runtime``，避免业务
流水线各自判断余额、重复请求或散落固定时间范围。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import config as config_dao
from api.db.mongodb import get_db
from core.logger import get_logger

logger = get_logger("tianyancha_runtime")

DEFAULT_BIDDING_LOOKBACK_DAYS = 30
MAX_BIDDING_LOOKBACK_DAYS = 30


@dataclass(frozen=True, slots=True)
class TianyanchaRuntimePolicy:
    enabled: bool = True
    disabled_reason: str = ""
    bidding_lookback_days: int = DEFAULT_BIDDING_LOOKBACK_DAYS


def parse_tianyancha_runtime_policy(config: dict[str, Any] | None) -> TianyanchaRuntimePolicy:
    """Convert shared collection config into a bounded provider policy."""
    runtime = config if isinstance(config, dict) else {}
    provider = runtime.get("tianyancha")
    bidding = runtime.get("bidding")
    provider = provider if isinstance(provider, dict) else {}
    bidding = bidding if isinstance(bidding, dict) else {}
    try:
        lookback_days = int(
            bidding.get("lookback_days") or DEFAULT_BIDDING_LOOKBACK_DAYS
        )
    except (TypeError, ValueError):
        lookback_days = DEFAULT_BIDDING_LOOKBACK_DAYS
    return TianyanchaRuntimePolicy(
        enabled=provider.get("enabled", True) is not False,
        disabled_reason=str(provider.get("disabled_reason") or "").strip(),
        bidding_lookback_days=max(1, min(lookback_days, MAX_BIDDING_LOOKBACK_DAYS)),
    )


async def get_tianyancha_runtime_policy(
    db: AsyncIOMotorDatabase | None = None,
) -> TianyanchaRuntimePolicy:
    """Read the current policy, falling back only during tests or startup."""
    try:
        database = db if db is not None else get_db()
        doc = await config_dao.get_config(database, "collection_runtime")
        return parse_tianyancha_runtime_policy(
            doc.get("config", {}) if isinstance(doc, dict) else {}
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取天眼查运行策略失败，使用默认值: %s", exc)
        return TianyanchaRuntimePolicy()


async def set_tianyancha_runtime_policy(
    *,
    enabled: bool | None = None,
    disabled_reason: str | None = None,
    bidding_lookback_days: int | None = None,
    db: AsyncIOMotorDatabase | None = None,
) -> TianyanchaRuntimePolicy:
    """Update Tianyancha-owned fields while preserving other collection tuning."""
    database = db if db is not None else get_db()
    doc = await config_dao.get_config(database, "collection_runtime")
    runtime = dict(doc.get("config", {}) if isinstance(doc, dict) else {})
    provider = dict(runtime.get("tianyancha") or {})
    bidding = dict(runtime.get("bidding") or {})

    if enabled is not None:
        provider["enabled"] = bool(enabled)
    if disabled_reason is not None:
        provider["disabled_reason"] = str(disabled_reason).strip()
    if enabled is True:
        provider.pop("disabled_at", None)
        if disabled_reason is None:
            provider["disabled_reason"] = ""
    elif enabled is False:
        provider["disabled_at"] = datetime.now(timezone.utc).isoformat()
    if bidding_lookback_days is not None:
        bidding["lookback_days"] = max(
            1,
            min(int(bidding_lookback_days), MAX_BIDDING_LOOKBACK_DAYS),
        )

    runtime["tianyancha"] = provider
    runtime["bidding"] = bidding
    saved = await config_dao.set_config(database, "collection_runtime", runtime)
    return parse_tianyancha_runtime_policy(saved.get("config", {}))


async def disable_tianyancha_for_quota(
    db: AsyncIOMotorDatabase | None = None,
) -> TianyanchaRuntimePolicy:
    """Trip the shared circuit breaker after the provider reports no balance."""
    policy = await set_tianyancha_runtime_policy(
        enabled=False,
        disabled_reason="quota_insufficient",
        bidding_lookback_days=DEFAULT_BIDDING_LOOKBACK_DAYS,
        db=db,
    )
    logger.warning("天眼查余额不足，已自动停用供应商调用")
    return policy
