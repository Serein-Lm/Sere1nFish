"""
工具 API Key 有效性探测 - 统一分派入口。

router 只做薄层分派，具体校验按 tool_name 收敛到各自适配层：
- fofa   → crawler_tools.fofa_tools.validate_key
- hunter → crawler_tools.hunter_tools.validate_key
- tianyancha → ICP 备案与对外投资接口最小查询
- bocha  → Web Search 接口最小检索

统一返回 (ok, message)，不向 router 暴露具体 HTTP 细节。
"""
from __future__ import annotations

import aiohttp

from core.logger import get_logger

logger = get_logger("tool_key_test")


async def _load_tool_api_key(tool_name: str) -> str:
    """读取已存储（加密）的工具 Key。"""
    try:
        from api.db.mongodb import get_db
        from api.dao import config as config_dao

        config = await config_dao.get_tool_config(get_db(), tool_name)
        return str(config.get("api_key") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取 {tool_name} 配置失败: {exc}")
        return ""


async def _validate_tianyancha(api_key: str) -> tuple[bool, str]:
    """校验 ICP 与公司扫描必需的对外投资接口 ID 823。"""
    from crawler_tools.tianyancha_tools import (
        TianyanchaApiError,
        TianyanchaClient,
        validate_key,
    )

    icp_ok, icp_message = await validate_key(api_key)
    if not icp_ok:
        return False, icp_message
    try:
        client = TianyanchaClient(api_key, timeout_seconds=15)
        await client.list_direct_wholly_owned_investments(
            "天津滨海国际机场",
            max_entities=1,
            page_concurrency=1,
        )
    except TianyanchaApiError as exc:
        return False, f"ICP备案接口可用；对外投资 ID 823 不可用({exc.code}): {exc.reason}"
    return True, "天眼查 API Key 可用（ICP备案 + 对外投资 ID 823）"


async def _validate_bocha(api_key: str) -> tuple[bool, str]:
    """博查 Web Search 接口最小检索校验。"""
    if not api_key:
        return False, "博查 API Key 未配置"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.bochaai.com/v1/web-search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": "test", "count": 1},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403):
                    return False, f"Key 无效: HTTP {resp.status}"
                if resp.status != 200:
                    return False, f"博查接口 HTTP {resp.status}"
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"博查接口请求异常: {exc}"

    code = data.get("code")
    if code in (200, None):
        return True, "博查 Key 有效"
    return False, f"Key 无效: {data.get('msg') or data.get('message') or '未知错误'}"


async def test_tool_key(tool_name: str, api_key: str | None = None) -> tuple[bool, str]:
    """
    按 tool_name 分派探测 API Key 有效性。

    Args:
        tool_name: 工具名（fofa/hunter/tianyancha/bocha）
        api_key: 可选显式 Key；不传则读取已存储配置

    Returns:
        (是否有效, 说明信息)
    """
    name = (tool_name or "").strip().lower()
    if not api_key:
        api_key = await _load_tool_api_key(name)

    if name == "fofa":
        from crawler_tools import fofa_tools

        return await fofa_tools.validate_key(api_key)
    if name == "hunter":
        from crawler_tools import hunter_tools

        return await hunter_tools.validate_key(api_key)
    if name == "tianyancha":
        return await _validate_tianyancha(api_key)
    if name == "bocha":
        return await _validate_bocha(api_key)

    return False, f"暂不支持工具 {tool_name} 的有效性探测"
