"""Crawler Tools - 爬虫工具集.

Package imports stay lazy so a lightweight module such as ``xhs_client_v2`` can
be imported without eagerly loading Playwright based crawlers.
"""

__all__ = [
    # 小红书
    "XhsCrawler",
    "XhsCrawlerConfig",
    "create_xhs_crawler",
    "xhs_quick_search",
    # 抖音
    "DouyinCrawler",
    "DouyinCrawlerConfig",
    "create_douyin_crawler",
    "douyin_quick_search",
]

_EXPORTS = {
    "XhsCrawler": (".xhs_crawler", "XhsCrawler"),
    "XhsCrawlerConfig": (".xhs_crawler", "CrawlerConfig"),
    "create_xhs_crawler": (".xhs_crawler", "create_crawler"),
    "xhs_quick_search": (".xhs_crawler", "quick_search"),
    "DouyinCrawler": (".douyin_crawler", "DouyinCrawler"),
    "DouyinCrawlerConfig": (".douyin_crawler", "DouyinCrawlerConfig"),
    "create_douyin_crawler": (".douyin_crawler", "create_douyin_crawler"),
    "douyin_quick_search": (".douyin_crawler", "quick_search"),
}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(name)
    from importlib import import_module

    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value
