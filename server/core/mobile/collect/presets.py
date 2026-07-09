"""采集任务预设模板 — 三大场景的配置化实例。

均为 CollectTaskDef 配置(不硬编码执行逻辑),用于前端一键创建 + 验证通用框架覆盖:
- 微信养号: 定时打开微信浏览, 轻量摘要记录;
- 小红书搜索采集: 关键词搜索 → 截屏 → 结构化 → 增量入库;
- 微信公众号搜索入库分析: 公众号搜索 → 截屏滑动 → 结构化 → 增量入库 → 每日增量通知。
"""
from __future__ import annotations

from typing import Any


PRESETS: list[dict[str, Any]] = [
    {
        "preset_id": "wechat_nurture",
        "title": "微信养号(定时)",
        "description": "定时打开微信浏览首页/朋友圈, 保持账号活跃, 记录画面轻量摘要。",
        "task": {
            "name": "微信养号",
            "app_name": "微信",
            "keywords": [],
            "swipe_times": 5,
            "swipe_interval": 2.0,
            "extract_fields": [],
            "dedup_key_fields": [],
            "notify_on": "none",
            "search_hint": "浏览微信首页与朋友圈",
        },
        "suggested_trigger": {"type": "cron", "cron": "0 9,20 * * *"},
    },
    {
        "preset_id": "xhs_search",
        "title": "小红书搜索采集",
        "description": "按关键词搜索小红书, 截屏识别笔记, 结构化入库。",
        "task": {
            "name": "小红书搜索采集",
            "app_name": "小红书",
            "keywords": ["示例关键词"],
            "swipe_times": 4,
            "swipe_interval": 1.5,
            "extract_fields": [
                {"name": "title", "description": "笔记标题", "type": "string"},
                {"name": "author", "description": "作者昵称", "type": "string"},
                {"name": "content", "description": "正文摘要/首段内容", "type": "string"},
                {"name": "note_type", "description": "笔记类型(图文/视频)", "type": "string"},
                {"name": "likes", "description": "点赞数", "type": "string"},
                {"name": "collects", "description": "收藏数", "type": "string"},
                {"name": "comments", "description": "评论数", "type": "string"},
                {"name": "tags", "description": "话题标签", "type": "list"},
                {"name": "publish_time", "description": "发布时间/位置信息", "type": "string"},
            ],
            "dedup_key_fields": ["title", "author"],
            "notify_on": "new",
            "search_hint": "",
        },
        "suggested_trigger": {"type": "interval", "interval_seconds": 3600},
    },
    {
        "preset_id": "wechat_official",
        "title": "微信公众号搜索入库分析",
        "description": "搜索微信公众号文章, 截屏滑动收集, 结构化分析入库, 每日增量监控通知。",
        "task": {
            "name": "公众号搜索入库分析",
            "app_name": "微信",
            "keywords": ["示例公司"],
            "swipe_times": 6,
            "swipe_interval": 1.8,
            "extract_fields": [
                {"name": "title", "description": "文章标题", "type": "string"},
                {"name": "account", "description": "公众号名称", "type": "string"},
                {"name": "author", "description": "作者/署名", "type": "string"},
                {"name": "publish_time", "description": "发布时间", "type": "string"},
                {"name": "summary", "description": "内容摘要/首段", "type": "string"},
                {"name": "contact", "description": "文中出现的联系方式(电话/座机/邮箱/微信号/联系人)", "type": "string"},
                {"name": "background", "description": "项目背景/建设内容/招标范围等背景信息", "type": "string"},
                {"name": "read_count", "description": "阅读量", "type": "string"},
                {"name": "like_count", "description": "点赞/在看数", "type": "string"},
                {"name": "digest_source", "description": "来源/原创标识", "type": "string"},
                {"name": "keyword_hit", "description": "命中的搜索关键词", "type": "string"},
            ],
            "dedup_key_fields": ["title", "account"],
            "notify_on": "both",
            "search_hint": "在微信顶部搜索框输入关键词, 选择公众号/文章结果",
            "deep_collect": True,
            "detail_max_items": 5,
            "detail_max_swipes": 12,
            "min_score_to_detail": 60,
            "min_subject_match": 70,
            "min_score_to_persist": 0,
        },
        "suggested_trigger": {"type": "cron", "cron": "0 8 * * *"},
    },
]
