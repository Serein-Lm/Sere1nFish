"""
内置工具函数定义。

这些工具可以被不同的 Agent / workflow 复用。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI
from langchain.tools import tool


def _run_coro_sync(coro):
    """Run an async DAO/config lookup from sync LangChain tools.

    DB 协程必须跑在拥有 Motor 连接池的事件循环上，否则会触发
    "attached to a different loop"。因此优先把协程调度回 Motor 的 io loop；
    仅在拿不到该 loop 时才回退到独立 loop 执行。
    """
    # 优先调度到 Motor 客户端所属的事件循环
    try:
        from api.db.mongodb import get_io_loop

        motor_loop = get_io_loop()
    except Exception:  # noqa: BLE001
        motor_loop = None

    if motor_loop is not None and motor_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, motor_loop)
        return future.result()

    # 回退：当前线程无运行中的 loop -> 直接 asyncio.run
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # 回退：当前线程有其它 loop 在跑 -> 放到子线程用新 loop 执行
    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _get_tool_api_key(tool_name: str) -> str:
    async def _load() -> str:
        from api.dao import config as config_dao
        from api.db.mongodb import get_db

        config = await config_dao.get_tool_config(get_db(), tool_name)
        return str(config.get("api_key") or "").strip()

    try:
        return _run_coro_sync(_load()) or ""
    except Exception:
        return ""


@tool(
    "tianyancha_get_domain",
    description=(
        "根据公司名称调用天眼查开放平台 ICP 备案接口（/services/open/ipr/icp/3.0，GET）"
        "查询该公司的网站备案信息，并从中推测官网域名。"
        "适用于：用户只给出公司/机构名称但未提供官网链接时，先用它拿到官网域名，再交给浏览器工具打开官网进行信息采集。"
    ),
)
def tianyancha_get_domain(company_name: str) -> str:
    """
    使用统一天眼查 adapter 调用 HTTPS ICP 备案接口。
    返回推测的官网域名。

    返回内容为**可直接给 BrowserAgent 使用**的一段简要说明，例如：
    - 成功：`公司 XXX 的官网域名为 example.com`
    - 失败：返回失败原因说明。
    """
    try:
        from crawler_tools.tianyancha_tools import TianyanchaClient

        async def _lookup():
            client = await TianyanchaClient.from_runtime_config()
            return await client.get_icp_records(company_name)

        records = _run_coro_sync(_lookup()) or []
    except Exception as e:
        return f"调用天眼查 ICP 接口时发生异常：{e}"
    if not records:
        return f"天眼查 ICP 接口调用成功，但未找到公司“{company_name}”的备案域名。"
    domains = list(dict.fromkeys(record.domain for record in records if record.domain))
    return f"公司“{company_name}”的备案域名为：{', '.join(domains)}"


@tool(
    "tianyancha_get_bids_mock",
    description=(
        "【Mock版本】根据公司名称查询招投标信息（使用测试数据）。"
        "默认查询最近半年的招标公告（type=2），返回详细的正文内容和链接。"
        "适用于：测试招投标信息采集功能，不产生API费用。"
    ),
)
def tianyancha_get_bids_mock(
    company_name: str,
    bid_type: str = "2",
    page_num: int = 1,
    page_size: int = 10,
) -> str:
    """
    Mock版本：从本地文件读取测试数据。
    
    参数：
    - company_name: 公司名称（必填，但mock版本会忽略）
    - bid_type: 公告类型（默认 2）
    - page_num: 页码（默认 1）
    - page_size: 每页数量（默认 10）
    
    返回：测试数据中的招投标信息
    """
    from pathlib import Path

    # 读取 mock 数据文件
    mock_file = Path(__file__).parent / "mock" / "bidtest.txt"

    try:
        if not mock_file.exists():
            return f"Mock数据文件不存在：{mock_file}"

        with open(mock_file, "r", encoding="utf-8") as f:
            mock_data = f.read()

        # 在返回数据前添加说明
        return f"【使用Mock数据 - 公司：{company_name}】\n\n{mock_data}"

    except Exception as e:
        return f"读取Mock数据失败：{e}"


@tool(
    "tianyancha_get_bids",
    description=(
        "根据公司名称调用天眼查开放平台招投标接口（/services/open/m/bids/2.0，GET）"
        "查询该公司的招投标信息。默认查询最近半年的招标公告（type=2），返回详细的正文内容和链接。"
        "适用于：查询公司的招投标历史、招标公告等信息。"
    ),
)
def tianyancha_get_bids(
    company_name: str,
    bid_type: str = "2",
    page_num: int = 1,
    page_size: int = 10,
) -> str:
    """
    调用天眼查招投标接口获取公司的招投标信息。
    
    参数：
    - company_name: 公司名称（必填）
    - bid_type: 公告类型（1=招标预告，2=招标公告，4=中标结果，默认 2）
    - page_num: 页码（默认 1）
    - page_size: 每页数量（默认 10，最大 20）
    
    返回：结构化的招投标信息，包含正文内容和链接
    """
    from datetime import datetime, timedelta

    api_key = _get_tool_api_key("tianyancha")

    if not api_key:
        return "天眼查 API Key 未配置，无法调用招投标接口。"

    # 自动计算时间范围：最近半年
    end_time = datetime.now()
    start_time = end_time - timedelta(days=180)
    publish_start_time = start_time.strftime("%Y-%m-%d")
    publish_end_time = end_time.strftime("%Y-%m-%d")

    # 构建请求 URL
    url = (
        "http://open.api.tianyancha.com/services/open/m/bids/2.0"
        f"?keyword={company_name}"
        f"&type={bid_type}"
        f"&publishStartTime={publish_start_time}"
        f"&publishEndTime={publish_end_time}"
        f"&pageNum={page_num}"
        f"&pageSize={page_size}"
    )
    headers = {"Authorization": api_key}

    try:
        resp = requests.get(url, headers=headers, timeout=15.0)
        if resp.status_code != 200:
            return f"调用天眼查招投标接口失败，HTTP 状态码：{resp.status_code}"

        data = resp.json()
    except Exception as e:
        return f"调用天眼查招投标接口时发生异常：{e}"

    # 解析响应
    error_code = data.get("error_code") or data.get("code")
    if error_code not in (0, "0", None):
        msg = data.get("reason") or data.get("message") or str(data)
        return f"天眼查招投标接口返回错误（code={error_code}）：{msg}"

    result = data.get("result") or data.get("data") or {}
    items = result.get("items") or result.get("list") or []
    total = result.get("total", 0)

    if not items:
        return f'未找到公司"{company_name}"的招投标信息（时间范围：{publish_start_time} 至 {publish_end_time}）。'

    # 格式化输出（返回详细信息供 agent 处理）
    output_lines = [
        f"找到 {total} 条招投标信息（显示前 {len(items)} 条）",
        f"查询时间范围：{publish_start_time} 至 {publish_end_time}\n"
    ]
    
    for idx, item in enumerate(items, 1):
        title = item.get("title", "无标题")
        pub_time = item.get("publishTime", "未知时间")
        bid_type_name = item.get("type", "未知类型")
        stage = item.get("stage", "")
        province = item.get("province", "")
        purchaser = item.get("purchaser", "")
        proxy = item.get("proxy", "")
        link = item.get("link", "")
        content = item.get("content", "")
        
        output_lines.append(f"【{idx}】{title}")
        output_lines.append(f"  类型：{bid_type_name}")
        output_lines.append(f"  发布时间：{pub_time}")
        if stage:
            output_lines.append(f"  进展阶段：{stage}")
        if province:
            output_lines.append(f"  省份地区：{province}")
        if purchaser:
            output_lines.append(f"  采购人：{purchaser}")
        if proxy:
            output_lines.append(f"  代理机构：{proxy}")
        if link:
            output_lines.append(f"  详情链接：{link}")
        
        # 返回完整正文内容（重要：供 agent 提取 PDF 链接和联系方式）
        if content:
            output_lines.append(f"  正文内容：\n{content}")
        
        output_lines.append("")

    return "\n".join(output_lines)
