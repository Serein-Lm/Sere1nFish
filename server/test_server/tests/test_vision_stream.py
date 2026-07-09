"""
测试视觉分析流式输出
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def test_vision_stream():
    """测试视觉分析流式输出"""
    from api.db.mongodb import init_mongo, get_db
    from api.services.xhs_vision_tools import (
        screenshot_user_profile,
        analyze_screenshots_with_vision_stream,
    )
    from Sere1nGraph.graph.config.loader import load_config
    
    # 初始化
    config_path = PROJECT_ROOT / "config.json"
    app_config = load_config(str(config_path) if config_path.exists() else None)
    init_mongo(app_config)
    db = get_db()
    
    # 输入 URL
    test_url = input("请输入小红书用户主页 URL: ").strip()
    if not test_url:
        print("URL 不能为空")
        return
    
    print(f"\n目标: {test_url}")
    print("=" * 60)
    
    # 1. 截屏
    print("\n[1/2] 截屏中...")
    result = await screenshot_user_profile(test_url, db)
    
    if result.get("error"):
        print(f"❌ 截屏失败: {result['error']}")
        return
    
    screenshots = result.get("screenshots", [])
    avatar_url = result.get("avatar_url")
    
    print(f"✅ 截屏成功: {len(screenshots)} 张")
    if avatar_url:
        print(f"✅ 头像: {avatar_url}")
    
    # 2. 流式视觉分析
    print("\n[2/2] 视觉分析（流式输出）...")
    print("-" * 60)
    
    async for chunk in analyze_screenshots_with_vision_stream(screenshots):
        print(chunk, end="", flush=True)
    
    print("\n" + "-" * 60)
    print("✅ 完成")


if __name__ == "__main__":
    asyncio.run(test_vision_stream())
