"""
截屏识别 Agent 测试

功能：
1. 使用 Playwright MCP 访问 URL 并截屏
2. 将截屏发送给通义千问 VL 模型进行识别
3. 输出结构化打标结果
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Sere1nGraph" / "graph"))

from openai import OpenAI


# ============ 配置 ============

# 通义千问 VL 配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-9903d8c41f2841a281da1c9d89e37c92")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VL_MODEL = "qwen-vl-max"  # 或 qwen-vl-plus

# 截图保存目录
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"


# ============ 工具函数 ============

def encode_image_to_base64(image_path: str) -> str:
    """将本地图像转换为 Base64 编码"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def save_base64_image(base64_data: str, filename: str) -> str:
    """保存 Base64 图像到本地"""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = SCREENSHOT_DIR / filename
    
    # 如果 base64_data 包含 data URL 前缀，去掉它
    if "," in base64_data:
        base64_data = base64_data.split(",", 1)[1]
    
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(base64_data))
    
    return str(filepath)


def analyze_image_with_vl(
    base64_image: str,
    prompt: str = "请详细描述这个网页截图的内容，包括页面类型、主要元素、可见的文字信息、联系方式、人员信息等。如果发现任何敏感信息泄露风险，请特别指出。",
    image_format: str = "png",
) -> str:
    """
    使用通义千问 VL 模型分析图像
    
    Args:
        base64_image: Base64 编码的图像
        prompt: 分析提示词
        image_format: 图像格式 (png, jpeg, webp)
        
    Returns:
        str: 模型分析结果
    """
    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )
    
    # 构建 Data URL
    mime_type = f"image/{image_format}"
    data_url = f"data:{mime_type};base64,{base64_image}"
    
    completion = client.chat.completions.create(
        model=VL_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    
    return completion.choices[0].message.content


def structure_analysis_result(
    url: str,
    raw_analysis: str,
    screenshot_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    将 VL 模型的分析结果进行结构化
    
    使用另一个 LLM 调用来提取结构化信息
    """
    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )
    
    structure_prompt = f"""基于以下网页分析结果，提取结构化信息并输出 JSON 格式。

原始分析结果：
{raw_analysis}

请输出以下 JSON 格式（只输出 JSON，不要其他内容）：
{{
  "page_analysis": {{
    "page_type": "页面类型（homepage/login/register/product/about/contact/news/blog/social/ecommerce/other）",
    "page_title": "页面标题",
    "page_description": "页面内容概述（1-2句话）"
  }},
  "risk_assessment": {{
    "risk_level": "风险等级（low/medium/high）",
    "risk_types": ["风险类型列表，如 employee_info, contact_info, org_structure, tech_stack, internal_system, credential_leak, none"],
    "risk_details": "具体风险描述"
  }},
  "extracted_info": {{
    "company_name": "公司名称（如有）",
    "contacts": [
      {{"type": "phone/email/address", "value": "具体值"}}
    ],
    "personnel": [
      {{"name": "姓名", "position": "职位", "contact": "联系方式（如有）"}}
    ],
    "social_links": ["社交媒体链接列表"]
  }}
}}"""

    completion = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {"role": "user", "content": structure_prompt}
        ],
    )
    
    response_text = completion.choices[0].message.content
    
    # 尝试解析 JSON
    try:
        # 提取 JSON 部分（可能被 ```json ``` 包裹）
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = response_text.strip()
        
        structured_data = json.loads(json_str)
    except json.JSONDecodeError:
        structured_data = {
            "page_analysis": {"page_type": "unknown", "page_title": "", "page_description": ""},
            "risk_assessment": {"risk_level": "unknown", "risk_types": [], "risk_details": ""},
            "extracted_info": {"company_name": "", "contacts": [], "personnel": [], "social_links": []},
        }
    
    # 添加元信息
    result = {
        "url": url,
        "screenshot_time": datetime.now().isoformat(),
        "screenshot_path": screenshot_path,
        **structured_data,
        "raw_analysis": raw_analysis,
    }
    
    return result


# ============ Playwright MCP 截屏 ============

async def take_screenshot_with_playwright(url: str) -> tuple[str, str]:
    """
    使用 Playwright MCP 访问 URL 并截屏
    
    Returns:
        tuple: (base64_image, screenshot_path)
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    
    server_params = StdioServerParameters(
        command="npx",
        args=["@playwright/mcp@latest", "--isolated", "--headless", "--viewport-size=1920,1080"],
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 1. 打开页面
            print(f"[1/3] 正在访问: {url}")
            navigate_result = await session.call_tool(
                "browser_navigate",
                arguments={"url": url}
            )
            print(f"  -> 导航结果: {navigate_result.content[0].text[:100]}...")
            
            # 2. 等待页面加载
            print("[2/3] 等待页面加载...")
            await asyncio.sleep(3)
            
            # 3. 截屏
            print("[3/3] 正在截屏...")
            screenshot_result = await session.call_tool(
                "browser_take_screenshot",
                arguments={}
            )
            
            # 提取 Base64 数据
            # MCP 返回的可能是 ImageContent 或 TextContent
            for content in screenshot_result.content:
                if hasattr(content, "data"):
                    base64_image = content.data
                    break
                elif hasattr(content, "text") and content.text.startswith("data:image"):
                    # Data URL 格式
                    base64_image = content.text.split(",", 1)[1]
                    break
            else:
                raise ValueError("截屏失败：未获取到图像数据")
            
            # 保存截图
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"
            screenshot_path = save_base64_image(base64_image, filename)
            print(f"  -> 截图已保存: {screenshot_path}")
            
            return base64_image, screenshot_path


# ============ 简化版本：不使用 MCP，直接用 Playwright ============

async def take_screenshot_simple(url: str) -> tuple[str, str]:
    """
    简化版本：直接使用 Playwright 截屏（不通过 MCP）
    """
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        print(f"[1/3] 正在访问: {url}")
        await page.goto(url, wait_until="networkidle", timeout=30000)
        
        print("[2/3] 等待页面加载...")
        await asyncio.sleep(2)
        
        print("[3/3] 正在截屏...")
        # 截图为 bytes
        screenshot_bytes = await page.screenshot(full_page=True)
        
        # 转 Base64
        base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
        
        # 保存截图
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        screenshot_path = str(SCREENSHOT_DIR / filename)
        
        with open(screenshot_path, "wb") as f:
            f.write(screenshot_bytes)
        
        print(f"  -> 截图已保存: {screenshot_path}")
        
        await browser.close()
        
        return base64_image, screenshot_path


# ============ 主流程 ============

async def screenshot_and_analyze(
    url: str,
    use_mcp: bool = False,  # 是否使用 MCP（默认使用简化版本）
) -> dict[str, Any]:
    """
    完整流程：截屏 + VL 分析 + 结构化打标
    
    Args:
        url: 要访问的 URL
        use_mcp: 是否使用 MCP 版本的 Playwright
        
    Returns:
        dict: 结构化的打标结果
    """
    print(f"\n{'='*60}")
    print(f"截屏识别 Agent 测试")
    print(f"URL: {url}")
    print(f"{'='*60}\n")
    
    # 1. 截屏
    print(">>> 步骤 1: 截屏")
    if use_mcp:
        base64_image, screenshot_path = await take_screenshot_with_playwright(url)
    else:
        base64_image, screenshot_path = await take_screenshot_simple(url)
    
    # 2. VL 分析
    print("\n>>> 步骤 2: 发送给通义千问 VL 进行分析...")
    raw_analysis = analyze_image_with_vl(base64_image)
    print(f"  -> VL 分析完成，结果长度: {len(raw_analysis)} 字符")
    print(f"\n--- VL 原始分析 ---\n{raw_analysis}\n-------------------\n")
    
    # 3. 结构化打标
    print(">>> 步骤 3: 结构化打标...")
    result = structure_analysis_result(url, raw_analysis, screenshot_path)
    
    print("\n>>> 最终结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    return result


# ============ 测试入口 ============

async def main():
    """测试入口"""
    # 测试 URL
    test_urls = [
        "https://www.baidu.com",
        # "https://www.xiaohongshu.com",
        # "https://www.bytedance.com/zh/",
    ]
    
    for url in test_urls:
        try:
            result = await screenshot_and_analyze(url, use_mcp=False)
            
            # 保存结果
            result_path = SCREENSHOT_DIR / f"result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n结果已保存: {result_path}")
            
        except Exception as e:
            print(f"处理 {url} 时出错: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
