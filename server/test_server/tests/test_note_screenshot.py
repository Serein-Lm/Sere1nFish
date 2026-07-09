"""
测试笔记详情截屏 — 串行 vs 并发（共享容器）对比

用法:
    python test_server/tests/test_note_screenshot.py
"""
import asyncio
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

COOKIE_FILE = Path(__file__).parent / "xhs_cookie.txt"

NOTES = [
    {
        "note_id": "66cae96c000000001f01b8f5",
        "xsec_token": "ABK2L_hlux1vLjSBg9UVHcX4IfblIb0go4A24tnD3TZfY=",
    },
    {
        "note_id": "687a0b43000000000d01b1e5",
        "xsec_token": "ABsBt0SNnEGj-WHlwZZYffIBKRnavMnlN9wTKhg3Tq7hQ=",
    },
]


def _get_container_memory(provider, task_id: str) -> str:
    try:
        if not hasattr(provider, 'task_map') or not hasattr(provider, 'containers'):
            return "N/A"
        cid = provider.task_map.get(task_id)
        if not cid:
            return "N/A"
        client = provider._get_docker_client()
        container = client.containers.get(cid)
        stats = container.stats(stream=False)
        mem_usage = stats.get("memory_stats", {}).get("usage", 0)
        mem_limit = stats.get("memory_stats", {}).get("limit", 1)
        return f"{mem_usage / 1024 / 1024:.0f}MB / {mem_limit / 1024 / 1024:.0f}MB"
    except Exception as e:
        return f"获取失败: {e}"


async def main():
    from api.services.xhs_vision_tools import screenshot_note_detail, save_screenshots_to_files
    from browser_manager.provider import get_browser_provider

    cookie_string = ""
    if COOKIE_FILE.exists():
        cookie_string = COOKIE_FILE.read_text(encoding="utf-8").strip()
        print(f"✅ Cookie 已加载 ({len(cookie_string)} 字符)")
    else:
        print("⚠️ 未找到 xhs_cookie.txt")

    provider = get_browser_provider()
    task_id = "test-screenshot-bench"
    out_dir = str(_root / "data" / "test_note_screenshots")

    # 申请一个容器，两个测试共用
    print(f"\n{'='*60}")
    print("申请 Chrome 容器...")
    cdp_endpoint = await provider.get_cdp_endpoint(task_id=task_id, purpose="xhs_screenshot")
    if not cdp_endpoint:
        print("❌ 无法获取容器")
        return
    print(f"  CDP: {cdp_endpoint}")

    async def _do_screenshot(note, idx, prefix):
        t0 = time.time()
        result = await screenshot_note_detail(
            note_id=note["note_id"],
            xsec_token=note["xsec_token"],
            cookie_string=cookie_string,
            cdp_endpoint=cdp_endpoint,
        )
        elapsed = time.time() - t0
        screenshots = result.get("screenshots", [])
        error = result.get("error")
        status = f"截图={len(screenshots)} 耗时={elapsed:.1f}s" + (f" 错误={error}" if error else "")
        print(f"    [{prefix}笔记{idx+1}] {note['note_id'][:12]}... {status}")
        if screenshots:
            save_screenshots_to_files(screenshots, f"{prefix}_{idx}_{note['note_id']}", output_dir=out_dir)
        return {"ok": len(screenshots) > 0, "elapsed": elapsed, "count": len(screenshots)}

    # ── 测试 A: 串行（复用同一个容器连接）──
    print(f"\n{'='*60}")
    print("测试 A: 串行截屏（复用容器）")
    print(f"  内存(开始): {_get_container_memory(provider, task_id)}")

    serial_results = []
    t_serial_start = time.time()
    for i, note in enumerate(NOTES):
        r = await _do_screenshot(note, i, "serial")
        serial_results.append(r)
    t_serial = time.time() - t_serial_start

    mem_after_serial = _get_container_memory(provider, task_id)
    print(f"  内存(串行后): {mem_after_serial}")
    print(f"  串行总耗时: {t_serial:.1f}s")

    # ── 测试 B: 并发（同一个容器，2 个 context 同时跑）──
    print(f"\n{'='*60}")
    print("测试 B: 并发截屏（共享容器，2 个 context）")
    print(f"  内存(开始): {_get_container_memory(provider, task_id)}")

    t_concurrent_start = time.time()
    concurrent_raw = await asyncio.gather(
        *[_do_screenshot(note, i, "concurrent") for i, note in enumerate(NOTES)],
        return_exceptions=True,
    )
    t_concurrent = time.time() - t_concurrent_start

    concurrent_results = []
    for r in concurrent_raw:
        if isinstance(r, Exception):
            print(f"    ❌ 异常: {r}")
            concurrent_results.append({"ok": False, "elapsed": 0, "count": 0})
        else:
            concurrent_results.append(r)

    mem_after_concurrent = _get_container_memory(provider, task_id)
    print(f"  内存(并发后): {mem_after_concurrent}")
    print(f"  并发总耗时: {t_concurrent:.1f}s")

    # ── 释放容器 ──
    await provider.release_cdp_endpoint(task_id=task_id)
    print(f"\n  容器已释放")

    # ── 对比 ──
    print(f"\n{'='*60}")
    print("对比结果")
    print(f"{'='*60}")
    print(f"  串行: 总耗时={t_serial:.1f}s  内存={mem_after_serial}")
    for i, r in enumerate(serial_results):
        print(f"    笔记{i+1}: 截图={r['count']} 耗时={r['elapsed']:.1f}s {'✅' if r['ok'] else '❌'}")
    print(f"  并发: 总耗时={t_concurrent:.1f}s  内存={mem_after_concurrent}")
    for i, r in enumerate(concurrent_results):
        print(f"    笔记{i+1}: 截图={r['count']} 耗时={r['elapsed']:.1f}s {'✅' if r['ok'] else '❌'}")
    speedup = t_serial / t_concurrent if t_concurrent > 0 else 0
    print(f"\n  加速比: {speedup:.2f}x")
    print(f"  截图目录: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
