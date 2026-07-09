"""
XhsCrawler - 小红书爬虫工具

功能：
- 二维码登录
- Cookie 文件读取登录（系统配置统一来自数据库）
- Cookie 有效性检验
- 关键词搜索，返回笔记列表
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import BrowserContext, Page, async_playwright

_logger = logging.getLogger("xhs_crawler")


def _ensure_mediacrawler_importable() -> None:
    """确保 MediaCrawler 模块可导入"""
    repo_root = Path(__file__).resolve().parents[1]
    mc_path = str(repo_root / "MediaCrawler")
    if mc_path not in sys.path:
        sys.path.insert(0, mc_path)


# 初始化导入路径
_ensure_mediacrawler_importable()


@dataclass
class CrawlerConfig:
    """爬虫配置"""
    # Cookie 相关 - 支持多账号
    cookies: Dict[str, str] = field(default_factory=dict)  # 多账号 cookie {账号名: cookie字符串}
    active_account: str = "default"  # 当前使用的账号名
    
    # 浏览器相关
    headless: bool = True  # 浏览器是否无头模式（默认 True）
    enable_cdp_mode: bool = True  # 是否使用 CDP 模式
    cdp_headless: bool = True  # CDP 模式下是否无头（默认 True）
    
    # 代理相关
    enable_ip_proxy: bool = False
    ip_proxy_pool_count: int = 2
    proxy_url: Optional[str] = None
    
    # User-Agent
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
    
    def get_active_cookie(self) -> Optional[str]:
        """获取当前激活账号的 cookie"""
        return self.cookies.get(self.active_account)
    
    def set_cookie(self, account_name: str, cookie_string: str) -> None:
        """设置指定账号的 cookie"""
        self.cookies[account_name] = cookie_string
        
    def list_accounts(self) -> List[str]:
        """列出所有账号"""
        return list(self.cookies.keys())

    @classmethod
    def from_dict(cls, xhs_config: dict[str, Any] | None) -> "CrawlerConfig":
        """从数据库配置段加载配置。"""
        config = cls()
        xhs_config = xhs_config or {}
        if "cookies" in xhs_config:
            config.cookies = xhs_config["cookies"]
        if "active_account" in xhs_config:
            config.active_account = xhs_config["active_account"]
        if "headless" in xhs_config:
            config.headless = xhs_config["headless"]
        if "enable_cdp_mode" in xhs_config:
            config.enable_cdp_mode = xhs_config["enable_cdp_mode"]
        if "cdp_headless" in xhs_config:
            config.cdp_headless = xhs_config["cdp_headless"]
        if "user_agent" in xhs_config:
            config.user_agent = xhs_config["user_agent"]
        if "enable_ip_proxy" in xhs_config:
            config.enable_ip_proxy = bool(xhs_config["enable_ip_proxy"])
        if "ip_proxy_pool_count" in xhs_config:
            config.ip_proxy_pool_count = int(xhs_config["ip_proxy_pool_count"])
        if "proxy_url" in xhs_config:
            config.proxy_url = xhs_config["proxy_url"]
        return config
    
    @classmethod
    def from_config_file(cls, config_path: str) -> "CrawlerConfig":
        """兼容旧调用签名；系统配置不再从 config.json 读取。"""
        _logger.warning(
            "[CrawlerConfig] 已忽略旧 config_path=%s；请通过前端配置写入数据库",
            config_path,
        )
        return cls()
    
    def save_to_config_file(self, config_path: str) -> bool:
        """兼容旧调用签名；系统配置不再回写 config.json。"""
        _logger.warning(
            "[CrawlerConfig] 已忽略旧配置文件写入 config_path=%s；请通过前端配置写入数据库",
            config_path,
        )
        return False


@dataclass
class LoginResult:
    """登录结果"""
    success: bool
    message: str
    cookies: Optional[Dict[str, str]] = None


@dataclass
class SearchResult:
    """搜索结果"""
    success: bool
    message: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    has_more: bool = False
    total: int = 0


class CookieManager:
    """Cookie 管理器"""
    
    def __init__(self, cookie_file_path: Optional[str] = None):
        self.cookie_file_path = cookie_file_path
        self._cookies: Dict[str, str] = {}
        
    def set_cookie_file_path(self, path: str) -> None:
        """设置 Cookie 文件路径"""
        self.cookie_file_path = path
        
    def load_from_file(self, file_path: Optional[str] = None) -> Dict[str, str]:
        """从文件加载 Cookie"""
        path = file_path or self.cookie_file_path
        if not path or not os.path.exists(path):
            return {}
            
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # 支持多种格式
        if isinstance(data, dict):
            if "cookies" in data:
                # 格式: {"cookies": {"key": "value"}}
                self._cookies = data["cookies"]
            else:
                # 格式: {"key": "value"}
                self._cookies = data
        elif isinstance(data, list):
            # 格式: [{"name": "key", "value": "value"}]
            self._cookies = {item["name"]: item["value"] for item in data if "name" in item}
            
        return self._cookies
    
    def load_from_string(self, cookie_string: str) -> Dict[str, str]:
        """从字符串加载 Cookie (格式: key1=value1; key2=value2)"""
        self._cookies = {}
        if not cookie_string:
            return self._cookies
            
        for item in cookie_string.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                self._cookies[key.strip()] = value.strip()
                
        return self._cookies
    
    def save_to_file(self, file_path: Optional[str] = None) -> bool:
        """保存 Cookie 到文件"""
        path = file_path or self.cookie_file_path
        if not path:
            return False
            
        # 确保目录存在
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cookies": self._cookies}, f, ensure_ascii=False, indent=2)
            
        return True
    
    def set_cookies(self, cookies: Dict[str, str]) -> None:
        """设置 Cookie"""
        self._cookies = cookies
        
    def get_cookies(self) -> Dict[str, str]:
        """获取 Cookie"""
        return self._cookies
    
    def get_cookie_string(self) -> str:
        """获取 Cookie 字符串"""
        return "; ".join([f"{k}={v}" for k, v in self._cookies.items()])
    
    def has_valid_session(self) -> bool:
        """检查是否有有效的 session cookie"""
        return bool(self._cookies.get("web_session"))


class XhsCrawler:
    """小红书爬虫"""
    
    def __init__(self, config: Optional[CrawlerConfig] = None, config_path: Optional[str] = None):
        self.config = config or CrawlerConfig()
        self.config_path = config_path  # 保存配置文件路径，用于保存 cookie
        self.cookie_manager = CookieManager()
        
        # 运行时状态
        self._playwright = None
        self._playwright_cm = None
        self._browser_context: Optional[BrowserContext] = None
        self._context_page: Optional[Page] = None
        self._client = None
        self._cdp_manager = None
        self._is_initialized = False
        
    async def _init_browser(self) -> None:
        """初始化浏览器"""
        if self._is_initialized:
            return
            
        _ensure_mediacrawler_importable()
        
        # 同步 MediaCrawler 配置；代理由上层 runtime service 统一决定。
        import config as mc_config
        mc_config.ENABLE_IP_PROXY = self.config.enable_ip_proxy
        mc_config.IP_PROXY_POOL_COUNT = self.config.ip_proxy_pool_count
        
        from tools.cdp_browser import CDPBrowserManager
        
        self._playwright_cm = async_playwright()
        self._playwright = await self._playwright_cm.__aenter__()
        
        _logger.debug(f"[XhsCrawler._init_browser] enable_cdp_mode={self.config.enable_cdp_mode}, cdp_headless={self.config.cdp_headless}, headless={self.config.headless}")
        
        if self.config.enable_cdp_mode:
            self._cdp_manager = CDPBrowserManager()
            self._browser_context = await self._cdp_manager.launch_and_connect(
                playwright=self._playwright,
                playwright_proxy=None,
                user_agent=self.config.user_agent,
                headless=self.config.cdp_headless,
                task_id=f"xhs_crawler_{id(self)}",
            )
        else:
            chromium = self._playwright.chromium
            browser = await chromium.launch(
                headless=self.config.headless,
                proxy=None
            )
            self._browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=self.config.user_agent,
            )
            
        self._context_page = await self._browser_context.new_page()
        await self._context_page.goto("https://www.xiaohongshu.com")
        
        self._is_initialized = True
        
    async def _init_client(self) -> None:
        """初始化 API 客户端"""
        _ensure_mediacrawler_importable()
        
        import config as mc_config
        mc_config.ENABLE_IP_PROXY = self.config.enable_ip_proxy
        mc_config.IP_PROXY_POOL_COUNT = self.config.ip_proxy_pool_count
        
        from media_platform.xhs.client import XiaoHongShuClient
        from tools import utils
        
        cookie_str, cookie_dict = utils.convert_cookies(
            await self._browser_context.cookies()
        )
        
        self._client = XiaoHongShuClient(
            proxy=self.config.proxy_url,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json;charset=UTF-8",
                "origin": "https://www.xiaohongshu.com",
                "pragma": "no-cache",
                "priority": "u=1, i",
                "referer": "https://www.xiaohongshu.com/",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": self.config.user_agent,
                "Cookie": cookie_str,
            },
            playwright_page=self._context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=None,  # 禁用代理池
        )
        
    async def _update_cookies_from_browser(self) -> None:
        """从浏览器更新 Cookie"""
        _ensure_mediacrawler_importable()
        from tools import utils
        
        browser_cookies = await self._browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(browser_cookies)
        self.cookie_manager.set_cookies(cookie_dict)
        
        if self._client:
            await self._client.update_cookies(self._browser_context)
            
    async def login_by_qrcode(self, account_name: Optional[str] = None, save_to_config: bool = True) -> LoginResult:
        """
        二维码登录
        
        Args:
            account_name: 保存到的账号名，默认使用 active_account
            save_to_config: 显式传入旧 config_path 时是否回写旧配置文件
            
        Returns:
            LoginResult: 登录结果
        """
        try:
            await self._init_browser()
            
            _ensure_mediacrawler_importable()
            from media_platform.xhs.login import XiaoHongShuLogin
            from tools import utils
            
            # 获取登录前的 session
            current_cookie = await self._browser_context.cookies()
            _, cookie_dict = utils.convert_cookies(current_cookie)
            
            # 创建登录对象
            login_obj = XiaoHongShuLogin(
                login_type="qrcode",
                login_phone="",
                browser_context=self._browser_context,
                context_page=self._context_page,
                cookie_str="",
            )
            
            # 执行登录
            await login_obj.begin()
            
            # 更新 Cookie
            await self._update_cookies_from_browser()
            
            # 初始化客户端
            await self._init_client()
            
            # 保存 Cookie 到配置
            target_account = account_name or self.config.active_account
            cookie_str = self.cookie_manager.get_cookie_string()
            self.config.set_cookie(target_account, cookie_str)
            
            if save_to_config and self.config_path:
                self.config.save_to_config_file(self.config_path)
                
            return LoginResult(
                success=True,
                message=f"二维码登录成功，已保存到账号: {target_account}",
                cookies=self.cookie_manager.get_cookies()
            )
            
        except Exception as e:
            return LoginResult(
                success=False,
                message=f"二维码登录失败: {str(e)}"
            )
            
    async def login_by_account(self, account_name: Optional[str] = None) -> LoginResult:
        """
        使用指定账号的 Cookie 登录
        
        Args:
            account_name: 账号名，默认使用 active_account
            
        Returns:
            LoginResult: 登录结果
        """
        try:
            target_account = account_name or self.config.active_account
            cookie_string = self.config.cookies.get(target_account)
            
            if not cookie_string:
                return LoginResult(
                    success=False,
                    message=f"账号 {target_account} 的 Cookie 为空"
                )
                
            # 解析 Cookie
            cookies = self.cookie_manager.load_from_string(cookie_string)
            if not cookies:
                return LoginResult(
                    success=False,
                    message=f"账号 {target_account} 的 Cookie 格式错误"
                )
                
            # 初始化浏览器
            await self._init_browser()
            
            # 清除浏览器中的所有旧 Cookie
            await self._browser_context.clear_cookies()
            
            # 将新 Cookie 注入浏览器
            for name, value in cookies.items():
                await self._browser_context.add_cookies([{
                    'name': name,
                    'value': value,
                    'domain': ".xiaohongshu.com",
                    'path': "/"
                }])
                
            # 刷新页面
            await self._context_page.reload(wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
            
            # 初始化客户端
            await self._init_client()
            
            # 验证 Cookie 有效性
            is_valid = await self.verify_cookies()
            if not is_valid:
                return LoginResult(
                    success=False,
                    message=f"账号 {target_account} 的 Cookie 已失效"
                )
            
            # 更新激活账号
            self.config.active_account = target_account
                
            return LoginResult(
                success=True,
                message=f"账号 {target_account} 登录成功",
                cookies=cookies
            )
            
        except Exception as e:
            return LoginResult(
                success=False,
                message=f"账号登录失败: {str(e)}"
            )
            
    async def login_by_cookie_string(self, cookie_string: str, account_name: Optional[str] = None, save_to_config: bool = False) -> LoginResult:
        """
        Cookie 字符串登录
        
        Args:
            cookie_string: Cookie 字符串，格式: key1=value1; key2=value2
            account_name: 保存到的账号名，默认使用 active_account
            save_to_config: 显式传入旧 config_path 时是否回写旧配置文件
            
        Returns:
            LoginResult: 登录结果
        """
        try:
            # 解析 Cookie 字符串
            cookies = self.cookie_manager.load_from_string(cookie_string)
            if not cookies:
                return LoginResult(
                    success=False,
                    message="Cookie 字符串为空或格式错误"
                )
                
            # 初始化浏览器
            await self._init_browser()
            
            # 清除浏览器中小红书域名的所有旧 Cookie
            await self._browser_context.clear_cookies()
            
            # 将新 Cookie 注入浏览器
            for name, value in cookies.items():
                await self._browser_context.add_cookies([{
                    'name': name,
                    'value': value,
                    'domain': ".xiaohongshu.com",
                    'path': "/"
                }])
                
            # 刷新页面
            await self._context_page.reload(wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
            
            # 初始化客户端
            await self._init_client()
            
            # 验证 Cookie 有效性
            is_valid = await self.verify_cookies()
            if not is_valid:
                return LoginResult(
                    success=False,
                    message="Cookie 已失效，请重新登录"
                )
            
            # 保存到配置
            if save_to_config:
                target_account = account_name or self.config.active_account
                self.config.set_cookie(target_account, cookie_string)
                if self.config_path:
                    self.config.save_to_config_file(self.config_path)
                
            return LoginResult(
                success=True,
                message="Cookie 字符串登录成功",
                cookies=cookies
            )
            
        except Exception as e:
            return LoginResult(
                success=False,
                message=f"Cookie 字符串登录失败: {str(e)}"
            )
    
    async def login(self, account_name: Optional[str] = None) -> LoginResult:
        """
        自动登录（优先尝试配置中的 Cookie，失败则二维码登录）
        
        Args:
            account_name: 指定账号名，默认使用 active_account
            
        Returns:
            LoginResult: 登录结果
        """
        target_account = account_name or self.config.active_account
        
        # 尝试使用指定账号的 Cookie 登录
        cookie_string = self.config.cookies.get(target_account)
        if cookie_string:
            result = await self.login_by_account(target_account)
            if result.success:
                return result
                
        # Cookie 登录失败，使用二维码登录
        return await self.login_by_qrcode(account_name=target_account)
    
    def list_accounts(self) -> List[str]:
        """列出所有账号"""
        return self.config.list_accounts()
    
    def get_active_account(self) -> str:
        """获取当前激活账号"""
        return self.config.active_account
    
    async def switch_account(self, account_name: str) -> LoginResult:
        """
        切换账号
        
        Args:
            account_name: 要切换到的账号名
            
        Returns:
            LoginResult: 登录结果
        """
        if account_name not in self.config.cookies:
            return LoginResult(
                success=False,
                message=f"账号 {account_name} 不存在"
            )
        
        # 关闭当前会话
        await self.close()
        
        # 使用新账号登录
        return await self.login_by_account(account_name)
    
    async def verify_cookies(self) -> bool:
        """
        验证 Cookie 有效性
        
        Returns:
            bool: Cookie 是否有效
        """
        if not self._client:
            await self._init_client()
            
        try:
            # 使用 pong 方法验证
            result = await self._client.pong()
            return result
        except Exception:
            return False
            
    async def search_notes(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        sort_type: str = "general",
    ) -> SearchResult:
        """
        关键词搜索笔记
        
        Args:
            keyword: 搜索关键词
            page: 页码，从 1 开始
            page_size: 每页数量，小红书固定为 20，其他值会导致搜索失败
            sort_type: 排序方式
                - general: 综合排序
                - popularity_descending: 热度降序
                - time_descending: 时间降序
                
        Returns:
            SearchResult: 搜索结果
        """
        if not self._client:
            return SearchResult(
                success=False,
                message="请先登录"
            )
        
        # 小红书 API 要求 page_size 必须为 20
        if page_size != 20:
            page_size = 20
            
        try:
            _ensure_mediacrawler_importable()
            from media_platform.xhs.field import SearchSortType
            from media_platform.xhs.help import get_search_id
            
            # 映射排序类型
            sort_map = {
                "general": SearchSortType.GENERAL,
                "popularity_descending": SearchSortType.MOST_POPULAR,
                "time_descending": SearchSortType.LATEST,
            }
            sort = sort_map.get(sort_type, SearchSortType.GENERAL)
            
            # 生成 search_id
            search_id = get_search_id()
            
            # 执行搜索
            result = await self._client.get_note_by_keyword(
                keyword=keyword,
                search_id=search_id,
                page=page,
                page_size=page_size,
                sort=sort,
            )
            
            items = result.get("items", [])
            has_more = result.get("has_more", False)
            
            # 提取简化的笔记信息
            notes = []
            for item in items:
                note_card = item.get("note_card", {})
                if not note_card:
                    continue
                    
                note = {
                    "note_id": item.get("id", ""),
                    "xsec_token": item.get("xsec_token", ""),
                    "xsec_source": item.get("xsec_source", ""),
                    "title": note_card.get("display_title", ""),
                    "desc": note_card.get("desc", ""),
                    "type": note_card.get("type", ""),
                    "liked_count": note_card.get("interact_info", {}).get("liked_count", "0"),
                    "user": {
                        "user_id": note_card.get("user", {}).get("user_id", ""),
                        "nickname": note_card.get("user", {}).get("nickname", ""),
                        "avatar": note_card.get("user", {}).get("avatar", ""),
                    },
                    "cover": note_card.get("cover", {}).get("url_default", ""),
                }
                notes.append(note)
                
            return SearchResult(
                success=True,
                message="搜索成功",
                items=notes,
                has_more=has_more,
                total=len(notes),
            )
            
        except Exception as e:
            return SearchResult(
                success=False,
                message=f"搜索失败: {str(e)}"
            )
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """
        获取用户信息（不爬取用户笔记）
        
        Args:
            user_id: 用户 ID
            
        Returns:
            Dict: 用户信息
            {
                "user_id": "用户ID",
                "nickname": "昵称",
                "gender": "性别",
                "avatar": "头像",
                "desc": "个人简介",
                "ip_location": "IP属地",
                "follows": 关注数,
                "fans": 粉丝数,
                "interaction": 互动数,
                "tags": [标签列表]
            }
        """
        if not self._client:
            raise RuntimeError("请先登录")
        
        try:
            # 获取用户信息
            creator_data = await self._client.get_creator_info(
                user_id=user_id,
                xsec_token="",
                xsec_source=""
            )
            
            if not creator_data:
                return {}
            
            # 解析用户信息
            user_info = creator_data.get("user", {})
            interactions = creator_data.get("interactions", [])
            
            # 提取互动数据
            follows = 0
            fans = 0
            interaction = 0
            for item in interactions:
                item_type = item.get("type")
                count = item.get("count", 0)
                if item_type == "follows":
                    follows = count
                elif item_type == "fans":
                    fans = count
                elif item_type == "interaction":
                    interaction = count
            
            # 提取标签
            tags = []
            for tag in creator_data.get("tags", []):
                tag_name = tag.get("name", "")
                if tag_name:
                    tags.append(tag_name)
            
            return {
                "user_id": user_id,
                "nickname": user_info.get("nickname", ""),
                "gender": user_info.get("gender", ""),
                "avatar": user_info.get("images", ""),
                "desc": user_info.get("desc", ""),
                "ip_location": user_info.get("ipLocation", ""),
                "follows": follows,
                "fans": fans,
                "interaction": interaction,
                "tags": tags,
            }
            
        except Exception as e:
            return {"error": str(e)}
            
    async def close(self) -> None:
        """关闭爬虫，释放资源"""
        try:
            if self._cdp_manager:
                await self._cdp_manager.cleanup(force=True)
            elif self._browser_context:
                await self._browser_context.close()
        except Exception:
            pass
            
        try:
            if self._playwright_cm:
                await self._playwright_cm.__aexit__(None, None, None)
        except Exception:
            pass
            
        self._is_initialized = False
        self._playwright = None
        self._browser_context = None
        self._context_page = None
        self._client = None
        
    async def __aenter__(self) -> "XhsCrawler":
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# 便捷函数
async def create_crawler(config_path: Optional[str] = None) -> XhsCrawler:
    """
    创建爬虫实例
    
    Args:
        config_path: 兼容旧调用；当前总是从数据库 xhs_crawler 配置段读取
        
    Returns:
        XhsCrawler: 爬虫实例
    """
    if config_path is not None:
        _logger.warning("已忽略旧 config_path=%s；XHS 爬虫配置从数据库读取", config_path)
    from api.services.runtime_config import get_runtime_config_section

    config = CrawlerConfig.from_dict(await get_runtime_config_section("xhs_crawler"))
    _logger.debug(f"[XhsCrawler] 配置加载: headless={config.headless}, cdp_headless={config.cdp_headless}, enable_cdp_mode={config.enable_cdp_mode}")
    return XhsCrawler(config, config_path=None)


async def quick_search(
    keyword: str,
    page: int = 1,
    page_size: int = 20,
    config_path: Optional[str] = None,
) -> SearchResult:
    """
    快速搜索（自动登录 + 搜索 + 关闭）
    
    Args:
        keyword: 搜索关键词
        page: 页码
        page_size: 每页数量
        config_path: 配置文件路径
        
    Returns:
        SearchResult: 搜索结果
    """
    async with await create_crawler(config_path) as crawler:
        login_result = await crawler.login()
        if not login_result.success:
            return SearchResult(
                success=False,
                message=f"登录失败: {login_result.message}"
            )
            
        return await crawler.search_notes(
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
