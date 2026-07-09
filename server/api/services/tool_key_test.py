"""
工具 API Key 有效性探测 - 统一分派入口。

router 只做薄层分派，具体校验按 tool_name 收敛到各自适配层：
- fofa   → crawler_tools.fofa_tools.validate_key
- hunter → crawler_tools.hunter_tools.validate_key
- tianyancha → ICP 备案接口最小查询
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
    """天眼查 ICP 备案接口最小查询校验。"""
    if not api_key:
        return False, "天眼查 API Key 未配置"
    url = (
        "http://open.api.tianyancha.com/services/open/ipr/icp/3.0"
        "?keyword=腾讯&icpType=1&pageNum=1&pageSize=1"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"Authorization": api_key},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return False, f"天眼查接口 HTTP {resp.status}"
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"天眼查接口请求异常: {exc}"

    code = data.get("error_code")
    if code is None:
        code = data.get("code")
    # error_code=0 正常；300000 系列多为无数据但 Key 有效
    if code in (0, "0"):
        return True, "天眼查 Key 有效"
    reason = data.get("reason") or data.get("message") or data.get("msg") or "未知错误"
    # 权限/账号类错误视为 Key 无效
    if any(kw in str(reason) for kw in ("权限", "账号", "token", "Token", "认证", "无效")):
        return False, f"Key 无效: {reason}"
    # 其它（如无数据）仍表示 Key 可用
    return True, f"天眼查 Key 有效（接口返回: {reason}）"


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
