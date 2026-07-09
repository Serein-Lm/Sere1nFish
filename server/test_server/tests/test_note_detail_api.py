"""
单测: XHS 笔记详情 API — 用 xhsvm.js 本地签名

用法:
    python test_server/tests/test_note_detail_api.py
"""
import asyncio
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

# 测试笔记
NOTES = [
    {"note_id": "66cae96c000000001f01b8f5", "xsec_token": "ABK2L_hlux1vLjSBg9UVHcX4IfblIb0go4A24tnD3TZfY="},
    {"note_id": "687a0b43000000000d01b1e5", "xsec_token": "ABsBt0SNnEGj-WHlwZZYffIBKRnavMnlN9wTKhg3Tq7hQ="},
]


def get_xs_xt(uri: str, data: dict, cookie: str) -> dict:
    import execjs
    with open(str(XHSVM_JS), "r", encoding="utf-8") as f:
        js = f.read()
    return json.loads(execjs.compile(js).call("GetXsXt", uri, data, cookie))


async def get_note_detail(cookie: str, note_id: str, xsec_token: str) -> dict:
    """用 xhsvm.js 签名 + httpx 获取笔记详情"""
    import httpx

    data = {
        "source_note_id": note_id,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": "1"},
        "xsec_source": "pc_feed",
        "xsec_token": xsec_token,
    }
    uri = "/api/sns/web/v1/feed"

    xsxt = get_xs_xt(uri, data, cookie)

    headers = {
        "content-type": "application/json;charset=UTF-8",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "x-s": xsxt["X-s"],
        "x-t": str(xsxt["X-t"]),
        "Cookie": cookie,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://edith.xiaohongshu.com{uri}",
            json=data, headers=headers, timeout=10,
        )

    result = resp.json()
    if result.get("success") and result.get("data", {}).get("items"):
        return result["data"]["items"][0].get("note_card", {})
    return {"_error": resp.status_code, "_body": resp.text[:200]}


async def main():
    cookie = COOKIE_FILE.read_text(encoding="utf-8").strip()
    print(f"Cookie: {len(cookie)} 字符\n")

    for i, note in enumerate(NOTES, 1):
        nid = note["note_id"]
        print(f"[{i}/{len(NOTES)}] note={nid}")

        t0 = time.time()
        detail = await get_note_detail(cookie, nid, note["xsec_token"])
        elapsed = time.time() - t0

        if "_error" in detail:
            print(f"  ❌ Status={detail['_error']} | {detail['_body']}")
        else:
            print(f"  ✅ 耗时={elapsed:.2f}s")
            print(f"  标题: {detail.get('title', '')}")
            print(f"  作者: {detail.get('user', {}).get('nickname', '')}")
            print(f"  内容: {detail.get('desc', '')[:200]}")
            print(f"  点赞: {detail.get('interact_info', {}).get('liked_count', '')}")
            print(f"  图片数: {len(detail.get('image_list', []))}")
            print(f"  评论数: {detail.get('interact_info', {}).get('comment_count', '')}")
        print()

        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
