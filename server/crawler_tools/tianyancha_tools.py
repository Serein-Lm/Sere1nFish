"""天眼查企业关系与 ICP 统一适配器。

第三方接口字段、鉴权和错误码只在本模块出现。业务层消费结构化结果，
不会直接依赖天眼查响应格式或明文 API Key。
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from core.logger import get_logger

logger = get_logger("tianyancha_tools")

TIANYANCHA_API_BASE = "https://open.api.tianyancha.com"
CONTROL_RIGHT_PATH = "/services/v4/open/companyholding"
ICP_PATH = "/services/open/ipr/icp/3.0"
SUCCESS_CODE = 0
NO_RESULT_CODE = 300000
PERMISSION_DENIED_CODE = 300005


class TianyanchaApiError(RuntimeError):
    """不包含密钥的稳定天眼查错误。"""

    def __init__(self, *, code: int, reason: str, endpoint: str) -> None:
        self.code = int(code)
        self.reason = str(reason or "天眼查请求失败")
        self.endpoint = endpoint
        super().__init__(f"天眼查接口错误({self.code}): {self.reason}")


@dataclass(slots=True)
class IcpRecord:
    domain: str
    websites: list[str] = field(default_factory=list)
    site_name: str = ""
    license_no: str = ""
    company_name: str = ""
    examined_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "websites": self.websites,
            "site_name": self.site_name,
            "license_no": self.license_no,
            "company_name": self.company_name,
            "examined_at": self.examined_at,
        }


@dataclass(slots=True)
class ControlledCompany:
    name: str
    provider_id: str = ""
    alias: str = ""
    ownership_percent: float = 100.0
    registration_status: str = ""
    legal_person_name: str = ""
    registered_capital: str = ""
    established_at: int | None = None
    relation_paths: list[list[dict[str, Any]]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider_id": self.provider_id,
            "alias": self.alias,
            "ownership_percent": self.ownership_percent,
            "registration_status": self.registration_status,
            "legal_person_name": self.legal_person_name,
            "registered_capital": self.registered_capital,
            "established_at": self.established_at,
            "relation_paths": self.relation_paths,
        }


@dataclass(slots=True)
class ControlRightResult:
    companies: list[ControlledCompany] = field(default_factory=list)
    total_reported: int = 0
    pages_fetched: int = 0
    truncated: bool = False


async def _load_config() -> dict[str, Any]:
    try:
        from api.dao import config as config_dao
        from api.db.mongodb import get_db

        return await config_dao.get_tool_config(get_db(), "tianyancha")
    except Exception as exc:  # noqa: BLE001
        logger.warning("天眼查配置读取失败: %s", exc)
        return {}


async def get_configured_api_key() -> str:
    config = await _load_config()
    return str(config.get("api_key") or "").strip()


def normalize_domain(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        hostname = (urlsplit(candidate).hostname or "").strip(".")
    except ValueError:
        return ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def parse_percent(value: Any) -> Decimal | None:
    """统一处理 ``1``、``100``、``100%`` 等供应商比例格式。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    has_percent = text.endswith("%")
    if has_percent:
        text = text[:-1].strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not has_percent and Decimal("0") <= number <= Decimal("1"):
        number *= Decimal("100")
    return number


def _company_nodes(path: Any) -> list[dict[str, Any]]:
    if not isinstance(path, list):
        return []
    return [
        node
        for node in path
        if isinstance(node, dict) and str(node.get("type") or "").lower() == "company"
    ]


def _is_direct_path(path: Any, *, root_name: str, child_name: str) -> bool:
    companies = _company_nodes(path)
    if len(companies) != 2:
        return False
    names = [str(node.get("value") or "").strip() for node in companies]
    if root_name and names[0] and names[0] != root_name:
        return False
    return not child_name or not names[1] or names[1] == child_name


def parse_direct_wholly_controlled_items(
    items: Any,
    *,
    root_name: str,
) -> list[ControlledCompany]:
    """只保留实际控制权结果中的第一层、最终持股恰好 100% 的企业。"""
    if not isinstance(items, list):
        return []
    parsed: dict[str, ControlledCompany] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name == root_name or parse_percent(item.get("percent")) != Decimal("100"):
            continue
        raw_paths = item.get("chainList") or []
        direct_paths = [
            path
            for path in raw_paths
            if _is_direct_path(path, root_name=root_name, child_name=name)
        ]
        if not direct_paths:
            continue
        provider_id = str(item.get("cid") or item.get("id") or "").strip()
        key = provider_id or name
        parsed[key] = ControlledCompany(
            name=name,
            provider_id=provider_id,
            alias=str(item.get("alias") or "").strip(),
            ownership_percent=100.0,
            registration_status=str(item.get("regStatus") or "").strip(),
            legal_person_name=str(item.get("legalPersonName") or "").strip(),
            registered_capital=str(item.get("regCapital") or "").strip(),
            established_at=(
                int(item["estiblishTime"])
                if isinstance(item.get("estiblishTime"), (int, float))
                else None
            ),
            relation_paths=direct_paths,
        )
    return list(parsed.values())


def parse_icp_records(items: Any) -> list[IcpRecord]:
    if not isinstance(items, list):
        return []
    records: list[IcpRecord] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        websites_raw = item.get("webSite") or item.get("website") or []
        if isinstance(websites_raw, str):
            websites_raw = [websites_raw]
        websites = [
            domain
            for domain in (normalize_domain(value) for value in websites_raw)
            if domain
        ]
        domain = normalize_domain(
            item.get("ym")
            or item.get("domain")
            or item.get("domainName")
            or (websites[0] if websites else "")
        )
        if not domain or domain in seen:
            continue
        seen.add(domain)
        records.append(
            IcpRecord(
                domain=domain,
                websites=list(dict.fromkeys(websites)),
                site_name=str(item.get("webName") or "").strip(),
                license_no=str(item.get("liscense") or item.get("license") or "").strip(),
                company_name=str(item.get("companyName") or "").strip(),
                examined_at=str(item.get("examineDate") or "").strip(),
            )
        )
    return records


class TianyanchaClient:
    """天眼查 HTTP 客户端；可注入 key/session，便于测试和复用连接。"""

    def __init__(
        self,
        api_key: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self._session = session
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    @classmethod
    async def from_runtime_config(cls) -> "TianyanchaClient":
        return cls(await get_configured_api_key())

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise TianyanchaApiError(code=300009, reason="API Key 未配置", endpoint=path)
        owns_session = self._session is None
        session = self._session or aiohttp.ClientSession()
        try:
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    async with session.get(
                        f"{TIANYANCHA_API_BASE}{path}",
                        params=params,
                        headers={"Authorization": self.api_key},
                        timeout=self.timeout,
                    ) as response:
                        if response.status == 429 or response.status >= 500:
                            raise aiohttp.ClientResponseError(
                                response.request_info,
                                response.history,
                                status=response.status,
                                message="天眼查服务暂时不可用",
                            )
                        response.raise_for_status()
                        data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise TianyanchaApiError(
                            code=300001,
                            reason="响应不是 JSON 对象",
                            endpoint=path,
                        )
                    code = int(data.get("error_code") or data.get("code") or 0)
                    if code in {300004, 300012} and attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    if code not in {SUCCESS_CODE, NO_RESULT_CODE}:
                        raise TianyanchaApiError(
                            code=code,
                            reason=str(data.get("reason") or data.get("message") or "请求失败"),
                            endpoint=path,
                        )
                    return data
                except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
            reason = "请求超时" if isinstance(last_error, asyncio.TimeoutError) else f"网络请求失败: {last_error}"
            raise TianyanchaApiError(code=300001, reason=reason, endpoint=path) from last_error
        finally:
            if owns_session:
                await session.close()

    async def list_direct_wholly_controlled(
        self,
        company_name: str,
        *,
        max_entities: int = 100,
        page_concurrency: int = 4,
    ) -> ControlRightResult:
        """分页读取实际控制权，并筛出一层 100% 控股企业。"""
        page_size = 20
        first = await self._request(
            CONTROL_RIGHT_PATH,
            {"keyword": company_name, "pageNum": 1, "pageSize": page_size},
        )
        result = first.get("result") if isinstance(first.get("result"), dict) else {}
        total = int(result.get("total") or 0)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        entity_limit = max(1, max_entities)
        companies: dict[str, ControlledCompany] = {}

        def _consume(payload: dict[str, Any]) -> None:
            for company in parse_direct_wholly_controlled_items(
                payload.get("items") or [],
                root_name=company_name,
            ):
                companies[company.provider_id or company.name] = company
                if len(companies) >= entity_limit:
                    break

        _consume(result)
        pages_fetched = 1
        next_page = 2
        batch_size = max(1, min(page_concurrency, 12))
        while next_page <= total_pages and len(companies) < entity_limit:
            page_numbers = list(
                range(next_page, min(total_pages + 1, next_page + batch_size))
            )
            payloads = await asyncio.gather(
                *[
                    self._request(
                        CONTROL_RIGHT_PATH,
                        {"keyword": company_name, "pageNum": page, "pageSize": page_size},
                    )
                    for page in page_numbers
                ]
            )
            pages_fetched += len(payloads)
            next_page += len(payloads)
            for payload in payloads:
                page_result = (
                    payload.get("result")
                    if isinstance(payload.get("result"), dict)
                    else {}
                )
                _consume(page_result)
                if len(companies) >= entity_limit:
                    break

        values = list(companies.values())[:entity_limit]
        return ControlRightResult(
            companies=values,
            total_reported=total,
            pages_fetched=pages_fetched,
            truncated=next_page <= total_pages or len(companies) > len(values),
        )

    async def get_icp_records(self, keyword: str) -> list[IcpRecord]:
        payload = await self._request(
            ICP_PATH,
            {"keyword": keyword, "icpType": 1, "pageNum": 1, "pageSize": 20},
        )
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        return parse_icp_records(result.get("items") or [])

async def validate_key(api_key: str | None = None) -> tuple[bool, str]:
    """使用低成本 ICP 查询验证密钥，不输出或记录明文密钥。"""
    client = TianyanchaClient(api_key or await get_configured_api_key(), timeout_seconds=15)
    try:
        await client.get_icp_records("北京百度网讯科技有限公司")
    except TianyanchaApiError as exc:
        return False, str(exc)
    return True, "天眼查 API Key 可用（ICP备案接口）"
