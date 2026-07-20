"""天眼查企业关系与 ICP 统一适配器。

第三方接口字段、鉴权和错误码只在本模块出现。业务层消费结构化结果，
不会直接依赖天眼查响应格式或明文 API Key。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from core.logger import get_logger

logger = get_logger("tianyancha_tools")

TIANYANCHA_API_BASE = "https://open.api.tianyancha.com"
OUTBOUND_INVESTMENT_PATH = "/services/open/ic/inverst/2.0"
OUTBOUND_INVESTMENT_INTERFACE_ID = 823
ICP_PATH = "/services/open/ipr/icp/3.0"
BIDDING_PATH = "/services/open/m/bids/2.0"
SUCCESS_CODE = 0
NO_RESULT_CODE = 300000
PERMISSION_DENIED_CODE = 300005
INACTIVE_REGISTRATION_MARKERS = (
    "注销",
    "吊销",
    "撤销",
    "清算",
    "歇业",
    "迁出",
)
CHINA_TIMEZONE = timezone(timedelta(hours=8))


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
class OutboundInvestmentResult:
    companies: list[ControlledCompany] = field(default_factory=list)
    total_reported: int = 0
    pages_fetched: int = 0
    truncated: bool = False


@dataclass(slots=True)
class BiddingRecord:
    """不暴露供应商字段命名的招投标记录。"""

    record_id: str
    provider_record_id: str = ""
    provider_uuid: str = ""
    title: str = ""
    announcement_type: str = ""
    stage: str = ""
    published_on: str = ""
    province: str = ""
    purchaser: str = ""
    agency: str = ""
    amount: str = ""
    winner: str = ""
    enterprise_identity: str = ""
    detail_url: str = ""
    provider_url: str = ""
    summary: str = ""
    introduction: str = ""
    content_html: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        result = {
            "record_id": self.record_id,
            "provider": "tianyancha",
            "provider_record_id": self.provider_record_id,
            "provider_uuid": self.provider_uuid,
            "title": self.title,
            "announcement_type": self.announcement_type,
            "stage": self.stage,
            "published_on": self.published_on,
            "province": self.province,
            "purchaser": self.purchaser,
            "agency": self.agency,
            "amount": self.amount,
            "winner": self.winner,
            "enterprise_identity": self.enterprise_identity,
            "detail_url": self.detail_url,
            "provider_url": self.provider_url,
            "summary": self.summary,
            "introduction": self.introduction,
        }
        if include_content:
            result["content_html"] = self.content_html
        return result


@dataclass(slots=True)
class BiddingSearchResult:
    records: list[BiddingRecord] = field(default_factory=list)
    total_reported: int = 0
    page_num: int = 1
    page_size: int = 20
    pages_fetched: int = 0
    truncated: bool = False
    bid_type: str = "2"
    publish_start: str = ""
    publish_end: str = ""


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


def is_operating_registration_status(value: Any) -> bool:
    """未知状态保留，仅排除供应商明确标记为非经营的主体。"""
    status = str(value or "").strip()
    return not any(marker in status for marker in INACTIVE_REGISTRATION_MARKERS)


def parse_direct_wholly_owned_investments(
    items: Any,
    *,
    root_name: str,
) -> list[ControlledCompany]:
    """保留直接持股恰好 100% 且仍经营的第一层企业。"""
    if not isinstance(items, list):
        return []
    parsed: dict[str, ControlledCompany] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if (
            not name
            or name == root_name
            or parse_percent(item.get("percent")) != Decimal("100")
            or not is_operating_registration_status(item.get("regStatus"))
        ):
            continue
        provider_id = str(item.get("id") or item.get("cid") or "").strip()
        key = provider_id or name
        relation_path: list[dict[str, Any]] = []
        if root_name:
            relation_path.append({"type": "company", "value": root_name})
        relation_path.extend(
            [
                {"type": "percent", "value": str(item.get("percent") or "100%")},
                {"type": "company", "value": name, "cid": provider_id},
            ]
        )
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
            relation_paths=[relation_path],
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


def _published_on(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        timestamp = int(text)
    except (TypeError, ValueError):
        return text[:10] if len(text) >= 10 else text
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=CHINA_TIMEZONE).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return text


def _provider_text(value: Any) -> str:
    """收敛供应商的空数组字符串、数组和对象字段，不向领域层泄漏原始形态。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "companyName", "title", "value"):
            normalized = _provider_text(value.get(key))
            if normalized:
                return normalized
        values = [_provider_text(item) for item in value.values()]
        return "、".join(dict.fromkeys(item for item in values if item))
    if isinstance(value, (list, tuple, set)):
        values = [_provider_text(item) for item in value]
        return "、".join(dict.fromkeys(item for item in values if item))

    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "undefined"}:
        return ""
    if text.startswith(("[", "{")):
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            pass
        else:
            return _provider_text(decoded)
    return text


def _bidding_record_id(item: dict[str, Any]) -> str:
    provider_key = str(item.get("uuid") or item.get("id") or "").strip()
    if not provider_key:
        provider_key = "|".join(
            str(item.get(key) or "").strip()
            for key in ("link", "title", "publishTime", "purchaser")
        )
    digest = hashlib.sha256(f"tianyancha:bidding:{provider_key}".encode("utf-8")).hexdigest()
    return "bid_" + digest[:24]


def parse_bidding_records(items: Any) -> list[BiddingRecord]:
    """将天眼查招投标响应转换为稳定领域结构并按记录 ID 去重。"""
    if not isinstance(items, list):
        return []
    records: dict[str, BiddingRecord] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        record_id = _bidding_record_id(item)
        records[record_id] = BiddingRecord(
            record_id=record_id,
            provider_record_id=_provider_text(item.get("id")),
            provider_uuid=_provider_text(item.get("uuid")),
            title=_provider_text(item.get("title")),
            announcement_type=_provider_text(item.get("type")),
            stage=_provider_text(item.get("stage")),
            published_on=_published_on(item.get("publishTime")),
            province=_provider_text(item.get("province")),
            purchaser=_provider_text(item.get("purchaser")),
            agency=_provider_text(item.get("proxy")),
            amount=_provider_text(item.get("bidAmount")),
            winner=_provider_text(item.get("bidWinner")),
            enterprise_identity=_provider_text(item.get("enterpriseIdentity")),
            detail_url=_provider_text(item.get("link")),
            provider_url=_provider_text(item.get("bidUrl")),
            summary=_provider_text(item.get("abs")),
            introduction=_provider_text(item.get("intro")),
            content_html=str(item.get("content") or ""),
            raw_payload=dict(item),
        )
    return list(records.values())


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

    async def list_direct_wholly_owned_investments(
        self,
        company_name: str,
        *,
        max_entities: int = 100,
        page_concurrency: int = 4,
    ) -> OutboundInvestmentResult:
        """分页读取对外投资，并筛出第一层直接持股 100% 的经营中企业。"""
        page_size = 20
        first = await self._request(
            OUTBOUND_INVESTMENT_PATH,
            {"keyword": company_name, "pageNum": 1, "pageSize": page_size},
        )
        result = first.get("result") if isinstance(first.get("result"), dict) else {}
        total = int(result.get("total") or 0)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        entity_limit = max(1, max_entities)
        companies: dict[str, ControlledCompany] = {}

        def _consume(payload: dict[str, Any]) -> None:
            for company in parse_direct_wholly_owned_investments(
                payload.get("items") or [],
                root_name=company_name,
            ):
                companies[company.provider_id or company.name] = company

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
                        OUTBOUND_INVESTMENT_PATH,
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

        values = list(companies.values())[:entity_limit]
        return OutboundInvestmentResult(
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

    async def search_bids(
        self,
        company_name: str,
        *,
        bid_type: str = "2",
        page_num: int = 1,
        page_size: int = 20,
        lookback_days: int = 180,
        end_date: date | None = None,
    ) -> BiddingSearchResult:
        """按法定主体查询单页招投标公告。"""
        keyword = str(company_name or "").strip()
        if not keyword:
            raise ValueError("公司法定名称不能为空")
        safe_page = max(1, int(page_num))
        safe_size = max(1, min(int(page_size), 20))
        safe_days = max(1, min(int(lookback_days), 3650))
        publish_end = end_date or datetime.now(CHINA_TIMEZONE).date()
        publish_start = publish_end - timedelta(days=safe_days)
        payload = await self._request(
            BIDDING_PATH,
            {
                "keyword": keyword,
                "type": str(bid_type or "2"),
                "publishStartTime": publish_start.isoformat(),
                "publishEndTime": publish_end.isoformat(),
                "pageNum": safe_page,
                "pageSize": safe_size,
            },
        )
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        return BiddingSearchResult(
            records=parse_bidding_records(result.get("items") or []),
            total_reported=int(result.get("total") or 0),
            page_num=safe_page,
            page_size=safe_size,
            pages_fetched=1,
            bid_type=str(bid_type or "2"),
            publish_start=publish_start.isoformat(),
            publish_end=publish_end.isoformat(),
        )

    async def search_all_bids(
        self,
        company_name: str,
        *,
        bid_type: str = "2",
        page_size: int = 20,
        max_records: int = 2000,
        page_concurrency: int = 3,
        lookback_days: int = 180,
        end_date: date | None = None,
    ) -> BiddingSearchResult:
        """分页读取招投标公告，并以稳定记录 ID 去重。"""
        safe_size = max(1, min(int(page_size), 20))
        safe_limit = max(1, min(int(max_records), 2000))
        first = await self.search_bids(
            company_name,
            bid_type=bid_type,
            page_num=1,
            page_size=safe_size,
            lookback_days=lookback_days,
            end_date=end_date,
        )
        total_pages = max(1, math.ceil(first.total_reported / safe_size))
        pages_to_fetch = min(total_pages, max(1, math.ceil(safe_limit / safe_size)))
        resolved_end_date = end_date or date.fromisoformat(first.publish_end)
        records_by_id = {record.record_id: record for record in first.records}
        pages_fetched = 1
        next_page = 2
        batch_size = max(1, min(int(page_concurrency), 6))

        while next_page <= pages_to_fetch:
            page_numbers = list(
                range(next_page, min(pages_to_fetch + 1, next_page + batch_size))
            )
            pages = await asyncio.gather(
                *[
                    self.search_bids(
                        company_name,
                        bid_type=bid_type,
                        page_num=page,
                        page_size=safe_size,
                        lookback_days=lookback_days,
                        end_date=resolved_end_date,
                    )
                    for page in page_numbers
                ]
            )
            pages_fetched += len(pages)
            next_page += len(pages)
            for page in pages:
                for record in page.records:
                    records_by_id.setdefault(record.record_id, record)

        records = list(records_by_id.values())[:safe_limit]
        return BiddingSearchResult(
            records=records,
            total_reported=first.total_reported,
            page_num=1,
            page_size=safe_size,
            pages_fetched=pages_fetched,
            truncated=first.total_reported > len(records),
            bid_type=first.bid_type,
            publish_start=first.publish_start,
            publish_end=first.publish_end,
        )


async def validate_key(api_key: str | None = None) -> tuple[bool, str]:
    """使用低成本 ICP 查询验证密钥，不输出或记录明文密钥。"""
    client = TianyanchaClient(api_key or await get_configured_api_key(), timeout_seconds=15)
    try:
        await client.get_icp_records("北京百度网讯科技有限公司")
    except TianyanchaApiError as exc:
        return False, str(exc)
    return True, "天眼查 API Key 可用（ICP备案接口）"
