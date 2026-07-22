# Sere1nFish 运行与验收教程

本文档按当前服务器 `/root/Sere1nFish` 的真实运行方式编写，用于部署、配置、验证和远程手机接入。后端源码在 `server/`，前端源码在 `view/`，运行时由根目录 `docker-compose.yml` 编排。

## 1. 端口和安全组

公网入站只开放：

| 协议 | 端口 | 用途 |
| --- | --- | --- |
| TCP | `443` | Web 前端、后端 API、登录后 APK 下载 |
| TCP | `11010` | EasyTier TCP peer |
| UDP | `11010` | EasyTier UDP peer |
| TCP | `11011` | EasyTier WebSocket peer |
| TCP | `11012` | EasyTier WSS peer |
| UDP | `11013` | EasyTier WireGuard peer |

云厂商支持端口段时可填：

```text
TCP: 443
TCP: 11010-11012
UDP: 11010
UDP: 11013
```

不要开放到公网：

```text
80
5555
8000
5173
27017
6379
9222
5900
6080
```

说明：

- `5555` 是手机 ADB 端口，只允许 EasyTier 虚拟网络内部访问。
- 多台手机和多台 Agent 机器接入时，服务端入站端口仍是这组固定端口，不按设备数量增加。
- 客户端侧只需要能出站访问服务端 `11010-11013`。

## 2. 启动与健康检查

当前服务器的运行时文件：

```text
/root/Sere1nFish/docker-compose.yml
/root/Sere1nFish/.env
/root/Sere1nFish/nginx/
/root/Sere1nFish/downloads/
```

启动：

```bash
cd /root/Sere1nFish
docker-compose -f docker-compose.yml up -d
```

检查 Compose 配置：

```bash
docker-compose -f /root/Sere1nFish/docker-compose.yml config
```

检查服务健康：

```bash
curl -k https://127.0.0.1/health
```

预期：

```json
{"status":"ok","mongodb":{"ok":true}}
```

查看容器：

```bash
docker-compose -f /root/Sere1nFish/docker-compose.yml ps
```

## 3. 前端登录和配置

访问：

```text
https://<服务器公网 IP 或域名>/
```

登录后进入“配置管理”。后续业务运行配置都从前端写入 MongoDB，加密字段由后端 DAO 层加密保存，读接口只返回脱敏值。

不要再使用本地 `config.json`：

- `POST /api/v1/config/import` 已固定返回 `410 Gone`。
- `scripts/sync_config.py` 已下线，只保留失败提示。
- 图工作流显式传入本地配置路径会报错，避免重新走文件配置。
- 运行目录中的 `server/config.json` 已删除；`server/config.example.json` 只保留废弃提示，不再作为配置模板。

常用配置段：

| 配置段 | 用途 |
| --- | --- |
| `llm` | 默认文本模型、视觉模型、手机 Agent 模型 |
| `runtime` | 运行时模型网关等兼容配置 |
| `bailian` | 百炼图片编辑、文生视频、图生视频 |
| `cosyvoice` | 百炼 CosyVoice / TTS |
| `mobile` | 手机 Agent 规划、执行、读屏、画像相关配置 |
| `chrome_docker` | 后端扫描用 Chrome Docker 配置 |
| `tools` / `mcpServers` | 工具和 MCP 配置 |

## 4. 百炼图片和视频

百炼 AIGC 配置段示例：

```json
{
  "api_key": "sk-your-bailian-key",
  "workspace_id": "your-workspace-id",
  "region": "beijing",
  "qwen_image_edit_model": "qwen-image-3.0-pro",
  "wanx_image_edit_model": "wanx2.1-imageedit",
  "text_to_video_model": "wan2.7-t2v-2026-06-12",
  "image_to_video_model": "wan2.7-i2v-2026-04-25",
  "timeout_seconds": 300
}
```

前端入口：

```text
AI 工具 -> 百炼图像工具
AI 工具 -> 百炼视频工具
```

后端接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/aigc/config` | 配置状态 |
| POST | `/api/v1/aigc/images/qwen-edit` | Qwen 图片指令编辑 |
| POST | `/api/v1/aigc/images/wanx-edit` | 万相异步图片编辑 |
| POST | `/api/v1/aigc/videos/text-to-video` | 万相 2.7 文生视频 |
| POST | `/api/v1/aigc/videos/image-to-video` | 万相 2.7 图生视频 |
| GET | `/api/v1/aigc/tasks/{task_id}` | 异步任务轮询 |

图生视频新版协议使用 `media` 数组，例如：

```json
{
  "prompt": "产品照片变成 5 秒展示视频，镜头缓慢环绕。",
  "media": [
    { "type": "first_frame", "url": "https://example.com/product.png" }
  ],
  "parameters": {
    "resolution": "720P",
    "duration": 5,
    "prompt_extend": true,
    "watermark": false
  }
}
```

详细教程见：

```text
server/docs/BAILIAN_AIGC_API.md
```

## 5. TTS / 音色克隆

CosyVoice 配置从前端“运行配置”写入 `cosyvoice` 配置段。上传本地音频后，后端会根据反向代理 `Host` 和 `X-Forwarded-Proto` 生成公网绝对 URL，供百炼服务端拉取。

常用接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/voice/uploads` | 上传音频，返回公网 URL |
| POST | `/api/v1/voice/voices` | 创建/登记音色 |
| POST | `/api/v1/voice/synthesize` | 合成语音 |

详细教程见：

```text
server/docs/VOICE_CLONE_API.md
```

## 6. Token 观测和日志

Token 观测和运行日志使用内存环形缓存，不写 MongoDB，避免长期任务把数据库打爆。

可用入口：

| 页面/API | 说明 |
| --- | --- |
| 前端“观测中心” | 全局 token、轮次、Agent、项目维度统计 |
| `/api/v1/observability/turns` | 每个轮次和每次模型调用的细粒度 token 明细 |
| `/api/v1/observability/logs` | 内存运行日志 |
| `/api/v1/stats/global` | 旧统计入口，兼容前端 |
| `/api/v1/stats/project/{id}` | 项目统计 |
| `/api/v1/stats/task/{task_id}` | 任务统计 |

注意：

- 重启后内存观测数据会清空。
- MongoDB 只保存业务数据、配置和必要元数据，不保存 token 调用流水和运行日志全文。
- 手机读屏/聊天建议流在截图已保存时只返回 `screenshot_id` 和 `screenshot_url`，不在 SSE 事件里继续推送截图 base64。

详细接口见：

```text
server/docs/OBSERVABILITY_API.md
```

## 7. Dashboard 和前端验证

前端 Dashboard 已接入更丰富的项目、任务、数据源、Token 和趋势图表。前端变更后必须使用 Chrome DevTools MCP 验证：

```text
https://127.0.0.1/
```

至少检查：

- 页面首屏是否渲染完整。
- Console 是否有错误。
- Network 中核心 API 是否返回 200。
- 桌面和手机视口是否无文字重叠。
- 涉及表单/弹窗/任务提交的流程是否能交互。

Chrome DevTools MCP 使用隔离浏览器实例，不复用后端扫描用 Chrome 容器。

## 8. 远程手机接入

EasyTier Android APK 已放在登录后下载接口下：

```text
/api/v1/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk
```

来源：

```text
https://github.com/EasyTier/EasyTier/releases/download/v2.6.4/app-arm64-release.apk
```

下载安全边界：

- Compose 把 `/root/Sere1nFish/downloads` 只读挂载到后端 `/srv/downloads`。
- nginx 不暴露原始 `/downloads/` 静态目录。
- 下载必须走 `/api/v1/downloads/...`，需要登录 Bearer Token。
- 后端下载接口有路径白名单。

手机接入流程：

1. 安装 EasyTier APK。
2. 使用前端“云手机操控台 -> 公网组网”下载 TOML 配置文件并导入 EasyTier；配置文件使用 EasyTier DHCP，并通过 `EASYTIER_VIRTUAL_CIDR` 指定固定 C 段。不支持导入时再使用页面里的网络名、密钥、DHCP 网段和 peer 手动加入虚拟网络。
3. 手机端开启项目 Mobile Agent 或无线 ADB，并确认首次 ADB 授权。
4. 后端通过 mDNS 或 EasyTier 虚拟网段自动发现设备。
5. 前端点击“自动接入”，把设备纳入资源池。
6. 给设备设置显示名、标签和分组。
7. 在项目详情的“手机操作”页执行读屏、操作、截图、日志、画像沉淀。

验证当前是否有设备在线：

```bash
docker exec sere1nfish_backend_1 sh -lc 'adb devices -l'
```

没有设备时，后端逻辑和模拟测试可以通过，但不能证明真实手机端到端操作完成。

详细教程见：

```text
server/docs/MOBILE_AGENT_E2E_RUNBOOK.md
docs/REMOTE_MOBILE_EASYTIER.md
```

## 9. 验收命令

后端目标测试：

```bash
docker exec sere1nfish_backend_1 pytest -q \
  test_server/tests/test_config_observability.py \
  test_server/tests/test_voice_tts.py \
  test_server/tests/test_bailian_aigc_client.py \
  test_server/tests/test_mobile_project_artifacts.py \
  test_server/tests/test_mobile_agent_pipeline.py
```

前端构建：

```bash
cd /root/Sere1nFish/view
npm run build
```

部署配置：

```bash
docker-compose -f /root/Sere1nFish/docker-compose.yml config
```

运行冒烟：

```bash
curl -k https://127.0.0.1/health
```

真机验收：

```bash
docker exec sere1nfish_backend_1 sh -lc 'adb devices -l'
```

真机在线后，在前端项目详情验证：

- 读屏结果正常。
- Agent 可执行点击、输入、滑动等动作。
- 操作截图保存为磁盘文件，MongoDB 只保存元数据。
- 操作日志、截图、人物画像都能按 `project_id` 在项目详情读取。
- 删除项目会级联清理相关手机操作产物引用。

## 10. 常见问题

### 手机连不上

检查：

- 安全组是否放行 `443/tcp`、`11010/tcp+udp`、`11011/tcp`、`11012/tcp`、`11013/udp`。
- 手机 EasyTier 网络名和密钥是否与服务器 `.env` 一致。
- 手机是否能出站访问服务端公网地址。
- 手机 ADB 授权是否已经确认。
- `easytier-backend-peer` 是否正常运行。

### 百炼任务创建失败

检查：

- `bailian.api_key` 是否已配置。
- `workspace_id`、`region`、模型和 API Key 是否属于同一地域。
- 输入的图片、音频、视频 URL 是否能被阿里云服务端公网访问。
- 视频任务是否使用任务轮询，不要重复提交同一个生成请求。

### 配置看不到明文密钥

这是预期行为。敏感字段写入时加密，读取时脱敏。需要更新密钥时直接在前端配置页重新填写完整新值。
