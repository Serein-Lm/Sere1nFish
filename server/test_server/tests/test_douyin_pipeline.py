"""
抖音爬虫完整流程测试

流程：
1. 搜索关键词 → 获取作品列表 → 存储到本地 JSON
2. 真实 Agent 打标 → 调用 LLM 判断是否为目标用户（B站内部员工）
3. 生成用户主页链接（不爬取）
4. 截图工具 → 测试抖音用户主页截图
"""

import asyncio
import base64
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

# 确保项目根目录在 sys.path 中
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from crawler_tools.douyin_crawler import create_douyin_crawler, DouyinCrawler
from openai import OpenAI

# ==================== 配置 ====================

# Cookie 文件路径
COOKIE_FILE = Path(__file__).parent / "douyin_cookie.txt"

# 数据存储目录
DATA_DIR = Path(__file__).parent / "douyin_data"
DATA_DIR.mkdir(exist_ok=True)

# 截图存储目录
SCREENSHOT_DIR = DATA_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# 搜索配置
SEARCH_KEYWORD = "b站实习"
SEARCH_COUNT = 20  # 默认20条

# Prompt 文件路径
TAGGING_PROMPT_PATH = repo_root / "Sere1nGraph" / "graph" / "prompts" / "douyin_profile" / "douyin_tagging.md"
PROFILE_PROMPT_PATH = repo_root / "Sere1nGraph" / "graph" / "prompts" / "douyin_profile" / "douyin_profile.md"


# ==================== 工具函数 ====================

def load_cookie_from_file() -> str:
    """从文件加载 Cookie"""
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


def save_json(data: any, filename: str) -> str:
    """保存数据到 JSON 文件"""
    filepath = DATA_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return str(filepath)


def load_json(filename: str) -> any:
    """从 JSON 文件加载数据"""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_user_profile_url(sec_uid: str) -> str:
    """生成用户主页链接"""
    return f"https://www.douyin.com/user/{sec_uid}"


def timestamp_to_str(ts: int) -> str:
    """时间戳转字符串"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(ts)


def load_prompt(prompt_path: Path) -> str:
    """加载 Prompt 文件"""
    if prompt_path.exists():
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    raise FileNotFoundError(f"Prompt 文件不存在: {prompt_path}")


def get_llm_config() -> dict:
    """获取 LLM 配置"""
    from Sere1nGraph.graph.config.loader import load_config
    
    app_config = load_config()
    runtime = getattr(app_config, "runtime", None)
    
    if not runtime:
        raise ValueError("配置未找到")
    
    models = getattr(runtime, "models", None)
    default_model = getattr(models, "default", "qwen3-max") if models else "qwen3-max"
    vision_model = getattr(models, "vision", "qwen3-vl-plus") if models else "qwen3-vl-plus"
    base_url = getattr(runtime, "base_url", "") or ""
    api_key = getattr(runtime, "api_key", "") or ""
    
    return {
        "base_url": base_url,
        "api_key": api_key,
        "default_model": default_model,
        "vision_model": vision_model,
    }


# ==================== 步骤1: 搜索并存储 ====================

async def step1_search_and_save(keyword: str = SEARCH_KEYWORD, count: int = SEARCH_COUNT) -> List[Dict]:
    """
    步骤1: 搜索关键词并保存结果
    
    Returns:
        搜索结果列表
    """
    print("\n" + "=" * 60)
    print(f"步骤1: 搜索关键词 [{keyword}]")
    print("=" * 60)
    
    cookie_str = load_cookie_from_file()
    if not cookie_str:
        print("✗ Cookie 文件为空，请先配置 douyin_cookie.txt")
        return []
    
    crawler = await create_douyin_crawler()
    
    try:
        # 登录
        print("正在登录...")
        result = await crawler.login_by_cookie_string(cookie_str)
        if not result.success:
            print(f"✗ 登录失败: {result.message}")
            return []
        print("✓ 登录成功")
        
        # 搜索
        print(f"\n正在搜索: {keyword}, 数量: {count}")
        search_result = await crawler.search_videos(keyword=keyword, count=count)
        
        if not search_result.success:
            print(f"✗ 搜索失败: {search_result.message}")
            return []
        
        print(f"✓ 获取到 {search_result.total} 条结果")
        
        # 处理数据，添加用户主页链接
        items = []
        for item in search_result.items:
            # 添加用户主页链接
            item["user_profile_url"] = generate_user_profile_url(item.get("sec_uid", ""))
            # 转换时间戳
            item["create_time_str"] = timestamp_to_str(item.get("create_time"))
            items.append(item)
        
        # 保存到 JSON
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"search_{keyword.replace(' ', '_')}_{timestamp}.json"
        
        save_data = {
            "keyword": keyword,
            "count": len(items),
            "search_time": datetime.now().isoformat(),
            "items": items
        }
        
        filepath = save_json(save_data, filename)
        print(f"\n✓ 数据已保存到: {filepath}")
        
        # 打印摘要
        print("\n" + "-" * 40)
        print("搜索结果摘要:")
        print("-" * 40)
        for i, item in enumerate(items[:5], 1):
            print(f"\n[{i}] {item.get('title', '')[:50]}...")
            print(f"    作者: {item.get('nickname')}")
            print(f"    点赞: {item.get('liked_count')} | 评论: {item.get('comment_count')}")
            print(f"    用户主页: {item.get('user_profile_url')}")
        
        if len(items) > 5:
            print(f"\n... 还有 {len(items) - 5} 条结果")
        
        return items
        
    finally:
        await crawler.close()
        print("\n✓ 浏览器已关闭")


# ==================== 步骤2: 真实 Agent 打标 ====================

def step2_agent_tagging(items: List[Dict]) -> List[Dict]:
    """
    步骤2: 使用真实 LLM 对搜索结果进行打标
    
    调用大模型判断:
    - 目标: B站内部员工 (potential_employee)
    - 排除: 营销号 (marketing)
    - 不确定: (uncertain)
    
    Returns:
        打标后的结果列表
    """
    print("\n" + "=" * 60)
    print("步骤2: Agent 打标 (真实 LLM)")
    print("=" * 60)
    
    if not items:
        print("没有数据需要打标")
        return []
    
    # 获取 LLM 配置
    try:
        llm_config = get_llm_config()
    except Exception as e:
        print(f"✗ 获取 LLM 配置失败: {e}")
        return items
    
    if not llm_config.get("api_key"):
        print("✗ LLM API Key 未配置")
        return items
    
    # 加载 Prompt
    try:
        tagging_prompt = load_prompt(TAGGING_PROMPT_PATH)
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return items
    
    print(f"使用模型: {llm_config['default_model']}")
    print(f"待打标数量: {len(items)} 条")
    
    # 准备输入数据
    input_data = {
        "keyword": SEARCH_KEYWORD,
        "items": [
            {
                "aweme_id": item.get("aweme_id"),
                "title": item.get("title", "")[:200],  # 限制长度
                "nickname": item.get("nickname"),
                "sec_uid": item.get("sec_uid"),
                "user_profile_url": item.get("user_profile_url"),
                "liked_count": item.get("liked_count"),
                "comment_count": item.get("comment_count"),
                "create_time_str": item.get("create_time_str"),
            }
            for item in items
        ]
    }
    
    # 构建消息
    user_message = f"""请根据以下搜索结果进行打标分析：

{json.dumps(input_data, ensure_ascii=False, indent=2)}

请按照 Prompt 中的输出格式，对每条作品进行打标。返回 JSON 数组格式。"""
    
    # 调用 LLM
    print("\n正在调用 LLM 进行打标分析...")
    
    try:
        client = OpenAI(
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
        )
        
        completion = client.chat.completions.create(
            model=llm_config["default_model"],
            messages=[
                {"role": "system", "content": tagging_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )
        
        response_text = completion.choices[0].message.content
        print("✓ LLM 响应成功")
        
        # 解析 LLM 响应
        tagged_results = parse_tagging_response(response_text, items)
        
    except Exception as e:
        print(f"✗ LLM 调用失败: {e}")
        # 失败时返回原数据，标记为 uncertain
        for item in items:
            item["tag"] = "uncertain"
            item["tag_reason"] = f"LLM 调用失败: {e}"
            item["confidence"] = "low"
        return items
    
    # 统计
    potential = [i for i in tagged_results if i.get("tag") == "potential_employee"]
    marketing = [i for i in tagged_results if i.get("tag") == "marketing"]
    uncertain = [i for i in tagged_results if i.get("tag") == "uncertain"]
    
    print(f"\n打标结果统计:")
    print(f"  - 潜在员工: {len(potential)} 条")
    print(f"  - 营销号: {len(marketing)} 条")
    print(f"  - 不确定: {len(uncertain)} 条")
    
    # 保存打标结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"tagged_{timestamp}.json"
    
    save_data = {
        "total": len(tagged_results),
        "potential_employee": len(potential),
        "marketing": len(marketing),
        "uncertain": len(uncertain),
        "tag_time": datetime.now().isoformat(),
        "model": llm_config["default_model"],
        "items": tagged_results
    }
    
    filepath = save_json(save_data, filename)
    print(f"\n✓ 打标结果已保存到: {filepath}")
    
    # 显示潜在员工
    if potential:
        print("\n" + "-" * 40)
        print("潜在员工列表:")
        print("-" * 40)
        for i, item in enumerate(potential[:10], 1):
            print(f"\n[{i}] {item.get('title', '')[:50]}...")
            print(f"    作者: {item.get('nickname')}")
            print(f"    置信度: {item.get('confidence', 'N/A')}")
            print(f"    理由: {item.get('tag_reason', 'N/A')}")
            print(f"    用户主页: {item.get('user_profile_url')}")
    
    return tagged_results


def parse_tagging_response(response_text: str, original_items: List[Dict]) -> List[Dict]:
    """
    解析 LLM 打标响应
    
    Args:
        response_text: LLM 返回的文本
        original_items: 原始数据列表
    
    Returns:
        合并后的打标结果
    """
    # 尝试从响应中提取 JSON
    import re
    
    # 尝试找到 JSON 数组
    json_match = re.search(r'\[[\s\S]*\]', response_text)
    
    if json_match:
        try:
            tagging_results = json.loads(json_match.group())
        except json.JSONDecodeError:
            tagging_results = []
    else:
        tagging_results = []
    
    # 创建 aweme_id -> tagging 映射
    tagging_map = {}
    for result in tagging_results:
        aweme_id = result.get("aweme_id")
        if aweme_id:
            tagging_map[aweme_id] = result
    
    # 合并结果
    merged_items = []
    for item in original_items:
        aweme_id = item.get("aweme_id")
        tagging = tagging_map.get(aweme_id, {})
        
        # 合并打标信息
        item["tag"] = tagging.get("tag", "uncertain")
        item["tag_reason"] = tagging.get("reason", "未获取到打标结果")
        item["confidence"] = tagging.get("confidence", "low")
        item["key_evidence"] = tagging.get("key_evidence", [])
        item["company_mentioned"] = tagging.get("company_mentioned", "")
        item["position_mentioned"] = tagging.get("position_mentioned", "")
        item["priority"] = tagging.get("priority", 5)
        
        merged_items.append(item)
    
    return merged_items


# ==================== 步骤3: 生成用户主页链接 ====================

def step3_generate_profile_urls(tagged_items: List[Dict]) -> List[Dict]:
    """
    步骤3: 生成用户主页链接（不爬取）
    
    筛选潜在员工，生成用户主页链接列表
    
    Returns:
        用户主页链接列表
    """
    print("\n" + "=" * 60)
    print("步骤3: 生成用户主页链接")
    print("=" * 60)
    
    # 筛选潜在员工
    potential_users = [i for i in tagged_items if i.get("tag") == "potential_employee"]
    
    if not potential_users:
        print("没有潜在员工需要处理")
        return []
    
    # 去重 (同一用户可能有多条作品)
    unique_users = {}
    for item in potential_users:
        sec_uid = item.get("sec_uid")
        if sec_uid and sec_uid not in unique_users:
            unique_users[sec_uid] = {
                "sec_uid": sec_uid,
                "nickname": item.get("nickname"),
                "user_profile_url": item.get("user_profile_url"),
                "sample_title": item.get("title", "")[:100],
                "tag_reason": item.get("tag_reason"),
                "confidence": item.get("confidence"),
                "priority": item.get("priority", 5),
            }
    
    # 按优先级排序
    user_list = sorted(unique_users.values(), key=lambda x: x.get("priority", 5), reverse=True)
    
    print(f"\n共 {len(user_list)} 个唯一用户")
    print("\n" + "-" * 40)
    print("用户主页链接列表:")
    print("-" * 40)
    
    for i, user in enumerate(user_list, 1):
        print(f"\n[{i}] {user['nickname']}")
        print(f"    主页: {user['user_profile_url']}")
        print(f"    置信度: {user['confidence']} | 优先级: {user['priority']}")
        print(f"    理由: {user['tag_reason'][:50]}..." if len(user.get('tag_reason', '')) > 50 else f"    理由: {user.get('tag_reason', 'N/A')}")
    
    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"profile_urls_{timestamp}.json"
    
    save_data = {
        "total": len(user_list),
        "generate_time": datetime.now().isoformat(),
        "users": user_list
    }
    
    filepath = save_json(save_data, filename)
    print(f"\n✓ 用户主页链接已保存到: {filepath}")
    
    return user_list


# ==================== 步骤4: 截图工具 ====================

async def screenshot_douyin_profile_stream(
    user_url: str,
    cookie_str: str,
    max_screenshots: int = 5,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    访问抖音用户主页并截屏（流式版本）
    
    Args:
        user_url: 用户主页 URL (https://www.douyin.com/user/{sec_uid})
        cookie_str: Cookie 字符串
        max_screenshots: 最大截图数量
    
    Yields:
        {"type": "progress", "message": "进度信息"}
        {"type": "result", "data": {...}}
    """
    from playwright.async_api import async_playwright
    
    if "douyin.com" not in user_url:
        yield {"type": "result", "data": {"screenshots": [], "error": "URL 格式错误，需要抖音链接"}}
        return
    
    screenshots = []
    
    # 解析 Cookie
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            name, value = item.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".douyin.com",
                "path": "/",
            })
    
    if not cookies:
        yield {"type": "result", "data": {"screenshots": [], "error": "Cookie 解析失败"}}
        return
    
    stealth_js = repo_root / "MediaCrawler" / "libs" / "stealth.min.js"
    
    try:
        yield {"type": "progress", "message": "正在启动浏览器..."}
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            
            if stealth_js.exists():
                await context.add_init_script(path=str(stealth_js))
            
            await context.add_cookies(cookies)
            page = await context.new_page()
            
            yield {"type": "progress", "message": f"正在访问: {user_url}"}
            
            await page.goto(user_url, wait_until="domcontentloaded", timeout=60000)
            
            yield {"type": "progress", "message": "等待页面加载..."}
            await asyncio.sleep(5)
            
            # 智能滚动截图
            screenshot_count = 0
            consecutive_no_scroll = 0
            max_no_scroll_attempts = 2
            
            while screenshot_count < max_screenshots:
                # 获取当前滚动位置
                scroll_info_before = await page.evaluate("""
                    () => {
                        return {
                            scrollTop: window.scrollY || document.documentElement.scrollTop,
                            scrollHeight: document.documentElement.scrollHeight,
                            clientHeight: document.documentElement.clientHeight
                        }
                    }
                """)
                
                # 截图
                screenshot_count += 1
                yield {"type": "progress", "message": f"正在截取第 {screenshot_count} 张截图..."}
                
                screenshot_bytes = await page.screenshot()
                base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
                screenshots.append({"base64": base64_image, "format": "png"})
                
                # 检查是否已经到底部
                is_at_bottom = (
                    scroll_info_before["scrollTop"] + scroll_info_before["clientHeight"] 
                    >= scroll_info_before["scrollHeight"] - 10
                )
                
                if is_at_bottom:
                    yield {"type": "progress", "message": "已滚动到页面底部，停止截图"}
                    break
                
                # 滚动页面
                yield {"type": "progress", "message": "滚动页面..."}
                await asyncio.sleep(random.uniform(0.3, 0.7))
                await page.mouse.move(random.randint(400, 1200), random.randint(300, 700))
                await page.mouse.wheel(0, 800)
                await asyncio.sleep(random.uniform(1.5, 2.5))
                
                # 获取滚动后的位置
                scroll_info_after = await page.evaluate("""
                    () => {
                        return {
                            scrollTop: window.scrollY || document.documentElement.scrollTop
                        }
                    }
                """)
                
                # 检查是否滚动成功
                if abs(scroll_info_after["scrollTop"] - scroll_info_before["scrollTop"]) < 10:
                    consecutive_no_scroll += 1
                    yield {"type": "progress", "message": f"页面未滚动（{consecutive_no_scroll}/{max_no_scroll_attempts}）"}
                    
                    if consecutive_no_scroll >= max_no_scroll_attempts:
                        yield {"type": "progress", "message": "连续多次无法滚动，停止截图"}
                        break
                else:
                    consecutive_no_scroll = 0
            
            yield {"type": "progress", "message": f"截图完成，共 {len(screenshots)} 张"}
            yield {"type": "progress", "message": "正在关闭浏览器..."}
            await browser.close()
        
        yield {"type": "result", "data": {"screenshots": screenshots, "error": None}}
        
    except Exception as e:
        yield {"type": "result", "data": {"screenshots": [], "error": f"截屏失败: {str(e)}"}}


async def screenshot_douyin_profile(user_url: str, cookie_str: str, max_screenshots: int = 5) -> dict:
    """
    访问抖音用户主页并截屏（非流式版本）
    """
    result = {"screenshots": [], "error": None}
    async for item in screenshot_douyin_profile_stream(user_url, cookie_str, max_screenshots):
        if item.get("type") == "progress":
            print(f"  {item.get('message')}")
        elif item.get("type") == "result":
            result = item.get("data", result)
    return result


def save_screenshots_to_files(screenshots: list, user_id: str) -> list:
    """将截图保存到文件"""
    saved_paths = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for idx, screenshot in enumerate(screenshots):
        base64_data = screenshot.get("base64", "")
        img_format = screenshot.get("format", "png")
        
        if not base64_data:
            continue
        
        filename = f"{user_id}_{timestamp}_{idx + 1}.{img_format}"
        filepath = SCREENSHOT_DIR / filename
        
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(base64_data))
        
        saved_paths.append(str(filepath))
    
    return saved_paths


async def step4_test_screenshot(user_url: str = None):
    """
    步骤4: 测试抖音用户主页截图
    
    Args:
        user_url: 用户主页 URL，如果不提供则使用示例
    """
    print("\n" + "=" * 60)
    print("步骤4: 测试抖音用户主页截图")
    print("=" * 60)
    
    if not user_url:
        # 使用示例 URL
        user_url = "https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4"
        print(f"使用示例 URL: {user_url}")
    else:
        print(f"测试 URL: {user_url}")
    
    cookie_str = load_cookie_from_file()
    if not cookie_str:
        print("✗ Cookie 文件为空")
        return
    
    print("\n开始截图...")
    result = await screenshot_douyin_profile(user_url, cookie_str, max_screenshots=3)
    
    if result.get("error"):
        print(f"\n✗ 截图失败: {result['error']}")
        return
    
    screenshots = result.get("screenshots", [])
    if not screenshots:
        print("\n✗ 未获取到截图")
        return
    
    print(f"\n✓ 获取到 {len(screenshots)} 张截图")
    
    # 提取 user_id
    user_id = user_url.split("/user/")[-1].split("?")[0][:20]
    
    # 保存截图
    saved_paths = save_screenshots_to_files(screenshots, user_id)
    
    print(f"\n✓ 截图已保存:")
    for path in saved_paths:
        print(f"  - {path}")


async def test_screenshot_with_vision(user_url: str = None):
    """
    测试截图 + 视觉分析
    """
    print("\n" + "=" * 60)
    print("测试: 截图 + 视觉分析")
    print("=" * 60)
    
    if not user_url:
        user_url = "https://www.douyin.com/user/MS4wLjABAAAA8U_l6rBzmy7bcy6xOJel4v0RzoR_wfAubGPeJimN__4"
        print(f"使用示例 URL: {user_url}")
    
    cookie_str = load_cookie_from_file()
    if not cookie_str:
        print("✗ Cookie 文件为空")
        return
    
    # 截图
    print("\n1. 开始截图...")
    result = await screenshot_douyin_profile(user_url, cookie_str, max_screenshots=3)
    
    if result.get("error"):
        print(f"\n✗ 截图失败: {result['error']}")
        return
    
    screenshots = result.get("screenshots", [])
    if not screenshots:
        print("\n✗ 未获取到截图")
        return
    
    print(f"✓ 获取到 {len(screenshots)} 张截图")
    
    # 保存截图
    user_id = user_url.split("/user/")[-1].split("?")[0][:20]
    saved_paths = save_screenshots_to_files(screenshots, user_id)
    print(f"✓ 截图已保存到: {SCREENSHOT_DIR}")
    
    # 视觉分析
    print("\n2. 开始视觉分析...")
    
    try:
        llm_config = get_llm_config()
    except Exception as e:
        print(f"✗ 获取 LLM 配置失败: {e}")
        return
    
    if not llm_config.get("api_key"):
        print("✗ LLM API Key 未配置")
        return
    
    # 加载 profile prompt
    try:
        profile_prompt = load_prompt(PROFILE_PROMPT_PATH)
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return
    
    print(f"使用视觉模型: {llm_config['vision_model']}")
    
    # 构建视觉分析请求
    client = OpenAI(
        api_key=llm_config["api_key"],
        base_url=llm_config["base_url"],
    )
    
    content = []
    for screenshot in screenshots:
        base64_data = screenshot.get("base64", "")
        if base64_data:
            data_url = f"data:image/png;base64,{base64_data}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
    
    content.append({"type": "text", "text": profile_prompt})
    
    print("正在调用视觉模型...")
    
    try:
        completion = client.chat.completions.create(
            model=llm_config["vision_model"],
            messages=[{"role": "user", "content": content}],
        )
        
        analysis_result = completion.choices[0].message.content
        print("\n✓ 视觉分析完成")
        print("\n" + "-" * 40)
        print("分析结果:")
        print("-" * 40)
        print(analysis_result)
        
        # 保存分析结果
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"vision_analysis_{user_id}_{timestamp}.json"
        
        save_data = {
            "user_url": user_url,
            "user_id": user_id,
            "screenshot_count": len(screenshots),
            "screenshot_paths": saved_paths,
            "analysis_time": datetime.now().isoformat(),
            "model": llm_config["vision_model"],
            "analysis_result": analysis_result,
        }
        
        filepath = save_json(save_data, filename)
        print(f"\n✓ 分析结果已保存到: {filepath}")
        
    except Exception as e:
        print(f"\n✗ 视觉分析失败: {e}")


# ==================== 完整流程 ====================

async def run_full_pipeline(keyword: str = SEARCH_KEYWORD, count: int = SEARCH_COUNT):
    """运行完整流程（不包含截图）"""
    print("\n" + "=" * 60)
    print("抖音爬虫完整流程测试")
    print("=" * 60)
    print(f"关键词: {keyword}")
    print(f"数量: {count}")
    print(f"数据目录: {DATA_DIR}")
    
    # 步骤1: 搜索
    items = await step1_search_and_save(keyword, count)
    if not items:
        print("\n✗ 搜索失败，流程终止")
        return
    
    # 步骤2: 真实 Agent 打标
    tagged_items = step2_agent_tagging(items)
    
    # 步骤3: 生成用户主页链接
    user_list = step3_generate_profile_urls(tagged_items)
    
    # 总结
    print("\n" + "=" * 60)
    print("流程完成")
    print("=" * 60)
    print(f"搜索结果: {len(items)} 条")
    print(f"潜在员工: {len([i for i in tagged_items if i.get('tag') == 'potential_employee'])} 条")
    print(f"唯一用户: {len(user_list)} 个")
    print(f"\n数据文件保存在: {DATA_DIR}")
    
    return user_list


# ==================== 单独测试函数 ====================

async def test_search_only(keyword: str = SEARCH_KEYWORD, count: int = SEARCH_COUNT):
    """仅测试搜索功能"""
    await step1_search_and_save(keyword, count)


def test_tagging_only(json_file: str = None):
    """
    仅测试打标功能（使用已有的搜索结果）
    
    Args:
        json_file: 搜索结果 JSON 文件名，如果不提供则使用最新的
    """
    if json_file:
        data = load_json(json_file)
    else:
        # 查找最新的搜索结果文件
        search_files = list(DATA_DIR.glob("search_*.json"))
        if not search_files:
            print("✗ 没有找到搜索结果文件")
            return
        latest_file = max(search_files, key=lambda x: x.stat().st_mtime)
        print(f"使用最新的搜索结果: {latest_file.name}")
        data = load_json(latest_file.name)
    
    if not data:
        print("✗ 无法加载数据")
        return
    
    items = data.get("items", [])
    if not items:
        print("✗ 数据为空")
        return
    
    # 打标
    tagged_items = step2_agent_tagging(items)
    
    # 生成链接
    step3_generate_profile_urls(tagged_items)


# ==================== 主入口 ====================

def print_menu():
    """打印菜单"""
    print("\n" + "=" * 50)
    print("抖音爬虫流程测试")
    print("=" * 50)
    print("1. 运行完整流程 (搜索 → Agent打标 → 生成链接)")
    print("2. 仅搜索并保存")
    print("3. 仅打标（使用已有搜索结果）")
    print("4. 测试截图工具")
    print("5. 测试截图 + 视觉分析")
    print("6. 自定义搜索关键词")
    print("0. 退出")
    print("=" * 50)


async def main():
    """主函数"""
    while True:
        print_menu()
        choice = input("请选择 [0-6]: ").strip()
        
        if choice == "0":
            print("\n再见!")
            break
        elif choice == "1":
            await run_full_pipeline()
        elif choice == "2":
            await test_search_only()
        elif choice == "3":
            test_tagging_only()
        elif choice == "4":
            url = input("请输入抖音用户主页 URL (回车使用示例): ").strip() or None
            await step4_test_screenshot(url)
        elif choice == "5":
            url = input("请输入抖音用户主页 URL (回车使用示例): ").strip() or None
            await test_screenshot_with_vision(url)
        elif choice == "6":
            keyword = input(f"请输入搜索关键词 [默认: {SEARCH_KEYWORD}]: ").strip() or SEARCH_KEYWORD
            count = input(f"请输入搜索数量 [默认: {SEARCH_COUNT}]: ").strip()
            count = int(count) if count.isdigit() else SEARCH_COUNT
            await run_full_pipeline(keyword, count)
        else:
            print("无效选择")
        
        input("\n按回车继续...")


if __name__ == "__main__":
    asyncio.run(main())
