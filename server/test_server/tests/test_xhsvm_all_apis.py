"""
测试: xhsvm.js 本地签名 + curl_cffi 覆盖所有 XHS 接口

验证:
1. 搜索 /api/sns/web/v1/search/notes
2. 笔记详情 /api/sns/web/v1/feed
3. 评论 /api/sns/web/v2/comment/page
4. 用户信息 /api/sns/web/v2/user/me

用法:
    python test_server/tests/test_xhsvm_all_apis.py
"""
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

COOKIE_FILE = Path(__file__).parent / "xhs_cookie.txt"
XHSVM_JS = _root / "xhs-mcp-main" / "xhs_mcp" / "api" / "xhsvm.js"
BASE_URL = "https://edith.xiaohongshu.com"

# 编译一次 JS，复用
_js_ctx = None
def _get_js_ctx():
    global _js_ctx
    if _js_ctx is None:
        import execjs
        with open(str(XHSVM_JS), "r", encoding="utf-8") as f:
            _js_ctx = execjs.compile(f.read())
    return _js_ctx

def sign(uri: str, data, cookie: str) -> dict:
    return json.loads(_get_js_ctx().call("GetXsXt", uri, data, cookie))

def parse_cookie(cookie: str) -> dict:
    d = {}
    for item in cookie.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            d[k.strip()] = v.strip()
    return d


async def request(cookie: str, uri: str, method: str = "POST", data=None, params=None) -> dict:
    """统一请求：xhsvm.js 签名 + curl_cffi"""
    from curl_cffi.requests import AsyncSession

    xsxt = sign(uri, data or params or {}, cookie)

    headers = {
        "content-type": "application/json;charset=UTF-8",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "x-s": xsxt["X-s"],
        "x-t": str(xsxt["X-t"]),
    }

    async with AsyncSession(verify=True, impersonate="chrome124") as session:
        resp = await session.request(
            method=method,
            url=f"{BASE_URL}{uri}",
            json=data if method == "POST" else None,
            params=params if method == "GET" else None,
            cookies=parse_cookie(cookie),
            headers=headers,
            stream=True,
        )
        content = await resp.acontent()
        return {"status": resp.status_code, "data": json.loads(content)}


async def test_search(cookie: str):
    """测试 1: 搜索"""
    print(f"\n{'='*50}")
    print("测试 1: 搜索 /api/sns/web/v1/search/notes")
    print(f"{'='*50}")

    # search_id 生成
    def base36(n):
        s = ""
        while n:
            n, i = divmod(n, 36)
            s = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i] + s
        return s or "0"
    search_id = base36((int(time.time() * 1000) << 64) + random.randint(0, 2147483646))

    data = {
        "keyword": "百度 实习",
        "page": 1,
        "page_size": 20,
        "search_id": search_id,
        "sort": "general",
        "note_type": 0,
        "ext_flags": [],
        "geo": "",
        "image_formats": ["jpg", "webp", "avif"],
    }

    t0 = time.time()
    r = await request(cookie, "/api/sns/web/v1/search/notes", "POST", data)
    elapsed = time.time() - t0

    status = r["status"]
    success = r["data"].get("success", False)
    items = r["data"].get("data", {}).get("items", []) if success else []

    print(f"  Status: {status} | Success: {success} | 耗时: {elapsed:.2f}s")
    if items:
        print(f"  ✅ 搜索到 {len(items)} 条")
        note = items[0].get("note_card", {})
        print(f"  第1条: {note.get('display_title', '')[:50]}")
        # 返回第一条的 note_id 和 xsec_token 给后续测试用
        return {
            "note_id": items[0].get("id", ""),
            "xsec_token": note.get("user", {}).get("xsec_token", ""),
        }
    else:
        print(f"  ❌ 无结果: {json.dumps(r['data'], ensure_ascii=False)[:200]}")
    return None


async def test_detail(cookie: str, note_id: str, xsec_token: str):
    """测试 2: 笔记详情"""
    print(f"\n{'='*50}")
    print(f"测试 2: 详情 /api/sns/web/v1/feed note={note_id}")
    print(f"{'='*50}")

    data = {
        "source_note_id": note_id,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": "1"},
        "xsec_source": "pc_feed",
        "xsec_token": xsec_token,
    }

    t0 = time.time()
    r = await request(cookie, "/api/sns/web/v1/feed", "POST", data)
    elapsed = time.time() - t0

    status = r["status"]
    success = r["data"].get("success", False)
    print(f"  Status: {status} | Success: {success} | 耗时: {elapsed:.2f}s")

    if success:
        items = r["data"].get("data", {}).get("items", [])
        if items:
            note = items[0].get("note_card", {})
            print(f"  ✅ 标题: {note.get('title', '')}")
            print(f"  ✅ 作者: {note.get('user', {}).get('nickname', '')}")
            print(f"  ✅ 内容: {note.get('desc', '')[:150]}")
            return note_id
    else:
        print(f"  ❌ {json.dumps(r['data'], ensure_ascii=False)[:200]}")
    return None


async def test_comments(cookie: str, note_id: str, xsec_token: str):
    """测试 3: 评论"""
    print(f"\n{'='*50}")
    print(f"测试 3: 评论 /api/sns/web/v2/comment/page note={note_id}")
    print(f"{'='*50}")

    params = {
        "note_id": note_id,
        "cursor": "",
        "top_comment_id": "",
        "image_formats": "jpg,webp,avif",
        "xsec_token": xsec_token,
    }

    t0 = time.time()
    r = await request(cookie, "/api/sns/web/v2/comment/page", "GET", params=params)
    elapsed = time.time() - t0

    status = r["status"]
    success = r["data"].get("success", False)
    print(f"  Status: {status} | Success: {success} | 耗时: {elapsed:.2f}s")

    if success:
        comments = r["data"].get("data", {}).get("comments", [])
        print(f"  ✅ 评论数: {len(comments)}")
        for c in comments[:3]:
            user = c.get("user_info", {}).get("nickname", "")
            content = c.get("content", "")[:60]
            print(f"    [{user}] {content}")
    else:
        print(f"  ❌ {json.dumps(r['data'], ensure_ascii=False)[:200]}")


async def test_user_me(cookie: str):
    """测试 4: 用户信息"""
    print(f"\n{'='*50}")
    print("测试 4: 用户信息 /api/sns/web/v2/user/me")
    print(f"{'='*50}")

    t0 = time.time()
    r = await request(cookie, "/api/sns/web/v2/user/me", "GET", params={})
    elapsed = time.time() - t0

    status = r["status"]
    success = r["data"].get("success", False)
    print(f"  Status: {status} | Success: {success} | 耗时: {elapsed:.2f}s")

    if success:
        user = r["data"].get("data", {})
        print(f"  ✅ 昵称: {user.get('nickname', '')}")
        print(f"  ✅ 红薯ID: {user.get('red_id', '')}")
    else:
        print(f"  ❌ {json.dumps(r['data'], ensure_ascii=False)[:200]}")


async def main():
    print("🧪 xhsvm.js + curl_cffi 全接口测试\n")

    cookie = COOKIE_FILE.read_text(encoding="utf-8").strip()
    print(f"Cookie: {len(cookie)} 字符")

    # 1. 搜索
    search_result = await test_search(cookie)
    await asyncio.sleep(2)

    # 2. 详情（用搜索到的第一条，或 fallback 到固定值）
    note_id = "66cae96c000000001f01b8f5"
    xsec_token = "ABK2L_hlux1vLjSBg9UVHcX4IfblIb0go4A24tnD3TZfY="
    if search_result:
        note_id = search_result["note_id"] or note_id
        xsec_token = search_result["xsec_token"] or xsec_token

    await test_detail(cookie, note_id, xsec_token)
    await asyncio.sleep(2)

    # 3. 评论
    await test_comments(cookie, note_id, xsec_token)
    await asyncio.sleep(2)

    # 4. 用户信息
    await test_user_me(cookie)

    print(f"\n{'='*50}")
    print("全部测试完成")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
