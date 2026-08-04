"""统一的信息采集并发参数与数据库运行时配置加载。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.logger import get_logger


logger = get_logger("collection_runtime_tuning")


DEFAULT_ASSET_PROBE_CONCURRENCY = 96
DEFAULT_URL_PROBE_CONCURRENCY = 64
DEFAULT_URL_SCAN_CONCURRENCY = 24
DEFAULT_COPYWRITING_CONCURRENCY = 6
DEFAULT_XHS_SEARCH_CONCURRENCY = 1
DEFAULT_COMPANY_SCAN_CONCURRENCY = 6
DEFAULT_SCHOLAR_CONCURRENCY = 2
DEFAULT_LLM_CONCURRENCY = 12
DEFAULT_URL_SCAN_AGENT_TIMEOUT_SECONDS = 900
DEFAULT_LLM_QUOTA_COOLDOWN_SECONDS = 120
DEFAULT_LLM_QUOTA_MAX_COOLDOWN_SECONDS = 900

MAX_ASSET_PROBE_CONCURRENCY = 128
MAX_URL_PROBE_CONCURRENCY = 128
MAX_URL_SCAN_CONCURRENCY = 48
MAX_COPYWRITING_CONCURRENCY = 12
MAX_XHS_SEARCH_CONCURRENCY = 8
MAX_COMPANY_SCAN_CONCURRENCY = 12
MAX_SCHOLAR_CONCURRENCY = 4
MAX_LLM_CONCURRENCY = 32
MAX_URL_SCAN_AGENT_TIMEOUT_SECONDS = 1500
MAX_LLM_QUOTA_COOLDOWN_SECONDS = 1800


def _bounded(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _bounded_timeout(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


@dataclass(frozen=True)
class CollectionRuntimeTuning:
    """单机采集并发预算；任务参数可在安全上限内覆盖这些默认值。"""

    asset_probe_concurrency: int = DEFAULT_ASSET_PROBE_CONCURRENCY
    url_probe_concurrency: int = DEFAULT_URL_PROBE_CONCURRENCY
    url_scan_concurrency: int = DEFAULT_URL_SCAN_CONCURRENCY
    copywriting_concurrency: int = DEFAULT_COPYWRITING_CONCURRENCY
    xhs_search_concurrency: int = DEFAULT_XHS_SEARCH_CONCURRENCY
    company_scan_concurrency: int = DEFAULT_COMPANY_SCAN_CONCURRENCY
    scholar_concurrency: int = DEFAULT_SCHOLAR_CONCURRENCY
    llm_concurrency: int = DEFAULT_LLM_CONCURRENCY
    url_scan_agent_timeout_seconds: int = DEFAULT_URL_SCAN_AGENT_TIMEOUT_SECONDS
    llm_quota_cooldown_seconds: int = DEFAULT_LLM_QUOTA_COOLDOWN_SECONDS
    llm_quota_max_cooldown_seconds: int = DEFAULT_LLM_QUOTA_MAX_COOLDOWN_SECONDS

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "CollectionRuntimeTuning":
        data = config or {}
        return cls(
            asset_probe_concurrency=_bounded(
                data.get("asset_probe_concurrency"),
                default=DEFAULT_ASSET_PROBE_CONCURRENCY,
                maximum=MAX_ASSET_PROBE_CONCURRENCY,
            ),
            url_probe_concurrency=_bounded(
                data.get("url_probe_concurrency"),
                default=DEFAULT_URL_PROBE_CONCURRENCY,
                maximum=MAX_URL_PROBE_CONCURRENCY,
            ),
            url_scan_concurrency=_bounded(
                data.get("url_scan_concurrency"),
                default=DEFAULT_URL_SCAN_CONCURRENCY,
                maximum=MAX_URL_SCAN_CONCURRENCY,
            ),
            copywriting_concurrency=_bounded(
                data.get("copywriting_concurrency"),
                default=DEFAULT_COPYWRITING_CONCURRENCY,
                maximum=MAX_COPYWRITING_CONCURRENCY,
            ),
            xhs_search_concurrency=_bounded(
                data.get("xhs_search_concurrency"),
                default=DEFAULT_XHS_SEARCH_CONCURRENCY,
                maximum=MAX_XHS_SEARCH_CONCURRENCY,
            ),
            company_scan_concurrency=_bounded(
                data.get("company_scan_concurrency"),
                default=DEFAULT_COMPANY_SCAN_CONCURRENCY,
                maximum=MAX_COMPANY_SCAN_CONCURRENCY,
            ),
            scholar_concurrency=_bounded(
                data.get("scholar_concurrency"),
                default=DEFAULT_SCHOLAR_CONCURRENCY,
                maximum=MAX_SCHOLAR_CONCURRENCY,
            ),
            llm_concurrency=_bounded(
                data.get("llm_concurrency"),
                default=DEFAULT_LLM_CONCURRENCY,
                maximum=MAX_LLM_CONCURRENCY,
            ),
            url_scan_agent_timeout_seconds=_bounded_timeout(
                data.get("url_scan_agent_timeout_seconds"),
                default=DEFAULT_URL_SCAN_AGENT_TIMEOUT_SECONDS,
                minimum=60,
                maximum=MAX_URL_SCAN_AGENT_TIMEOUT_SECONDS,
            ),
            llm_quota_cooldown_seconds=_bounded(
                data.get("llm_quota_cooldown_seconds"),
                default=DEFAULT_LLM_QUOTA_COOLDOWN_SECONDS,
                maximum=MAX_LLM_QUOTA_COOLDOWN_SECONDS,
            ),
            llm_quota_max_cooldown_seconds=_bounded(
                data.get("llm_quota_max_cooldown_seconds"),
                default=DEFAULT_LLM_QUOTA_MAX_COOLDOWN_SECONDS,
                maximum=MAX_LLM_QUOTA_COOLDOWN_SECONDS,
            ),
        )

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def with_overrides(self, **overrides: Any) -> "CollectionRuntimeTuning":
        """应用单任务覆盖，并复用同一套类型转换和安全上限。"""
        data: dict[str, Any] = self.as_dict()
        for key, value in overrides.items():
            if key in data and value is not None:
                data[key] = value
        return type(self).from_config(data)


async def get_collection_runtime_tuning(db: Any | None = None) -> CollectionRuntimeTuning:
    """从 MongoDB 配置中心读取采集预算，缺失时使用适配当前服务器的默认值。"""
    try:
        if db is None:
            from api.services.runtime_config import get_runtime_config_section

            config = await get_runtime_config_section("collection_runtime")
        else:
            from api.dao import config as config_dao

            document = await config_dao.get_config(db, "collection_runtime")
            config = document.get("config", {}) if document else {}
    except Exception as exc:
        logger.warning("读取采集并发配置失败，使用内置安全默认值: %s", exc)
        config = {}
    return CollectionRuntimeTuning.from_config(config)
