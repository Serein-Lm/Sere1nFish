"""
钉钉机器人工具

功能：
- 发送 Markdown 消息
- 发送文本消息
- 支持签名验证
- 支持关键词过滤
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import parse

import aiohttp


@dataclass
class DingTalkConfig:
    """钉钉机器人配置"""
    name: str  # 机器人名称
    access_token: str  # Webhook access_token
    secret: str  # 签名密钥
    keyword: str = ""  # 关键词（可选）
    enabled: bool = True


@dataclass
class SendResult:
    """发送结果"""
    success: bool
    message: str
    errcode: int | None = None


class DingTalkBot:
    """钉钉机器人"""
    
    def __init__(
        self,
        access_token: str,
        secret: str,
        keyword: str = "",
        proxy_url: str = "",
    ):
        """
        初始化钉钉机器人
        
        Args:
            access_token: Webhook access_token
            secret: 签名密钥
            keyword: 关键词（消息中必须包含此关键词才能发送）
            proxy_url: 代理地址
        """
        self.access_token = access_token
        self.secret = secret
        self.keyword = keyword
        self.proxy_url = proxy_url
    
    def _sign(self, timestamp: str) -> str:
        """生成签名"""
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = f'{timestamp}\n{self.secret}'
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(
            secret_enc,
            string_to_sign_enc,
            digestmod=hashlib.sha256
        ).digest()
        return parse.quote_plus(base64.b64encode(hmac_code))
    
    def _build_url(self) -> str:
        """构建请求 URL"""
        timestamp = str(round(time.time() * 1000))
        sign = self._sign(timestamp)
        return (
            f"https://oapi.dingtalk.com/robot/send"
            f"?access_token={self.access_token}"
            f"&timestamp={timestamp}"
            f"&sign={sign}"
        )
    
    def _add_keyword(self, text: str) -> str:
        """添加关键词到消息中"""
        if self.keyword and self.keyword not in text:
            return f"{self.keyword}\n\n{text}"
        return text
    
    async def send_markdown(
        self,
        title: str,
        text: str,
        at_mobiles: list[str] | None = None,
        at_all: bool = False,
    ) -> SendResult:
        """
        发送 Markdown 消息
        
        Args:
            title: 消息标题
            text: Markdown 内容
            at_mobiles: @的手机号列表
            at_all: 是否@所有人
        
        Returns:
            发送结果
        """
        text = self._add_keyword(text)
        
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all,
            },
        }
        
        return await self._send(data)
    
    async def send_text(
        self,
        content: str,
        at_mobiles: list[str] | None = None,
        at_all: bool = False,
    ) -> SendResult:
        """
        发送文本消息
        
        Args:
            content: 文本内容
            at_mobiles: @的手机号列表
            at_all: 是否@所有人
        
        Returns:
            发送结果
        """
        content = self._add_keyword(content)
        
        data = {
            "msgtype": "text",
            "text": {
                "content": content,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all,
            },
        }
        
        return await self._send(data)
    
    async def send_link(
        self,
        title: str,
        text: str,
        message_url: str,
        pic_url: str = "",
    ) -> SendResult:
        """
        发送链接消息
        
        Args:
            title: 消息标题
            text: 消息内容
            message_url: 点击消息跳转的 URL
            pic_url: 图片 URL
        
        Returns:
            发送结果
        """
        data = {
            "msgtype": "link",
            "link": {
                "title": title,
                "text": self._add_keyword(text),
                "messageUrl": message_url,
                "picUrl": pic_url,
            },
        }
        
        return await self._send(data)
    
    async def _send(self, data: dict[str, Any], url: str | None = None) -> SendResult:
        """发送消息。

        url 省略时使用带签名的机器人 Webhook（self._build_url）；
        传入 url 时直接向该地址 POST（用于钉钉回调的 sessionWebhook，其本身已预签名）。
        """
        url = url or self._build_url()
        headers = {"Content-Type": "application/json"}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    data=json.dumps(data),
                    proxy=self.proxy_url if self.proxy_url else None,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = await resp.json()
                    
                    if resp.status == 200 and result.get("errcode") == 0:
                        return SendResult(
                            success=True,
                            message="发送成功",
                            errcode=0,
                        )
                    else:
                        return SendResult(
                            success=False,
                            message=result.get("errmsg", "未知错误"),
                            errcode=result.get("errcode"),
                        )
        except Exception as e:
            return SendResult(
                success=False,
                message=str(e),
            )


# ==================== 从数据库加载配置 ====================

async def get_dingtalk_config_from_db(bot_name: str = "default") -> DingTalkConfig | None:
    """
    从数据库获取钉钉机器人配置
    
    Args:
        bot_name: 机器人名称
    
    Returns:
        钉钉机器人配置
    """
    try:
        from api.db.mongodb import get_db
        from api.dao import config as config_dao
        
        db = get_db()
        config = await config_dao.get_dingtalk_config(db, bot_name)
        
        if not config:
            return None
        
        return DingTalkConfig(
            name=bot_name,
            access_token=config.get("access_token", ""),
            secret=config.get("secret", ""),
            keyword=config.get("keyword", ""),
            enabled=config.get("enabled", True),
        )
    except Exception:
        return None


async def create_dingtalk_bot(bot_name: str = "default") -> DingTalkBot | None:
    """
    创建钉钉机器人实例（从数据库加载配置）
    
    Args:
        bot_name: 机器人名称
    
    Returns:
        钉钉机器人实例
    """
    config = await get_dingtalk_config_from_db(bot_name)
    
    if not config or not config.enabled:
        return None
    
    if not config.access_token or not config.secret:
        return None
    
    return DingTalkBot(
        access_token=config.access_token,
        secret=config.secret,
        keyword=config.keyword,
    )


# ==================== 便捷函数 ====================

async def send_dingtalk_message(
    title: str,
    content: str,
    bot_name: str = "default",
    msg_type: str = "markdown",
) -> SendResult:
    """
    发送钉钉消息（便捷函数）
    
    Args:
        title: 消息标题
        content: 消息内容
        bot_name: 机器人名称
        msg_type: 消息类型（markdown/text）
    
    Returns:
        发送结果
    """
    bot = await create_dingtalk_bot(bot_name)
    
    if not bot:
        return SendResult(
            success=False,
            message=f"钉钉机器人 {bot_name} 未配置或未启用",
        )
    
    if msg_type == "text":
        return await bot.send_text(content)
    else:
        return await bot.send_markdown(title, content)


async def send_alert(
    title: str,
    content: str,
    level: str = "info",
    bot_name: str = "default",
) -> SendResult:
    """
    发送告警消息
    
    Args:
        title: 告警标题
        content: 告警内容
        level: 告警级别（info/warning/error/critical）
        bot_name: 机器人名称
    
    Returns:
        发送结果
    """
    level_emoji = {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
        "critical": "🚨",
    }
    
    emoji = level_emoji.get(level, "ℹ️")
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    text = f"""## {emoji} {title}

**时间**: {today}
**级别**: {level.upper()}

---

{content}
"""
    
    return await send_dingtalk_message(
        title=f"{emoji} {title}",
        content=text,
        bot_name=bot_name,
        msg_type="markdown",
    )


# ==================== 入站回调（钉钉群 @机器人） ====================

def verify_inbound_signature(
    timestamp: str,
    sign: str,
    app_secret: str,
    *,
    max_skew_seconds: int = 3600,
) -> bool:
    """校验钉钉 outgoing 回调请求的签名。

    钉钉在回调请求头带 timestamp、sign：
        sign = base64(HMAC_SHA256(appSecret, "{timestamp}\n{appSecret}"))
    该函数用配置里的 app_secret 复算并比对，同时拒绝过期时间戳（防重放）。

    Args:
        timestamp: 请求头 timestamp（毫秒字符串）
        sign: 请求头 sign
        app_secret: 机器人加签密钥（outgoing_app_secret）
        max_skew_seconds: 允许的时间偏移（秒），默认 1 小时

    Returns:
        校验是否通过
    """
    if not timestamp or not sign or not app_secret:
        return False

    # 时间戳新鲜度校验，拒绝过期/重放
    try:
        ts_ms = int(timestamp)
    except (TypeError, ValueError):
        return False
    now_ms = round(time.time() * 1000)
    if abs(now_ms - ts_ms) > max_skew_seconds * 1000:
        return False

    string_to_sign = f"{timestamp}\n{app_secret}"
    hmac_code = hmac.new(
        app_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    expected = base64.b64encode(hmac_code).decode("utf-8")
    return hmac.compare_digest(expected, sign)


async def reply_to_session_webhook(
    session_webhook: str,
    title: str,
    text: str,
    *,
    at_user_ids: list[str] | None = None,
    at_all: bool = False,
) -> SendResult:
    """向钉钉回调的 sessionWebhook 回推 Markdown 消息。

    sessionWebhook 由钉钉在回调 body 中下发，临时有效且已预签名，
    因此直接 POST 即可，无需 access_token/secret。

    Args:
        session_webhook: 回调 body 中的 sessionWebhook 地址
        title: 消息标题
        text: Markdown 内容
        at_user_ids: 需要 @ 的用户 userId 列表（钉钉 staffId）
        at_all: 是否 @所有人
    """
    if not session_webhook:
        return SendResult(success=False, message="缺少 sessionWebhook")

    data: dict[str, Any] = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
        "at": {"atUserIds": at_user_ids or [], "isAtAll": at_all},
    }
    headers = {"Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                session_webhook,
                headers=headers,
                data=json.dumps(data),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if resp.status == 200 and result.get("errcode") == 0:
                    return SendResult(success=True, message="回推成功", errcode=0)
                return SendResult(
                    success=False,
                    message=result.get("errmsg", "未知错误"),
                    errcode=result.get("errcode"),
                )
    except Exception as e:  # noqa: BLE001
        return SendResult(success=False, message=str(e))
