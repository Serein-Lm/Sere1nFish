"""运行时配置入口。

业务代码应通过这里读取数据库中的前端配置，而不是直接读 config.json。
旧配置文件导入入口已下线，不参与正常服务启动或运维同步。
"""

from __future__ import annotations

from api.dao import config as config_dao
from api.db.mongodb import get_db


async def get_runtime_app_config():
    """从 system_config 组装 AppConfig。"""
    return await config_dao.get_app_config(get_db())


async def get_runtime_config_section(category: str) -> dict:
    """读取一个原始配置段，已由 DAO 透明解密。"""
    doc = await config_dao.get_config(get_db(), category)
    return doc.get("config", {}) if doc else {}
