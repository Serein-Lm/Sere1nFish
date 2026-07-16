"""
FOFA API 查询工具

功能：
1. FOFA API 查询（支持 domain 和 cert 两种搜索）
2. API Key 有效性校验

设计上镜像 crawler_tools/hunter_tools.py，保持外部资产情报适配层结构一致。
key 统一经 api.dao.config.get_tool_config(db, "fofa") 读取（Fernet 加密存储）。
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from core.logger import get_logger

logger = get_logger("fofa_tools")

FOFA_SEARCH_URL = "https://fofa.info/api/v1/search/all"
FOFA_INFO_URL = "https://fofa.info/api/v1/info/my"

# FOFA 返回字段顺序（与请求 fields 一一对应）
FOFA_FIELDS = [
    "host",
    "ip",
    "port",
    "protocol",
    "domain",
    "title",
    "link",
    "cert.domain",
]

# 含 cert 的查询 size 上限为 2000（见 FOFA API 文档）
_CERT_SIZE_CAP = 2000
_DEFAULT_SIZE_CAP = 10000


@dataclass
class FofaAsset:
    """FOFA 查询结果（单条资产）"""

    host: str = ""
    ip: str = ""
    port: str = ""
    protocol: str = ""
    domain: str = ""
    title: str = ""
    link: str = ""
    cert_domain: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "ip": self.ip,
            "port": self.port,
            "protocol": self.protocol,
            "domain": self.domain,
            "title": self.title,
            "link": self.link,
            "cert_domain": self.cert_domain,
        }


async def _load_fofa_config_from_db() -> dict[str, Any]:
    """从数据库加载 FOFA 配置"""
    try:
        from api.db.mongodb import get_db
        from api.dao import config as config_dao

        db = get_db()
        return await config_dao.get_tool_config(db, "fofa")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"FOFA 配置读取失败: {exc}")
        return {}


async def get_configured_api_key() -> str:
    """读取数据库中已解密的 FOFA Key，供统一 Provider 做快速预检。"""
    config = await _load_fofa_config_from_db()
    return str(config.get("api_key") or "").strip()


def _fofa_base64_encode(query: str) -> str:
    """FOFA qbase64 使用标准 base64 编码"""
    return base64.b64encode(query.encode()).decode()


def _format_fofa_query(search_type: str, query: str) -> str:
    """
    格式化 FOFA 查询语法。

    Args:
        search_type: "domain" 或 "cert"
        query: 查询值（根域名或证书关键字）；若已含 = 视为原生语法直接返回
    """
    if "=" in query or " || " in query or " && " in query:
        return query
    if search_type == "domain":
        return f'domain="{query}"'
    if search_type == "cert":
        return f'cert="{query}"'
    return query


def _parse_results(results: list[list[Any]], fields: list[str]) -> list[FofaAsset]:
    """把 FOFA results 二维数组按 fields 顺序映射为 FofaAsset。"""
    field_index = {name: idx for idx, name in enumerate(fields)}

    def _cell(row: list[Any], name: str) -> str:
        idx = field_index.get(name)
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx] or "").strip()

    assets: list[FofaAsset] = []
    for row in results:
        if not isinstance(row, list):
            continue
        assets.append(
            FofaAsset(
                host=_cell(row, "host"),
                ip=_cell(row, "ip"),
                port=_cell(row, "port"),
                protocol=_cell(row, "protocol"),
                domain=_cell(row, "domain"),
                title=_cell(row, "title"),
                link=_cell(row, "link"),
                cert_domain=_cell(row, "cert.domain"),
            )
        )
    return assets


async def search_fofa(
    query: str,
    search_type: str = "domain",
    size: int = 100,
    api_key: str | None = None,
    full: bool = False,
    raise_on_error: bool = False,
) -> list[FofaAsset]:
    """
    FOFA API 搜索（支持 domain / cert 两种搜索）。

    Args:
        query: 查询内容（根域名 或 证书关键字）
        search_type: "domain" 或 "cert"
        size: 每次查询数量（cert 查询上限 2000，其它上限 10000）
        api_key: FOFA API Key（不提供则从数据库读取）
        full: 是否搜索全部历史数据（默认仅一年内）

    Returns:
        FofaAsset 列表
    """
    if not api_key:
        api_key = await get_configured_api_key()

    if not api_key:
        logger.warning("FOFA API Key 未配置")
        return []

    size_cap = _CERT_SIZE_CAP if search_type == "cert" else _DEFAULT_SIZE_CAP
    size = max(1, min(size, size_cap))

    formatted_query = _format_fofa_query(search_type, query)
    logger.info(f"FOFA 查询语句: {formatted_query} (size={size}, full={full})")

    params = {
        "key": api_key,
        "qbase64": _fofa_base64_encode(formatted_query),
        "fields": ",".join(FOFA_FIELDS),
        "page": "1",
        "size": str(size),
        "full": "true" if full else "false",
        "r_type": "json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                FOFA_SEARCH_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
    except asyncio.TimeoutError:
        logger.warning("FOFA 查询超时")
        if raise_on_error:
            raise TimeoutError("FOFA 查询超时")
        return []
    except Exception as exc:  # noqa: BLE001
        logger.error(f"FOFA 查询失败: {exc}")
        if raise_on_error:
            raise RuntimeError(f"FOFA 查询失败: {exc}") from exc
        return []

    if data.get("error"):
        error_message = str(data.get("errmsg") or data)
        logger.warning(f"FOFA 查询返回错误: {error_message}")
        if raise_on_error:
            raise RuntimeError(f"FOFA 查询返回错误: {error_message}")
        return []

    results = data.get("results") or []
    assets = _parse_results(results, FOFA_FIELDS)
    logger.info(f"FOFA 查询完成，共获取 {len(assets)} 条资产（总量 {data.get('size', '?')}）")
    return assets


async def validate_key(api_key: str | None = None) -> tuple[bool, str]:
    """
    校验 FOFA API Key 是否有效（调用账号信息接口，不消耗 F 点）。

    Returns:
        (是否有效, 说明信息)
    """
    if not api_key:
        api_key = await get_configured_api_key()

    if not api_key:
        return False, "FOFA API Key 未配置"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                FOFA_INFO_URL,
                params={"key": api_key},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
    except asyncio.TimeoutError:
        return False, "FOFA 接口请求超时"
    except Exception as exc:  # noqa: BLE001
        return False, f"FOFA 接口请求异常: {exc}"

    if data.get("error"):
        return False, f"Key 无效: {data.get('errmsg') or '未知错误'}"

    username = data.get("username") or data.get("email") or ""
    fofa_points = data.get("fofa_point") or data.get("fcoin") or ""
    return True, f"FOFA Key 有效（账号 {username}，剩余点数 {fofa_points}）"
