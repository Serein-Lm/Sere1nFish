"""
系统配置管理 - DAO 层

配置分类：
- llm: LLM 相关配置（api_key, base_url, models）
- tools: 工具 API Key（tianyancha, hunter, bocha 等）
- langsmith: LangSmith 配置
- langfuse: Langfuse 配置
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import SYSTEM_CONFIG_COLLECTION
from api.utils.config_crypto import decrypt_config, encrypt_config, is_encrypted_value, is_sensitive_key
from Sere1nGraph.graph.config.loader import load_config_from_data


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 配置分类定义
CONFIG_CATEGORIES = {
    "llm": {
        "description": "LLM 大模型配置",
        "fields": ["api_key", "base_url", "default_model", "vision_model"],
    },
    "tools": {
        "description": "工具 API Key 配置",
        "sub_keys": ["tianyancha", "hunter", "bocha"],
    },
    "langsmith": {
        "description": "LangSmith 追踪配置",
        "fields": ["enabled", "api_key", "project", "endpoint"],
    },
    "langfuse": {
        "description": "Langfuse 追踪配置",
        "fields": ["enabled", "secret_key", "public_key", "base_url"],
    },
}

APP_CONFIG_SECTIONS = {
    "app",
    "chrome_docker",
    "xhs_crawler",
    "douyin_crawler",
    "mongodb",
    "redis",
    "runtime",
    "mobile",
    "mcpServers",
    "tools",
    "logging",
    "cosyvoice",
    "bailian",
    "auth",
    "langsmith",
    "langfuse",
    "easytier",
    "notifications",
}

LLM_RUNTIME_MODEL_KEYS = {
    "default_model": "default",
    "vision_model": "vision",
    "mobile_planner_model": "mobile_planner",
    "mobile_executor_model": "mobile_executor",
    "mobile_screen_model": "mobile_screen",
    "mobile_chat_model": "mobile_chat",
}


def _decrypt_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    result = dict(doc)
    result["config"] = decrypt_config(result.get("config", {}))
    return result


async def get_config(
    db: AsyncIOMotorDatabase,
    category: str,
    key: str | None = None,
) -> dict[str, Any] | None:
    """
    获取配置
    
    Args:
        db: 数据库连接
        category: 配置分类（llm, tools, langsmith, langfuse）
        key: 子键（仅 tools 分类需要，如 tianyancha, hunter）
    
    Returns:
        配置内容
    """
    if key:
        # 获取 tools 下的子配置
        doc = await db[SYSTEM_CONFIG_COLLECTION].find_one({
            "category": category,
            "key": key,
        })
    else:
        # 获取整个分类的配置
        doc = await db[SYSTEM_CONFIG_COLLECTION].find_one({
            "category": category,
            "key": {"$exists": False},
        })
    
    return _decrypt_doc(doc)


async def set_config(
    db: AsyncIOMotorDatabase,
    category: str,
    config_data: dict[str, Any],
    key: str | None = None,
) -> dict[str, Any]:
    """
    设置配置（创建或更新）
    
    Args:
        db: 数据库连接
        category: 配置分类
        config_data: 配置内容
        key: 子键（仅 tools 分类需要）
    
    Returns:
        更新后的配置
    """
    now = _now()
    
    query = {"category": category}
    if key:
        query["key"] = key
    else:
        query["key"] = {"$exists": False}
    
    update_data = {
        "category": category,
        "config": encrypt_config(config_data),
        "updated_at": now,
    }
    if key:
        update_data["key"] = key
    
    doc = await db[SYSTEM_CONFIG_COLLECTION].find_one_and_update(
        query,
        {
            "$setOnInsert": {"created_at": now},
            "$set": update_data,
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    
    return _decrypt_doc(doc)


async def delete_config(
    db: AsyncIOMotorDatabase,
    category: str,
    key: str | None = None,
) -> bool:
    """
    删除配置
    
    Args:
        db: 数据库连接
        category: 配置分类
        key: 子键
    
    Returns:
        是否删除成功
    """
    query = {"category": category}
    if key:
        query["key"] = key
    else:
        query["key"] = {"$exists": False}
    
    result = await db[SYSTEM_CONFIG_COLLECTION].delete_one(query)
    return bool(result.deleted_count)


async def list_configs(
    db: AsyncIOMotorDatabase,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """
    列出配置
    
    Args:
        db: 数据库连接
        category: 配置分类（可选，不提供则返回所有）
    
    Returns:
        配置列表
    """
    query = {}
    if category:
        query["category"] = category
    
    cursor = db[SYSTEM_CONFIG_COLLECTION].find(query).sort("category", 1)
    return [_decrypt_doc(doc) async for doc in cursor]


async def reencrypt_all_configs(db: AsyncIOMotorDatabase) -> dict[str, int]:
    """Rewrite config documents through encrypt_config.

    Older documents may contain plaintext sensitive fields.  This migration is
    idempotent: decrypt_config transparently handles both plaintext and already
    encrypted values, then encrypt_config writes sensitive fields back as
    Fernet payloads.
    """
    scanned = 0
    updated = 0
    cursor = db[SYSTEM_CONFIG_COLLECTION].find({"config": {"$exists": True}})
    async for doc in cursor:
        scanned += 1
        if not _has_plaintext_sensitive_value(doc.get("config", {})):
            continue
        raw_config = decrypt_config(doc.get("config", {}))
        encrypted_config = encrypt_config(raw_config)
        result = await db[SYSTEM_CONFIG_COLLECTION].update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "config": encrypted_config,
                    "updated_at": _now(),
                }
            },
        )
        updated += int(result.modified_count > 0)
    return {"scanned": scanned, "updated": updated}


def _has_plaintext_sensitive_value(config: Any, *, parent_key: str = "") -> bool:
    if parent_key and is_sensitive_key(parent_key):
        return config is not None and config != "" and not is_encrypted_value(config)
    if is_encrypted_value(config):
        return False
    if isinstance(config, dict):
        return any(
            _has_plaintext_sensitive_value(value, parent_key=str(key))
            for key, value in config.items()
        )
    if isinstance(config, list):
        return any(_has_plaintext_sensitive_value(item, parent_key=parent_key) for item in config)
    return False


async def get_all_configs(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """
    获取所有配置（合并为一个字典）
    
    Returns:
        {
            "llm": {...},
            "tools": {"tianyancha": {...}, "hunter": {...}},
            "langsmith": {...},
            "langfuse": {...}
        }
    """
    docs = await list_configs(db)
    
    result: dict[str, Any] = {}
    
    for doc in docs:
        category = doc.get("category")
        key = doc.get("key")
        config = doc.get("config", {})
        
        if key:
            # tools 下的子配置
            if category not in result:
                result[category] = {}
            result[category][key] = config
        else:
            if isinstance(result.get(category), dict) and isinstance(config, dict):
                result[category] = {**config, **result[category]}
            else:
                result[category] = config
    
    return result


# ==================== LLM 配置 ====================

async def get_llm_config(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """获取 LLM 配置"""
    runtime_doc = await get_config(db, "runtime")
    runtime = runtime_doc.get("config", {}) if runtime_doc else {}
    models = runtime.get("models", {}) if isinstance(runtime.get("models"), dict) else {}
    fallback = {
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "default_model": models.get("default"),
        "vision_model": models.get("vision"),
        "mobile_planner_model": models.get("mobile_planner"),
        "mobile_executor_model": models.get("mobile_executor"),
        "mobile_screen_model": models.get("mobile_screen"),
        "mobile_chat_model": models.get("mobile_chat"),
    }
    doc = await get_config(db, "llm")
    config = doc.get("config", {}) if doc else {}
    return {k: v for k, v in {**fallback, **config}.items() if v is not None}


async def set_llm_config(
    db: AsyncIOMotorDatabase,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    vision_model: str | None = None,
    mobile_planner_model: str | None = None,
    mobile_executor_model: str | None = None,
    mobile_screen_model: str | None = None,
    mobile_chat_model: str | None = None,
) -> dict[str, Any]:
    """设置 LLM 配置"""
    # 获取现有配置
    existing = await get_llm_config(db)
    
    # 合并更新
    config = {**existing}
    if api_key is not None:
        config["api_key"] = api_key
    if base_url is not None:
        config["base_url"] = base_url
    if default_model is not None:
        config["default_model"] = default_model
    if vision_model is not None:
        config["vision_model"] = vision_model
    if mobile_planner_model is not None:
        config["mobile_planner_model"] = mobile_planner_model
    if mobile_executor_model is not None:
        config["mobile_executor_model"] = mobile_executor_model
    if mobile_screen_model is not None:
        config["mobile_screen_model"] = mobile_screen_model
    if mobile_chat_model is not None:
        config["mobile_chat_model"] = mobile_chat_model
    
    doc = await set_config(db, "llm", config)
    await _sync_llm_config_to_runtime(db, config)
    return doc


async def _sync_llm_config_to_runtime(
    db: AsyncIOMotorDatabase,
    llm_config: dict[str, Any],
) -> None:
    """把前端 LLM 配置同步到 runtime，保留非 LLM 运行参数。"""
    runtime_doc = await get_config(db, "runtime")
    runtime = dict(runtime_doc.get("config", {}) if runtime_doc else {})

    if llm_config.get("api_key") is not None:
        runtime["api_key"] = llm_config.get("api_key")
    if llm_config.get("base_url") is not None:
        runtime["base_url"] = llm_config.get("base_url")

    models = dict(runtime.get("models", {}) or {})
    for source_key, model_key in LLM_RUNTIME_MODEL_KEYS.items():
        if llm_config.get(source_key) is not None:
            models[model_key] = llm_config[source_key]
    if models:
        runtime["models"] = models

    await set_config(db, "runtime", runtime)


async def delete_llm_config(db: AsyncIOMotorDatabase) -> bool:
    """删除 LLM 配置，并清理 runtime 中对应的 LLM 字段。"""
    deleted = await delete_config(db, "llm")
    runtime_doc = await get_config(db, "runtime")
    if not runtime_doc:
        return deleted

    runtime = dict(runtime_doc.get("config", {}) or {})
    changed = False
    for key in ("api_key", "base_url"):
        if key in runtime:
            runtime.pop(key, None)
            changed = True

    models = dict(runtime.get("models", {}) or {})
    for model_key in LLM_RUNTIME_MODEL_KEYS.values():
        if model_key in models:
            models.pop(model_key, None)
            changed = True
    if changed:
        if models:
            runtime["models"] = models
        else:
            runtime.pop("models", None)
        await set_config(db, "runtime", runtime)
        deleted = True
    return deleted


# ==================== Tools 配置 ====================

async def get_tool_config(db: AsyncIOMotorDatabase, tool_name: str) -> dict[str, Any]:
    """获取工具配置"""
    doc = await get_config(db, "tools", key=tool_name)
    if doc:
        return doc.get("config", {})
    root = await get_config(db, "tools")
    tools = root.get("config", {}) if root else {}
    value = tools.get(tool_name, {}) if isinstance(tools, dict) else {}
    return value if isinstance(value, dict) else {}


async def set_tool_config(
    db: AsyncIOMotorDatabase,
    tool_name: str,
    api_key: str,
    **extra_config,
) -> dict[str, Any]:
    """设置工具配置"""
    config = {"api_key": api_key, **extra_config}
    root = await get_config(db, "tools")
    tools = dict(root.get("config", {}) if root else {})
    tools[tool_name] = config
    await set_config(db, "tools", tools)
    return await set_config(db, "tools", config, key=tool_name)


async def delete_tool_config(db: AsyncIOMotorDatabase, tool_name: str) -> bool:
    """删除工具配置"""
    deleted = await delete_config(db, "tools", key=tool_name)
    root = await get_config(db, "tools")
    if root:
        tools = dict(root.get("config", {}) or {})
        if tool_name in tools:
            tools.pop(tool_name, None)
            await set_config(db, "tools", tools)
            deleted = True
    return deleted


async def list_tool_configs(db: AsyncIOMotorDatabase) -> dict[str, dict[str, Any]]:
    """列出所有工具配置"""
    root = await get_config(db, "tools")
    result = dict(root.get("config", {}) if root else {})
    docs = await list_configs(db, category="tools")
    result.update({doc.get("key"): doc.get("config", {}) for doc in docs if doc.get("key")})
    return result


def build_app_config_from_db_configs(configs: dict[str, Any]):
    """把 system_config 聚合结果转换成 AppConfig。llm 兼容分类覆盖 runtime。"""
    data = {key: value for key, value in configs.items() if key in APP_CONFIG_SECTIONS}
    runtime = dict(data.get("runtime", {}) or {})
    llm = configs.get("llm", {}) or {}
    if llm:
        if llm.get("api_key") is not None:
            runtime["api_key"] = llm.get("api_key")
        if llm.get("base_url") is not None:
            runtime["base_url"] = llm.get("base_url")
        models = dict(runtime.get("models", {}) or {})
        for source_key, model_key in LLM_RUNTIME_MODEL_KEYS.items():
            if llm.get(source_key):
                models[model_key] = llm[source_key]
        runtime["models"] = models
    data["runtime"] = runtime
    return load_config_from_data(data, apply_env_overrides=False)


async def get_app_config(db: AsyncIOMotorDatabase):
    """从数据库配置组装 AppConfig，供业务运行时使用。"""
    return build_app_config_from_db_configs(await get_all_configs(db))


# ==================== LangSmith 配置 ====================

async def get_langsmith_config(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """获取 LangSmith 配置"""
    doc = await get_config(db, "langsmith")
    return doc.get("config", {}) if doc else {}


async def set_langsmith_config(
    db: AsyncIOMotorDatabase,
    enabled: bool | None = None,
    api_key: str | None = None,
    project: str | None = None,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """设置 LangSmith 配置"""
    existing = await get_langsmith_config(db)
    
    config = {**existing}
    if enabled is not None:
        config["enabled"] = enabled
    if api_key is not None:
        config["api_key"] = api_key
    if project is not None:
        config["project"] = project
    if endpoint is not None:
        config["endpoint"] = endpoint
    
    return await set_config(db, "langsmith", config)


# ==================== Langfuse 配置 ====================

async def get_langfuse_config(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """获取 Langfuse 配置"""
    doc = await get_config(db, "langfuse")
    return doc.get("config", {}) if doc else {}


async def set_langfuse_config(
    db: AsyncIOMotorDatabase,
    enabled: bool | None = None,
    secret_key: str | None = None,
    public_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """设置 Langfuse 配置"""
    existing = await get_langfuse_config(db)
    
    config = {**existing}
    if enabled is not None:
        config["enabled"] = enabled
    if secret_key is not None:
        config["secret_key"] = secret_key
    if public_key is not None:
        config["public_key"] = public_key
    if base_url is not None:
        config["base_url"] = base_url
    
    return await set_config(db, "langfuse", config)


# ==================== DingTalk 配置 ====================

async def get_dingtalk_config(
    db: AsyncIOMotorDatabase,
    bot_name: str = "default",
) -> dict[str, Any]:
    """
    获取钉钉机器人配置
    
    Args:
        db: 数据库连接
        bot_name: 机器人名称（默认 default）
    
    Returns:
        钉钉机器人配置
    """
    doc = await get_config(db, "dingtalk", key=bot_name)
    return doc.get("config", {}) if doc else {}


async def set_dingtalk_config(
    db: AsyncIOMotorDatabase,
    bot_name: str = "default",
    access_token: str | None = None,
    secret: str | None = None,
    keyword: str | None = None,
    enabled: bool | None = None,
    outgoing_app_secret: str | None = None,
) -> dict[str, Any]:
    """
    设置钉钉机器人配置
    
    Args:
        db: 数据库连接
        bot_name: 机器人名称
        access_token: Webhook access_token
        secret: 签名密钥
        keyword: 关键词
        enabled: 是否启用
        outgoing_app_secret: 群 @机器人 入站回调加签密钥（用于校验钉钉回调）
    
    Returns:
        更新后的配置
    """
    existing = await get_dingtalk_config(db, bot_name)
    
    config = {**existing}
    if access_token is not None:
        config["access_token"] = access_token
    if secret is not None:
        config["secret"] = secret
    if keyword is not None:
        config["keyword"] = keyword
    if enabled is not None:
        config["enabled"] = enabled
    if outgoing_app_secret is not None:
        config["outgoing_app_secret"] = outgoing_app_secret
    
    return await set_config(db, "dingtalk", config, key=bot_name)


async def delete_dingtalk_config(
    db: AsyncIOMotorDatabase,
    bot_name: str = "default",
) -> bool:
    """删除钉钉机器人配置"""
    return await delete_config(db, "dingtalk", key=bot_name)


async def list_dingtalk_configs(db: AsyncIOMotorDatabase) -> dict[str, dict[str, Any]]:
    """列出所有钉钉机器人配置"""
    docs = await list_configs(db, category="dingtalk")
    return {doc.get("key", "default"): doc.get("config", {}) for doc in docs}


# ==================== 旧配置文件导入兼容入口 ====================

async def import_from_config_json(
    db: AsyncIOMotorDatabase,
    config_path: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    旧配置文件导入已下线。

    运行时配置必须从前端写入 MongoDB，并由 set_config/set_tool_config 等接口加密保存。
    这个函数保留返回结构只为旧脚本/调用方提供明确失败信息，不再读取本地文件。
    """
    _ = (db, config_path, overwrite)
    return {
        "error": "旧配置文件导入入口已下线；请在前端配置页写入 MongoDB 加密配置。",
        "imported": [],
        "skipped": [],
        "errors": [],
    }
