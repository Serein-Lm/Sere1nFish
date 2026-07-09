"""
抖音数据入库测试

测试将本地 JSON 文件入库到 MongoDB
- search_*.json → douyin_search_results
- tagged_*.json → douyin_tagged_results
- profile_urls_*.json → douyin_profiles
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# 确保项目根目录在 sys.path 中
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from motor.motor_asyncio import AsyncIOMotorClient

from Sere1nGraph.graph.config.loader import load_config
from api.dao import douyin as douyin_dao

# ==================== 配置 ====================

DATA_DIR = Path(__file__).parent / "douyin_data"

# 项目配置
TEST_PROJECT_ID = "6970c09e27b9715e54c7a83e"  # console-test-project


# ==================== 数据库连接 ====================

def get_db_client():
    """获取数据库客户端"""
    config = load_config()
    mongodb = config.mongodb
    
    if not mongodb or not mongodb.uri:
        raise ValueError("MongoDB 配置未找到")
    
    # 构建连接 URI
    uri = mongodb.uri
    if mongodb.username and mongodb.password:
        # 如果 URI 中没有认证信息，添加认证
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


# ==================== 工具函数 ====================

def load_json_file(filepath: Path) -> dict | None:
    """加载 JSON 文件"""
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def find_latest_file(pattern: str) -> Path | None:
    """查找最新的匹配文件"""
    files = list(DATA_DIR.glob(pattern))
    if not files:
        return None
    return max(files, key=lambda x: x.stat().st_mtime)


# ==================== 删除函数 ====================

async def delete_all_douyin_data(db, project_id: str):
    """
    删除指定项目的所有抖音数据
    """
    from api.db.collections import (
        DOUYIN_SEARCH_RESULTS_COLLECTION,
        DOUYIN_TAGGED_RESULTS_COLLECTION,
        DOUYIN_PROFILES_COLLECTION,
    )
    
    print("\n" + "=" * 50)
    print(f"删除项目 {project_id} 的抖音数据")
    print("=" * 50)
    
    # 删除搜索结果
    result1 = await db[DOUYIN_SEARCH_RESULTS_COLLECTION].delete_many({"project_id": project_id})
    print(f"✓ 删除搜索结果: {result1.deleted_count} 条")
    
    # 删除打标结果
    result2 = await db[DOUYIN_TAGGED_RESULTS_COLLECTION].delete_many({"project_id": project_id})
    print(f"✓ 删除打标结果: {result2.deleted_count} 条")
    
    # 删除用户画像
    result3 = await db[DOUYIN_PROFILES_COLLECTION].delete_many({"project_id": project_id})
    print(f"✓ 删除用户画像: {result3.deleted_count} 条")
    
    total = result1.deleted_count + result2.deleted_count + result3.deleted_count
    print(f"\n总计删除: {total} 条")
    
    return {
        "search_results": result1.deleted_count,
        "tagged_results": result2.deleted_count,
        "profiles": result3.deleted_count,
        "total": total,
    }


# ==================== 入库函数 ====================

async def import_search_results(
    db,
    project_id: str,
    json_file: Path | None = None,
) -> dict:
    """
    导入搜索结果到数据库
    
    Args:
        db: 数据库连接
        project_id: 项目 ID
        json_file: JSON 文件路径，不提供则使用最新的
    
    Returns:
        {"inserted": int, "updated": int, "total": int}
    """
    if json_file is None:
        json_file = find_latest_file("search_*.json")
    
    if not json_file:
        print("✗ 没有找到搜索结果文件")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    print(f"导入文件: {json_file.name}")
    
    data = load_json_file(json_file)
    if not data:
        print("✗ 文件为空或无法解析")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    keyword = data.get("keyword", "")
    items = data.get("items", [])
    
    if not items:
        print("✗ 没有数据")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    print(f"关键词: {keyword}, 数据量: {len(items)}")
    
    result = await douyin_dao.create_search_results_batch(db, project_id, keyword, items)
    
    print(f"✓ 入库完成: 新增 {result['inserted']}, 更新 {result['updated']}, 总计 {result['total']}")
    
    return result


async def import_tagged_results(
    db,
    project_id: str,
    json_file: Path | None = None,
) -> dict:
    """
    导入打标结果到数据库
    
    Args:
        db: 数据库连接
        project_id: 项目 ID
        json_file: JSON 文件路径，不提供则使用最新的
    
    Returns:
        {"inserted": int, "updated": int, "total": int}
    """
    if json_file is None:
        json_file = find_latest_file("tagged_*.json")
    
    if not json_file:
        print("✗ 没有找到打标结果文件")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    print(f"导入文件: {json_file.name}")
    
    data = load_json_file(json_file)
    if not data:
        print("✗ 文件为空或无法解析")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    items = data.get("items", [])
    
    if not items:
        print("✗ 没有数据")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    print(f"数据量: {len(items)}")
    print(f"潜在员工: {data.get('potential_employee', 0)}, 营销号: {data.get('marketing', 0)}, 不确定: {data.get('uncertain', 0)}")
    
    result = await douyin_dao.create_tagged_results_batch(db, project_id, items)
    
    print(f"✓ 入库完成: 新增 {result['inserted']}, 更新 {result['updated']}, 总计 {result['total']}")
    
    return result


async def import_profile_urls(
    db,
    project_id: str,
    json_file: Path | None = None,
) -> dict:
    """
    导入用户画像到数据库
    
    Args:
        db: 数据库连接
        project_id: 项目 ID
        json_file: JSON 文件路径，不提供则使用最新的
    
    Returns:
        {"inserted": int, "updated": int, "total": int}
    """
    if json_file is None:
        json_file = find_latest_file("profile_urls_*.json")
    
    if not json_file:
        print("✗ 没有找到用户画像文件")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    print(f"导入文件: {json_file.name}")
    
    data = load_json_file(json_file)
    if not data:
        print("✗ 文件为空或无法解析")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    users = data.get("users", [])
    
    if not users:
        print("✗ 没有数据")
        return {"inserted": 0, "updated": 0, "total": 0}
    
    print(f"用户数量: {len(users)}")
    
    result = await douyin_dao.create_profiles_batch(db, project_id, users)
    
    print(f"✓ 入库完成: 新增 {result['inserted']}, 更新 {result['updated']}, 总计 {result['total']}")
    
    return result


# ==================== 查询函数 ====================

async def query_search_results(db, project_id: str, limit: int = 10):
    """查询搜索结果"""
    print("\n" + "=" * 50)
    print("搜索结果查询")
    print("=" * 50)
    
    count = await douyin_dao.count_search_results(db, project_id)
    print(f"总数: {count}")
    
    results, _ = await douyin_dao.list_search_results(db, project_id, limit=limit)
    
    for i, item in enumerate(results, 1):
        print(f"\n[{i}] {item.get('title', '')[:50]}...")
        print(f"    作者: {item.get('nickname')}")
        print(f"    aweme_id: {item.get('aweme_id')}")
        print(f"    用户主页: {item.get('user_profile_url')}")


async def query_tagged_results(db, project_id: str, limit: int = 10):
    """查询打标结果"""
    print("\n" + "=" * 50)
    print("打标结果查询")
    print("=" * 50)
    
    stats = await douyin_dao.count_tagged_results(db, project_id)
    print(f"总数: {stats['total']}")
    print(f"潜在员工: {stats['potential_employee']}, 营销号: {stats['marketing']}, 不确定: {stats['uncertain']}")
    
    # 只查询潜在员工
    results, _ = await douyin_dao.list_tagged_results(db, project_id, tag="potential_employee", limit=limit)
    
    print(f"\n潜在员工列表 (前 {limit} 条):")
    for i, item in enumerate(results, 1):
        print(f"\n[{i}] {item.get('nickname')}")
        print(f"    标签: {item.get('tag')} | 置信度: {item.get('confidence')} | 优先级: {item.get('priority')}")
        print(f"    理由: {item.get('tag_reason', '')[:50]}...")
        print(f"    用户主页: {item.get('user_profile_url')}")


async def query_profiles(db, project_id: str, limit: int = 10):
    """查询用户画像"""
    print("\n" + "=" * 50)
    print("用户画像查询")
    print("=" * 50)
    
    count = await douyin_dao.count_profiles(db, project_id)
    print(f"总数: {count}")
    
    results, _ = await douyin_dao.list_profiles(db, project_id, limit=limit)
    
    for i, item in enumerate(results, 1):
        print(f"\n[{i}] {item.get('nickname')}")
        print(f"    sec_uid: {item.get('sec_uid')}")
        print(f"    置信度: {item.get('confidence')} | 优先级: {item.get('priority')}")
        print(f"    用户主页: {item.get('user_profile_url')}")
        if item.get("vision_analysis"):
            print(f"    视觉分析: 已完成")


# ==================== 完整入库流程 ====================

async def import_all(project_id: str = TEST_PROJECT_ID):
    """导入所有数据"""
    print("\n" + "=" * 60)
    print("抖音数据入库测试")
    print("=" * 60)
    print(f"项目 ID: {project_id}")
    print(f"数据目录: {DATA_DIR}")
    
    # 连接数据库
    client, db_name = get_db_client()
    db = client[db_name]
    
    print(f"数据库: {db_name}")
    
    try:
        # 1. 导入搜索结果
        print("\n" + "-" * 40)
        print("1. 导入搜索结果")
        print("-" * 40)
        await import_search_results(db, project_id)
        
        # 2. 导入打标结果
        print("\n" + "-" * 40)
        print("2. 导入打标结果")
        print("-" * 40)
        await import_tagged_results(db, project_id)
        
        # 3. 导入用户画像
        print("\n" + "-" * 40)
        print("3. 导入用户画像")
        print("-" * 40)
        await import_profile_urls(db, project_id)
        
        # 4. 查询验证
        print("\n" + "-" * 40)
        print("4. 查询验证")
        print("-" * 40)
        await query_search_results(db, project_id, limit=3)
        await query_tagged_results(db, project_id, limit=3)
        await query_profiles(db, project_id, limit=3)
        
        print("\n" + "=" * 60)
        print("入库完成")
        print("=" * 60)
        
    finally:
        client.close()


# ==================== 主入口 ====================

def print_menu():
    """打印菜单"""
    print("\n" + "=" * 50)
    print("抖音数据入库测试")
    print("=" * 50)
    print("1. 导入所有数据")
    print("2. 仅导入搜索结果")
    print("3. 仅导入打标结果")
    print("4. 仅导入用户画像")
    print("5. 查询数据库")
    print("6. 删除后重新导入（推荐）")
    print("7. 仅删除数据")
    print("0. 退出")
    print("=" * 50)


async def main():
    """主函数"""
    while True:
        print_menu()
        choice = input("请选择 [0-7]: ").strip()
        
        if choice == "0":
            print("\n再见!")
            break
        
        project_id = input(f"请输入项目 ID [默认: {TEST_PROJECT_ID}]: ").strip() or TEST_PROJECT_ID
        
        client, db_name = get_db_client()
        db = client[db_name]
        
        try:
            if choice == "1":
                await import_all(project_id)
            elif choice == "2":
                await import_search_results(db, project_id)
            elif choice == "3":
                await import_tagged_results(db, project_id)
            elif choice == "4":
                await import_profile_urls(db, project_id)
            elif choice == "5":
                await query_search_results(db, project_id)
                await query_tagged_results(db, project_id)
                await query_profiles(db, project_id)
            elif choice == "6":
                # 删除后重新导入
                confirm = input("确认删除并重新导入？(y/n): ").strip().lower()
                if confirm == "y":
                    await delete_all_douyin_data(db, project_id)
                    await import_all(project_id)
                else:
                    print("已取消")
            elif choice == "7":
                # 仅删除
                confirm = input("确认删除所有抖音数据？(y/n): ").strip().lower()
                if confirm == "y":
                    await delete_all_douyin_data(db, project_id)
                else:
                    print("已取消")
            else:
                print("无效选择")
        finally:
            client.close()
        
        input("\n按回车继续...")


if __name__ == "__main__":
    asyncio.run(main())
