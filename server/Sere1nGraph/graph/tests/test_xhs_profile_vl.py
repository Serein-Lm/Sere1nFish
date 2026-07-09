"""
XHS 用户主页视觉分析 Agent 测试

功能：
1. 从控制台输入用户主页 URL
2. 使用 Agent 访问主页并滚动截屏
3. 调用视觉模型分析截图
4. 生成结构化人物画像

用法：
    python test_xhs_profile_vl.py
    然后输入小红书用户主页 URL
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# ============ 日志配置 ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("xhs_profile_vl")


# ============ 配置 ============

# 结果保存目录
RESULT_DIR = Path(__file__).parent / "results"


# ============ Agent 测试 ============

async def test_xhs_profile_vl_agent(user_url: str) -> dict:
    """
    测试 XHS 用户主页视觉分析 Agent
    
    Args:
        user_url: 小红书用户主页 URL
        
    Returns:
        dict: Agent 返回的结构化人物画像
    """
    from api.db.mongodb import init_mongo
    from api.services.runtime_config import get_runtime_app_config
    from agents.factory import create_xhs_profile_vl_agent
    from langchain_core.messages import HumanMessage
    
    logger.info("="*60)
    logger.info("XHS 用户主页视觉分析 Agent 测试")
    logger.info("="*60)
    logger.info(f"目标 URL: {user_url}")
    
    # 1. 加载配置
    logger.info("[1/4] 加载配置...")
    init_mongo()
    app_config = await get_runtime_app_config()
    logger.info(f"  - 主模型: {app_config.runtime.models.default}")
    logger.info(f"  - 视觉模型: {app_config.runtime.models.vision}")
    
    # 2. 创建 Agent
    logger.info("[2/4] 创建 Agent...")
    agent = await create_xhs_profile_vl_agent(
        app_config=app_config,
        server_name="playwright",
        output_mode="streaming",  # 使用 streaming 模式看到实时输出
    )
    logger.info("  - Agent 创建成功")
    
    # 3. 运行 Agent
    logger.info("[3/4] 运行 Agent...")
    logger.info(f"  - 发送任务: 分析用户主页 {user_url}")
    
    input_message = HumanMessage(content=f"请分析这个小红书用户主页: {user_url}")
    
    result = None
    start_time = datetime.now()
    
    try:
        # 调用 Agent
        response = await agent({"messages": [input_message]})
        
        # 提取最终结果
        messages = response.get("messages", [])
        if messages:
            last_message = messages[-1]
            result = getattr(last_message, "content", str(last_message))
            
    except Exception as e:
        logger.error(f"Agent 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
    
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"  - Agent 执行完成，耗时: {elapsed:.2f}s")
    
    # 4. 处理结果
    logger.info("[4/4] 处理结果...")
    
    # 尝试解析 JSON
    parsed_result = None
    if result:
        try:
            # 提取 JSON 部分
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0].strip()
            else:
                json_str = result.strip()
            
            parsed_result = json.loads(json_str)
            logger.info("  - JSON 解析成功")
        except json.JSONDecodeError:
            logger.warning("  - JSON 解析失败，返回原始文本")
            parsed_result = {"raw_output": result}
    
    # 保存结果
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = RESULT_DIR / f"profile_{timestamp}.json"
    
    final_result = {
        "user_url": user_url,
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": elapsed,
        "result": parsed_result,
    }
    
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)
    
    logger.info(f"  - 结果已保存: {result_path}")
    
    return final_result


# ============ 简化测试（不使用 Agent，直接调用工具） ============

async def test_vision_analysis_direct(user_url: str) -> dict:
    """
    直接测试视觉分析（不使用 Agent，便于调试）
    
    流程：
    1. Playwright 截屏
    2. 调用 VL 模型分析
    """
    import base64
    from playwright.async_api import async_playwright
    from tools.builtin import analyze_multiple_images_with_vision_model
    from prompts.loader import load_prompt
    
    logger.info("=" * 60)
    logger.info("视觉分析直接测试（不使用 Agent）")
    logger.info("=" * 60)
    logger.info(f"目标 URL: {user_url}")
    
    screenshots = []
    
    # 1. 截屏
    logger.info("[1/3] 启动浏览器并截屏...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        
        logger.info(f"  - 访问: {user_url}")
        await page.goto(user_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        for i in range(5):
            logger.info(f"  - 截取第 {i + 1}/5 张截图...")
            screenshot_bytes = await page.screenshot(full_page=False)
            base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
            screenshots.append({"base64": base64_image, "format": "png"})
            
            if i < 4:
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await asyncio.sleep(1)
        
        await browser.close()
    
    logger.info(f"  - 截屏完成，共 {len(screenshots)} 张")
    
    # 2. VL 分析
    logger.info("[2/3] 调用视觉模型分析...")
    vision_prompt = load_prompt("xhs_profile_vl/vision_analysis")
    
    start_time = datetime.now()
    analysis_result = analyze_multiple_images_with_vision_model(screenshots, vision_prompt)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    logger.info(f"  - VL 分析完成，耗时: {elapsed:.2f}s")
    logger.info(f"  - 分析结果长度: {len(analysis_result)} 字符")
    
    # 3. 输出结果
    logger.info("[3/3] 输出结果...")
    
    print("\n" + "=" * 60)
    print("视觉分析结果")
    print("=" * 60)
    print(analysis_result)
    print("=" * 60 + "\n")
    
    # 保存结果
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = RESULT_DIR / f"vl_analysis_{timestamp}.txt"
    
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(f"URL: {user_url}\n")
        f.write(f"Time: {datetime.now().isoformat()}\n")
        f.write(f"Elapsed: {elapsed:.2f}s\n")
        f.write("=" * 60 + "\n")
        f.write(analysis_result)
    
    logger.info(f"  - 结果已保存: {result_path}")
    
    return {"analysis": analysis_result}


# ============ 主入口 ============

async def main():
    """主入口"""
    print("\n" + "=" * 60)
    print("XHS 用户主页视觉分析测试")
    print("=" * 60)
    print("\n请输入小红书用户主页 URL:")
    print("格式: https://www.xiaohongshu.com/user/profile/xxxxx")
    print("-" * 60)
    
    user_url = input("URL: ").strip()
    
    if not user_url:
        print("错误: URL 不能为空")
        return
    
    if "xiaohongshu.com/user/profile" not in user_url:
        print("警告: URL 格式可能不正确，继续执行...")
    
    print("-" * 60)
    print("\n选择测试模式:")
    print("1. Agent 模式（完整流程，截屏 + VL分析 + 结构化输出）")
    print("2. 直接模式（仅 VL 分析，便于调试）")
    print("-" * 60)
    
    mode = input("模式 (1/2, 默认1): ").strip() or "1"
    
    print("\n")
    
    if mode == "2":
        result = await test_vision_analysis_direct(user_url)
    else:
        result = await test_xhs_profile_vl_agent(user_url)
    
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
