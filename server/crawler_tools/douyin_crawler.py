"""
DouyinCrawler - 抖音爬虫工具

功能：
- 二维码登录
- Cookie 文件读取登录（系统配置统一来自数据库）
- Cookie 有效性检验
- 关键词搜索，返回作品列表
- 作品详情获取
- 用户主页信息获取
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import BrowserContext, Page, async_playwright


_logger = logging.getLogger("douyin_crawler")


def _ensure_mediacrawler_importable() -> None:
    """确保 MediaCrawler 模块可导入，并设置正确的工作目录"""
    repo_root = Path(__file__).resolve().parents[1]
    mc_path = str(repo_root / "MediaCrawler")
    if mc_path not in sys.path:
        sys.path.insert(0, mc_path)
    
    # 切换工作目录到 MediaCrawler，确保 libs/douyin.js 能被正确加载
    import os
    if os.getcwd() != mc_path:
        os.chdir(mc_path)


# 初始化导入路径
_ensure_mediacrawler_importable()


@dataclass
class DouyinCrawlerConfig:
    """抖音爬虫配置"""
    # Cookie 相关 - 支持多账号
    cookies: Dict[str, str] = field(default_factory=dict)
    active_account: str = "default"
    
    # 浏览器相关
    headless: bool = True
    enable_cdp_mode: bool = True
    cdp_headless: bool = True
    
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
    def from_dict(cls, dy_config: dict[str, Any] | None) -> "DouyinCrawlerConfig":
        """从数据库配置段加载配置。"""
        config = cls()
        dy_config = dy_config or {}
        if "cookies" in dy_config:
            config.cookies = dy_config["cookies"]
        if "active_account" in dy_config:
            config.active_account = dy_config["active_account"]
        if "headless" in dy_config:
            config.headless = dy_config["headless"]
        if "enable_cdp_mode" in dy_config:
            config.enable_cdp_mode = dy_config["enable_cdp_mode"]
        if "cdp_headless" in dy_config:
            config.cdp_headless = dy_config["cdp_headless"]
        if "user_agent" in dy_config:
            config.user_agent = dy_config["user_agent"]
        return config
    
    @classmethod
    def from_config_file(cls, config_path: str) -> "DouyinCrawlerConfig":
        """兼容旧调用签名；系统配置不再从 config.json 读取。"""
        _logger.warning(
            "[DouyinCrawlerConfig] 已忽略旧 config_path=%s；请通过前端配置写入数据库",
            config_path,
        )
        return cls()
    
    def save_to_config_file(self, config_path: str) -> bool:
        """兼容旧调用签名；系统配置不再回写 config.json。"""
        _logger.warning(
            "[DouyinCrawlerConfig] 已忽略旧配置文件写入 config_path=%s；请通过前端配置写入数据库",
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


class DouyinCookieManager:
    """Cookie 管理器"""
    
    def __init__(self):
        self._cookies: Dict[str, str] = {}
        
    def load_from_string(self, cookie_string: str) -> Dict[str, str]:
        """从字符串加载 Cookie"""
        self._cookies = {}
        if not cookie_string:
            return self._cookies
            
        for item in cookie_string.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                self._cookies[key.strip()] = value.strip()
                
        return self._cookies
    
    def set_cookies(self, cookies: Dict[str, str]) -> None:
        """设置 Cookie"""
        self._cookies = cookies
        
    def get_cookies(self) -> Dict[str, str]:
        """获取 Cookie"""
        return self._cookies
    
    def get_cookie_string(self) -> str:
        """获取 Cookie 字符串"""
        return "; ".join([f"{k}={v}" for k, v in self._cookies.items()])
    
    def is_logged_in(self) -> bool:
        """检查是否已登录"""
        return self._cookies.get("LOGIN_STATUS") == "1"


class DouyinCrawler:
    """抖音爬虫"""
    
    def __init__(self, config: Optional[DouyinCrawlerConfig] = None, config_path: Optional[str] = None):
        self.config = config or DouyinCrawlerConfig()
        self.config_path = config_path
        self.cookie_manager = DouyinCookieManager()
        
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
        
        # 覆盖 MediaCrawler 的配置
        import config as mc_config
        mc_config.ENABLE_IP_PROXY = False
        
        from tools.cdp_browser import CDPBrowserManager
        
        self._playwright_cm = async_playwright()
        self._playwright = await self._playwright_cm.__aenter__()
        
        if self.config.enable_cdp_mode:
            self._cdp_manager = CDPBrowserManager()
            self._browser_context = await self._cdp_manager.launch_and_connect(
                playwright=self._playwright,
                playwright_proxy=None,
                user_agent=self.config.user_agent,
                headless=self.config.cdp_headless,
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
            # 添加反检测脚本 - 注意路径
            repo_root = Path(__file__).resolve().parents[1]
            stealth_path = repo_root / "MediaCrawler" / "libs" / "stealth.min.js"
            await self._browser_context.add_init_script(path=str(stealth_path))
            
        self._context_page = await self._browser_context.new_page()
        await self._context_page.goto("https://www.douyin.com")
        
        self._is_initialized = True
    
    async def _init_client(self) -> None:
        """初始化 API 客户端"""
        _ensure_mediacrawler_importable()
        
        import config as mc_config
        mc_config.ENABLE_IP_PROXY = False
        
        from media_platform.douyin.client import DouYinClient
        from tools import utils
        
        cookie_str, cookie_dict = utils.convert_cookies(
            await self._browser_context.cookies()
        )
        
        self._client = DouYinClient(
            proxy=None,
            headers={
                "User-Agent": await self._context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self._context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=None,
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
        """二维码登录 - 使用 MediaCrawler 的 DouYinLogin 类"""
        try:
            await self._init_browser()
            
            _ensure_mediacrawler_importable()
            from media_platform.douyin.login import DouYinLogin
            from tenacity import RetryError
            from tools import utils
            
            login_obj = DouYinLogin(
                login_type="qrcode",
                login_phone="",
                browser_context=self._browser_context,
                context_page=self._context_page,
                cookie_str="",
            )
            
            try:
                # begin() 方法会处理二维码显示、滑块验证和登录状态检查
                await login_obj.begin()
            except (RetryError, SystemExit):
                await self.close()
                return LoginResult(success=False, message="二维码登录失败或超时")
            
            await self._update_cookies_from_browser()
            await self._init_client()
            
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
            await self.close()
            return LoginResult(success=False, message=f"二维码登录失败: {str(e)}")

    async def login_by_account(self, account_name: Optional[str] = None) -> LoginResult:
        """使用指定账号的 Cookie 登录"""
        try:
            target_account = account_name or self.config.active_account
            cookie_string = self.config.cookies.get(target_account)
            
            if not cookie_string:
                return LoginResult(success=False, message=f"账号 {target_account} 的 Cookie 为空")
            
            # 使用 cookie 字符串登录
            result = await self.login_by_cookie_string(cookie_string, account_name=target_account, save_to_config=False)
            
            if result.success:
                self.config.active_account = target_account
                
            return result
            
        except Exception as e:
            await self.close()
            return LoginResult(success=False, message=f"账号登录失败: {str(e)}")

    async def login_by_cookie_string(self, cookie_string: str, account_name: Optional[str] = None, 
                                      save_to_config: bool = False) -> LoginResult:
        """
        Cookie 字符串登录 - 使用 MediaCrawler 的 DouYinLogin 类
        
        Args:
            cookie_string: Cookie 字符串，格式: key1=value1; key2=value2
            account_name: 保存到的账号名，默认使用 active_account
            save_to_config: 显式传入旧 config_path 时是否回写旧配置文件
            
        Returns:
            LoginResult: 登录结果
        """
        try:
            cookies = self.cookie_manager.load_from_string(cookie_string)
            if not cookies:
                return LoginResult(success=False, message="Cookie 字符串为空或格式错误")
            
            # 初始化浏览器（如果还没初始化）
            await self._init_browser()
            
            _ensure_mediacrawler_importable()
            from media_platform.douyin.login import DouYinLogin
            from tenacity import RetryError
            from tools import utils
            
            # 创建 DouYinLogin 实例
            login_obj = DouYinLogin(
                login_type="cookie",
                login_phone="",
                browser_context=self._browser_context,
                context_page=self._context_page,
                cookie_str=cookie_string,
            )
            
            # 直接调用 login_by_cookies 注入 Cookie（跳过 popup_login_dialog）
            await login_obj.login_by_cookies()
            
            # 刷新页面让 Cookie 生效
            await self._context_page.goto("https://www.douyin.com", timeout=60000)
            await asyncio.sleep(3)
            
            # 检查页面标题，如果是验证码页面则处理滑块
            current_page_title = await self._context_page.title()
            if "验证码中间页" in current_page_title:
                utils.logger.info("[DouyinCrawler] 检测到验证码页面，正在处理滑块...")
                try:
                    await login_obj.check_page_display_slider(move_step=3, slider_level="hard")
                except SystemExit:
                    await self.close()
                    return LoginResult(success=False, message="滑块验证失败，请稍后重试")
            
            # 检查是否有滑块验证（即使不在验证码中间页也可能出现）
            try:
                await login_obj.check_page_display_slider(move_step=3, slider_level="hard")
            except SystemExit:
                await self.close()
                return LoginResult(success=False, message="滑块验证失败，请稍后重试")
            
            # 验证登录状态
            try:
                await login_obj.check_login_state()
            except (RetryError, SystemExit):
                await self.close()
                return LoginResult(success=False, message="Cookie 已失效或登录验证失败，请重新获取 Cookie")
            
            # 更新 Cookie 并初始化客户端
            await self._update_cookies_from_browser()
            await self._init_client()
            
            if save_to_config:
                target_account = account_name or self.config.active_account
                self.config.set_cookie(target_account, cookie_string)
                if self.config_path:
                    self.config.save_to_config_file(self.config_path)
            
            return LoginResult(success=True, message="Cookie 字符串登录成功", cookies=cookies)
            
        except Exception as e:
            await self.close()
            return LoginResult(success=False, message=f"Cookie 字符串登录失败: {str(e)}")
    
    async def login(self, account_name: Optional[str] = None) -> LoginResult:
        """自动登录（优先尝试配置中的 Cookie，失败则二维码登录）"""
        target_account = account_name or self.config.active_account
        
        cookie_string = self.config.cookies.get(target_account)
        if cookie_string:
            result = await self.login_by_account(target_account)
            if result.success:
                return result
                
        return await self.login_by_qrcode(account_name=target_account)
    
    async def verify_cookies(self) -> bool:
        """验证 Cookie 有效性"""
        if not self._client:
            await self._init_client()
            
        try:
            return await self._client.pong(browser_context=self._browser_context)
        except Exception:
            return False

    async def search_videos(
        self,
        keyword: str,
        count: int = 10,
        publish_time: int = 0,
    ) -> SearchResult:
        """
        关键词搜索作品
        
        Args:
            keyword: 搜索关键词
            count: 获取数量
            publish_time: 发布时间筛选 (0=不限, 1=一天内, 7=一周内, 180=半年内)
                
        Returns:
            SearchResult: 搜索结果
        """
        if not self._client or not self._is_initialized:
            return SearchResult(success=False, message="请先登录")
            
        try:
            _ensure_mediacrawler_importable()
            from media_platform.douyin.field import PublishTimeType
            
            results = []
            offset = 0
            
            while len(results) < count:
                res = await self._client.search_info_by_keyword(
                    keyword=keyword,
                    offset=offset,
                    publish_time=PublishTimeType(publish_time),
                )
                
                data = res.get("data", [])
                if not data:
                    break
                    
                for item in data:
                    try:
                        aweme_info = item.get("aweme_info") or \
                                    item.get("aweme_mix_info", {}).get("mix_items", [{}])[0]
                    except (TypeError, IndexError):
                        continue
                        
                    if aweme_info:
                        results.append(self._parse_aweme(aweme_info, keyword))
                        
                    if len(results) >= count:
                        break
                        
                offset += 15
                await asyncio.sleep(1)
                
            return SearchResult(
                success=True,
                message="搜索成功",
                items=results[:count],
                has_more=len(results) >= count,
                total=len(results),
            )
            
        except Exception as e:
            return SearchResult(success=False, message=f"搜索失败: {str(e)}")

    async def get_video_detail(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """
        获取作品详情
        
        Args:
            aweme_id: 作品ID (支持完整URL、短链接、纯ID)
            
        Returns:
            作品详情字典
        """
        if not self._client:
            raise RuntimeError("请先登录")
        
        try:
            _ensure_mediacrawler_importable()
            from media_platform.douyin.help import parse_video_info_from_url
            
            # 解析 URL 获取 aweme_id
            video_info = parse_video_info_from_url(aweme_id)
            
            # 处理短链接
            if video_info.url_type == "short":
                resolved_url = await self._client.resolve_short_url(aweme_id)
                if resolved_url:
                    video_info = parse_video_info_from_url(resolved_url)
                else:
                    return None
            
            res = await self._client.get_video_by_id(video_info.aweme_id)
            if res:
                return self._parse_aweme(res)
            return None
            
        except Exception as e:
            return {"error": str(e)}

    async def get_user_info(self, sec_user_id: str) -> Optional[Dict[str, Any]]:
        """
        获取用户主页信息
        
        Args:
            sec_user_id: 用户 sec_uid (支持完整URL或纯ID)
            
        Returns:
            用户信息字典
        """
        if not self._client:
            raise RuntimeError("请先登录")
        
        try:
            _ensure_mediacrawler_importable()
            from media_platform.douyin.help import parse_creator_info_from_url
            
            creator_info = parse_creator_info_from_url(sec_user_id)
            res = await self._client.get_user_info(creator_info.sec_user_id)
            
            if res and res.get("user"):
                return self._parse_creator(res)
            return None
            
        except Exception as e:
            return {"error": str(e)}

    async def get_user_videos(self, sec_user_id: str, count: int = 20) -> List[Dict[str, Any]]:
        """
        获取用户发布的作品列表
        
        Args:
            sec_user_id: 用户 sec_uid
            count: 获取数量
            
        Returns:
            作品列表
        """
        if not self._client:
            raise RuntimeError("请先登录")
        
        try:
            _ensure_mediacrawler_importable()
            from media_platform.douyin.help import parse_creator_info_from_url
            
            creator_info = parse_creator_info_from_url(sec_user_id)
            results = []
            max_cursor = ""
            
            while len(results) < count:
                res = await self._client.get_user_aweme_posts(creator_info.sec_user_id, max_cursor)
                aweme_list = res.get("aweme_list", [])
                
                if not aweme_list:
                    break
                    
                for aweme in aweme_list:
                    results.append(self._parse_aweme(aweme))
                    if len(results) >= count:
                        break
                        
                if not res.get("has_more"):
                    break
                    
                max_cursor = str(res.get("max_cursor", ""))
                await asyncio.sleep(1)
                
            return results[:count]
            
        except Exception as e:
            return [{"error": str(e)}]

    def _parse_aweme(self, aweme: Dict, keyword: str = "") -> Dict:
        """解析作品数据为标准格式"""
        author = aweme.get("author", {})
        stats = aweme.get("statistics", {})
        video = aweme.get("video", {})
        
        # 获取封面
        cover_urls = (video.get("cover", {}) or video.get("origin_cover", {})).get("url_list", [])
        cover_url = cover_urls[0] if cover_urls else ""
        
        # 获取视频下载链接
        video_urls = video.get("play_addr", {}).get("url_list", [])
        video_url = video_urls[-1] if video_urls else ""
        
        # 获取图文图片
        images = aweme.get("images", []) or []
        note_urls = []
        for img in images:
            url_list = img.get("url_list", [])
            if url_list:
                note_urls.append(url_list[0])
        
        return {
            "aweme_id": aweme.get("aweme_id"),
            "aweme_type": str(aweme.get("aweme_type", "")),
            "title": aweme.get("desc", ""),
            "create_time": aweme.get("create_time"),
            "ip_location": aweme.get("ip_label", ""),
            "liked_count": str(stats.get("digg_count", 0)),
            "collected_count": str(stats.get("collect_count", 0)),
            "comment_count": str(stats.get("comment_count", 0)),
            "share_count": str(stats.get("share_count", 0)),
            "user_id": author.get("uid"),
            "sec_uid": author.get("sec_uid"),
            "nickname": author.get("nickname"),
            "avatar": author.get("avatar_thumb", {}).get("url_list", [""])[0],
            "cover_url": cover_url,
            "video_download_url": video_url,
            "note_download_url": ",".join(note_urls) if note_urls else "",
            "aweme_url": f"https://www.douyin.com/video/{aweme.get('aweme_id')}",
            "source_keyword": keyword,
        }
    
    def _parse_creator(self, data: Dict) -> Dict:
        """解析创作者数据为标准格式"""
        user = data.get("user", {})
        gender_map = {0: "Unknown", 1: "Male", 2: "Female"}
        avatar_urls = user.get("avatar_300x300", {}).get("url_list", [])
        
        return {
            "user_id": user.get("uid"),
            "sec_uid": user.get("sec_uid"),
            "nickname": user.get("nickname"),
            "avatar": avatar_urls[0] if avatar_urls else "",
            "desc": user.get("signature", ""),
            "gender": gender_map.get(user.get("gender"), "Unknown"),
            "ip_location": user.get("ip_location", ""),
            "follows": str(user.get("following_count", 0)),
            "fans": str(user.get("max_follower_count", 0)),
            "interaction": str(user.get("total_favorited", 0)),
            "videos_count": str(user.get("aweme_count", 0)),
        }

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
        
    async def __aenter__(self) -> "DouyinCrawler":
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# 便捷函数
async def create_douyin_crawler(config_path: Optional[str] = None) -> DouyinCrawler:
    """创建抖音爬虫实例"""
    if config_path is not None:
        _logger.warning("已忽略旧 config_path=%s；抖音爬虫配置从数据库读取", config_path)
    from api.services.runtime_config import get_runtime_config_section

    config = DouyinCrawlerConfig.from_dict(await get_runtime_config_section("douyin_crawler"))
    return DouyinCrawler(config, config_path=None)


async def quick_search(keyword: str, count: int = 10, config_path: Optional[str] = None) -> SearchResult:
    """快速搜索（自动登录 + 搜索 + 关闭）"""
    async with await create_douyin_crawler(config_path) as crawler:
        login_result = await crawler.login()
        if not login_result.success:
            return SearchResult(success=False, message=f"登录失败: {login_result.message}")
            
        return await crawler.search_videos(keyword=keyword, count=count)
