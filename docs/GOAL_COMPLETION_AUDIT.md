# 目标完成度审计

审计日期：2026-07-06  
运行目录：`/root/Sere1nFish`  
后端源码：`/root/Sere1nFish/server`  
前端源码：`/root/Sere1nFish/view`

本文档按原始目标逐项记录当前证据、验证命令和剩余边界。结论先行：除真实手机端到端操作需要外部设备在线后补验外，代码侧、配置侧、前端侧、文档侧和目标测试已完成并通过。

## 1. Token 观测和日志不落库

状态：已由代码和测试证明。

当前实现：

- Token 记录只保留在 `Sere1nGraph/graph/observability/tracker.py` 的进程内环形缓冲。
- 运行日志只保留在 `core/observability/logs.py` 的进程内环形缓冲。
- 旧集合名 `token_usage_records`、`task_logs` 只用于兼容清理，不再作为运行时写入路径。
- 删除任务、批量删除任务、删除项目时，会同步清理当前进程内 TokenTracker 和 ObservabilityLogger 的对应记录。

证据：

```text
server/Sere1nGraph/graph/observability/tracker.py
server/core/observability/logs.py
server/api/routers/project_api.py
server/api/routers/projects.py
server/docs/OBSERVABILITY_API.md
server/docs/OBSERVABILITY_INTEGRATION.md
```

测试覆盖：

```text
test_config_observability.py::test_token_tracker_turns_are_memory_only
test_config_observability.py::test_legacy_stats_routes_use_memory_tracker
test_config_observability.py::test_observability_logger_is_memory_only
```

## 2. 轮次级 Token 全局观测

状态：已由代码、前端页面和测试证明。

当前实现：

- `/api/v1/observability/turns` 返回轮次聚合。
- 每个轮次包含 `calls` 单次 LLM 调用明细。
- 支持 `project_id`、`task_id` 过滤。
- 前端观测中心包含轮次详情、层级树、统计卡片和可展开调用表。

证据：

```text
server/Sere1nGraph/graph/observability/tracker.py
server/api/routers/observability.py
server/docs/OBSERVABILITY_API.md
view/src/pages/Observability/Observability.tsx
view/src/services/observabilityService.ts
```

测试覆盖：

```text
test_config_observability.py::test_token_tracker_turns_are_memory_only
```

## 3. 配置统一从前端写入 MongoDB 并加密

状态：已由代码、前端页面和测试证明。

当前实现：

- 敏感字段由 `api/utils/config_crypto.py` 加密保存。
- `api_key`、`api_token`、`token`、`password` 等敏感字段读取时脱敏。
- `llm` 配置会同步映射到运行时模型配置。
- `runtime`、`mobile`、`bailian`、`cosyvoice`、`chrome_docker` 等通用配置段由前端配置页写入 MongoDB。
- masked 占位符写回时会保留旧密钥，不会把 `***` 当成新密钥覆盖。

证据：

```text
server/api/utils/config_crypto.py
server/api/dao/config.py
server/api/services/runtime_config.py
server/api/routers/config.py
view/src/pages/ConfigManagement/ConfigManagement.tsx
view/src/services/configService.ts
server/docs/CONFIG_API.md
view/CONFIG_API.md
```

测试覆盖：

```text
test_config_observability.py::test_sensitive_config_is_encrypted_and_masked
test_config_observability.py::test_llm_frontend_config_feeds_runtime_app_config
test_config_observability.py::test_llm_config_syncs_to_runtime_and_delete_clears_fallbacks
test_config_observability.py::test_delete_tool_config_removes_root_and_keyed_docs
test_config_observability.py::test_generic_config_merge_preserves_masked_secrets
```

## 4. 旧 config 文件入口清理

状态：已由代码和测试证明。

当前实现：

- `load_config(config_path)` 显式传路径会报错。
- 默认 `load_config()` 不再读取本地 `config.json`。
- `scripts/sync_config.py` 固定失败提示旧脚本已下线。
- `POST /api/v1/config/import` 固定返回 `410 Gone`。
- 运行目录 `server/config.json` 已删除。
- `server/config.example.json` 已改为废弃提示，不再提供可复制的本地运行配置模板。

证据：

```text
server/Sere1nGraph/graph/config/loader.py
server/Sere1nGraph/graph/main.py
server/scripts/sync_config.py
server/api/routers/config.py
server/docs/CONFIG_API.md
```

测试覆盖：

```text
test_config_observability.py::test_load_config_without_path_does_not_read_default_config
test_config_observability.py::test_sync_config_script_is_disabled
test_config_observability.py::test_graph_runtime_rejects_file_config_entrypoint
```

## 5. TTS、图片编辑、视频生成可直接使用

状态：代码侧已完成，外部服务真实调用取决于有效百炼 API Key、Workspace、地域和公网可访问输入文件。

当前实现：

- TTS / CosyVoice 从 MongoDB 运行配置读取。
- 上传音频返回基于代理 Host/Proto 的公网 URL，便于百炼拉取。
- Qwen Image Edit、万相图片编辑、Wan2.7 文生视频、Wan2.7 图生视频接口已实现。
- Wan2.7 图生视频支持新版 `media` 数组。
- 旧版图生视频 payload 仍兼容。
- 前端 AI 工具页提供图片、视频、参数 JSON、media JSON 和任务轮询入口。

证据：

```text
server/api/routers/voice.py
server/api/routers/aigc.py
server/api/services/bailian_aigc.py
view/src/pages/AITools/BailianMedia.tsx
view/src/services/aigcService.ts
server/docs/VOICE_CLONE_API.md
server/docs/BAILIAN_AIGC_API.md
```

测试覆盖：

```text
test_voice_tts.py::test_voice_config_prefers_cosyvoice_and_builds_workspace_endpoint
test_voice_tts.py::test_voice_upload_public_url_uses_forwarded_headers
test_voice_tts.py::test_voice_synthesize_returns_audio_and_records_metadata
test_bailian_aigc_client.py::test_image_to_video_uses_wan27_media_payload
test_bailian_aigc_client.py::test_image_to_video_legacy_payload_still_supported
test_bailian_aigc_client.py::test_aigc_routes_delegate_to_runtime_client
test_bailian_aigc_client.py::test_aigc_route_validation_and_error_mapping
```

## 6. Dashboard 和观测前端优化

状态：已由前端构建和 Chrome DevTools 验证。

当前实现：

- Dashboard 增加项目、任务、数据源、Token、趋势图表和明细表。
- Observability 页面增加总览、轮次详情、层级视图、日志查询和统计卡片。
- AGENTS 指南已明确前端变更需用 Chrome DevTools MCP 验证。

证据：

```text
view/src/pages/Dashboard/Dashboard.tsx
view/src/pages/Dashboard/Dashboard.css
view/src/pages/Observability/Observability.tsx
view/src/pages/Observability/Observability.css
AGENTS.md
```

验证命令：

```bash
cd /root/Sere1nFish/view
npm run build
```

运行时调试入口：

```text
https://127.0.0.1/
```

## 7. AI 操作手机后端逻辑

状态：代码侧和模拟测试已完成；真实手机端到端需要设备在线后补验。

当前实现：

- 执行层复用 AutoGLM GUI 视觉 Agent。
- 模型配置来自前端写入的 MongoDB 运行配置。
- 支持读屏、规划、执行、失败重规划、聊天建议、自动回复、画像提取和自动聊天。
- 支持设备池、自动发现、EasyTier 虚拟网段扫描、唤醒、解锁、分组、备注和占用。

证据：

```text
server/core/mobile/executor.py
server/core/mobile/planner.py
server/core/mobile/chat_assist.py
server/core/mobile/profiling.py
server/core/mobile/auto_chat.py
server/api/routers/mobile.py
server/docs/MOBILE_AGENT_REVIEW.md
server/docs/MOBILE_AGENT_E2E_RUNBOOK.md
```

测试覆盖：

```text
test_mobile_agent_pipeline.py::test_executor_stream_persists_step_screenshot_and_operation_log
test_mobile_agent_pipeline.py::test_read_screen_saves_project_screenshot
test_mobile_agent_pipeline.py::test_suggest_stream_omits_saved_screenshot_base64
test_mobile_agent_pipeline.py::test_profile_analysis_persists_persona_and_project_event
test_mobile_agent_pipeline.py::test_auto_chat_sessions_are_project_queryable
```

## 8. 手机截图、日志、人物画像关联项目

状态：已由代码和测试证明。

当前实现：

- 手机截图保存为磁盘 PNG 文件。
- MongoDB 只保存截图元数据、文件路径、项目 ID、设备 ID、任务 ID、联系人 ID 和鉴权 URL。
- 操作日志保存项目、任务、设备、联系人、动作和截图 ID。
- 人物画像支持 `project_id` / `project_ids` / observations 的项目级查询。
- 项目详情页“手机操作”标签可读取画像、截图、操作日志和自动聊天 session。
- 项目删除会级联清理手机截图文件、截图元数据、操作日志、自动聊天 session 和画像项目引用。
- 聊天建议 SSE 在截图已保存时只返回 `screenshot_id` / `screenshot_url`，不继续推送截图 base64。

证据：

```text
server/api/dao/mobile_artifacts.py
server/api/dao/contact_profiles.py
server/api/dao/auto_chat_sessions.py
server/api/routers/mobile.py
server/api/routers/projects.py
view/src/pages/ProjectDetail/ProjectDetail.tsx
view/src/services/mobileService.ts
```

测试覆盖：

```text
test_mobile_project_artifacts.py::test_mobile_project_artifacts_roundtrip
test_mobile_project_artifacts.py::test_mobile_event_bus_project_filter
test_mobile_project_artifacts.py::test_mobile_project_routes_return_linked_artifacts
test_mobile_project_artifacts.py::test_project_delete_cleans_mobile_artifacts_and_project_profile_refs
test_mobile_agent_pipeline.py::test_suggest_stream_omits_saved_screenshot_base64
```

## 9. 远程手机接入和安全组

状态：服务端配置、下载和教程已完成；真实接入需要手机在线后补验。

当前安全组入站：

```text
TCP 443
TCP 11010-11012
UDP 11010
UDP 11013
```

不要开放：

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

证据：

```text
docker-compose.yml
docs/RUNTIME_OPERATIONS_TUTORIAL.md
docs/REMOTE_MOBILE_EASYTIER.md
server/docs/MOBILE_AGENT_E2E_RUNBOOK.md
```

当前 APK：

```text
/api/v1/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk
```

来源：

```text
https://github.com/EasyTier/EasyTier/releases/download/v2.6.4/app-arm64-release.apk
```

## 10. 当前验证结果

后端目标测试：

```bash
docker exec sere1nfish_backend_1 pytest -q \
  test_server/tests/test_config_observability.py \
  test_server/tests/test_voice_tts.py \
  test_server/tests/test_bailian_aigc_client.py \
  test_server/tests/test_mobile_project_artifacts.py \
  test_server/tests/test_mobile_agent_pipeline.py
```

当前结果：

```text
27 passed, 1 warning
```

健康检查：

```bash
curl -k https://127.0.0.1/health
```

当前结果：

```json
{"status":"ok","mongodb":{"ok":true}}
```

Compose 配置：

```bash
docker-compose -f /root/Sere1nFish/docker-compose.yml config
```

当前结果：通过。

真机状态：

```bash
docker exec sere1nfish_backend_1 sh -lc 'adb devices -l'
```

当前结果：

```text
List of devices attached
```

含义：当前没有在线手机，因此不能声称真实手机端到端已完成。

## 11. 剩余外部验收

真机上线后必须补充以下证据，补齐后才可标记整个目标完成：

1. `adb devices -l` 显示至少一个在线设备。
2. `/api/v1/mobile/overview` 显示 `devices.online > 0`。
3. 前端项目详情“手机操作”能读取设备并执行读屏。
4. Agent 可完成至少一次真实点击、输入或滑动。
5. 项目详情能看到本次操作生成的截图。
6. 截图图片通过 `/api/v1/mobile/screenshots/{screenshot_id}/image` 登录后可访问。
7. 项目详情能看到本次操作日志。
8. 画像分析能沉淀联系人画像，并能按项目读取。
9. `/api/v1/observability/turns` 能看到本次手机 Agent 调用的 token 轮次。
10. 前端 Chrome DevTools 检查手机操作页面无 Console 错误，关键请求 200。

## 12. 结论

代码侧、配置侧、前端侧、文档侧和测试侧已完成。唯一未完成项是外部设备条件：当前服务器没有真实手机在线，无法证明“真实手机 Agent 端到端操作”已经完成。
