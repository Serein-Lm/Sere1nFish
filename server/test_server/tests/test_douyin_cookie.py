"""
抖音 Cookie 管理测试

测试 Cookie 的增删改查、激活、验证功能
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from motor.motor_asyncio import AsyncIOMotorClient

from Sere1nGraph.graph.config.loader import load_config
from api.dao import douyin as douyin_dao


# ==================== 配置 ====================

COOKIE_FILE = Path(__file__).parent / "douyin_cookie.txt"


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


def load_cookie_from_file() -> str:
    """从文件加载 Cookie"""
    if COOKIE_FILE.exists():
        return COOKIE_FILE.read_text(encoding="utf-8").strip()
    return ""


# ==================== 测试函数 ====================

async def test_create_cookie(db, account_name: str, cookie_string: str):
    """测试创建 Cookie"""
    print("\n" + "=" * 50)
    print("测试: 创建 Cookie")
    print("=" * 50)
    
    doc = await douyin_dao.create_cookie(db, account_name, cookie_string)
    
    print(f"✓ 创建成功")
    print(f"  账号名: {doc.get('account_name')}")
    print(f"  ID: {doc.get('_id')}")
    print(f"  激活状态: {doc.get('is_active')}")
    print(f"  有效性: {doc.get('is_valid')}")
    
    return doc


async def test_list_cookies(db):
    """测试列出所有 Cookie"""
    print("\n" + "=" * 50)
    print("测试: 列出所有 Cookie")
    print("=" * 50)
    
    docs, _ = await douyin_dao.list_cookies(db)
    
    print(f"✓ 共 {len(docs)} 个账号")
    for doc in docs:
        status = "✓ 激活" if doc.get("is_active") else "○ 未激活"
        valid = "有效" if doc.get("is_valid") else ("无效" if doc.get("is_valid") is False else "未验证")
        print(f"  [{status}] {doc.get('account_name')} - {valid}")
    
    return docs


async def test_activate_cookie(db, account_name: str):
    """测试激活 Cookie"""
    print("\n" + "=" * 50)
    print(f"测试: 激活 Cookie - {account_name}")
    print("=" * 50)
    
    doc = await douyin_dao.activate_cookie(db, account_name)
    
    if doc:
        print(f"✓ 激活成功")
        print(f"  账号名: {doc.get('account_name')}")
        print(f"  激活状态: {doc.get('is_active')}")
    else:
        print(f"✗ 激活失败: 账号不存在")
    
    return doc


async def test_get_active_cookie(db):
    """测试获取激活的 Cookie"""
    print("\n" + "=" * 50)
    print("测试: 获取激活的 Cookie")
    print("=" * 50)
    
    doc = await douyin_dao.get_active_cookie(db)
    
    if doc:
        print(f"✓ 当前激活账号: {doc.get('account_name')}")
        cookie_str = doc.get("cookie_string", "")
        print(f"  Cookie 长度: {len(cookie_str)} 字符")
    else:
        print(f"✗ 没有激活的账号")
    
    return doc


async def test_verify_cookie(db, account_name: str):
    """测试验证 Cookie（通过 DouyinCrawler）"""
    print("\n" + "=" * 50)
    print(f"测试: 验证 Cookie - {account_name}")
    print("=" * 50)
    
    doc = await douyin_dao.get_cookie_by_name(db, account_name)
    if not doc:
        print(f"✗ 账号不存在")
        return None
    
    cookie_string = doc.get("cookie_string", "")
    if not cookie_string:
        print(f"✗ Cookie 为空")
        return None
    
    print("正在验证 Cookie（通过 DouyinCrawler 登录验证）...")
    
    try:
        from crawler_tools.douyin_crawler import DouyinCrawler, DouyinCrawlerConfig
        
        config = DouyinCrawlerConfig()
        config.set_cookie(account_name, cookie_string)
        config.active_account = account_name
        config.cdp_headless = True
        
        crawler = DouyinCrawler(config)
        login_result = await crawler.login_by_cookie_string(cookie_string)
        is_valid = login_result.success
        await crawler.close()
        
        # 更新数据库
        doc = await douyin_dao.set_cookie_valid(db, account_name, is_valid)
        
        if is_valid:
            print(f"✓ Cookie 有效")
            print(f"  消息: {login_result.message}")
        else:
            print(f"✗ Cookie 无效")
            print(f"  消息: {login_result.message}")
        
        return doc
        
    except Exception as e:
        print(f"✗ 验证失败: {e}")
        await douyin_dao.set_cookie_valid(db, account_name, False)
        return None


async def test_delete_cookie(db, account_name: str):
    """测试删除 Cookie"""
    print("\n" + "=" * 50)
    print(f"测试: 删除 Cookie - {account_name}")
    print("=" * 50)
    
    deleted = await douyin_dao.delete_cookie(db, account_name)
    
    if deleted:
        print(f"✓ 删除成功")
    else:
        print(f"✗ 删除失败: 账号不存在")
    
    return deleted


# ==================== 主入口 ====================

def print_menu():
    """打印菜单"""
    print("\n" + "=" * 50)
    print("抖音 Cookie 管理测试")
    print("=" * 50)
    print("1. 从文件导入 Cookie")
    print("2. 列出所有 Cookie")
    print("3. 激活 Cookie")
    print("4. 获取激活的 Cookie")
    print("5. 验证 Cookie（访问抖音）")
    print("6. 删除 Cookie")
    print("0. 退出")
    print("=" * 50)


async def main():
    """主函数"""
    client, db_name = get_db_client()
    db = client[db_name]
    
    print(f"数据库: {db_name}")
    
    try:
        while True:
            print_menu()
            choice = input("请选择 [0-6]: ").strip()
            
            if choice == "0":
                print("\n再见!")
                break
            
            if choice == "1":
                # 从文件导入
                cookie_str = load_cookie_from_file()
                if not cookie_str:
                    print(f"✗ Cookie 文件不存在或为空: {COOKIE_FILE}")
                    continue
                
                account_name = input("请输入账号名 [默认: default]: ").strip() or "default"
                await test_create_cookie(db, account_name, cookie_str)
                
            elif choice == "2":
                await test_list_cookies(db)
                
            elif choice == "3":
                account_name = input("请输入要激活的账号名: ").strip()
                if account_name:
                    await test_activate_cookie(db, account_name)
                
            elif choice == "4":
                await test_get_active_cookie(db)
                
            elif choice == "5":
                account_name = input("请输入要验证的账号名: ").strip()
                if account_name:
                    await test_verify_cookie(db, account_name)
                
            elif choice == "6":
                account_name = input("请输入要删除的账号名: ").strip()
                if account_name:
                    confirm = input(f"确认删除 {account_name}？(y/n): ").strip().lower()
                    if confirm == "y":
                        await test_delete_cookie(db, account_name)
            
            input("\n按回车继续...")
    
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
