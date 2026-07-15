# 百炼 AIGC API 使用教程

本文档覆盖当前系统中阿里云百炼图片编辑、文生视频、图生视频和任务轮询的配置与调用方式。所有接口都需要登录后的 Bearer Token。

参考的阿里云官方文档：

- Qwen Image Edit: https://help.aliyun.com/zh/model-studio/qwen-image-edit-api
- 万相 2.7 文生视频: https://help.aliyun.com/zh/model-studio/text-to-video-api-reference
- 万相 2.7 图生视频: https://www.alibabacloud.com/help/zh/model-studio/image-to-video-general-api-reference

## 1. 前端配置

进入前端系统配置页，打开“运行配置”页签，编辑 `bailian` 配置段。敏感字段会写入 MongoDB 并加密保存，接口返回时自动脱敏。

```json
{
  "api_key": "sk-your-bailian-key",
  "workspace_id": "your-workspace-id",
  "region": "beijing",
  "qwen_image_edit_model": "qwen-image-2.0-pro",
  "wanx_image_edit_model": "wanx2.1-imageedit",
  "text_to_video_model": "wan2.7-t2v-2026-06-12",
  "image_to_video_model": "wan2.7-i2v-2026-04-25",
  "timeout_seconds": 300
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `api_key` | 百炼 API Key，必填，加密保存 |
| `workspace_id` | 百炼业务空间 ID；新版协议推荐使用 Workspace 专属域名 |
| `region` | `beijing`、`singapore`、`frankfurt`，也支持 `cn-beijing`、`ap-southeast-1` 等别名 |
| `base_url` | 可选，覆盖 Workspace API 根域名；支持完整 URL 或裸域名，并自动补 `https://` 和 `/api/v1` |
| `legacy_base_url` | 可选，覆盖旧 DashScope API 根域名；北京默认 `https://dashscope.aliyuncs.com/api/v1` |
| `qwen_image_edit_model` | Qwen 图片指令编辑模型 |
| `wanx_image_edit_model` | 万相异步图片编辑模型 |
| `text_to_video_model` | 万相 2.7 文生视频模型 |
| `image_to_video_model` | 万相 2.7 图生视频模型 |
| `timeout_seconds` | HTTP 请求超时时间 |

也可以直接用配置 API 写入：

```bash
curl -k -X POST https://127.0.0.1/api/v1/config/sections/bailian \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "api_key": "sk-your-bailian-key",
      "workspace_id": "your-workspace-id",
      "region": "beijing",
      "qwen_image_edit_model": "qwen-image-2.0-pro",
      "wanx_image_edit_model": "wanx2.1-imageedit",
      "text_to_video_model": "wan2.7-t2v-2026-06-12",
      "image_to_video_model": "wan2.7-i2v-2026-04-25"
    }
  }'
```

## 2. 配置状态

```http
GET /api/v1/aigc/config
```

返回当前百炼配置状态，不返回明文 API Key：

```json
{
  "provider": "aliyun_bailian",
  "configured": true,
  "region": "beijing",
  "has_api_key": true,
  "has_workspace_id": true,
  "qwen_image_edit_model": "qwen-image-2.0-pro",
  "wanx_image_edit_model": "wanx2.1-imageedit",
  "text_to_video_model": "wan2.7-t2v-2026-06-12",
  "image_to_video_model": "wan2.7-i2v-2026-04-25"
}
```

## 3. Qwen 图片指令编辑

Qwen 图片编辑是同步接口，系统调用百炼 `multimodal-generation/generation`。输入图像可以是公网 URL 或 data URL；公网 URL 必须能被阿里云服务端直接访问。

```http
POST /api/v1/aigc/images/qwen-edit
Content-Type: application/json
```

```json
{
  "images": ["https://example.com/input.png"],
  "prompt": "把背景改成清晨的办公室，保留主体人物。",
  "parameters": {
    "n": 1,
    "watermark": false,
    "prompt_extend": true
  }
}
```

响应中 `images` 是解析出的结果图 URL，`response` 保留百炼原始响应：

```json
{
  "ok": true,
  "provider": "aliyun_bailian",
  "mode": "qwen_image_edit",
  "model": "qwen-image-2.0-pro",
  "images": ["https://..."],
  "response": {}
}
```

## 4. 万相异步图片编辑

```http
POST /api/v1/aigc/images/wanx-edit
Content-Type: application/json
```

```json
{
  "base_image_url": "https://example.com/input.png",
  "prompt": "把天空改成黄昏，增强产品质感。",
  "function": "description_edit",
  "parameters": {
    "n": 1
  }
}
```

返回 `task_id` 后，用 `/api/v1/aigc/tasks/{task_id}` 轮询。

## 5. 万相 2.7 文生视频

文生视频是异步任务。官方要求 `X-DashScope-Async: enable`，后端已经自动添加该请求头。

```http
POST /api/v1/aigc/videos/text-to-video
Content-Type: application/json
```

```json
{
  "prompt": "一个商务产品发布会现场，镜头缓慢推进，画面干净真实。",
  "negative_prompt": "低清晰度、畸形文字",
  "parameters": {
    "resolution": "720P",
    "ratio": "16:9",
    "duration": 5,
    "prompt_extend": true,
    "watermark": false
  }
}
```

返回：

```json
{
  "ok": true,
  "provider": "aliyun_bailian",
  "mode": "text_to_video",
  "model": "wan2.7-t2v-2026-06-12",
  "task_protocol": "workspace",
  "task_id": "0385dc79-...",
  "task_status": "PENDING"
}
```

## 6. 万相 2.7 图生视频

新版 Wan2.7 图生视频使用 `media` 数组。前端页面已经支持直接填写 `media JSON`；后端也兼容简化字段 `img_url`、`last_frame_url`、`first_clip_url`、`audio_url`。

首帧生视频：

```http
POST /api/v1/aigc/videos/image-to-video
Content-Type: application/json
```

```json
{
  "prompt": "产品照片变成 5 秒展示视频，镜头缓慢环绕。",
  "media": [
    {
      "type": "first_frame",
      "url": "https://example.com/product.png"
    }
  ],
  "parameters": {
    "resolution": "720P",
    "duration": 5,
    "prompt_extend": true,
    "watermark": false
  }
}
```

首尾帧生视频：

```json
{
  "prompt": "从首帧自然过渡到尾帧。",
  "img_url": "https://example.com/first.png",
  "last_frame_url": "https://example.com/last.png"
}
```

带驱动音频：

```json
{
  "prompt": "人物根据音频自然说话。",
  "media": [
    { "type": "first_frame", "url": "https://example.com/person.png" },
    { "type": "driving_audio", "url": "https://example.com/audio.mp3" }
  ],
  "parameters": {
    "resolution": "720P",
    "duration": 10
  }
}
```

旧模型兼容：如果模型名以 `wan2.6`、`wan2.5` 或 `wanx2.1` 开头，后端会自动使用旧 `img_url` payload；也可以显式传 `protocol: "legacy"`。

## 7. 轮询任务

```http
GET /api/v1/aigc/tasks/{task_id}?protocol=workspace
```

`protocol` 可选值：

| 值 | 说明 |
| --- | --- |
| `workspace` | 使用 Workspace 专属域名查询，默认值 |
| `legacy` | 使用 DashScope 旧域名查询 |
| `auto` | 有 `workspace_id` 或 `base_url` 时走 Workspace，否则走旧域名 |

成功后，响应会尽量提取 `result_urls`、`video_url` 和 `task_status`：

```json
{
  "ok": true,
  "provider": "aliyun_bailian",
  "task_protocol": "workspace",
  "task_id": "0385dc79-...",
  "task_status": "SUCCEEDED",
  "result_urls": ["https://..."],
  "video_url": "https://...",
  "response": {}
}
```

## 8. 前端入口

前端“AI 工具”页已经集成：

- 图片工具：Qwen 图片编辑、万相异步图片编辑
- 视频工具：万相 2.7 文生视频、图生视频、任务轮询
- JSON 参数：`parameters`、Wan2.7 `media`、万相图片编辑 `extra_input`

运行时先确认 `/api/v1/aigc/config` 返回 `configured: true`，再提交生成任务。
