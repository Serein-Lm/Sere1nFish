"""
内置工具函数定义。

这些工具可以被不同的 Agent / workflow 复用。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

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

    if motor_loop is not None and not motor_loop.is_closed():
        if motor_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, motor_loop)
            return future.result()

        # Motor may already be bound to a loop before a CLI/test starts it.
        # Running the coroutine through asyncio.run() would create another loop
        # and fail with "Future attached to a different loop".
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return motor_loop.run_until_complete(coro)

        result: dict[str, Any] = {}

        def _motor_worker() -> None:
            try:
                asyncio.set_event_loop(motor_loop)
                result["value"] = motor_loop.run_until_complete(coro)
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        thread = threading.Thread(target=_motor_worker, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result.get("value")

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
    page_size: int = 20,
) -> str:
    """
    调用天眼查招投标接口获取公司的招投标信息。
    
    参数：
    - company_name: 公司名称（必填）
    - bid_type: 公告类型（1=招标预告，2=招标公告，4=中标结果，默认 2）
    - page_num: 页码（默认 1）
    - page_size: 每页数量（默认 20，最大 20）
    
    返回：结构化的招投标信息，包含正文内容和链接
    """
    try:
        from crawler_tools.tianyancha_tools import TianyanchaClient

        async def _lookup():
            client = await TianyanchaClient.from_runtime_config()
            return await client.search_bids(
                company_name,
                bid_type=bid_type,
                page_num=page_num,
                page_size=page_size,
            )

        result = _run_coro_sync(_lookup())
    except Exception as exc:
        return f"调用天眼查招投标接口时发生异常：{exc}"

    if not result.records:
        return (
            f'未找到公司“{company_name}”的招投标信息'
            f'（时间范围：{result.publish_start} 至 {result.publish_end}）。'
        )

    # 格式化输出（返回详细信息供 agent 处理）
    output_lines = [
        f"找到 {result.total_reported} 条招投标信息（显示前 {len(result.records)} 条）",
        f"查询时间范围：{result.publish_start} 至 {result.publish_end}\n"
    ]

    for idx, record in enumerate(result.records, 1):
        output_lines.append(f"【{idx}】{record.title or '无标题'}")
        output_lines.append(f"  类型：{record.announcement_type or '未知类型'}")
        output_lines.append(f"  发布时间：{record.published_on or '未知时间'}")
        if record.stage:
            output_lines.append(f"  进展阶段：{record.stage}")
        if record.province:
            output_lines.append(f"  省份地区：{record.province}")
        if record.purchaser:
            output_lines.append(f"  采购人：{record.purchaser}")
        if record.agency:
            output_lines.append(f"  代理机构：{record.agency}")
        if record.detail_url:
            output_lines.append(f"  详情链接：{record.detail_url}")
        if record.content_html:
            output_lines.append(f"  正文内容：\n{record.content_html}")
        output_lines.append("")

    return "\n".join(output_lines)
