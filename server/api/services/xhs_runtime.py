"""Unified runtime helpers for XHS collection.

The collection pipeline should ask this layer for accounts, proxies and signer
health instead of hard-coding active-cookie or proxy selection rules.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.dao import xhs as xhs_dao
from api.db.collections import XHS_COOKIES_COLLECTION
from api.services.runtime_config import get_runtime_config_section
from core.logger import get_logger


logger = get_logger("api.services.xhs_runtime")

_static_proxy_idx = 0
_static_proxy_lock = asyncio.Lock()
_provider_pool_cache: dict[str, Any] = {}
_provider_pool_lock = asyncio.Lock()
_request_pacer_lock = asyncio.Lock()
_next_request_at = 0.0

DEFAULT_XHS_SEARCH_PAGE_SIZE = 20
DEFAULT_XHS_SEARCH_MAX_PAGES_PER_KEYWORD = 1
DEFAULT_XHS_REQUEST_INTERVAL_MIN_SECONDS = 4.0
DEFAULT_XHS_REQUEST_INTERVAL_MAX_SECONDS = 8.0
DEFAULT_XHS_MAX_CONSECUTIVE_FAILURES = 1


@dataclass
class XhsAccountLease:
    account_name: str
    cookie_string: str
    is_active: bool = False
    source: str = "pool"

    def to_log_context(self) -> dict[str, Any]:
        return {
            "account_name": self.account_name,
            "is_active": self.is_active,
            "source": self.source,
        }


@dataclass
class XhsProxyLease:
    proxy_url: str | None = None
    source: str = "none"
    provider: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.proxy_url)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("proxy_url"):
            data["proxy_url"] = _mask_proxy_url(data["proxy_url"])
        return data


@dataclass
class XhsErrorDecision:
    invalidate: bool = False
    risk_control: bool = False
    cooldown_seconds: int = 300
    reason: str = "error"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.splitlines() if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _account_pool_config(config: dict[str, Any]) -> dict[str, Any]:
    pool = config.get("account_pool", {})
    return pool if isinstance(pool, dict) else {}


@dataclass(frozen=True)
class XhsRequestPolicy:
    """小红书请求节奏策略；业务流水线不感知具体限速字段。"""

    page_size: int = DEFAULT_XHS_SEARCH_PAGE_SIZE
    max_pages_per_keyword: int = DEFAULT_XHS_SEARCH_MAX_PAGES_PER_KEYWORD
    interval_min_seconds: float = DEFAULT_XHS_REQUEST_INTERVAL_MIN_SECONDS
    interval_max_seconds: float = DEFAULT_XHS_REQUEST_INTERVAL_MAX_SECONDS

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "XhsRequestPolicy":
        pool = _account_pool_config(config or {})
        interval_min = max(
            0.0,
            min(
                _as_float(
                    pool.get("request_interval_min_seconds"),
                    DEFAULT_XHS_REQUEST_INTERVAL_MIN_SECONDS,
                ),
                60.0,
            ),
        )
        interval_max = max(
            interval_min,
            min(
                _as_float(
                    pool.get("request_interval_max_seconds"),
                    DEFAULT_XHS_REQUEST_INTERVAL_MAX_SECONDS,
                ),
                120.0,
            ),
        )
        return cls(
            page_size=DEFAULT_XHS_SEARCH_PAGE_SIZE,
            max_pages_per_keyword=max(
                1,
                min(
                    _as_int(
                        pool.get("search_max_pages_per_keyword"),
                        DEFAULT_XHS_SEARCH_MAX_PAGES_PER_KEYWORD,
                    ),
                    5,
                ),
            ),
            interval_min_seconds=interval_min,
            interval_max_seconds=interval_max,
        )


async def wait_for_xhs_request_slot(
    purpose: str,
    *,
    config: dict[str, Any] | None = None,
) -> float:
    """在进程内统一串行分配请求时隙，避免多个 stage 同时冲击账号池。"""
    global _next_request_at

    policy = XhsRequestPolicy.from_config(
        config if config is not None else await get_xhs_runtime_config()
    )
    if policy.interval_max_seconds <= 0:
        return 0.0

    async with _request_pacer_lock:
        wait_seconds = max(0.0, _next_request_at - time.monotonic())
        if wait_seconds > 0:
            logger.info(
                "小红书请求错峰 purpose=%s wait=%.2fs",
                purpose,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
        interval = random.uniform(
            policy.interval_min_seconds,
            policy.interval_max_seconds,
        )
        _next_request_at = time.monotonic() + interval
        return wait_seconds


def _proxy_pool_config(config: dict[str, Any]) -> dict[str, Any]:
    pool = config.get("proxy_pool", {})
    return pool if isinstance(pool, dict) else {}


def _masked_error(error: str | None) -> str:
    if not error:
        return ""
    text = str(error)
    return text[:500]


def _mask_proxy_url(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return proxy_url
    if "@" not in proxy_url:
        return proxy_url
    scheme, rest = proxy_url.split("://", 1) if "://" in proxy_url else ("", proxy_url)
    auth, host = rest.rsplit("@", 1)
    if ":" in auth:
        user = auth.split(":", 1)[0]
        masked = f"{user}:***@{host}"
    else:
        masked = f"***@{host}"
    return f"{scheme}://{masked}" if scheme else masked


def _proxy_model_to_url(proxy: Any) -> str:
    protocol = str(getattr(proxy, "protocol", "http://") or "http://")
    if not protocol.endswith("://"):
        protocol = f"{protocol}://"
    user = getattr(proxy, "user", "") or ""
    password = getattr(proxy, "password", "") or ""
    host = getattr(proxy, "ip", "")
    port = getattr(proxy, "port", "")
    auth = f"{user}:{password}@" if user or password else ""
    return f"{protocol}{auth}{host}:{port}"


def _account_selection_query(
    *,
    now: datetime,
    include_unverified: bool,
    max_consecutive_failures: int,
    exclude_accounts: list[str],
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "cookie_string": {"$type": "string", "$ne": ""},
        "account_name": {"$nin": exclude_accounts},
        "is_enabled": {"$ne": False},
        "$and": [
            {
                "$or": [
                    {"cooldown_until": {"$exists": False}},
                    {"cooldown_until": None},
                    {"cooldown_until": {"$lte": now}},
                ]
            }
        ],
    }
    if include_unverified:
        query["is_valid"] = {"$ne": False}
    else:
        query["is_valid"] = True
    if max_consecutive_failures > 0:
        query["$and"].append(
            {
                "$or": [
                    {"consecutive_failures": {"$exists": False}},
                    {"consecutive_failures": {"$lt": max_consecutive_failures}},
                ]
            }
        )
    return query


def _account_is_leaseable(
    doc: dict[str, Any] | None,
    *,
    now: datetime,
    include_unverified: bool,
    max_consecutive_failures: int,
    exclude_accounts: list[str],
) -> bool:
    if not doc or not doc.get("cookie_string"):
        return False
    if doc.get("account_name") in set(exclude_accounts):
        return False
    if doc.get("is_enabled") is False:
        return False
    if include_unverified:
        if doc.get("is_valid") is False:
            return False
    elif doc.get("is_valid") is not True:
        return False
    cooldown_until = _as_utc_datetime(doc.get("cooldown_until"))
    if cooldown_until and cooldown_until > now:
        return False
    if max_consecutive_failures > 0:
        failures = _as_int(doc.get("consecutive_failures"), 0)
        if failures >= max_consecutive_failures:
            return False
    return True


async def get_xhs_runtime_config() -> dict[str, Any]:
    config = await get_runtime_config_section("xhs_crawler")
    return config if isinstance(config, dict) else {}


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[XHS_COOKIES_COLLECTION]
    try:
        await coll.create_index("account_name", unique=True)
    except Exception:
        pass
    await coll.create_index("is_active")
    await coll.create_index("is_valid")
    await coll.create_index("is_enabled")
    await coll.create_index("last_used_at")
    await coll.create_index("cooldown_until")
    await coll.create_index([("is_valid", 1), ("cooldown_until", 1), ("last_used_at", 1)])


async def select_xhs_account(
    db: AsyncIOMotorDatabase,
    *,
    purpose: str = "collection",
    config: dict[str, Any] | None = None,
    exclude_accounts: list[str] | None = None,
) -> XhsAccountLease:
    """Select one usable XHS account with a stable pool strategy."""
    config = config if config is not None else await get_xhs_runtime_config()
    pool = _account_pool_config(config)
    pool_enabled = _as_bool(pool.get("enabled"), default=True)
    exclude_accounts = exclude_accounts or []

    if not pool_enabled:
        active = await xhs_dao.get_active_cookie(db)
        if not active or not active.get("cookie_string"):
            raise RuntimeError("没有激活的账号，请先导入并激活 Cookie")
        return XhsAccountLease(
            account_name=active.get("account_name", ""),
            cookie_string=active.get("cookie_string", ""),
            is_active=bool(active.get("is_active")),
            source="active",
        )

    now = _now()
    include_unverified = _as_bool(pool.get("include_unverified"), default=True)
    max_consecutive_failures = _as_int(
        pool.get("max_consecutive_failures"),
        DEFAULT_XHS_MAX_CONSECUTIVE_FAILURES,
    )
    strategy = str(pool.get("strategy") or "least_recently_used")

    query = _account_selection_query(
        now=now,
        include_unverified=include_unverified,
        max_consecutive_failures=max_consecutive_failures,
        exclude_accounts=exclude_accounts,
    )

    sort = [("last_used_at", 1), ("updated_at", 1)]
    if strategy == "least_failures":
        sort = [("consecutive_failures", 1), ("failure_count", 1), ("last_used_at", 1)]
    elif strategy == "active_first":
        sort = [("is_active", -1), ("last_used_at", 1)]

    doc = await db[XHS_COOKIES_COLLECTION].find_one_and_update(
        query,
        {
            "$set": {"last_used_at": now, "last_selected_purpose": purpose, "updated_at": now},
            "$inc": {"lease_count": 1},
        },
        sort=sort,
        return_document=ReturnDocument.AFTER,
    )
    if doc and doc.get("cookie_string"):
        return XhsAccountLease(
            account_name=doc.get("account_name", ""),
            cookie_string=doc.get("cookie_string", ""),
            is_active=bool(doc.get("is_active")),
            source=f"pool:{strategy}",
        )

    if _as_bool(pool.get("fallback_to_active"), default=True):
        active = await xhs_dao.get_active_cookie(db)
        if _account_is_leaseable(
            active,
            now=now,
            include_unverified=include_unverified,
            max_consecutive_failures=max_consecutive_failures,
            exclude_accounts=exclude_accounts,
        ):
            return XhsAccountLease(
                account_name=active.get("account_name", ""),
                cookie_string=active.get("cookie_string", ""),
                is_active=bool(active.get("is_active")),
                source="active:fallback",
            )

    raise RuntimeError("没有可用的小红书账号：账号池为空、Cookie 无效、仍在冷却中或已达到连续失败阈值")


def classify_xhs_account_error(
    error: Any,
    *,
    config: dict[str, Any] | None = None,
) -> XhsErrorDecision:
    """Classify search/detail errors into invalid-cookie or risk-control cooldowns."""
    config = config or {}
    pool = _account_pool_config(config)
    text = str(error or "").lower()

    invalid_keywords = (
        "cookie 已失效",
        "cookie失效",
        "未登录",
        "登录失败",
        "请先登录",
        "unauthorized",
        "forbidden",
        "invalid cookie",
    )
    if any(keyword in text for keyword in invalid_keywords):
        return XhsErrorDecision(
            invalidate=True,
            cooldown_seconds=_as_int(pool.get("invalid_cooldown_seconds"), 900),
            reason="invalid_cookie",
        )

    risk_keywords = (
        "风控",
        "访问频繁",
        "操作频繁",
        "请求频繁",
        "验证码",
        "滑块",
        "安全验证",
        "安全校验",
        "captcha",
        "rate limit",
        "too many",
        "too frequent",
        "risk",
        "406",
        "429",
        "461",
    )
    if any(keyword in text for keyword in risk_keywords):
        return XhsErrorDecision(
            risk_control=True,
            cooldown_seconds=_as_int(pool.get("risk_cooldown_seconds"), 1800),
            reason="risk_control",
        )

    return XhsErrorDecision(
        cooldown_seconds=_as_int(pool.get("error_cooldown_seconds"), 300),
        reason="transient_error",
    )


async def record_xhs_account_result(
    db: AsyncIOMotorDatabase,
    account_name: str | None,
    *,
    success: bool,
    error: str | None = None,
    invalidate: bool = False,
    cooldown_seconds: int | None = None,
) -> None:
    if not account_name:
        return

    now = _now()
    if success:
        await db[XHS_COOKIES_COLLECTION].update_one(
            {"account_name": account_name},
            {
                "$set": {
                    "is_valid": True,
                    "last_success_at": now,
                    "last_error": None,
                    "consecutive_failures": 0,
                    "cooldown_until": None,
                    "quarantined_at": None,
                    "quarantine_reason": None,
                    "updated_at": now,
                },
                "$inc": {"success_count": 1},
            },
        )
        return

    config = await get_xhs_runtime_config()
    pool = _account_pool_config(config)
    max_consecutive_failures = _as_int(
        pool.get("max_consecutive_failures"),
        DEFAULT_XHS_MAX_CONSECUTIVE_FAILURES,
    )
    current = await db[XHS_COOKIES_COLLECTION].find_one({"account_name": account_name}) or {}
    next_failures = _as_int(current.get("consecutive_failures"), 0) + 1
    should_quarantine = max_consecutive_failures > 0 and next_failures >= max_consecutive_failures

    patch: dict[str, Any] = {
        "last_failure_at": now,
        "last_error": _masked_error(error),
        "updated_at": now,
    }
    if invalidate and (max_consecutive_failures <= 0 or should_quarantine):
        patch["is_valid"] = False
    if should_quarantine:
        patch["quarantined_at"] = now
        patch["quarantine_reason"] = (
            f"连续失败 {next_failures} 次，达到阈值 {max_consecutive_failures}"
        )
    if cooldown_seconds and cooldown_seconds > 0:
        patch["cooldown_until"] = now + timedelta(seconds=cooldown_seconds)

    await db[XHS_COOKIES_COLLECTION].update_one(
        {"account_name": account_name},
        {"$set": patch, "$inc": {"failure_count": 1, "consecutive_failures": 1}},
    )


async def select_xhs_proxy(config: dict[str, Any] | None = None) -> XhsProxyLease:
    """Select a proxy for one XHS request/client."""
    config = config if config is not None else await get_xhs_runtime_config()
    pool = _proxy_pool_config(config)
    if not _as_bool(pool.get("enabled"), default=False):
        return XhsProxyLease()

    provider = str(pool.get("provider") or "static").lower()
    static_proxies = _as_list(pool.get("proxies") or pool.get("static_proxies"))
    if provider == "static" and static_proxies:
        global _static_proxy_idx
        async with _static_proxy_lock:
            proxy_url = static_proxies[_static_proxy_idx % len(static_proxies)]
            _static_proxy_idx += 1
        return XhsProxyLease(proxy_url=proxy_url, source="static", provider="static")

    if provider in {"kuaidaili", "wandouhttp"}:
        try:
            pool_count = _as_int(pool.get("pool_count"), 2)
            validate = _as_bool(pool.get("validate"), default=False)
            cache_key = f"{provider}:{pool_count}:{validate}"
            async with _provider_pool_lock:
                proxy_pool = _provider_pool_cache.get(cache_key)
                if proxy_pool is None:
                    _ensure_mediacrawler_proxy_path()
                    import config as mc_config
                    from proxy.proxy_ip_pool import create_ip_pool

                    mc_config.IP_PROXY_PROVIDER_NAME = provider
                    mc_config.IP_PROXY_POOL_COUNT = pool_count
                    proxy_pool = await create_ip_pool(pool_count, enable_validate_ip=validate)
                    _provider_pool_cache[cache_key] = proxy_pool
            proxy_info = await proxy_pool.get_or_refresh_proxy()
            return XhsProxyLease(
                proxy_url=_proxy_model_to_url(proxy_info),
                source="provider",
                provider=provider,
            )
        except Exception as exc:
            logger.warning(f"[XHS] 代理池获取失败 provider={provider}: {exc}")
            if _as_bool(pool.get("fail_open"), default=True):
                return XhsProxyLease(source="provider_failed", provider=provider)
            raise

    return XhsProxyLease(source="unsupported", provider=provider)


def _ensure_mediacrawler_proxy_path() -> None:
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    mc_path = str(repo_root / "MediaCrawler")
    if mc_path not in sys.path:
        sys.path.insert(0, mc_path)


async def test_xhs_signer(
    *,
    db: AsyncIOMotorDatabase | None = None,
    account_name: str | None = None,
    verify_network: bool = False,
) -> dict[str, Any]:
    """Check xhsvm.js and optional network availability."""
    started = time.perf_counter()
    result: dict[str, Any] = {
        "ok": False,
        "script": {},
        "sign": {},
        "network": None,
        "elapsed_ms": 0,
    }
    try:
        from crawler_tools.xhs_client_v2 import XhsClientV2, _XHSVM_JS, _sign

        result["script"] = {
            "path": str(_XHSVM_JS),
            "exists": _XHSVM_JS.exists(),
        }
        if not _XHSVM_JS.exists():
            result["message"] = "xhsvm.js 不存在"
            return result

        sign = _sign("/api/sns/web/v2/user/me", {}, "")
        result["sign"] = {
            "ok": bool(sign.get("X-s") and sign.get("X-t")),
            "x_s_prefix": str(sign.get("X-s", ""))[:8],
            "x_t": sign.get("X-t"),
        }
        if not result["sign"]["ok"]:
            result["message"] = "签名字段缺失"
            return result

        if verify_network and db is not None:
            account_doc = None
            if account_name:
                account_doc = await xhs_dao.get_cookie_by_name(db, account_name)
            if not account_doc:
                lease = await select_xhs_account(db, purpose="signer_health")
                account_doc = {"account_name": lease.account_name, "cookie_string": lease.cookie_string}
            cookie = account_doc.get("cookie_string", "")
            config = await get_xhs_runtime_config()
            proxy = await select_xhs_proxy(config)
            client = XhsClientV2(
                cookie,
                proxy_url=proxy.proxy_url,
                request_timeout=_as_float(_proxy_pool_config(config).get("request_timeout"), 20.0),
            )
            try:
                pong = await client.pong()
                result["network"] = {
                    "ok": pong,
                    "account_name": account_doc.get("account_name"),
                    "proxy": proxy.to_dict(),
                }
                await record_xhs_account_result(
                    db,
                    account_doc.get("account_name"),
                    success=pong,
                    error=None if pong else "pong failed",
                    invalidate=not pong,
                )
            finally:
                await client.close()

        result["ok"] = bool(result["sign"]["ok"] and (result["network"] is None or result["network"]["ok"]))
        result["message"] = "签名脚本可用" if result["ok"] else "签名脚本可用，但网络验证失败"
        return result
    except Exception as exc:
        result["message"] = str(exc)
        return result
    finally:
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)


async def get_xhs_runtime_status(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    config = await get_xhs_runtime_config()
    pool = _account_pool_config(config)
    proxy_pool = _proxy_pool_config(config)
    now = _now()
    include_unverified = _as_bool(pool.get("include_unverified"), default=True)
    max_consecutive_failures = _as_int(
        pool.get("max_consecutive_failures"),
        DEFAULT_XHS_MAX_CONSECUTIVE_FAILURES,
    )
    request_policy = XhsRequestPolicy.from_config(config)
    total = await db[XHS_COOKIES_COLLECTION].count_documents({})
    usable_query = _account_selection_query(
        now=now,
        include_unverified=include_unverified,
        max_consecutive_failures=max_consecutive_failures,
        exclude_accounts=[],
    )
    usable = await db[XHS_COOKIES_COLLECTION].count_documents(usable_query)
    invalid = await db[XHS_COOKIES_COLLECTION].count_documents({"is_valid": False})
    cooled = await db[XHS_COOKIES_COLLECTION].count_documents({"cooldown_until": {"$gt": now}})
    quarantined_query: dict[str, Any] = (
        {"consecutive_failures": {"$gte": max_consecutive_failures}}
        if max_consecutive_failures > 0
        else {"_id": {"$exists": False}}
    )
    quarantined = await db[XHS_COOKIES_COLLECTION].count_documents(quarantined_query)
    return {
        "account_pool": {
            "enabled": _as_bool(pool.get("enabled"), default=True),
            "strategy": pool.get("strategy", "least_recently_used"),
            "search_pages_per_account": max(1, _as_int(pool.get("search_pages_per_account"), 1)),
            "search_retries_per_page": max(1, _as_int(pool.get("search_retries_per_page"), 1)),
            "search_page_size": request_policy.page_size,
            "search_max_pages_per_keyword": request_policy.max_pages_per_keyword,
            "request_interval_min_seconds": request_policy.interval_min_seconds,
            "request_interval_max_seconds": request_policy.interval_max_seconds,
            "search_fallback_enabled": _as_bool(
                pool.get("search_fallback_enabled"),
                default=False,
            ),
            "search_health_check_enabled": _as_bool(
                pool.get("search_health_check_enabled"),
                default=False,
            ),
            "total": total,
            "usable": usable,
            "invalid": invalid,
            "cooling_down": cooled,
            "quarantined": quarantined,
            "max_consecutive_failures": max_consecutive_failures,
        },
        "proxy_pool": {
            "enabled": _as_bool(proxy_pool.get("enabled"), default=False),
            "provider": proxy_pool.get("provider", "static"),
            "static_count": len(_as_list(proxy_pool.get("proxies") or proxy_pool.get("static_proxies"))),
            "fail_open": _as_bool(proxy_pool.get("fail_open"), default=True),
        },
    }


async def resolve_xhs_search_concurrency(
    db: AsyncIOMotorDatabase,
    *,
    requested: int,
    workload_size: int,
) -> int:
    """按任务上限、关键词数和当前可用账号数计算搜索 worker 数。"""
    from api.services.info_collection.tuning import MAX_XHS_SEARCH_CONCURRENCY

    requested = max(1, min(int(requested), MAX_XHS_SEARCH_CONCURRENCY))
    workload_size = max(1, int(workload_size))
    try:
        status = await get_xhs_runtime_status(db)
        usable_accounts = int((status.get("account_pool") or {}).get("usable") or 0)
    except Exception as exc:
        logger.warning("读取小红书账号池容量失败，搜索并发降级为 1: %s", exc)
        usable_accounts = 0

    effective = min(requested, workload_size, max(1, usable_accounts))
    logger.info(
        "小红书搜索并发预算 requested=%s workload=%s usable_accounts=%s effective=%s",
        requested,
        workload_size,
        usable_accounts,
        effective,
    )
    return effective
