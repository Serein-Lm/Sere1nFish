"""
截图工具测试

测试通用截图工具对抖音用户主页的截图功能
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中（必须在其他 import 之前）
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# 先导入 api.dao（在 crawler_tools 之前，因为 crawler_tools 会改变工作目录）
from api.dao import douyin as douyin_dao  # noqa: E402

# 以下 import 依赖 sys.path 设置
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from Sere1nGraph.graph.config.loader import load_config  # noqa: E402
from crawler_tools.screenshot_tool import (  # noqa: E402
    screenshot_douyin_profile,
    screenshot_douyin_profile_stream,
    save_screenshots,
    ScreenshotConfig,
)

# ==================== 配置 ====================

COOKIE_FILE = Path(__file__).parent / "douyin_cookie.txt"
SCREENSHOT_DIR = Path(__file__).parent / "douyin_data" / "screenshots"


def get_db_client():
    """获取数据库客户端"""
    config = load_config()
    mongodb = config.mongodb
    
    if not mongodb or not mongodb.uri:
        raise ValueError("MongoDB 配置未找到")
    
    uri = mongodb.uri
    if mongodb.username and mongodb.password:
        if "@" not in uri:
            protocol = uri.split("://")[0]
            host = uri.split("://")[1]
            uri = f"{protocol}://{mongodb.username}:{mongodb.password}@{host}"
    
    client = AsyncIOMotorClient(
        uri,
        authSource=mongodb.auth_source or "admin",
        directConnection=mongodb.direct or False,
    )
    
    return client, mongodb.database_name or "Sere1nG0Fish"

# 测试用抖音用户主页
TEST_DOUYIN_URLS = [
    "https://www.douyin.com/user/MS4wLjABAAAAUuABaN5sU39Kpg-dwfTRoaTM7ZLdK7C-MKuBC1RZyX0NOtBAQAUwGwjtzBW8qoo6",
    "https://www.douyin.com/user/MS4wLjABAAAAb0MmlIpWXwHdECPdB8CXeG3qhk1miDDsyDGrj7YCYcqFZYij8cQcpyR7AKkU1Hnh",
]


def load_cookie() -> str:
    """加载 Cookie"""
    if not COOKIE_FILE.exists():
        return ""
    
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    cookie_lines = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            cookie_lines.append(line)
    
    return "".join(cookie_lines)


async def test_screenshot_basic():
    """基础截图测试"""
    print("\n" + "=" * 60)
    print("测试: 基础截图功能")
    print("=" * 60)
    
    cookie_str = load_cookie()
    if not cookie_str:
        print("✗ Cookie 文件为空")
        return
    
    user_url = TEST_DOUYIN_URLS[0]
    print(f"测试 URL: {user_url}")
    
    print("\n开始截图...")
    result = await screenshot_douyin_profile(
        user_url=user_url,
        cookie_str=cookie_str,
        max_screenshots=3,
        page_load_wait=8.0,
        verbose=True,
    )
    
    if result.get("error"):
        print(f"\n✗ 截图失败: {result['error']}")
        return
    
    screenshots = result.get("screenshots", [])
    if not screenshots:
        print("\n✗ 未获取到截图")
        return
    
    print(f"\n✓ 获取到 {len(screenshots)} 张截图")
    
    # 保存截图
    user_id = user_url.split("/user/")[-1][:20]
    saved_paths = save_screenshots(screenshots, user_id, SCREENSHOT_DIR)
    
    print(f"\n✓ 截图已保存:")
    for path in saved_paths:
        print(f"  - {path}")


async def test_screenshot_stream():
    """流式截图测试"""
    print("\n" + "=" * 60)
    print("测试: 流式截图功能")
    print("=" * 60)
    
    cookie_str = load_cookie()
    if not cookie_str:
        print("✗ Cookie 文件为空")
        return
    
    user_url = TEST_DOUYIN_URLS[0]
    print(f"测试 URL: {user_url}")
    
    print("\n开始流式截图...")
    screenshots = []
    
    async for item in screenshot_douyin_profile_stream(
        user_url=user_url,
        cookie_str=cookie_str,
        max_screenshots=3,
        page_load_wait=8.0,
    ):
        if item.get("type") == "progress":
            print(f"  [进度] {item.get('message')}")
        elif item.get("type") == "result":
            data = item.get("data", {})
            if data.get("error"):
                print(f"\n✗ 截图失败: {data['error']}")
                return
            screenshots = data.get("screenshots", [])
    
    if not screenshots:
        print("\n✗ 未获取到截图")
        return
    
    print(f"\n✓ 获取到 {len(screenshots)} 张截图")
    
    # 保存截图
    user_id = user_url.split("/user/")[-1][:20]
    saved_paths = save_screenshots(screenshots, f"{user_id}_stream", SCREENSHOT_DIR)
    
    print(f"\n✓ 截图已保存:")
    for path in saved_paths:
        print(f"  - {path}")


async def test_screenshot_custom_config():
    """自定义配置测试"""
    print("\n" + "=" * 60)
    print("测试: 自定义配置截图")
    print("=" * 60)
    
    cookie_str = load_cookie()
    if not cookie_str:
        print("✗ Cookie 文件为空")
        return
    
    user_url = TEST_DOUYIN_URLS[1]
    print(f"测试 URL: {user_url}")
    
    # 自定义配置：更长的等待时间
    print("\n使用自定义配置:")
    print("  - 页面加载等待: 10秒")
    print("  - 滚动后等待: 3-4秒")
    print("  - 最大截图: 2张")
    
    result = await screenshot_douyin_profile(
        user_url=user_url,
        cookie_str=cookie_str,
        max_screenshots=2,
        page_load_wait=10.0,
        verbose=True,
    )
    
    if result.get("error"):
        print(f"\n✗ 截图失败: {result['error']}")
        return
    
    screenshots = result.get("screenshots", [])
    print(f"\n✓ 获取到 {len(screenshots)} 张截图")
    
    # 保存截图
    user_id = user_url.split("/user/")[-1][:20]
    saved_paths = save_screenshots(screenshots, f"{user_id}_custom", SCREENSHOT_DIR)
    
    print(f"\n✓ 截图已保存:")
    for path in saved_paths:
        print(f"  - {path}")


async def test_screenshot_with_input():
    """交互式截图测试"""
    print("\n" + "=" * 60)
    print("测试: 交互式截图")
    print("=" * 60)
    
    cookie_str = load_cookie()
    if not cookie_str:
        print("✗ Cookie 文件为空")
        return
    
    user_url = input("请输入抖音用户主页 URL: ").strip()
    if not user_url:
        print("URL 不能为空")
        return
    
    max_screenshots = input("最大截图数量 [默认: 3]: ").strip()
    max_screenshots = int(max_screenshots) if max_screenshots.isdigit() else 3
    
    page_load_wait = input("页面加载等待时间(秒) [默认: 8]: ").strip()
    page_load_wait = float(page_load_wait) if page_load_wait else 8.0
    
    print(f"\n配置:")
    print(f"  - URL: {user_url}")
    print(f"  - 最大截图: {max_screenshots}")
    print(f"  - 等待时间: {page_load_wait}秒")
    
    print("\n开始截图...")
    result = await screenshot_douyin_profile(
        user_url=user_url,
        cookie_str=cookie_str,
        max_screenshots=max_screenshots,
        page_load_wait=page_load_wait,
        verbose=True,
    )
    
    if result.get("error"):
        print(f"\n✗ 截图失败: {result['error']}")
        return
    
    screenshots = result.get("screenshots", [])
    if not screenshots:
        print("\n✗ 未获取到截图")
        return
    
    print(f"\n✓ 获取到 {len(screenshots)} 张截图")
    
    # 保存截图
    user_id = user_url.split("/user/")[-1][:20] if "/user/" in user_url else "unknown"
    saved_paths = save_screenshots(screenshots, user_id, SCREENSHOT_DIR)
    
    print(f"\n✓ 截图已保存:")
    for path in saved_paths:
        print(f"  - {path}")


async def test_screenshot_from_database():
    """从数据库获取 Cookie 进行截图测试"""
    print("\n" + "=" * 60)
    print("测试: 从数据库获取 Cookie 截图")
    print("=" * 60)
    
    # 连接数据库
    client, db_name = get_db_client()
    db = client[db_name]
    
    try:
        # 获取激活的 Cookie
        print("正在从数据库获取激活的 Cookie...")
        cookie_doc = await douyin_dao.get_active_cookie(db)
        
        if not cookie_doc:
            print("✗ 数据库中没有激活的 Cookie")
            print("\n提示: 请先通过以下方式添加并激活 Cookie:")
            print("  1. 运行 test_douyin_cookie.py 导入 Cookie")
            print("  2. 或调用 API: POST /api/v1/douyin/cookies")
            print("  3. 然后激活: POST /api/v1/douyin/cookies/{account_name}/activate")
            return
        
        account_name = cookie_doc.get("account_name", "unknown")
        cookie_str = cookie_doc.get("cookie_string", "")
        is_valid = cookie_doc.get("is_valid")
        
        print(f"✓ 获取到激活账号: {account_name}")
        print(f"  Cookie 长度: {len(cookie_str)} 字符")
        print(f"  有效性: {'有效' if is_valid else ('无效' if is_valid is False else '未验证')}")
        
        if not cookie_str:
            print("✗ Cookie 字符串为空")
            return
        
        # 使用数据库中的 Cookie 进行截图
        user_url = TEST_DOUYIN_URLS[0]
        print(f"\n测试 URL: {user_url}")
        
        print("\n开始截图（使用数据库 Cookie）...")
        result = await screenshot_douyin_profile(
            user_url=user_url,
            cookie_str=cookie_str,
            max_screenshots=3,
            page_load_wait=8.0,
            verbose=True,
        )
        
        if result.get("error"):
            print(f"\n✗ 截图失败: {result['error']}")
            # 更新 Cookie 有效性
            await douyin_dao.set_cookie_valid(db, account_name, False)
            print(f"  已将账号 {account_name} 标记为无效")
            return
        
        screenshots = result.get("screenshots", [])
        if not screenshots:
            print("\n✗ 未获取到截图")
            return
        
        print(f"\n✓ 获取到 {len(screenshots)} 张截图")
        
        # 更新 Cookie 有效性
        await douyin_dao.set_cookie_valid(db, account_name, True)
        print(f"✓ 已将账号 {account_name} 标记为有效")
        
        # 保存截图
        user_id = user_url.split("/user/")[-1][:20]
        saved_paths = save_screenshots(screenshots, f"{user_id}_db", SCREENSHOT_DIR)
        
        print(f"\n✓ 截图已保存:")
        for path in saved_paths:
            print(f"  - {path}")
    
    finally:
        client.close()


async def test_screenshot_from_database_interactive():
    """交互式从数据库获取 Cookie 截图"""
    print("\n" + "=" * 60)
    print("测试: 交互式数据库 Cookie 截图")
    print("=" * 60)
    
    # 连接数据库
    client, db_name = get_db_client()
    db = client[db_name]
    
    try:
        # 列出所有 Cookie
        print("数据库中的 Cookie 账号:")
        cookies, _ = await douyin_dao.list_cookies(db)
        
        if not cookies:
            print("✗ 数据库中没有 Cookie")
            print("\n提示: 请先添加 Cookie")
            return
        
        for i, doc in enumerate(cookies, 1):
            status = "✓ 激活" if doc.get("is_active") else "○ 未激活"
            valid = "有效" if doc.get("is_valid") else ("无效" if doc.get("is_valid") is False else "未验证")
            print(f"  [{i}] {status} {doc.get('account_name')} - {valid}")
        
        # 获取激活的 Cookie
        cookie_doc = await douyin_dao.get_active_cookie(db)
        
        if not cookie_doc:
            print("\n✗ 没有激活的账号，请先激活一个账号")
            return
        
        account_name = cookie_doc.get("account_name")
        cookie_str = cookie_doc.get("cookie_string", "")
        
        print(f"\n当前激活账号: {account_name}")
        
        # 输入 URL
        user_url = input("\n请输入抖音用户主页 URL: ").strip()
        if not user_url:
            print("URL 不能为空")
            return
        
        max_screenshots = input("最大截图数量 [默认: 3]: ").strip()
        max_screenshots = int(max_screenshots) if max_screenshots.isdigit() else 3
        
        print(f"\n配置:")
        print(f"  - URL: {user_url}")
        print(f"  - 账号: {account_name}")
        print(f"  - 最大截图: {max_screenshots}")
        
        print("\n开始截图...")
        result = await screenshot_douyin_profile(
            user_url=user_url,
            cookie_str=cookie_str,
            max_screenshots=max_screenshots,
            page_load_wait=30.0,
            verbose=True,
        )
        
        if result.get("error"):
            print(f"\n✗ 截图失败: {result['error']}")
            return
        
        screenshots = result.get("screenshots", [])
        if not screenshots:
            print("\n✗ 未获取到截图")
            return
        
        print(f"\n✓ 获取到 {len(screenshots)} 张截图")
        
        # 保存截图
        user_id = user_url.split("/user/")[-1][:20] if "/user/" in user_url else "unknown"
        saved_paths = save_screenshots(screenshots, f"{user_id}_db_interactive", SCREENSHOT_DIR)
        
        print(f"\n✓ 截图已保存:")
        for path in saved_paths:
            print(f"  - {path}")
    
    finally:
        client.close()


async def test_screenshot_horizontal():
    """布局修复截图测试（备选方案）"""
    print("\n" + "=" * 60)
    print("测试: 布局修复截图（先向右滚动 → 等待20秒 → 垂直截图）")
    print("=" * 60)
    
    # 连接数据库
    client, db_name = get_db_client()
    db = client[db_name]
    
    try:
        # 获取激活的 Cookie
        cookie_doc = await douyin_dao.get_active_cookie(db)
        
        if not cookie_doc:
            print("✗ 数据库中没有激活的 Cookie")
            return
        
        account_name = cookie_doc.get("account_name")
        cookie_str = cookie_doc.get("cookie_string", "")
        
        print(f"✓ 使用账号: {account_name}")
        
        # 输入 URL
        user_url = input("\n请输入抖音用户主页 URL: ").strip()
        # 清理多余的引号
        user_url = user_url.strip('"').strip("'")
        if not user_url:
            user_url = TEST_DOUYIN_URLS[0]
            print(f"使用默认 URL: {user_url}")
        
        max_screenshots = input("最大截图数量 [默认: 3]: ").strip()
        max_screenshots = int(max_screenshots) if max_screenshots.isdigit() else 3
        
        print(f"\n配置:")
        print(f"  - URL: {user_url}")
        print(f"  - 布局修复: 向右滚动 → 等待20秒恢复 → 垂直截图")
        print(f"  - 最大截图: {max_screenshots}")
        
        print("\n开始布局修复截图...")
        result = await screenshot_douyin_profile(
            user_url=user_url,
            cookie_str=cookie_str,
            max_screenshots=max_screenshots,
            page_load_wait=30.0,
            fix_layout=True,  # 启用布局修复
            verbose=True,
        )
        
        if result.get("error"):
            print(f"\n✗ 截图失败: {result['error']}")
            return
        
        screenshots = result.get("screenshots", [])
        if not screenshots:
            print("\n✗ 未获取到截图")
            return
        
        print(f"\n✓ 获取到 {len(screenshots)} 张截图")
        
        # 保存截图
        user_id = user_url.split("/user/")[-1][:20] if "/user/" in user_url else "unknown"
        saved_paths = save_screenshots(screenshots, f"{user_id}_fixlayout", SCREENSHOT_DIR)
        
        print(f"\n✓ 截图已保存:")
        for path in saved_paths:
            print(f"  - {path}")
    
    finally:
        client.close()


# ==================== 主入口 ====================

def print_menu():
    print("\n" + "=" * 50)
    print("截图工具测试")
    print("=" * 50)
    print("1. 基础截图测试（文件 Cookie）")
    print("2. 流式截图测试（文件 Cookie）")
    print("3. 自定义配置测试（文件 Cookie）")
    print("4. 交互式截图（文件 Cookie）")
    print("5. 数据库 Cookie 截图测试 ⭐")
    print("6. 交互式数据库 Cookie 截图 ⭐")
    print("7. 布局修复截图测试 ⭐（向右滚动→等待→垂直截图）")
    print("0. 退出")
    print("=" * 50)


async def main():
    while True:
        print_menu()
        choice = input("请选择 [0-7]: ").strip()
        
        if choice == "0":
            print("\n再见!")
            break
        elif choice == "1":
            await test_screenshot_basic()
        elif choice == "2":
            await test_screenshot_stream()
        elif choice == "3":
            await test_screenshot_custom_config()
        elif choice == "4":
            await test_screenshot_with_input()
        elif choice == "5":
            await test_screenshot_from_database()
        elif choice == "6":
            await test_screenshot_from_database_interactive()
        elif choice == "7":
            await test_screenshot_horizontal()
        else:
            print("无效选择")
        
        input("\n按回车继续...")


if __name__ == "__main__":
    asyncio.run(main())
