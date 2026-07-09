# 手机 Agent 后端链路 Review

更新时间: 2026-07-06

## 结论

当前手机 Agent 后端采用「规划层 + 执行层 + 读屏/画像沉淀 + 项目归档」结构:

- 执行层复用 AutoGLM 的视觉 agent、ADB device 和动作解析能力。
- 本项目统一从 MongoDB 运行时配置读取模型、采样、移动端参数，不再走 AutoGLM 或本地 config 文件。
- 手机截图保存到磁盘，MongoDB 只保存元数据和登录态读取 URL。
- 操作日志、截图、人物画像、自动聊天 session 都带 `project_id`，项目详情页可以按项目聚合读取。

## AutoGLM 借鉴点

已对照本地 `AutoGLM-GUI-main` 的关键实现:

- `AutoGLM_GUI/agents/glm/async_agent.py`: 每一步执行「截图 -> LLM -> 解析动作 -> 执行动作 -> 更新上下文」，本项目执行层沿用这个视觉闭环。
- `AutoGLM_GUI/phone_agent_manager.py`: 用 agent 生命周期、状态和取消能力管理手机任务，本项目用 `_running` 注册运行中的 agent，并暴露 `cancel_task`。
- `AutoGLM_GUI/device_manager.py`: 设备发现同时考虑 ADB、mDNS、远端连接和稳定 serial，本项目设备池保持 mDNS 自动发现，并补充 EasyTier 虚拟网 ADB 扫描。
- AutoGLM 测试里大量使用 mock device/mock agent，本项目新增无真机测试覆盖执行事件、截图、画像和 session 归档。

## 本项目实现映射

- `core/mobile/executor.py`
  - `run_task_stream`: 启动 AutoGLM 执行层 agent，流式返回 `task_start/step/done/error/cancelled`。
  - `_save_event_screenshot`: 对 agent step 里的截图落盘，并把 `screenshot_id/screenshot_url` 回填到事件。
  - `_log_operation`: 记录 agent 任务、step、done、error、cancelled；日志数据里的 base64 截图会被替换为 `<stored-on-disk>`。

- `core/mobile/planner.py`
  - `plan_task`: 用移动规划模型拆解高层目标。
  - `describe_screen`: 看当前屏幕，为规划和重规划提供上下文，并保存项目截图。
  - `run_planned_task`: 复用同一个执行层 agent 跑多个子任务，失败时读屏重规划。

- `core/mobile/chat_assist.py`
  - `read_screen`: 截图后用视觉模型分析聊天界面，并按项目保存真实截图。
  - `suggest_stream`: 基于读屏、我的背景、对方画像生成候选话术，并发布 SSE 事件。
  - `send_reply`: 输入回复并可选点击发送按钮，操作日志可关联项目。

- `core/mobile/profiling.py`
  - `analyze_and_update`: 从读屏内容提取人物画像，合并历史画像，追加观察记录并发布 `profile_updated`。

- `api/dao/mobile_artifacts.py`
  - 截图文件落盘到 `MOBILE_SCREENSHOT_DIR` 或 `data/mobile_screenshots`。
  - MongoDB 仅保存 `project_id/task_id/device_id/contact_id/source/file_path/url/meta` 等元数据。

## 已验证

无真机可跑测试已覆盖:

- agent step 截图自动保存、事件回填、操作日志脱敏。
- 读屏分析保存项目截图。
- 画像提取合并、观察记录和项目事件发布。
- 自动聊天 session 按项目查询。
- 项目维度截图、操作日志、画像过滤。
- 事件总线按 `project_id` 过滤。

测试入口:

```bash
pytest -q \
  test_server/tests/test_config_observability.py \
  test_server/tests/test_voice_tts.py \
  test_server/tests/test_bailian_aigc_client.py \
  test_server/tests/test_mobile_project_artifacts.py \
  test_server/tests/test_mobile_agent_pipeline.py
```

## 真实手机待验证

当前服务器接口显示 `devices=0`、`online=0`，因此以下项仍需要在线真机完成端到端验证:

- EasyTier 入网后自动发现手机并纳入池。
- 真机截图、点击、滑动、输入、返回、启动应用。
- 规划层实际控制 App 完成多步任务。
- 自动聊天 watch/start 在真实微信/IM 场景里的连续读屏、画像更新和回复发送。

