"""
测试: 用 xhs-mcp 的方案（curl_cffi + xhsvm.js 签名）调笔记详情接口

对比 httpx vs curl_cffi，验证 TLS 指纹是否是 406 的根因

用法:
    pip install curl_cffi PyExecJS
    python test_server/tests/test_curl_cffi_detail.py
"""
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

COOKIE_FILE = Path(__file__).parent / "xhs_cookie.txt"
XHSVM_JS = _root / "xhs-mcp-main" / "xhs_mcp" / "api" / "xhsvm.js"

NOTE_ID = "66cae96c000000001f01b8f5"
XSEC_TOKEN = "ABK2L_hlux1vLjSBg9UVHcX4IfblIb0go4A24tnD3TZfY="


def parse_cookie(cookie: str) -> dict:
    d = {}
    for item in cookie.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def get_xs_xt(uri: str, data: dict, cookie: str) -> dict:
    """用 xhsvm.js 本地算签名"""
    import execjs
    js_path = str(XHSVM_JS)
    with open(js_path, "r", encoding="utf-8") as f:
        js_code = f.read()
    result = execjs.compile(js_code).call("GetXsXt", uri, data, cookie)
    return json.loads(result)


async def test_curl_cffi():
    """用 curl_cffi 调详情接口"""
    from curl_cffi.requests import AsyncSession

    cookie_string = COOKIE_FILE.read_text(encoding="utf-8").strip()
    print(f"Cookie: {len(cookie_string)} 字符")

    data = {
        "source_note_id": NOTE_ID,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": "1"},
        "xsec_source": "pc_feed",
        "xsec_token": XSEC_TOKEN,
    }
    uri = "/api/sns/web/v1/feed"

    # 用 xhsvm.js 算签名
    print("计算签名...")
    xsxt = get_xs_xt(uri, data, cookie_string)
    print(f"  X-s: {xsxt['X-s'][:50]}...")
    print(f"  X-t: {xsxt['X-t']}")

    headers = {
        "content-type": "application/json;charset=UTF-8",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "x-s": xsxt["X-s"],
        "x-t": str(xsxt["X-t"]),
    }

    url = f"https://edith.xiaohongshu.com{uri}"

    print(f"\n{'='*50}")
    print(f"curl_cffi (impersonate=chrome124)")
    print(f"{'='*50}")

    async with AsyncSession(verify=True, impersonate="chrome124") as session:
        t0 = time.time()
        resp = await session.request(
            method="POST",
            url=url,
            json=data,
            cookies=parse_cookie(cookie_string),
            headers=headers,
            stream=True,
        )
        content = await resp.acontent()
        elapsed = time.time() - t0

        result = json.loads(content)
        status = resp.status_code
        success = result.get("success", False)

        print(f"  Status: {status}")
        print(f"  Success: {success}")
        print(f"  耗时: {elapsed:.2f}s")

        if success and result.get("data", {}).get("items"):
            note = result["data"]["items"][0].get("note_card", {})
            print(f"  ✅ 标题: {note.get('title', '')}")
            print(f"  ✅ 作者: {note.get('user', {}).get('nickname', '')}")
            print(f"  ✅ 内容: {note.get('desc', '')[:100]}")
        else:
            print(f"  ❌ 响应: {content[:200]}")


async def test_httpx():
    """用 httpx 调同样的接口（对照组）"""
    import httpx

    cookie_string = COOKIE_FILE.read_text(encoding="utf-8").strip()

    data = {
        "source_note_id": NOTE_ID,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": "1"},
        "xsec_source": "pc_feed",
        "xsec_token": XSEC_TOKEN,
    }
    uri = "/api/sns/web/v1/feed"

    xsxt = get_xs_xt(uri, data, cookie_string)

    headers = {
        "content-type": "application/json;charset=UTF-8",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "x-s": xsxt["X-s"],
        "x-t": str(xsxt["X-t"]),
        "Cookie": cookie_string,
    }

    url = f"https://edith.xiaohongshu.com{uri}"

    print(f"\n{'='*50}")
    print(f"httpx (对照组)")
    print(f"{'='*50}")

    async with httpx.AsyncClient() as client:
        t0 = time.time()
        resp = await client.post(url, json=data, headers=headers, timeout=10)
        elapsed = time.time() - t0

        result = resp.json()
        print(f"  Status: {resp.status_code}")
        print(f"  Success: {result.get('success', False)}")
        print(f"  耗时: {elapsed:.2f}s")

        if result.get("success") and result.get("data", {}).get("items"):
            note = result["data"]["items"][0].get("note_card", {})
            print(f"  ✅ 标题: {note.get('title', '')}")
            print(f"  ✅ 作者: {note.get('user', {}).get('nickname', '')}")
            print(f"  ✅ 内容: {note.get('desc', '')[:200]}")
        else:
            print(f"  ❌ 响应: {resp.text[:200]}")


async def main():
    print("🧪 XHS 详情接口: curl_cffi vs httpx\n")

    if not XHSVM_JS.exists():
        print(f"❌ 找不到 {XHSVM_JS}")
        return

    await test_curl_cffi()
    await asyncio.sleep(2)
    await test_httpx()


if __name__ == "__main__":
    asyncio.run(main())
