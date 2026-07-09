# 头像提取功能说明

## 功能概述

在截屏用户主页时，同时提取用户头像链接，无需额外的浏览器会话。

## 实现方式

### 单次浏览器会话流程

```
打开浏览器 → 访问页面 → 提取头像 → 截图 → 关闭浏览器
```

所有操作在 **一次** 浏览器会话中完成，不会重复打开浏览器。

### 核心函数

#### `screenshot_user_profile(user_url, db)`

**功能**: 访问小红书用户主页，截屏并提取头像链接

**返回值**:
```python
{
    "screenshots": [
        {"base64": "...", "format": "png"},
        {"base64": "...", "format": "png"}
    ],
    "avatar_url": "https://sns-avatar-qc.xhscdn.com/avatar/xxx.jpg",
    "error": None
}
```

**实现细节**:
1. 从数据库获取 Cookie
2. 启动 Playwright 浏览器（单次会话）
3. 访问用户主页
4. 使用多个 CSS 选择器提取头像链接:
   - `img[class*="avatar"]`
   - `img[class*="Avatar"]`
   - `img[src*="avatar"]`
   - `img[alt*="头像"]`
5. 截取 2 张截图（首屏 + 滚动后）
6. 关闭浏览器

### 头像 URL 格式

小红书头像 CDN 格式:
```
https://sns-avatar-qc.xhscdn.com/avatar/{hash}.jpg?imageView2/2/w/80/format/jpg
```

## 使用示例

### 测试脚本

```bash
cd test_server/tests
python test_screenshot_avatar.py
```

选择选项 1 进行完整测试。

### 在 Pipeline 中使用

```python
from api.services.xhs_vision_tools import get_user_profile_vision_analysis

result = await get_user_profile_vision_analysis(
    user_url="https://www.xiaohongshu.com/user/profile/xxx",
    db=db,
    save_files=True
)

# 获取头像链接
avatar_url = result["avatar_url"]
```

## 优势

1. **高效**: 单次浏览器会话完成所有操作
2. **准确**: 从实际页面 DOM 提取，确保链接有效
3. **无需爬虫**: 不依赖 API，直接从页面获取
4. **格式验证**: 自动验证是否符合小红书 CDN 格式

## 注意事项

1. 需要有效的 Cookie（从数据库获取）
2. 头像链接可能包含参数（如 `imageView2`），这是正常的
3. 如果页面未找到头像元素，`avatar_url` 为 `None`
