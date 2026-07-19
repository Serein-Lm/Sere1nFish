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
DEFAULT_LLM_CONCURRENCY = 12
DEFAULT_LLM_QUOTA_COOLDOWN_SECONDS = 120
DEFAULT_LLM_QUOTA_MAX_COOLDOWN_SECONDS = 900

MAX_ASSET_PROBE_CONCURRENCY = 128
MAX_URL_PROBE_CONCURRENCY = 128
MAX_URL_SCAN_CONCURRENCY = 48
MAX_COPYWRITING_CONCURRENCY = 12
MAX_XHS_SEARCH_CONCURRENCY = 8
MAX_COMPANY_SCAN_CONCURRENCY = 12
MAX_LLM_CONCURRENCY = 32
MAX_LLM_QUOTA_COOLDOWN_SECONDS = 1800


def _bounded(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


@dataclass(frozen=True)
class CollectionRuntimeTuning:
    """单机采集并发预算；任务参数可在安全上限内覆盖这些默认值。"""

    asset_probe_concurrency: int = DEFAULT_ASSET_PROBE_CONCURRENCY
    url_probe_concurrency: int = DEFAULT_URL_PROBE_CONCURRENCY
    url_scan_concurrency: int = DEFAULT_URL_SCAN_CONCURRENCY
    copywriting_concurrency: int = DEFAULT_COPYWRITING_CONCURRENCY
    xhs_search_concurrency: int = DEFAULT_XHS_SEARCH_CONCURRENCY
    company_scan_concurrency: int = DEFAULT_COMPANY_SCAN_CONCURRENCY
    llm_concurrency: int = DEFAULT_LLM_CONCURRENCY
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
            llm_concurrency=_bounded(
                data.get("llm_concurrency"),
                default=DEFAULT_LLM_CONCURRENCY,
                maximum=MAX_LLM_CONCURRENCY,
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


async def get_collection_runtime_tuning() -> CollectionRuntimeTuning:
    """从 MongoDB 配置中心读取采集预算，缺失时使用适配当前服务器的默认值。"""
    from api.services.runtime_config import get_runtime_config_section

    try:
        config = await get_runtime_config_section("collection_runtime")
    except Exception as exc:
        logger.warning("读取采集并发配置失败，使用内置安全默认值: %s", exc)
        config = {}
    return CollectionRuntimeTuning.from_config(config)
