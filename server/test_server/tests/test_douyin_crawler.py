"""
抖音爬虫交互式测试

支持交互式菜单选择测试项目
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from crawler_tools.douyin_crawler import (
    DouyinCrawler,
    create_douyin_crawler,
)


# Cookie 文件路径
COOKIE_FILE = Path(__file__).parent / "douyin_cookie.txt"


def load_cookie_from_file() -> str:
    """从文件加载 Cookie"""
    if not COOKIE_FILE.exists():
        return ""
    
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # 过滤注释行和空行
    cookie_lines = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            cookie_lines.append(line)
    
    return "".join(cookie_lines)


class DouyinCrawlerTester:
    """抖音爬虫交互式测试器"""
    
    def __init__(self):
        self.crawler: DouyinCrawler = None
    
    async def _login_with_cookie_file(self) -> bool:
        """使用 cookie.txt 登录，返回是否成功"""
        cookie_str = load_cookie_from_file()
        if not cookie_str:
            print(f"✗ Cookie 文件为空或不存在: {COOKIE_FILE}")
            return False
        
        # 每次都创建新的爬虫实例
        self.crawler = await create_douyin_crawler()
        result = await self.crawler.login_by_cookie_string(cookie_str)
        
        if result.success:
            print(f"✓ {result.message}")
            return True
        else:
            print(f"✗ {result.message}")
            await self.crawler.close()
            self.crawler = None
            return False
    
    async def _ensure_crawler_ready(self) -> bool:
        """确保爬虫已准备好（登录状态）"""
        # 关闭旧的实例
        if self.crawler:
            await self.crawler.close()
            self.crawler = None
        
        print("\n使用 Cookie 文件登录...")
        return await self._login_with_cookie_file()
    
    async def test_cookie_file_login(self):
        """测试 Cookie 文件登录（仅验证，验证后关闭）"""
        print("\n" + "=" * 50)
        print("测试 Cookie 文件登录")
        print("=" * 50)
        
        cookie_str = load_cookie_from_file()
        
        if not cookie_str:
            print(f"✗ Cookie 文件为空或不存在: {COOKIE_FILE}")
            print("请将 Cookie 字符串粘贴到 douyin_cookie.txt 文件中")
            return
        
        print(f"读取到 Cookie 长度: {len(cookie_str)} 字符")
        
        crawler = await create_douyin_crawler()
        try:
            result = await crawler.login_by_cookie_string(cookie_str)
            
            if result.success:
                print(f"✓ {result.message}")
                print("✓ Cookie 有效，可以正常使用")
            else:
                print(f"✗ {result.message}")
        finally:
            # 测试完成后关闭
            await crawler.close()
            print("✓ 测试完成，浏览器已关闭")
    
    async def test_cookie_string_login(self):
        """测试手动输入 Cookie 字符串登录"""
        print("\n" + "=" * 50)
        print("测试 Cookie 字符串登录")
        print("=" * 50)
        
        print("请粘贴 Cookie 字符串 (从浏览器开发者工具复制):")
        cookie_str = input().strip()
        
        if not cookie_str:
            print("✗ Cookie 字符串为空")
            return
        
        crawler = await create_douyin_crawler()
        try:
            result = await crawler.login_by_cookie_string(cookie_str)
            
            if result.success:
                print(f"✓ {result.message}")
                save = input("是否保存到 douyin_cookie.txt? [y/N]: ").strip().lower() == "y"
                if save:
                    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                        f.write(cookie_str)
                    print(f"✓ Cookie 已保存到 {COOKIE_FILE}")
            else:
                print(f"✗ {result.message}")
        finally:
            await crawler.close()
            print("✓ 测试完成，浏览器已关闭")
    
    async def test_search(self):
        """关键词搜索"""
        print("\n" + "=" * 50)
        print("关键词搜索")
        print("=" * 50)
        
        # 每次操作前重新登录
        if not await self._ensure_crawler_ready():
            return
        
        keyword = input("请输入搜索关键词 [默认: Python编程]: ").strip() or "Python编程"
        count = input("请输入获取数量 [默认: 5]: ").strip()
        count = int(count) if count.isdigit() else 5
        
        print(f"\n搜索: {keyword}, 数量: {count}")
        
        try:
            result = await self.crawler.search_videos(keyword=keyword, count=count)
            
            if not result.success:
                print(f"✗ {result.message}")
                return
            
            print(f"✓ 获取到 {result.total} 条结果\n")
            
            for i, item in enumerate(result.items, 1):
                print(f"[{i}] {item.get('title', '')[:40]}")
                print(f"    作者: {item.get('nickname')} | 点赞: {item.get('liked_count')} | 评论: {item.get('comment_count')}")
                print(f"    链接: {item.get('aweme_url')}")
                print()
        finally:
            # 操作完成后关闭
            await self.crawler.close()
            self.crawler = None
            print("✓ 操作完成，浏览器已关闭")

    async def test_video_detail(self):
        """获取作品详情"""
        print("\n" + "=" * 50)
        print("获取作品详情")
        print("=" * 50)
        
        if not await self._ensure_crawler_ready():
            return
        
        url = input("请输入作品链接或ID [默认: 7525538910311632128]: ").strip()
        url = url or "7525538910311632128"
        
        print(f"\n获取作品: {url[:50]}...")
        
        try:
            detail = await self.crawler.get_video_detail(url)
            
            if not detail or "error" in detail:
                print(f"✗ 获取失败: {detail}")
                return
            
            print(f"\n✓ 作品详情:")
            print(f"   标题: {detail.get('title', '')[:50]}")
            print(f"   作者: {detail.get('nickname')}")
            print(f"   点赞: {detail.get('liked_count')}")
            print(f"   收藏: {detail.get('collected_count')}")
            print(f"   评论: {detail.get('comment_count')}")
            print(f"   分享: {detail.get('share_count')}")
            print(f"   链接: {detail.get('aweme_url')}")
        finally:
            await self.crawler.close()
            self.crawler = None
            print("✓ 操作完成，浏览器已关闭")
    
    async def test_user_info(self):
        """获取用户信息"""
        print("\n" + "=" * 50)
        print("获取用户信息")
        print("=" * 50)
        
        if not await self._ensure_crawler_ready():
            return
        
        default_user = "MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE"
        url = input(f"请输入用户主页链接或sec_uid [默认: {default_user[:30]}...]: ").strip()
        url = url or default_user
        
        print(f"\n获取用户: {url[:40]}...")
        
        try:
            user_info = await self.crawler.get_user_info(url)
            
            if not user_info or "error" in user_info:
                print(f"✗ 获取失败: {user_info}")
                return
            
            print(f"\n✓ 用户信息:")
            print(f"   昵称: {user_info.get('nickname')}")
            print(f"   简介: {user_info.get('desc', '')[:40]}")
            print(f"   粉丝: {user_info.get('fans')}")
            print(f"   获赞: {user_info.get('interaction')}")
            print(f"   作品数: {user_info.get('videos_count')}")
            print(f"   IP属地: {user_info.get('ip_location')}")
        finally:
            await self.crawler.close()
            self.crawler = None
            print("✓ 操作完成，浏览器已关闭")

    async def test_user_videos(self):
        """获取用户作品列表"""
        print("\n" + "=" * 50)
        print("获取用户作品列表")
        print("=" * 50)
        
        if not await self._ensure_crawler_ready():
            return
        
        default_user = "MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE"
        url = input(f"请输入用户sec_uid [默认: {default_user[:30]}...]: ").strip()
        url = url or default_user
        
        count = input("请输入获取数量 [默认: 5]: ").strip()
        count = int(count) if count.isdigit() else 5
        
        print(f"\n获取用户作品: {url[:30]}..., 数量: {count}")
        
        try:
            videos = await self.crawler.get_user_videos(url, count=count)
            
            if not videos or (len(videos) == 1 and "error" in videos[0]):
                print(f"✗ 获取失败: {videos}")
                return
            
            print(f"\n✓ 获取到 {len(videos)} 条作品\n")
            
            for i, video in enumerate(videos, 1):
                print(f"[{i}] {video.get('title', '')[:40]}")
                print(f"    点赞: {video.get('liked_count')} | 评论: {video.get('comment_count')}")
                print(f"    链接: {video.get('aweme_url')}")
                print()
        finally:
            await self.crawler.close()
            self.crawler = None
            print("✓ 操作完成，浏览器已关闭")
    
    async def close(self):
        """关闭爬虫"""
        if self.crawler:
            await self.crawler.close()
            self.crawler = None
            print("✓ 爬虫已关闭")


def print_menu():
    """打印菜单"""
    print("\n" + "=" * 50)
    print("抖音爬虫测试菜单")
    print("=" * 50)
    print("1. Cookie 文件登录 (douyin_cookie.txt)")
    print("2. Cookie 字符串登录 (手动输入)")
    print("3. 关键词搜索")
    print("4. 获取作品详情")
    print("5. 获取用户信息")
    print("6. 获取用户作品列表")
    print("7. 运行所有测试")
    print("0. 退出")
    print("=" * 50)


async def interactive_mode():
    """交互式模式"""
    tester = DouyinCrawlerTester()
    
    try:
        while True:
            print_menu()
            choice = input("请选择 [0-7]: ").strip()
            
            if choice == "0":
                print("\n再见!")
                break
            elif choice == "1":
                await tester.test_cookie_file_login()
            elif choice == "2":
                await tester.test_cookie_string_login()
            elif choice == "3":
                await tester.test_search()
            elif choice == "4":
                await tester.test_video_detail()
            elif choice == "5":
                await tester.test_user_info()
            elif choice == "6":
                await tester.test_user_videos()
            elif choice == "7":
                await tester.test_search()
                await tester.test_video_detail()
                await tester.test_user_info()
                await tester.test_user_videos()
            else:
                print("无效选择，请重试")
            
            input("\n按回车继续...")
    finally:
        await tester.close()


async def quick_test(test_name: str):
    """快速测试指定项"""
    tester = DouyinCrawlerTester()
    
    try:
        test_map = {
            "cookie": tester.test_cookie_file_login,
            "cookie_str": tester.test_cookie_string_login,
            "search": tester.test_search,
            "detail": tester.test_video_detail,
            "user": tester.test_user_info,
            "videos": tester.test_user_videos,
        }
        
        if test_name in test_map:
            await test_map[test_name]()
        else:
            print(f"未知测试: {test_name}")
            print(f"可用测试: {', '.join(test_map.keys())}")
    finally:
        await tester.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="抖音爬虫交互式测试")
    parser.add_argument("--test", "-t", type=str, default=None,
                       help="直接运行指定测试: cookie, cookie_str, qrcode, search, detail, user, videos, comments")
    
    args = parser.parse_args()
    
    if args.test:
        asyncio.run(quick_test(args.test))
    else:
        asyncio.run(interactive_mode())
