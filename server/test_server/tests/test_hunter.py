"""
Hunter API 和 URL 探活测试
"""
import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from crawler_tools.hunter_tools import (
    search_hunter,
    probe_url,
    probe_urls_batch,
    search_and_probe,
    search_by_domain,
    search_by_company,
)


async def test_probe_single():
    """测试单个 URL 探活"""
    print("\n" + "=" * 50)
    print("测试单个 URL 探活")
    print("=" * 50)
    
    urls = [
        "https://www.bilibili.com",
        "https://www.baidu.com",
        "http://not-exist-domain-12345.com",
    ]
    
    for url in urls:
        result = await probe_url(url, timeout=5)
        print(f"\n{url}")
        print(f"  存活: {result.is_alive}")
        print(f"  状态码: {result.status_code}")
        print(f"  标题: {result.title}")
        print(f"  响应时间: {result.response_time}s")
        if result.error:
            print(f"  错误: {result.error}")


async def test_probe_batch():
    """测试批量 URL 探活"""
    print("\n" + "=" * 50)
    print("测试批量 URL 探活")
    print("=" * 50)
    
    urls = [
        "https://www.bilibili.com",
        "https://www.baidu.com",
        "https://www.qq.com",
        "https://www.taobao.com",
        "https://www.jd.com",
        "http://not-exist-1.com",
        "http://not-exist-2.com",
    ]
    
    results = await probe_urls_batch(urls, concurrency=5, timeout=5, only_alive=False)
    
    print(f"\n总计: {len(results)} 个")
    print(f"存活: {sum(1 for r in results if r.is_alive)} 个")
    
    for r in results:
        status = "✓" if r.is_alive else "✗"
        print(f"  {status} {r.url} - {r.status_code or r.error}")


async def test_hunter_search():
    """测试 Hunter 查询（需要配置 API Key）"""
    print("\n" + "=" * 50)
    print("测试 Hunter 查询")
    print("=" * 50)
    
    # 测试域名查询
    print("\n1. 域名查询: bilibili.com")
    results = await search_hunter("bilibili.com", search_type="domain", size=10)
    
    if not results:
        print("  无结果（可能 API Key 未配置）")
    else:
        print(f"  获取 {len(results)} 条结果")
        for r in results[:5]:
            print(f"    - {r.url} | {r.web_title[:30] if r.web_title else 'N/A'}")


async def test_search_and_probe():
    """测试 Hunter 查询 + 探活"""
    print("\n" + "=" * 50)
    print("测试 Hunter 查询 + 探活")
    print("=" * 50)
    
    # 测试域名查询
    print("\n查询域名: bilibili.com")
    results = await search_and_probe("bilibili.com", search_type="domain", size=20)
    
    if not results:
        print("  无结果（可能 API Key 未配置）")
    else:
        print(f"  存活 URL: {len(results)} 个")
        for r in results[:10]:
            print(f"    - {r['url']}")
            print(f"      标题: {r.get('title', 'N/A')[:50] if r.get('title') else 'N/A'}")
            print(f"      IP: {r.get('ip', 'N/A')} | 响应时间: {r.get('response_time', 'N/A')}s")


async def main():
    """主函数"""
    print("Hunter API 和 URL 探活测试")
    print("=" * 50)
    
    # 1. 测试单个 URL 探活
    await test_probe_single()
    
    # 2. 测试批量 URL 探活
    await test_probe_batch()
    
    # 3. 测试 Hunter 查询（需要 API Key）
    # await test_hunter_search()
    
    # 4. 测试 Hunter + 探活（需要 API Key）
    # await test_search_and_probe()
    
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
