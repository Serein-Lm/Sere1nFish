"""
系统配置管理 - API 路由

提供配置的增删改查接口
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from api.auth import get_current_active_user, require_admin, User
from api.db.mongodb import get_db
from api.dao import config as config_dao
from api.dao import users as users_dao
from api.utils.config_crypto import is_sensitive_key, mask_secret, mask_sensitive_config


router = APIRouter()


# ==================== Pydantic 模型 ====================

class LLMConfigUpdate(BaseModel):
    """LLM 配置更新"""
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    vision_model: str | None = None
    mobile_planner_model: str | None = None
    mobile_executor_model: str | None = None
    mobile_screen_model: str | None = None
    mobile_chat_model: str | None = None


class LLMConfigOut(BaseModel):
    """LLM 配置输出"""
    api_key: str | None
    base_url: str | None
    default_model: str | None
    vision_model: str | None
    mobile_planner_model: str | None = None
    mobile_executor_model: str | None = None
    mobile_screen_model: str | None = None
    mobile_chat_model: str | None = None


class ToolConfigUpdate(BaseModel):
    """工具配置更新"""
    api_key: str


class ToolConfigOut(BaseModel):
    """工具配置输出"""
    tool_name: str
    api_key: str
    has_key: bool


class LangSmithConfigUpdate(BaseModel):
    """LangSmith 配置更新"""
    enabled: bool | None = None
    api_key: str | None = None
    project: str | None = None
    endpoint: str | None = None


class LangSmithConfigOut(BaseModel):
    """LangSmith 配置输出"""
    enabled: bool
    api_key: str | None
    project: str | None
    endpoint: str | None


class LangfuseConfigUpdate(BaseModel):
    """Langfuse 配置更新"""
    enabled: bool | None = None
    secret_key: str | None = None
    public_key: str | None = None
    base_url: str | None = None


class LangfuseConfigOut(BaseModel):
    """Langfuse 配置输出"""
    enabled: bool
    secret_key: str | None
    public_key: str | None
    base_url: str | None


class DingTalkConfigUpdate(BaseModel):
    """钉钉机器人配置更新"""
    access_token: str | None = None
    secret: str | None = None
    keyword: str | None = None
    enabled: bool | None = None
    outgoing_app_secret: str | None = None
    stream_enabled: bool | None = None
    client_id: str | None = None
    client_secret: str | None = None
    ai_card_streaming: bool | None = None
    public_base_url: str | None = None
    reconnect_seconds: int | None = None


class DingTalkConfigOut(BaseModel):
    """钉钉机器人配置输出"""
    bot_name: str
    access_token: str | None
    secret: str | None
    keyword: str | None
    enabled: bool
    has_token: bool
    has_outgoing_secret: bool = False
    stream_enabled: bool = False
    client_id: str | None = None
    client_secret: str | None = None
    has_client_secret: bool = False
    ai_card_streaming: bool = True
    public_base_url: str | None = None
    reconnect_seconds: int = 5
    stream_state: str = "stopped"
    stream_connected: bool = False
    stream_last_error: str = ""
    stream_last_connected_at: str | None = None


class ConfigImportRequest(BaseModel):
    """配置导入请求"""
    overwrite: bool = False


class GenericConfigUpdate(BaseModel):
    """通用配置段更新"""
    config: dict[str, Any]


class ConfigRevealRequest(BaseModel):
    """明文查看配置请求。"""
    password: str
    category: str | None = None
    key: str | None = None


class ConfigRevealPasswordUpdate(BaseModel):
    """配置明文查看二级密码更新。"""
    current_password: str
    new_password: str


class AllConfigsOut(BaseModel):
    """所有配置输出"""
    llm: dict[str, Any] | None
    tools: dict[str, dict[str, Any]] | None
    langsmith: dict[str, Any] | None
    langfuse: dict[str, Any] | None
    dingtalk: dict[str, dict[str, Any]] | None


# ==================== 辅助函数 ====================

def _mask_api_key(key: str | None) -> str | None:
    """遮蔽 API Key（只显示前4位和后4位）"""
    return mask_secret(key)


def _llm_config_out(config: dict[str, Any], mask: bool = True) -> LLMConfigOut:
    """转换 LLM 配置输出"""
    api_key = config.get("api_key")
    return LLMConfigOut(
        api_key=_mask_api_key(api_key) if mask else api_key,
        base_url=config.get("base_url"),
        default_model=config.get("default_model"),
        vision_model=config.get("vision_model"),
        mobile_planner_model=config.get("mobile_planner_model"),
        mobile_executor_model=config.get("mobile_executor_model"),
        mobile_screen_model=config.get("mobile_screen_model"),
        mobile_chat_model=config.get("mobile_chat_model"),
    )


def _tool_config_out(tool_name: str, config: dict[str, Any], mask: bool = True) -> ToolConfigOut:
    """转换工具配置输出"""
    api_key = config.get("api_key", "")
    return ToolConfigOut(
        tool_name=tool_name,
        api_key=_mask_api_key(api_key) if mask else api_key,
        has_key=bool(api_key),
    )


def _langsmith_config_out(config: dict[str, Any], mask: bool = True) -> LangSmithConfigOut:
    """转换 LangSmith 配置输出"""
    api_key = config.get("api_key")
    return LangSmithConfigOut(
        enabled=config.get("enabled", False),
        api_key=_mask_api_key(api_key) if mask else api_key,
        project=config.get("project"),
        endpoint=config.get("endpoint"),
    )


def _langfuse_config_out(config: dict[str, Any], mask: bool = True) -> LangfuseConfigOut:
    """转换 Langfuse 配置输出"""
    secret_key = config.get("secret_key")
    public_key = config.get("public_key")
    return LangfuseConfigOut(
        enabled=config.get("enabled", False),
        secret_key=_mask_api_key(secret_key) if mask else secret_key,
        public_key=_mask_api_key(public_key) if mask else public_key,
        base_url=config.get("base_url"),
    )


def _dingtalk_config_out(
    bot_name: str,
    config: dict[str, Any],
    mask: bool = True,
    status: dict[str, Any] | None = None,
) -> DingTalkConfigOut:
    """转换钉钉机器人配置输出"""
    access_token = config.get("access_token", "")
    secret = config.get("secret", "")
    client_secret = config.get("client_secret", "")
    status = status or {}
    return DingTalkConfigOut(
        bot_name=bot_name,
        access_token=_mask_api_key(access_token) if mask else access_token,
        secret=_mask_api_key(secret) if mask else secret,
        keyword=config.get("keyword", ""),
        enabled=config.get("enabled", True),
        has_token=bool(access_token),
        has_outgoing_secret=bool(config.get("outgoing_app_secret")),
        stream_enabled=bool(config.get("stream_enabled", False)),
        client_id=config.get("client_id") or None,
        client_secret=_mask_api_key(client_secret) if mask else client_secret,
        has_client_secret=bool(client_secret),
        ai_card_streaming=bool(config.get("ai_card_streaming", True)),
        public_base_url=config.get("public_base_url") or None,
        reconnect_seconds=int(config.get("reconnect_seconds") or 5),
        stream_state=str(status.get("state") or "stopped"),
        stream_connected=bool(status.get("connected", False)),
        stream_last_error=str(status.get("last_error") or ""),
        stream_last_connected_at=status.get("last_connected_at"),
    )


def _looks_masked_secret(value: Any) -> bool:
    """判断前端回传值是否只是脱敏占位。"""
    return isinstance(value, str) and (value == "***" or "..." in value)


def _merge_config_update(existing: Any, incoming: Any, *, parent_key: str = "") -> Any:
    """合并通用配置段，避免把脱敏 secret 回写成真实值。"""
    if parent_key and is_sensitive_key(parent_key) and _looks_masked_secret(incoming):
        return existing
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(existing)
        for key, value in incoming.items():
            merged[key] = _merge_config_update(existing.get(key), value, parent_key=str(key))
        return merged
    return incoming


# ==================== 获取所有配置 ====================

@router.get("")
async def get_all_configs(_: User = Depends(get_current_active_user)):
    """
    获取所有配置
    
    返回所有配置分类的内容（API Key 会被遮蔽）
    """
    db = get_db()
    configs = await config_dao.get_all_configs(db)
    masked_configs = mask_sensitive_config(configs)
    llm = mask_sensitive_config(await config_dao.get_llm_config(db))
    tools = mask_sensitive_config(await config_dao.list_tool_configs(db))

    dingtalk_configs = await config_dao.list_dingtalk_configs(db)
    dingtalk = mask_sensitive_config(dingtalk_configs)

    return {
        "storage": "mongodb_encrypted",
        "configs": masked_configs,
        "llm": llm,
        "tools": tools if tools else None,
        "langsmith": masked_configs.get("langsmith"),
        "langfuse": masked_configs.get("langfuse"),
        "dingtalk": dingtalk if dingtalk else None,
        "app": masked_configs.get("app"),
        "runtime": masked_configs.get("runtime"),
        "mobile": masked_configs.get("mobile"),
        "mongodb": masked_configs.get("mongodb"),
        "redis": masked_configs.get("redis"),
        "mcpServers": masked_configs.get("mcpServers"),
        "logging": masked_configs.get("logging"),
        "cosyvoice": masked_configs.get("cosyvoice"),
        "bailian": masked_configs.get("bailian"),
        "easytier": masked_configs.get("easytier"),
        "notifications": masked_configs.get("notifications"),
        "chrome_docker": masked_configs.get("chrome_docker"),
        "collection_runtime": masked_configs.get("collection_runtime"),
        "xhs_crawler": masked_configs.get("xhs_crawler"),
        "douyin_crawler": masked_configs.get("douyin_crawler"),
        "object_storage": masked_configs.get("object_storage"),
    }


@router.get("/reveal/status")
async def get_config_reveal_status(admin: User = Depends(require_admin)):
    """查询配置明文查看二级密码状态。"""
    db = get_db()
    configured = await users_dao.has_config_reveal_password(db)
    return {
        "configured": configured,
        "bootstrap_with": "admin_password" if admin.is_admin and not configured else None,
    }


async def _verify_reveal_password(
    password: str,
    admin: User,
    *,
    allow_admin_fallback: bool = False,
) -> None:
    db = get_db()
    if not password:
        raise HTTPException(status_code=400, detail="请输入二级密码")
    configured = await users_dao.has_config_reveal_password(db)
    if not configured and not allow_admin_fallback:
        raise HTTPException(status_code=409, detail="请先设置配置明文查看二级密码")
    ok = await users_dao.verify_config_reveal_password(
        db,
        password,
        fallback_username=admin.username if allow_admin_fallback else None,
    )
    if not ok:
        raise HTTPException(status_code=403, detail="二级密码错误")


@router.post("/reveal")
async def reveal_configs(body: ConfigRevealRequest, admin: User = Depends(require_admin)):
    """管理员输入二级密码后查看解密明文配置。"""
    await _verify_reveal_password(body.password, admin)
    db = get_db()

    if body.category:
        doc = await config_dao.get_config(db, body.category, key=body.key)
        if not doc:
            raise HTTPException(status_code=404, detail=f"配置段 {body.category} 不存在")
        return {
            "storage": "mongodb_encrypted",
            "revealed": True,
            "category": body.category,
            "key": body.key,
            "config": doc.get("config", {}),
        }

    configs = await config_dao.get_all_configs(db)
    return {
        "storage": "mongodb_encrypted",
        "revealed": True,
        "configs": configs,
        "llm": await config_dao.get_llm_config(db),
        "tools": await config_dao.list_tool_configs(db),
        "langsmith": configs.get("langsmith"),
        "langfuse": configs.get("langfuse"),
        "dingtalk": await config_dao.list_dingtalk_configs(db),
        "app": configs.get("app"),
        "runtime": configs.get("runtime"),
        "mobile": configs.get("mobile"),
        "mongodb": configs.get("mongodb"),
        "redis": configs.get("redis"),
        "mcpServers": configs.get("mcpServers"),
        "logging": configs.get("logging"),
        "cosyvoice": configs.get("cosyvoice"),
        "bailian": configs.get("bailian"),
        "easytier": configs.get("easytier"),
        "notifications": configs.get("notifications"),
        "chrome_docker": configs.get("chrome_docker"),
        "collection_runtime": configs.get("collection_runtime"),
        "xhs_crawler": configs.get("xhs_crawler"),
        "douyin_crawler": configs.get("douyin_crawler"),
        "object_storage": configs.get("object_storage"),
    }


@router.post("/reveal/password")
async def set_config_reveal_password(
    body: ConfigRevealPasswordUpdate,
    admin: User = Depends(require_admin),
):
    """设置或修改配置明文查看二级密码。"""
    db = get_db()
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新二级密码长度至少 8 位")
    await _verify_reveal_password(
        body.current_password,
        admin,
        allow_admin_fallback=True,
    )
    await users_dao.set_config_reveal_password(db, body.new_password)
    return {"ok": True, "configured": True}


# ==================== LLM 配置 ====================

@router.get("/llm", response_model=LLMConfigOut)
async def get_llm_config(_: User = Depends(get_current_active_user)):
    """获取 LLM 配置"""
    db = get_db()
    config = await config_dao.get_llm_config(db)
    return _llm_config_out(config)


@router.post("/llm", response_model=LLMConfigOut)
async def set_llm_config(body: LLMConfigUpdate, _: User = Depends(require_admin)):
    """
    设置 LLM 配置
    
    可以只更新部分字段，未提供的字段保持不变
    """
    db = get_db()
    await config_dao.set_llm_config(
        db,
        api_key=body.api_key,
        base_url=body.base_url,
        default_model=body.default_model,
        vision_model=body.vision_model,
        mobile_planner_model=body.mobile_planner_model,
        mobile_executor_model=body.mobile_executor_model,
        mobile_screen_model=body.mobile_screen_model,
        mobile_chat_model=body.mobile_chat_model,
    )
    config = await config_dao.get_llm_config(db)
    return _llm_config_out(config)


@router.delete("/llm")
async def delete_llm_config(_: User = Depends(require_admin)):
    """删除 LLM 配置"""
    db = get_db()
    deleted = await config_dao.delete_llm_config(db)
    return {"ok": deleted}


# ==================== Tools 配置 ====================

@router.get("/tools")
async def list_tool_configs(_: User = Depends(get_current_active_user)):
    """列出所有工具配置"""
    db = get_db()
    configs = await config_dao.list_tool_configs(db)
    
    return {
        "tools": [
            _tool_config_out(name, config)
            for name, config in configs.items()
        ]
    }


@router.get("/tools/{tool_name}", response_model=ToolConfigOut)
async def get_tool_config(tool_name: str, _: User = Depends(get_current_active_user)):
    """获取指定工具配置"""
    db = get_db()
    config = await config_dao.get_tool_config(db, tool_name)
    
    if not config:
        raise HTTPException(status_code=404, detail=f"工具 {tool_name} 配置不存在")
    
    return _tool_config_out(tool_name, config)


# ==================== 通用配置段 ====================

@router.get("/sections/{category}")
async def get_config_section(category: str, _: User = Depends(get_current_active_user)):
    """读取任意配置段，输出自动脱敏。"""
    db = get_db()
    doc = await config_dao.get_config(db, category)
    if not doc:
        raise HTTPException(status_code=404, detail=f"配置段 {category} 不存在")
    return {
        "category": category,
        "config": mask_sensitive_config(doc.get("config", {})),
        "storage": "mongodb_encrypted",
    }


@router.post("/sections/{category}")
async def set_config_section(
    category: str,
    body: GenericConfigUpdate,
    _: User = Depends(require_admin),
):
    """写入任意配置段，敏感字段会在 DAO 层加密。"""
    db = get_db()
    existing_doc = await config_dao.get_config(db, category)
    existing_config = existing_doc.get("config", {}) if existing_doc else {}
    merged_config = _merge_config_update(existing_config, body.config)
    doc = await config_dao.set_config(db, category, merged_config)
    if category == "easytier":
        try:
            from core.mobile.easytier import set_easytier_runtime_config

            set_easytier_runtime_config(doc.get("config", {}))
        except Exception:
            pass
    return {
        "category": category,
        "config": mask_sensitive_config(doc.get("config", {})),
        "storage": "mongodb_encrypted",
    }


@router.post("/tools/{tool_name}", response_model=ToolConfigOut)
async def set_tool_config(tool_name: str, body: ToolConfigUpdate, _: User = Depends(require_admin)):
    """
    设置工具配置
    
    支持的工具：tianyancha, hunter, fofa, bocha 等
    """
    db = get_db()
    await config_dao.set_tool_config(db, tool_name, body.api_key)
    config = await config_dao.get_tool_config(db, tool_name)
    return _tool_config_out(tool_name, config)


@router.post("/tools/{tool_name}/test")
async def test_tool_config(tool_name: str, _: User = Depends(require_admin)):
    """
    探测工具 API Key 有效性

    按 tool_name 分派到各工具的轻量校验（tianyancha/hunter/fofa/bocha），
    使用已存储的加密 Key。返回 {ok, message}。
    """
    from api.services.tool_key_test import test_tool_key

    ok, message = await test_tool_key(tool_name)
    return {"ok": ok, "message": message}


@router.delete("/tools/{tool_name}")
async def delete_tool_config(tool_name: str, _: User = Depends(require_admin)):
    """删除工具配置"""
    db = get_db()
    deleted = await config_dao.delete_tool_config(db, tool_name)
    
    if not deleted:
        raise HTTPException(status_code=404, detail=f"工具 {tool_name} 配置不存在")
    
    return {"ok": True}


# ==================== LangSmith 配置 ====================

@router.get("/langsmith", response_model=LangSmithConfigOut)
async def get_langsmith_config(_: User = Depends(get_current_active_user)):
    """获取 LangSmith 配置"""
    db = get_db()
    config = await config_dao.get_langsmith_config(db)
    return _langsmith_config_out(config)


@router.post("/langsmith", response_model=LangSmithConfigOut)
async def set_langsmith_config(body: LangSmithConfigUpdate, _: User = Depends(require_admin)):
    """
    设置 LangSmith 配置
    
    可以只更新部分字段
    """
    db = get_db()
    await config_dao.set_langsmith_config(
        db,
        enabled=body.enabled,
        api_key=body.api_key,
        project=body.project,
        endpoint=body.endpoint,
    )
    config = await config_dao.get_langsmith_config(db)
    return _langsmith_config_out(config)


@router.post("/langsmith/toggle")
async def toggle_langsmith(enabled: bool, _: User = Depends(require_admin)):
    """快速开关 LangSmith"""
    db = get_db()
    await config_dao.set_langsmith_config(db, enabled=enabled)
    return {"enabled": enabled}


@router.delete("/langsmith")
async def delete_langsmith_config(_: User = Depends(require_admin)):
    """删除 LangSmith 配置"""
    db = get_db()
    deleted = await config_dao.delete_config(db, "langsmith")
    return {"ok": deleted}


# ==================== Langfuse 配置 ====================

@router.get("/langfuse", response_model=LangfuseConfigOut)
async def get_langfuse_config(_: User = Depends(get_current_active_user)):
    """获取 Langfuse 配置"""
    db = get_db()
    config = await config_dao.get_langfuse_config(db)
    return _langfuse_config_out(config)


@router.post("/langfuse", response_model=LangfuseConfigOut)
async def set_langfuse_config(body: LangfuseConfigUpdate, _: User = Depends(require_admin)):
    """
    设置 Langfuse 配置
    
    可以只更新部分字段
    """
    db = get_db()
    await config_dao.set_langfuse_config(
        db,
        enabled=body.enabled,
        secret_key=body.secret_key,
        public_key=body.public_key,
        base_url=body.base_url,
    )
    config = await config_dao.get_langfuse_config(db)
    return _langfuse_config_out(config)


@router.post("/langfuse/toggle")
async def toggle_langfuse(enabled: bool, _: User = Depends(require_admin)):
    """快速开关 Langfuse"""
    db = get_db()
    await config_dao.set_langfuse_config(db, enabled=enabled)
    return {"enabled": enabled}


@router.delete("/langfuse")
async def delete_langfuse_config(_: User = Depends(require_admin)):
    """删除 Langfuse 配置"""
    db = get_db()
    deleted = await config_dao.delete_config(db, "langfuse")
    return {"ok": deleted}


# ==================== DingTalk 配置 ====================

@router.get("/dingtalk")
async def list_dingtalk_configs(_: User = Depends(get_current_active_user)):
    """列出所有钉钉机器人配置"""
    db = get_db()
    configs = await config_dao.list_dingtalk_configs(db)
    
    from api.services.dingtalk_stream import DingTalkStreamManager

    manager = DingTalkStreamManager.get_instance()
    return {
        "bots": [
            _dingtalk_config_out(name, config, status=manager.get_status(name))
            for name, config in configs.items()
        ]
    }


@router.get("/dingtalk/{bot_name}", response_model=DingTalkConfigOut)
async def get_dingtalk_config(bot_name: str, _: User = Depends(get_current_active_user)):
    """获取指定钉钉机器人配置"""
    db = get_db()
    config = await config_dao.get_dingtalk_config(db, bot_name)
    
    if not config:
        raise HTTPException(status_code=404, detail=f"钉钉机器人 {bot_name} 配置不存在")
    
    from api.services.dingtalk_stream import DingTalkStreamManager

    return _dingtalk_config_out(
        bot_name,
        config,
        status=DingTalkStreamManager.get_instance().get_status(bot_name),
    )


@router.post("/dingtalk/{bot_name}", response_model=DingTalkConfigOut)
async def set_dingtalk_config(bot_name: str, body: DingTalkConfigUpdate, _: User = Depends(require_admin)):
    """
    设置钉钉机器人配置
    
    可以只更新部分字段，未提供的字段保持不变
    
    配置说明：
    - access_token: Webhook URL 中的 access_token 参数
    - secret: 签名密钥（安全设置中的加签密钥）
    - keyword: 关键词（安全设置中的自定义关键词）
    - enabled: 是否启用
    """
    db = get_db()
    await config_dao.set_dingtalk_config(
        db,
        bot_name=bot_name,
        access_token=body.access_token,
        secret=body.secret,
        keyword=body.keyword,
        enabled=body.enabled,
        outgoing_app_secret=body.outgoing_app_secret,
        stream_enabled=body.stream_enabled,
        client_id=body.client_id,
        client_secret=body.client_secret,
        ai_card_streaming=body.ai_card_streaming,
        public_base_url=body.public_base_url,
        reconnect_seconds=body.reconnect_seconds,
    )
    from api.services.dingtalk_stream import DingTalkStreamManager

    manager = DingTalkStreamManager.get_instance()
    await manager.reload_bot(bot_name)
    config = await config_dao.get_dingtalk_config(db, bot_name)
    return _dingtalk_config_out(bot_name, config, status=manager.get_status(bot_name))


@router.post("/dingtalk/{bot_name}/toggle")
async def toggle_dingtalk(bot_name: str, enabled: bool, _: User = Depends(require_admin)):
    """快速开关钉钉机器人"""
    db = get_db()
    await config_dao.set_dingtalk_config(db, bot_name=bot_name, enabled=enabled)
    from api.services.dingtalk_stream import DingTalkStreamManager

    await DingTalkStreamManager.get_instance().reload_bot(bot_name)
    return {"bot_name": bot_name, "enabled": enabled}


@router.delete("/dingtalk/{bot_name}")
async def delete_dingtalk_config(bot_name: str, _: User = Depends(require_admin)):
    """删除钉钉机器人配置"""
    db = get_db()
    deleted = await config_dao.delete_dingtalk_config(db, bot_name)
    
    if not deleted:
        raise HTTPException(status_code=404, detail=f"钉钉机器人 {bot_name} 配置不存在")
    
    from api.services.dingtalk_stream import DingTalkStreamManager

    await DingTalkStreamManager.get_instance().reload_bot(bot_name)
    return {"ok": True}


@router.get("/dingtalk/{bot_name}/status")
async def get_dingtalk_stream_status(
    bot_name: str,
    _: User = Depends(get_current_active_user),
):
    """查询 Stream Mode 长连接运行状态。"""
    from api.services.dingtalk_stream import DingTalkStreamManager

    config = await config_dao.get_dingtalk_config(get_db(), bot_name)
    if not config:
        raise HTTPException(status_code=404, detail=f"钉钉机器人 {bot_name} 配置不存在")
    return DingTalkStreamManager.get_instance().get_status(bot_name)


@router.post("/dingtalk/{bot_name}/test")
async def test_dingtalk_bot(bot_name: str, _: User = Depends(require_admin)):
    """
    测试钉钉机器人
    
    发送一条测试消息验证配置是否正确
    """
    from api.services.notifications import notify_event

    result = await notify_event(
        event="config.dingtalk.test",
        title="钉钉机器人配置测试",
        content="钉钉机器人已正确配置，可以正常发送消息。",
        level="info",
        source="config",
        channels=["dingtalk"],
        bot_name=bot_name,
        force=True,
    )

    if result.ok:
        return {"ok": True, "message": "测试消息发送成功", "dispatch": result.to_dict()}
    raise HTTPException(status_code=400, detail=result.to_dict())


# ==================== 导入导出 ====================

@router.post("/import")
async def import_from_config_json(body: ConfigImportRequest, _: User = Depends(require_admin)):
    """
    旧 config.json 导入入口已下线。
    
    Args:
        overwrite: 是否覆盖已有配置（默认 False）
    
    Returns:
        导入结果统计
    """
    _ = body
    raise HTTPException(
        status_code=410,
        detail="config.json 导入入口已下线；请在前端配置页写入 MongoDB 加密配置。",
    )
