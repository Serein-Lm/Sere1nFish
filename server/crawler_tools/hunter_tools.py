"""
Hunter API 查询和 URL 探活工具

功能：
1. Hunter API 查询（支持 domain 和 icp.name 查询）
2. URL 批量探活
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from core.logger import get_logger

logger = get_logger("hunter_tools")
@dataclass
class HunterResult:
    """Hunter 查询结果"""
    url: str
    ip: str
    port: int
    domain: str
    web_title: str
    protocol: str
    update_time: str
    status_code: int
    banner: str
    fingerprints: list[str] = field(default_factory=list)


@dataclass
class ProbeResult:
    """探活结果"""
    url: str
    is_alive: bool
    status_code: int | None = None
    title: str | None = None
    content_length: int | None = None
    response_time: float | None = None  # 响应时间（秒）
    error: str | None = None


async def _load_hunter_config_from_db() -> dict[str, Any]:
    """从数据库加载 Hunter 配置"""
    try:
        from api.db.mongodb import get_db
        from api.dao import config as config_dao
        
        db = get_db()
        return await config_dao.get_tool_config(db, "hunter")
    except Exception as exc:
        logger.warning(f"Hunter 配置读取失败: {exc}")
        return {}


def _hunter_base64_encode(query: str) -> str:
    """Hunter base64 编码"""
    return base64.urlsafe_b64encode(query.encode()).decode()


def _format_hunter_query(search_type: str, query: str) -> str:
    """
    格式化 Hunter 查询语句
    
    Args:
        search_type: 查询类型 ("domain" 或 "icp")
        query: 查询值
    
    Returns:
        格式化后的查询语句
    """
    # 如果已经是格式化的查询语句，直接返回
    if "=" in query or " || " in query or " && " in query:
        return query
    
    if search_type == "domain":
        return f'domain.suffix="{query}"'
    elif search_type == "icp":
        return f'icp.name="{query}"'
    else:
        return query


async def search_hunter(
    query: str,
    search_type: str = "domain",
    size: int = 100,
    api_key: str | None = None,
    months: int = 1,
) -> list[HunterResult]:
    """
    Hunter API 搜索（支持分页查询）
    
    Args:
        query: 查询内容（domain 或公司名）
        search_type: 查询类型 ("domain" 或 "icp")
        size: 最大返回数量
        api_key: Hunter API Key（不提供则从数据库读取）
        months: 查询时间范围（月）
    
    Returns:
        Hunter 查询结果列表
    """
    # 获取 API Key
    if not api_key:
        # 优先从数据库读取
        config = await _load_hunter_config_from_db()
        api_key = config.get("api_key", "")
    
    if not api_key:
        logger.warning("API Key 未配置")
        return []
    
    # 设置时间范围
    end_time = datetime.now()
    start_time = end_time - timedelta(days=30 * months)
    
    # 格式化查询
    formatted_query = _format_hunter_query(search_type, query)
    logger.info(f"查询语句: {formatted_query}")
    
    # 分页参数
    page_size = min(100, size)  # Hunter 每页最大 100 条
    total_pages = (size + page_size - 1) // page_size
    total_pages = min(total_pages, 100)  # 最多 100 页
    
    all_results: list[HunterResult] = []
    
    async with aiohttp.ClientSession() as session:
        logger.info(f"开始查询，预计需要查询 {total_pages} 页，每页 {page_size} 条")
        
        for page in range(1, total_pages + 1):
            # 添加延迟避免被限速
            if page > 1:
                await asyncio.sleep(0.5)
            
            # 构建请求 URL
            url = (
                f"https://hunter.qianxin.com/openApi/search"
                f"?api-key={api_key}"
                f"&search={_hunter_base64_encode(formatted_query)}"
                f"&page={page}"
                f"&page_size={page_size}"
                f"&is_web=3"
                f"&port_filter=false"
                f"&start_time={start_time.strftime('%Y-%m-%d')}"
                f"&end_time={end_time.strftime('%Y-%m-%d')}"
            )
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            
            try:
                async with session.get(url, headers=headers, timeout=30) as resp:
                    data = await resp.json()
                    
                    if data.get("code") != 200:
                        logger.warning(f"第 {page} 页查询错误: {data.get('message')}")
                        break
                    
                    arr = data.get("data", {}).get("arr", [])
                    
                    if not arr:
                        logger.debug(f"第 {page} 页无数据，结束查询")
                        break
                    
                    # 解析结果
                    for item in arr:
                        all_results.append(HunterResult(
                            url=item.get("url", ""),
                            ip=item.get("ip", ""),
                            port=item.get("port", 0),
                            domain=item.get("domain", ""),
                            web_title=item.get("web_title", ""),
                            protocol=item.get("protocol", ""),
                            update_time=item.get("update_time", ""),
                            status_code=item.get("status_code", 0),
                            banner=item.get("banner", ""),
                            fingerprints=item.get("components", []),
                        ))
                        
                        if len(all_results) >= size:
                            logger.info(f"已获取足够数据 ({len(all_results)} 条)，结束查询")
                            return all_results[:size]
                    
                    # 打印剩余配额
                    rest_quota = data.get("data", {}).get("rest_quota", "")
                    logger.info(f"第 {page} 页查询完成，当前共 {len(all_results)} 条结果，剩余配额: {rest_quota}")
                    
            except asyncio.TimeoutError:
                logger.warning(f"第 {page} 页查询超时")
                break
            except Exception as e:
                logger.error(f"第 {page} 页查询失败: {e}")
                break
    
    logger.info(f"查询完成，共获取 {len(all_results)} 条结果")
    return all_results


async def validate_key(api_key: str | None = None) -> tuple[bool, str]:
    """
    校验 Hunter API Key 是否有效（最小 size=1 查询）。

    Returns:
        (是否有效, 说明信息)
    """
    if not api_key:
        config = await _load_hunter_config_from_db()
        api_key = str(config.get("api_key") or "").strip()

    if not api_key:
        return False, "Hunter API Key 未配置"

    end_time = datetime.now()
    start_time = end_time - timedelta(days=30)
    probe_query = _hunter_base64_encode('domain.suffix="example.com"')
    url = (
        f"https://hunter.qianxin.com/openApi/search"
        f"?api-key={api_key}"
        f"&search={probe_query}"
        f"&page=1&page_size=1&is_web=3&port_filter=false"
        f"&start_time={start_time.strftime('%Y-%m-%d')}"
        f"&end_time={end_time.strftime('%Y-%m-%d')}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
    except asyncio.TimeoutError:
        return False, "Hunter 接口请求超时"
    except Exception as exc:  # noqa: BLE001
        return False, f"Hunter 接口请求异常: {exc}"

    code = data.get("code")
    if code == 200:
        rest_quota = data.get("data", {}).get("rest_quota", "")
        return True, f"Hunter Key 有效（剩余配额 {rest_quota}）"
    return False, f"Key 无效: {data.get('message') or '未知错误'}"


async def probe_url(
    url: str,
    timeout: float = 10.0,
    session: aiohttp.ClientSession | None = None,
) -> ProbeResult:
    """
    探测单个 URL 是否存活
    
    Args:
        url: 要探测的 URL
        timeout: 超时时间（秒）
        session: 复用的 aiohttp session
    
    Returns:
        探活结果
    """
    import time
    import re
    
    start_time = time.time()
    
    # 确保 URL 有协议
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True
    
    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False,  # 忽略 SSL 证书验证
            allow_redirects=True,
        ) as resp:
            response_time = time.time() - start_time
            
            # 读取部分内容获取标题
            content = await resp.text(errors="ignore")
            content_length = len(content)
            
            # 提取标题
            title = None
            title_match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()[:200]
            
            return ProbeResult(
                url=url,
                is_alive=True,
                status_code=resp.status,
                title=title,
                content_length=content_length,
                response_time=round(response_time, 3),
            )
            
    except asyncio.TimeoutError:
        return ProbeResult(
            url=url,
            is_alive=False,
            error="timeout",
            response_time=timeout,
        )
    except aiohttp.ClientError as e:
        return ProbeResult(
            url=url,
            is_alive=False,
            error=str(e)[:100],
        )
    except Exception as e:
        return ProbeResult(
            url=url,
            is_alive=False,
            error=str(e)[:100],
        )
    finally:
        if close_session:
            await session.close()


async def probe_urls_batch(
    urls: list[str],
    concurrency: int = 20,
    timeout: float = 10.0,
    only_alive: bool = True,
) -> list[ProbeResult]:
    """
    批量探测 URL 是否存活
    
    Args:
        urls: URL 列表
        concurrency: 并发数
        timeout: 单个请求超时时间（秒）
        only_alive: 是否只返回存活的 URL
    
    Returns:
        探活结果列表
    """
    if not urls:
        return []
    
    logger.info(f"开始探活，共 {len(urls)} 个 URL，并发数: {concurrency}")
    
    results: list[ProbeResult] = []
    semaphore = asyncio.Semaphore(concurrency)
    
    async def probe_with_semaphore(url: str, session: aiohttp.ClientSession) -> ProbeResult:
        async with semaphore:
            return await probe_url(url, timeout, session)
    
    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [probe_with_semaphore(url, session) for url in urls]
        results = await asyncio.gather(*tasks)
    
    # 统计
    alive_count = sum(1 for r in results if r.is_alive)
    logger.info(f"探活完成，存活: {alive_count}/{len(urls)}")
    
    if only_alive:
        return [r for r in results if r.is_alive]
    
    return list(results)


async def search_and_probe(
    query: str,
    search_type: str = "domain",
    size: int = 100,
    probe_concurrency: int = 20,
    probe_timeout: float = 10.0,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Hunter 查询 + URL 探活（组合函数）
    
    Args:
        query: 查询内容（domain 或公司名）
        search_type: 查询类型 ("domain" 或 "icp")
        size: 最大返回数量
        probe_concurrency: 探活并发数
        probe_timeout: 探活超时时间
        api_key: Hunter API Key
    
    Returns:
        存活的 URL 信息列表
    """
    # 1. Hunter 查询
    hunter_results = await search_hunter(
        query=query,
        search_type=search_type,
        size=size,
        api_key=api_key,
    )
    
    if not hunter_results:
        logger.info("Hunter 查询无结果")
        return []
    
    # 2. 提取 URL 并去重
    urls = list(set(r.url for r in hunter_results if r.url))
    logger.info(f"去重后共 {len(urls)} 个 URL")
    
    # 3. 批量探活
    probe_results = await probe_urls_batch(
        urls=urls,
        concurrency=probe_concurrency,
        timeout=probe_timeout,
        only_alive=True,
    )
    
    # 4. 合并结果
    # 创建 URL -> Hunter 结果映射
    hunter_map = {r.url: r for r in hunter_results}
    
    combined_results = []
    for probe in probe_results:
        hunter = hunter_map.get(probe.url)
        
        result = {
            "url": probe.url,
            "is_alive": probe.is_alive,
            "status_code": probe.status_code,
            "title": probe.title or (hunter.web_title if hunter else None),
            "response_time": probe.response_time,
            "content_length": probe.content_length,
        }
        
        if hunter:
            result.update({
                "ip": hunter.ip,
                "port": hunter.port,
                "domain": hunter.domain,
                "protocol": hunter.protocol,
                "fingerprints": hunter.fingerprints,
                "update_time": hunter.update_time,
            })
        
        combined_results.append(result)
    
    logger.info(f"完成，存活 URL: {len(combined_results)} 个")
    return combined_results


# ==================== 便捷函数 ====================

async def search_by_domain(domain: str, size: int = 100) -> list[dict[str, Any]]:
    """
    通过域名查询并探活
    
    Args:
        domain: 域名（如 "bilibili.com"）
        size: 最大返回数量
    
    Returns:
        存活的 URL 信息列表
    """
    return await search_and_probe(
        query=domain,
        search_type="domain",
        size=size,
    )


async def search_by_company(company_name: str, size: int = 100) -> list[dict[str, Any]]:
    """
    通过公司名（ICP 备案名）查询并探活
    
    Args:
        company_name: 公司名（如 "哔哩哔哩"）
        size: 最大返回数量
    
    Returns:
        存活的 URL 信息列表
    """
    return await search_and_probe(
        query=company_name,
        search_type="icp",
        size=size,
    )
