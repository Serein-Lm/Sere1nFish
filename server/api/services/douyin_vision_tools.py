"""
抖音视觉分析工具

提供截图和视觉分析功能
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, AsyncGenerator

from motor.motor_asyncio import AsyncIOMotorDatabase
from core.llm_params import disable_thinking_extra_body

# 确保 crawler_tools 可导入
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


async def screenshot_user_profile_stream(
    user_url: str,
    db: AsyncIOMotorDatabase,
    max_screenshots: int = 3,
    fix_layout: bool = True,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    截取抖音用户主页（流式）
    
    Args:
        user_url: 用户主页 URL
        db: 数据库连接
        max_screenshots: 最大截图数量
        fix_layout: 是否启用布局修复
    
    Yields:
        {"type": "progress", "message": "..."}
        {"type": "result", "data": {"screenshots": [...], "error": None}}
    """
    from api.dao import douyin as douyin_dao
    from crawler_tools.screenshot_tool import screenshot_douyin_profile_stream
    
    # 获取激活的 Cookie
    cookie_doc = await douyin_dao.get_active_cookie(db)
    if not cookie_doc:
        yield {"type": "result", "data": {"screenshots": [], "error": "没有激活的 Cookie"}}
        return
    
    cookie_str = cookie_doc.get("cookie_string", "")
    if not cookie_str:
        yield {"type": "result", "data": {"screenshots": [], "error": "Cookie 为空"}}
        return
    
    # 流式截图
    async for item in screenshot_douyin_profile_stream(
        user_url=user_url,
        cookie_str=cookie_str,
        max_screenshots=max_screenshots,
        fix_layout=fix_layout,
    ):
        yield item


async def analyze_screenshots_with_vision_stream(
    screenshots: list[dict[str, Any]],
    prompt: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    使用视觉模型分析截图（流式）
    
    Args:
        screenshots: 截图列表 [{"base64": "...", "format": "png"}, ...]
        prompt: 自定义 prompt，不提供则使用默认
    
    Yields:
        分析内容片段
    """
    from openai import OpenAI
    from api.services.runtime_config import get_runtime_app_config
    from Sere1nGraph.graph.prompts.loader import load_prompt

    app_config = await get_runtime_app_config()
    
    # 加载默认 Prompt
    if not prompt:
        prompt = load_prompt("douyin_profile/douyin_profile")
    
    # 构建消息内容
    content = []
    for screenshot in screenshots:
        base64_data = screenshot.get("base64", "")
        if base64_data:
            data_url = f"data:image/png;base64,{base64_data}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
    
    content.append({"type": "text", "text": prompt})
    
    rt = app_config.runtime
    api_key = rt.api_key or ""
    base_url = rt.base_url or ""
    vision_model = rt.models.vision
    
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    stream = client.chat.completions.create(
        model=vision_model,
        messages=[{"role": "user", "content": content}],
        extra_body=disable_thinking_extra_body({"vl_high_resolution_images": True}),
        stream=True,
    )
    
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def get_user_profile_vision_analysis(
    user_url: str,
    db: AsyncIOMotorDatabase,
    keyword: str = "",
    max_screenshots: int = 3,
    fix_layout: bool = True,
    save_files: bool = False,
) -> dict[str, Any]:
    """
    获取用户主页视觉分析（非流式）
    
    Args:
        user_url: 用户主页 URL
        db: 数据库连接
        keyword: 搜索关键词（用于关联度分析）
        max_screenshots: 最大截图数量
        fix_layout: 是否启用布局修复
        save_files: 是否保存截图文件
    
    Returns:
        {
            "success": bool,
            "vision_analysis": str,
            "analysis_json": dict | None,
            "screenshots": list,
            "screenshot_paths": list,
            "error": str | None
        }
    """
    from api.utils.json_extract import extract_json_object
    
    result = {
        "success": False,
        "vision_analysis": "",
        "analysis_json": None,
        "screenshots": [],
        "screenshot_paths": [],
        "error": None,
    }
    
    # 截图
    screenshots = []
    async for item in screenshot_user_profile_stream(user_url, db, max_screenshots, fix_layout):
        if item.get("type") == "result":
            data = item.get("data", {})
            screenshots = data.get("screenshots", [])
            if data.get("error"):
                result["error"] = data["error"]
                return result
    
    if not screenshots:
        result["error"] = "未获取到截图"
        return result
    
    result["screenshots"] = screenshots
    
    # 保存截图文件
    if save_files:
        import base64
        from datetime import datetime
        
        screenshot_dir = _project_root / "data" / "douyin_screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        
        # 提取 sec_uid
        sec_uid = ""
        if "/user/" in user_url:
            sec_uid = user_url.split("/user/")[-1].split("?")[0][:20]
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for idx, screenshot in enumerate(screenshots):
            base64_data = screenshot.get("base64", "")
            if base64_data:
                filename = f"{sec_uid}_{timestamp}_{idx + 1}.png"
                filepath = screenshot_dir / filename
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(base64_data))
                result["screenshot_paths"].append(str(filepath))
    
    # 构建带关键词的 prompt
    prompt = None
    if keyword:
        from Sere1nGraph.graph.prompts.loader import load_prompt

        base_prompt = load_prompt("douyin_profile/douyin_profile")
        prompt = f"搜索关键词: {keyword}\n\n{base_prompt}"
    
    # 视觉分析
    analysis_chunks = []
    async for chunk in analyze_screenshots_with_vision_stream(screenshots, prompt):
        analysis_chunks.append(chunk)
    
    vision_analysis = "".join(analysis_chunks)
    result["vision_analysis"] = vision_analysis
    
    # 尝试解析 JSON
    try:
        result["analysis_json"] = extract_json_object(vision_analysis.strip())
    except Exception:
        pass
    
    result["success"] = True
    
    return result
