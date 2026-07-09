# 抖音爬虫 API 使用文档

## 概述

基于 MediaCrawler 封装的抖音爬虫工具，支持 Cookie 登录、关键词搜索、作品详情、用户信息等功能。

---

## 可爬取的数据字段

### 1. 搜索结果 / 作品详情 / 用户作品列表 (通用字段)

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `aweme_id` | string | 作品唯一ID |
| `aweme_type` | string | 作品类型 (0=视频, 68=图文) |
| `title` | string | **作品标题/描述** (即 desc 字段，包含简介内容) |
| `create_time` | int | 发布时间戳 |
| `ip_location` | string | 发布IP属地 |
| `liked_count` | string | 点赞数 |
| `collected_count` | string | 收藏数 |
| `comment_count` | string | 评论数 |
| `share_count` | string | 分享数 |
| `user_id` | string | 作者用户ID |
| `sec_uid` | string | **作者安全用户ID** (用于获取用户详情) |
| `nickname` | string | 作者昵称 |
| `avatar` | string | 作者头像URL |
| `cover_url` | string | 作品封面URL |
| `video_download_url` | string | 视频下载链接 |
| `note_download_url` | string | 图文图片链接 (逗号分隔) |
| `aweme_url` | string | 作品链接 |
| `source_keyword` | string | 搜索关键词 (仅搜索结果有) |

> **注意**: `title` 字段即为作品的描述/简介内容，搜索结果中已包含，无需额外请求详情。

### 2. 用户主页信息

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `user_id` | string | 用户ID |
| `sec_uid` | string | 安全用户ID |
| `nickname` | string | 昵称 |
| `avatar` | string | 头像URL (300x300) |
| `desc` | string | **个人简介** (signature) |
| `gender` | string | 性别 (Unknown/Male/Female) |
| `ip_location` | string | IP属地 |
| `follows` | string | 关注数 |
| `fans` | string | 粉丝数 |
| `interaction` | string | 获赞数 |
| `videos_count` | string | 作品数 |

---

## 典型业务流程

```
1. 关键词搜索 → 获取作品列表 (包含 title/描述、sec_uid)
       ↓
2. 大模型判断 → 根据作品 title 判断是否为目标用户
       ↓
3. 获取用户信息 → 使用 sec_uid 获取用户主页详情
       ↓
4. 获取用户作品 → 获取该用户的更多作品列表
```

---

## API 方法

| 方法 | 说明 |
|------|------|
| `login_by_cookie_string(cookie_str)` | Cookie 字符串登录 |
| `search_videos(keyword, count, publish_time)` | 关键词搜索 |
| `get_video_detail(aweme_id)` | 获取作品详情 |
| `get_user_info(sec_uid)` | 获取用户主页信息 |
| `get_user_videos(sec_uid, count)` | 获取用户作品列表 |
| `close()` | 关闭爬虫释放资源 |

---

## 代码示例

```python
from crawler_tools.douyin_crawler import create_douyin_crawler

async def main():
    crawler = await create_douyin_crawler()
    
    # 1. Cookie 登录
    cookie_str = "your_cookie_string"
    result = await crawler.login_by_cookie_string(cookie_str)
    if not result.success:
        print(f"登录失败: {result.message}")
        return
    
    # 2. 搜索关键词
    search_result = await crawler.search_videos(keyword="Python编程", count=10)
    for item in search_result.items:
        print(f"标题/描述: {item['title']}")  # 这就是作品的描述/简介
        print(f"作者: {item['nickname']}")
        print(f"sec_uid: {item['sec_uid']}")  # 用于获取用户详情
        print(f"点赞: {item['liked_count']}")
    
    # 3. 获取用户详情 (使用搜索结果中的 sec_uid)
    sec_uid = search_result.items[0]['sec_uid']
    user_info = await crawler.get_user_info(sec_uid)
    print(f"用户昵称: {user_info['nickname']}")
    print(f"用户简介: {user_info['desc']}")
    print(f"粉丝数: {user_info['fans']}")
    
    # 4. 获取用户作品列表
    videos = await crawler.get_user_videos(sec_uid, count=20)
    for video in videos:
        print(f"作品: {video['title']}")
        print(f"点赞: {video['liked_count']}")
    
    await crawler.close()
```

---

## 测试菜单

```bash
python test_server/tests/test_douyin_crawler.py
```

```
1. Cookie 文件登录 (douyin_cookie.txt)
2. Cookie 字符串登录 (手动输入)
3. 关键词搜索
4. 获取作品详情
5. 获取用户信息
6. 获取用户作品列表
7. 运行所有测试
0. 退出
```

---

## 关键说明

1. **搜索结果已包含描述**: `title` 字段就是作品的完整描述，可直接用于大模型判断
2. **sec_uid 是关键**: 通过搜索获取 `sec_uid`，然后用它获取用户详情和作品列表
3. **每次操作独立**: 测试时每个操作都会重新登录并在完成后关闭浏览器

---

*最后更新: 2026-01-28*
