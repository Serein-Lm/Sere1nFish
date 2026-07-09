# 手机可视化与控制 API（前端接入指南）

> 在现有后端之上集成 AutoGLM 手机操作内核。提供「设备管理 + 实时画面 + 交互控制」三大核心能力，供前端做可视化操作面板。

## 概览

| 能力 | 传输 | 入口 |
|---|---|---|
| 设备管理 / 控制 / 截图 | HTTP REST（JWT） | 前缀 `/api/v1/mobile` |
| 实时视频画面 | Socket.IO | 路径 `/socket.io` |
| 资源池 / 组网（系统1） | HTTP REST | `/api/v1/mobile/pool/**` |
| AI 任务 / 规划层（系统2） | SSE | `/api/v1/mobile/agent/**` |
| 人物画像（系统3） | HTTP REST | `/api/v1/mobile/profiles/**` |
| 辅助聊天 + 画像建议（系统4） | SSE | `/api/v1/mobile/chat-assist/**` |
| 自动聊天（系统5） | HTTP REST | `/api/v1/mobile/auto-chat/**` |

- 所有坐标均为**设备真实像素坐标系**（不是前端画面像素）。
- REST 接口需 JWT 鉴权；Socket.IO 当前不鉴权（仅本地/内网，生产需自行加）。

---

## 0. 本次优化交付（v3 · 前端重点）

> 本轮对「手机操作路由」做深度优化，**行为保持兼容**，新增健壮性与可调性。前端按下列要点对接即可。

### 0.1 LLM 配置统一到 MongoDB 加密运行配置

AI 操作手机的**全部** LLM 配置统一从后端 MongoDB 加密配置读取（执行层 / 规划层 / 读屏 / 话术 / 画像**完全一致**），**不使用** AutoGLM 自带 config_manager，也不再读取本地 `config.json`。配置入口是前端配置页，或直接调用 `/api/v1/config/llm` 与 `/api/v1/config/sections/{category}`。

| 配置字段 | 作用 | 默认 |
|---|---|---|
| `runtime.base_url` | LLM API Base URL（**必填**，否则 AI 任务 / 读屏不可用） | 空 |
| `runtime.api_key` | API Key | 空→`EMPTY` |
| `runtime.models.default` | 文本 / 规划 / 结构化模型 | `qwen3-max` |
| `runtime.models.vision` | 视觉读屏 / 执行层模型 | `qwen-vl-max` |
| `runtime.max_tokens` / `temperature` / `top_p` / `frequency_penalty` | 采样参数 | 3000 / 0.0 / 0.85 / 0.2 |
| `runtime.agent_timeout` | Agent 单次执行超时（秒） | 500 |

前端无需直接读任何本地文件：`GET /api/v1/mobile/overview` 与 `GET /api/v1/bootstrap` 已返回**脱敏**的 LLM 就绪状态与模型名（`config.llm_configured` / `models`）。修改配置后由后端运行时配置层读取 MongoDB，敏感字段只以脱敏值返回。

### 0.2 屏幕传输 & Web 控制：清晰度 / 流畅度可调且有保障

**画面参数**经 Socket.IO `connect-device` 逐连接可调（默认值来自 MongoDB 配置段 `mobile.video`，前端可覆盖）：

| 参数 | 作用 | 默认 | 调优方向 |
|---|---|---|---|
| `maxSize` | 长边分辨率（清晰度） | 1920 | 清晰↑调大；弱网/流畅↑调小（1280/960） |
| `bitRate` | 码率 bps（清晰度） | 8_000_000 | 清晰↑调大；省带宽调小（4_000_000） |
| `maxFps` | 帧率（流畅度，1–120） | 60 | 流畅↑调大；弱网调小（30） |
| `downsizeOnError` | 失败自动降分辨率重试 | false | 弱网建议 true |

推荐档位：**高清** `{maxSize:1920,bitRate:8M,maxFps:60}`；**均衡** `{1280,4M,45}`；**弱网流畅** `{960,2M,30,downsizeOnError:true}`。

**控制路径**（流畅度关键，按场景二选一）：
- **低延迟拖拽 / 连续手动操作 → Socket.IO `control-touch`**（与视频同一连接，毫秒级，见 §4.1）。这是保证「Web 操作手机流畅度」的推荐路径。
- **离散点击 / 脚本化 → HTTP `/tap` `/swipe`**（带超时与统一错误，见 §4）。

### 0.3 健壮性增强（行为兼容）

- **设备操作超时**：所有 `/devices/{id}/*` 控制类接口与截图加统一超时（`config.mobile.adb_timeout`，默认 30s + buffer）。设备无响应返回 **504**（修复 `type_text` 等底层无子进程超时、可能无限挂起的问题）。
- **`/events` SSE 心跳**：每 ~15s 发送 `: keepalive` 注释行，既保活又能及时回收空闲断连订阅者（修复订阅者队列泄漏）。前端 `EventSource` 自动忽略注释行，**无需改动**。
- **错误响应新增 `path`**：统一为 `{ "detail": ..., "path": "/api/v1/mobile/..." }`（见 §1）。

### 0.4 性能

- `/overview`、`/devices` 的「刷新 + 列举」合并为单次线程调用；`/overview` 的配置加载移出事件循环并与设备查询并行。

---

## 1. 鉴权

### 登录
```http
POST /api/v1/auth/login
Content-Type: application/json

{ "username": "admin", "password": "admin123" }
```
**返回**:
```json
{ "access_token": "<JWT>", "token_type": "bearer", "server_token": null }
```
- `access_token`：后续所有 `/api/v1/mobile/**` 请求 Header `Authorization: Bearer <access_token>`。
- `server_token`：可为 `null`；系统启用「登录 Key」时返回服务器端证据。
- **TTL = 24 小时**（`ACCESS_TOKEN_EXPIRE_MINUTES = 60*24`）。
- 默认用户 `admin / admin123`（生产请改）。

### 登出 / 主动撤销 token
```http
POST /api/v1/auth/logout
Authorization: Bearer <access_token>
```
返回 `{ "status": "ok" }`。服务端 token store 会即刻失效该 token。

### 401 处理约定
任何 `/api/v1/mobile/**` 返回 `401 Unauthorized` 意味着 token 过期或被撤销。**前端需重走登录**（本项目未提供 refresh token）。建议在请求拦截器里统一拦 401 → 跳登录页。

### 错误响应统一格式（v3：统一加 `path`）
业务错误（401/403/404/409/502/504 等）——本轮起统一附带 `path`：
```json
{ "detail": "设备 emulator-5554 已被 xxx 占用", "path": "/api/v1/mobile/pool/acquire" }
```
请求体验证错（422）：
```json
{ "detail": [{ "loc": ["body", "device_id"], "msg": "field required", "type": "value_error.missing" }],
  "path": "/api/v1/mobile/devices/.../tap" }
```
服务器内部错（500）：`{ "detail": "服务器内部错误", "path": "..." }`（不再泄漏堆栈，堆栈仅入服务端日志）。
前端提示用户时优先展 `detail`（字符串）；422 打包成字段错误 Map。

### 在线调试 / 生成类型
- **Swagger UI**：`http://127.0.0.1:8000/docs`（FastAPI 自带）可直接填 token + 在线发请求。
- **OpenAPI schema**：`http://127.0.0.1:8000/openapi.json`，可一键生成 TS 类型：
  ```bash
  npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/types.d.ts
  ```

---

## 2. 设备管理

### 列出设备
```http
GET /api/v1/mobile/devices
```
```json
{ "devices": [
  { "device_id": "emulator-5554", "status": "device", "model": "Pixel_6", "connection_type": "usb" }
]}
```

### 设备健康
```http
GET /api/v1/mobile/devices/{device_id}/health
```
```json
{ "device_id": "...", "online": true, "screenshot_ready": true,
  "input_ready": true, "current_app_ready": true, "capture_failed": false, "error": null }
```
> 设备无响应时该接口不再挂起：超时返回 `online:false, capture_failed:true, error:"health probe timeout"`。

### 当前前台应用（聊天 hook / 状态判断）
```http
GET /api/v1/mobile/devices/{device_id}/current_app
```
```json
{ "device_id": "...", "current_app": "WeChat" }
```

---

## 3. 画面

### 3a. 实时视频（推荐）— Socket.IO

```js
import { io } from "socket.io-client";

const socket = io("http://127.0.0.1:8000", { path: "/socket.io" });

socket.on("connect", () => {
  // 画质/流畅度参数见 §0.2（maxSize/bitRate/maxFps/downsizeOnError，缺省取 MongoDB mobile.video）
  socket.emit("connect-device", {
    device_id: "emulator-5554",
    maxSize: 1280, bitRate: 4_000_000, maxFps: 45, downsizeOnError: true,
  });
});

socket.on("video-metadata", (meta) => {
  // { deviceName, width, height, codec }  ← width/height 是设备像素，用于坐标换算
});

socket.on("video-data", (pkt) => {
  // { type: "data"|"config", data: ArrayBuffer(H264 Annex-B), keyframe, pts }
  // 喂给解码器（见第 8 节）
});

socket.on("error", (e) => console.error(e));
```

- 断开连接会自动停流。
- 同一设备同一时刻只允许一个视频流（服务端按设备加锁，新连接会顶替旧连接）。

### 3b. 静态截图（低门槛接入）

```http
GET /api/v1/mobile/devices/{device_id}/screenshot   →  image/png
```

前端按需轮询即可实现低帧率预览（无需 H264 解码，适合快速打通）。

---

## 4. 交互控制（坐标 = 设备像素）

| 方法 | 路径 | Body |
|---|---|---|
| POST | `/api/v1/mobile/devices/{id}/tap` | `{ "x": 100, "y": 200 }` |
| POST | `/api/v1/mobile/devices/{id}/swipe` | `{ "start_x", "start_y", "end_x", "end_y", "duration_ms"? }` |
| POST | `/api/v1/mobile/devices/{id}/text` | `{ "text": "你好" }` |
| POST | `/api/v1/mobile/devices/{id}/key` | `{ "key": "back" \| "home" }` |
| POST | `/api/v1/mobile/devices/{id}/launch` | `{ "app_name": "WeChat" }` |
| POST | `/api/v1/mobile/video/reset` | `?device_id=...`（可选，停止视频流） |

成功统一返回 `{ "ok": true }`（`launch` 额外返回 `app_name`）。失败 `502`、设备无响应超时 `504`。

> **坐标空间** `coord_space`（`tap`/`swipe` 可选，默认 `pixel`）：`pixel`（设备像素）/ `normalized_1000`（0–1000，Agent 尺度）/ `normalized_10000`（0–10000，API 尺度）/ `auto`（自动识别归一化区间并换算）。用归一化可免去前端自己按 §5 换算。

### 4.1 低延迟手动控制（Socket.IO `control-touch`，推荐用于拖拽/连续操作）

HTTP `/tap` `/swipe` 每次都新起 `adb` 子进程（百毫秒级），**不适合连续拖拽**。需要「跟手」的手动操作请走视频同一条 Socket.IO 连接上的 `control-touch` 事件（经 scrcpy 控制通道，毫秒级）：

```js
// 已 connect-device 并在推流后可用；x/y 为设备像素（见 §5 换算）
function sendTouch(action, x, y) {
  // action: "down" | "move" | "up" | "cancel"
  socket.emit("control-touch", { device_id: "emulator-5554", action, x, y }, (ack) => {
    if (!ack?.success) console.warn("touch failed:", ack?.error);
  });
}
// 拖拽：pointerdown→down，pointermove→move（按帧节流），pointerup→up
```

- 回调 `{ success: true }` 或 `{ success: false, error }`。
- **流畅度策略**：拖拽/滑动/手势用 `control-touch`；单击按钮等离散操作用 HTTP `/tap`（带超时与统一错误，便于脚本化与重试）。
- `move` 建议按 ~60fps 节流（`requestAnimationFrame`）以平衡跟手与带宽。

---

## 5. 坐标换算（关键）

前端画面尺寸通常 ≠ 设备分辨率。点击前先换算为设备像素：

```js
// meta 来自 video-metadata（或截图的真实宽高）
const deviceX = Math.round(touchX / viewWidth  * meta.width);
const deviceY = Math.round(touchY / viewHeight * meta.height);
// POST .../tap { x: deviceX, y: deviceY }
```

---

## 6. 错误码

| 码 | 含义 |
|---|---|
| 401 | 未登录 / token 失效 |
| 400 | 参数错误（如不支持的 key） |
| 404 | 资源不存在（画像 / 建议 / 任务等） |
| 409 | 设备已被他人独占（`/pool/acquire`） |
| 403 | 非本人持有设备（`/pool/release`） |
| 502 | 设备操作失败（设备离线 / adb 异常） |
| 504 | **设备无响应超时**（v3 新增，控制类/截图统一超时） |
| 500 | 服务器内部错误（`{detail,path}`，堆栈仅入日志） |

---

## 7. 前端最小打通流程

1. `POST /api/v1/auth/login` 拿 token
2. `GET /api/v1/mobile/devices` 选一个 `device_id`
3. 画面二选一：Socket.IO `connect-device`（实时）/ 轮询 `screenshot`（简单）
4. 画面上监听点击/拖拽 → 第 5 节换算坐标 → `POST tap/swipe`
5. 输入框 → `POST text`；返回/桌面 → `POST key`

---

## 8. 视频解码提示

`video-data` 是 H264 Annex-B 裸流。前端解码可选：

- **jmuxer**（最快上手）：把 `data` 持续 feed 给 jmuxer，输出到 `<video>`。
- **WebCodecs `VideoDecoder`**（低延迟）：用 `config` 包初始化，`data` 包逐帧解码渲染到 `<canvas>`。

---

## 9. 本地启动

```bash
uv run python run.py          # http://127.0.0.1:8000 ，文档 /docs
```

集成方式：`AutoGLM-GUI-main/` 源码通过 `api/__init__.py` 注入 `sys.path` 复用（未做 editable 安装，不影响现有 `pydantic` 等 pin 版本）。

---

## 10. AI 自助任务（执行层 = AutoGLM 视觉 agent，配置 = 本项目）

让 AI 自己看屏操作手机完成任务。执行层复用 AutoGLM 视觉 agent，模型配置取自本项目 MongoDB 加密运行配置（`runtime.models.vision`，默认 `qwen-vl-max`）。

```http
POST /api/v1/mobile/agent/task        (SSE)
{ "device_id": "emulator-5554", "task": "打开微信给张三发消息说我晚点到", "max_steps": 30 }
```

SSE 事件（逐步推送）：

- `{"type":"task_start","data":{"task_id","device_id","task"}}` ← 记下 `task_id` 以便取消
- `{"type":"thinking","data":{"chunk"}}` AI 思考流
- `{"type":"step","data":{"step","action","success","finished","message","screenshot"}}` 每步动作 + 截图
- `{"type":"done","data":{"message","steps","success","stop_reason"}}` 结束
- `{"type":"cancelled" | "error", ...}`

前端介入（中途停止）：

```http
POST /api/v1/mobile/agent/cancel
{ "task_id": "<task_start 里的 task_id>" }
```

> 需先在前端配置页或 `/api/v1/config/llm` 配好 `runtime.base_url / api_key / models.vision`。执行层内置看门狗：重复动作 / 无进展 / 超时会自动停止。

---

## 11. 辅助聊天（读屏 → 话术 → 建议 → 发送）

读屏分析用本项目视觉模型，话术用本项目 copywriting skills（自动加载技能库 + 注入「我的背景」「对方画像」做定制化）。

### 11a. 生成候选话术（不自动发送，IDE 模式）

```http
POST /api/v1/mobile/chat-assist/suggest      (SSE)
{ "device_id": "...", "my_background": "我是XX公司商务", "contact_profile": "对方是采购，谨慎、关注价格" }
```

SSE 事件（`stage`）：

- `{"stage":"reading"}` → `{"stage":"screen","data":{analysis,screenshot}}` 读屏结果
- `{"stage":"generating"}` → `{"stage":"skill","data":{tool}}` 命中的技能
- `{"stage":"suggestion_chunk","data":"..."}` 话术流式片段
- `{"stage":"done","data":{"suggestions":"..."}}`
- `{"stage":"error","data":{message}}`

### 11b. 发送选定话术

```http
POST /api/v1/mobile/chat-assist/send
{ "device_id": "...", "text": "您好，关于报价...", "send_button": {"x":1000,"y":2100} }
```

不传 `send_button` 则只输入文本，由前端再调 `/tap` 点发送（不同 app 发送键位置不同）。

> 全自动模式 = 前端拿到 `suggestions` 自动选一条调 `send`；IDE 模式 = 人工挑选后再 `send`。

---

## 12. 概览

```http
GET /api/v1/mobile/overview
```

返回设备汇总 + 配置状态 + 能力开关 + 运行中任务，前端首页可用它判断「能否使用 AI 任务 / 辅助聊天」。**（v3）新增 `socketio` 块**，advertise 视频/控制事件与可调参数 keys：

```json
{
  "devices": { "total": 1, "online": 1, "items": [ { "device_id": "...", "status": "device", "model": "...", "connection_type": "usb" } ] },
  "config": { "llm_configured": true, "models": { "default": "qwen3-max", "vision": "qwen-vl-max" },
              "sampling": { "max_tokens": 3000, "temperature": 0.0, "top_p": 0.85, "frequency_penalty": 0.2 } },
  "video_defaults": { "maxSize": 1920, "bitRate": 8000000, "maxFps": 60, "downsizeOnError": false },
  "coordinate_scales": { "agent": 1000, "api": 10000 },
  "socketio": {
    "path": "/socket.io", "connect_device": "connect-device",
    "video_events": ["video-metadata", "video-data"],
    "control_event": "control-touch",
    "video_payload_keys": ["device_id", "port", "maxSize", "bitRate", "maxFps", "downsizeOnError"]
  },
  "running_tasks": ["<task_id>"],
  "capabilities": { "visualization": true, "control": true, "ai_task": true, "chat_assist": true }
}
```
- `config.llm_configured` / `models`：判断 AI 能力是否可用（LLM 配置见 §0.1）。
- `socketio`：前端据此连视频（§3a）与低延迟控制（§4.1），无需硬编码事件名。

## 13. 系统1：设备资源池 + 网络组网

把「已连 USB + WiFi/远程 + mDNS 自动发现」的手机聚成一个资源池，支持**独占申请/释放**（AI 任务/自动聊天排他用），以及远程组网接入。

### 资源池全景
```http
GET /api/v1/mobile/pool                       # 全部设备
GET /api/v1/mobile/pool?group_id=grp_xxx      # 仅某分组（v3）
GET /api/v1/mobile/pool?group_id=ungrouped    # 仅未分组（v3）
```
```json
{ "devices": [
  { "device_id":"emulator-5554","status":"device","model":"Pixel_6",
    "connection_type":"usb","online":true,
    "reserved":false,"owner":null,"since":null,"note":null,
    "device_key":"<ro.serialno 稳定 key>",
    "meta":{ "display_name":"测试机A","note":"客服号","tags":["微信","一线"],"group_id":"grp_xxx" } }
], "total": 1 }
```
> （v3）每个设备附带 `device_key`（稳定硬件 key，掉线重连不变）与 `meta`（分组/备注/标签/显示名，见 §13b）。注意 `note`（顶层）是**占用备注**，`meta.note` 是**设备备注**，两者不同。

### 申请 / 释放（独占）
```http
POST /api/v1/mobile/pool/acquire
{ "device_id":"emulator-5554", "note":"跑自动聊天" }
```
- 成功 `{ "ok":true, "device_id", "device_key", "owner", "since" }`；被他人占用返回 **409**。
- **（v3）占用按稳定 key（`ro.serialno`）记录并持久化**：设备掉线重连（含 WiFi `ip:port` 变化）后占用仍对应同一台手机；服务重启自动恢复。
```http
POST /api/v1/mobile/pool/release
{ "device_id":"emulator-5554" }
```
- 非本人持有返回 **403**。`owner` 取自 JWT 当前用户。返回含 `device_key`。

### 网络组网接入（easytier 远程手机）
| 方法 | 路径 | Body | 说明 |
|---|---|---|---|
| POST | `/pool/connect/wifi` | `{ "ip":"10.1.1.2","port":5555 }` | easytier 组网后，直接 ip:port 纳入池 |
| POST | `/pool/connect/usb-to-wifi` | `{ "device_id":"...","port":5555 }` | 把 USB 设备切到 WiFi（便于拔线远程） |
| POST | `/pool/disconnect` | `{ "device_id":"..." }` | 断开 WiFi 连接 |
| POST | `/pool/remote/discover` | `{ "base_url":"http://host:port" }` | 从远程 Device Agent 发现设备 |
| POST | `/pool/remote/add` | `{ "base_url":"...","device_id":"..." }` | 远程 HTTP 代理设备纳入池 |
| POST | `/pool/remote/remove` | `{ "serial":"..." }` | 移除远程设备 |
| GET | `/network/easytier/access` | - | 登录后获取 EasyTier 手机接入配置，前端只使用固定 DHCP 网段 `phone_ipv4_cidr`、标注版 `config_toml`、`config_filename`、APK 下载链接、手动命令和安全组提示 |

**前端建议**：做「设备池」页，卡片展示每台手机（在线/占用/owner）。`reserved=true && owner=我` 才显示「操作/自动聊天」按钮，他人占用置灰。点「公网组网」先调 `/network/easytier/access` 下载 TOML 配置文件给手机导入；如果手机 GUI 只提示导入成功但未保存，提示用户回到 EasyTier 网络编辑页确认保存并启动，或复制 TOML 配置粘贴到自定义配置；如果 GUI 不支持导入，再展示网络名、密钥、DHCP 网段和 peer 手动填写。点「接入远程」弹窗填 ip:port 调 `/pool/connect/wifi`。占用状态轮询 `GET /pool`（或复用 `/overview`）。

---

## 13b. 系统1b：设备分组 + 元数据（备注 / 标签 / 显示名）

> **v3 新增。与 AutoGLM 解耦**：分组与元数据均存 **Mongo**（集合 `device_groups` / `device_metadata`），不依赖 AutoGLM 文件版管理器。元数据按**稳定设备 key**（`ro.serialno`）存储——WiFi 设备 `ip:port` 重连变化后，元数据仍能对应回同一台手机。

### 设备身份与重连对应（关键）

| 数据 | 存储 key | 重连对应 |
|---|---|---|
| 人物画像 / 建议 | `contact_id`（平台:昵称） | ✅ 与设备无关 |
| 设备分组 / 备注 / 标签 | `device_key` = `ro.serialno`（稳定硬件序列号） | ✅ USB / WiFi 重连不变 |
| 独占预约 | `device_key` = `ro.serialno`（**v3 已迁移**） | ✅ USB / WiFi 重连不变；重启自动恢复 |

- `device_key`：在线设备由 `adb getprop ro.serialno` 解析（结果缓存）；离线回退 `device_id`。
- **设置元数据建议在设备在线时进行**，以绑定到稳定 key。

### 分组 CRUD

| 方法 | 路径 | Body | 说明 |
|---|---|---|---|
| POST | `/api/v1/mobile/groups` | `{ "name":"客服组", "color":"#10b981" }` | 新建，返回 `{group_id,name,color,order,...}` |
| GET | `/api/v1/mobile/groups` | — | 列表，每组含 `device_count`：`{ "groups":[...], "total":n }` |
| PATCH | `/api/v1/mobile/groups/{group_id}` | `{ "name"?, "color"?, "order"? }` | 部分更新；不存在 404 |
| DELETE | `/api/v1/mobile/groups/{group_id}` | — | 删除；**组内设备自动解绑**（不删元数据）；不存在 404 |

### 设备元数据（备注 / 标签 / 显示名 / 分组归属）

```http
GET /api/v1/mobile/devices/{device_id}/meta
```
```json
{ "device_id":"emulator-5554","device_key":"<ro.serialno>",
  "display_name":"测试机A","note":"客服号","tags":["微信","一线"],"group_id":"grp_xxx" }
```
无记录时返回空模板（`note:""`,`tags:[]`,`group_id:null`）。

```http
PUT /api/v1/mobile/devices/{device_id}/meta
{ "display_name":"测试机A", "note":"客服号", "tags":["微信","一线"], "group_id":"grp_xxx" }
```
- **部分更新**：只写传入的字段（基于 `model_fields_set`）。要「移出分组」传 `{"group_id": null}`；不传则保持不变。
- `group_id` 指向不存在的分组返回 **404**。
- 返回更新后的完整元数据文档。

**前端建议**：设备池页支持「按分组分栏 / 过滤」（`GET /groups` + `GET /pool` 的 `meta.group_id`）；卡片可改显示名、加标签、写备注（`PUT .../meta`）；拖拽设备到分组 = `PUT .../meta {group_id}`。因按 `device_key` 存，手机掉线重连（哪怕 WiFi 改了 ip:port）分组/备注/标签都不丢。

---

## 14. 系统2：规划层（高层目标 → 子任务 → 执行）

「规划层 + 执行层」：规划层（本项目 `qwen3-max`）把一句话目标拆成有序子任务，逐个交执行层（第 10 节 AutoGLM 视觉 agent）完成。

### 只规划（预览步骤）
```http
POST /api/v1/mobile/agent/plan
{ "goal": "帮我在微信约张三周五下午开会" }
```
```json
{ "goal":"...", "subtasks":["打开微信","搜索联系人张三","进入聊天","发送会议邀约消息"] }
```

### 规划 + 执行（SSE，v2 增强）
```http
POST /api/v1/mobile/agent/run-planned     (SSE)
{ "device_id":"...", "goal":"...",
  "max_steps_per_subtask": 20,   // 可选，默认 None（走看门狗：60min/12 重复/20 无进展）
  "screen_aware": true,           // v2 默认 true：初始看屏再规划
  "max_replans": 2 }              // v2 默认 2：子任务失败时最多重规划次数
```

SSE 事件（`stage`）【v2 全量】：

| stage | data | 说明 |
|---|---|---|
| `planning` | `{plan_id, goal}` | 开始规划，**记下 plan_id 即 /agent/cancel 的 task_id** |
| `screen` | `{analysis}` | (`screen_aware=true`)初始看屏描述 |
| `screen_error` | `{message}` | 看屏失败，已降级为仅文本规划 |
| `plan` | `{plan_id, subtasks, replanned?}` | 子任务列表；`replanned:true` 表示是重规划后的新列表 |
| `subtask_start` | `{index, total, task}` | 一个子任务开始执行 |
| `exec` | `{index, event}` | **透传执行层事件**：`event.type` 为 `task_start/thinking/step/done/cancelled/error`；`step.data` 含截图 |
| `subtask_done` | `{index, result, success}` | 子任务结束；`success` 驱动重规划决策 |
| `replanning` | `{plan_id, failed_index, attempt}` | 子任务失败，看当前屏重规划剩余步骤 |
| `aborted` | `{plan_id, reason}` | 重规划判定无法继续 / 重规划次数用尽 |
| `cancelled` | `{plan_id, index}` | 被 `/agent/cancel` 中断 |
| `done` | `{plan_id, subtasks, completed}` | 全部完成 |
| `error` | `{message}` | 规划/初始化失败、重规划失败 |

**前端建议**：左侧子任务清单（`plan` 事件渲染，`subtask_start/done` 更新进度勾选），右侧手机画面 + 执行层步骤流（`exec.event` 复用第 10 节 step 渲染）。先调 `/agent/plan` 让用户确认步骤，再调 `/agent/run-planned` 执行，体验更可控。

---

## 15. 系统3：人物画像（实时识别 + 沉淀 + 查看）

读屏 → LLM 结构化提取对方画像（背景/性格/兴趣/沟通风格/摘要）→ 合并进已有画像存 MongoDB（`contact_profiles`）。聊得越多越准。

### 触发一次识别沉淀
```http
POST /api/v1/mobile/profiles/analyze
{ "device_id":"...", "contact_id":"wx_zhangsan", "name":"张三", "platform":"wechat" }
```
返回最新画像文档。`contact_id` 是唯一键（建议 `平台_对方标识`）。

### 列表 / 查看 / 改 / 删
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/profiles?device_id=&limit=100` | 画像列表（按更新时间倒序） |
| GET | `/profiles/{contact_id}` | 单个画像详情 |
| PUT | `/profiles/{contact_id}` | 手动新建/修正（`name/platform/persona`） |
| DELETE | `/profiles/{contact_id}` | 删除 |

画像文档：
```json
{ "contact_id":"wx_zhangsan","name":"张三","platform":"wechat","device_id":"...",
  "persona":{ "background":"...","personality":"...","communication_style":"...",
    "summary":"...","interests":["篮球"],"tags":["决策人"] },
  "observations":[{"ts":"...","content":"...","source":"device_id"}],
  "created_at":"...","updated_at":"..." }
```

**前端建议**：聊天辅助页右侧「对方画像卡」，进聊天时按 `contact_id` 拉 `GET /profiles/{id}` 展示；点「刷新画像」调 `/profiles/analyze`。也可做独立「联系人画像库」页（列表 + 详情 + 手动编辑 PUT）。

---

## 16. 系统4：建议结合画像（在第 11 节基础上）

第 11 节 `/chat-assist/suggest` 现支持传 `contact_id`：后端自动从画像库读取该联系人画像并注入话术生成，无需前端再拼 `contact_profile`。

```http
POST /api/v1/mobile/chat-assist/suggest     (SSE)
{ "device_id":"...", "my_background":"我是XX商务", "contact_id":"wx_zhangsan" }
```
- 传 `contact_id`：自动注入画像（推荐，画像越聊越准，话术越贴合）。
- 传 `contact_profile`：手填画像（临时/未建档时用）。两者都传以 `contact_profile` 优先。

**前端建议**：聊天页有 `contact_id` 时只传 id 即可，画像由后端联动注入，前端无需关心画像内容拼接。

---

## 17. 系统5：自动聊天（加人后自动聊）

串起系统 1-4 的后台循环：定时读屏 → 沉淀画像（系统3）→（开 `auto_send` 时）基于画像+我的背景生成回复（系统4）→ 自动发送。界面有新内容才回，避免重复刷屏。**一个人添加后挂上，即可自动识别+持续聊天**。

### 启动（v2 增强）
```http
POST /api/v1/mobile/auto-chat/start
{ "device_id":"...",
  "contact_id":"wechat:张三",        // v2 可选：留空则从屏幕识别推导
  "contact_name":"张三",            // v2 新增：用于导航/画像入库
  "my_background":"我是XX商务",
  "platform":"微信",
  "interval": 8,                     // 秒，最小 2
  "auto_send": false,
  "ensure_chat": false,              // v2 新增：true 时不在对话界面会用执行层自动导航
  "send_button":{"x":1000,"y":2100}  // auto_send=true 时发完会点这个坐标
}
```
- 返回 `{ "ok":true, "task_id":"..." }`。
- **仅在「对方发了且未被我回复」才会生成回复**（LLM 判断，避免自言自语/重复发）。
- `auto_send=false` 下仍会生成**建议**并落库+推送，供“随时查看”。

### 停止 / 状态
```http
POST /api/v1/mobile/auto-chat/stop      { "task_id":"..." }
GET  /api/v1/mobile/auto-chat/status?task_id=...      // 省略 task_id 返回所有会话
```

`status` 返回【v2 完整字段】：
```json
{ "sessions": [{
    "task_id": "...", "device_id": "...",
    "contact_id": "wechat:张三", "contact_name": "张三",
    "running": true, "auto_send": false, "ensure_chat": true,
    "rounds": 12, "replies_sent": 0,
    "observed": 7, "skipped": 2,
    "last_reply": "", "last_suggestion": "我现在忙点东西，晚点回你",
    "last_state": { "is_chat_screen": true, "contact_name": "张三",
                     "last_from": "other", "unreplied": false },
    "last_error": null, "started_at": 1700000000.0
}]}
```
会话快照同时写入 MongoDB 集合 `auto_chat_sessions`（索引：`task_id` unique，`device_id`，`contact_id`，`updated_at`）。

### 新好友 watcher（v2 新增，详见 §20.7）
周期性检测新好友请求 →（可选）自动通过 → 进对话 → **为新联系人自动起一条 auto-chat**。
```http
POST /api/v1/mobile/auto-chat/watch/start
POST /api/v1/mobile/auto-chat/watch/stop
```

**前端建议**：每个联系人一个「自动聊天」开关。打开先 `acquire` 占用设备（系统1）再 `start`；用 `task_id` 轮询 `status` 展示「已回合数/已发条数/最后回复」。建议先 `auto_send=false` 观察画像沉淀，确认无误再开 `auto_send`。关闭调 `stop` 并 `release` 设备。

---

## 18. 五大系统协作关系（全景）

```
系统1 资源池/组网 ──提供独占设备──► 系统2 规划层 ──拆任务──► 执行层（AutoGLM 视觉 agent）
        │                                                          ▲
        └──提供设备──► 系统5 自动聊天 ───────────────────────────────┘
                            │  循环：读屏
                            ├─► 系统3 画像识别沉淀（MongoDB contact_profiles）
                            └─► 系统4 建议（读画像→话术）──► 自动发送
```

前端落地顺序建议：**设备池（系统1）→ 画面+控制（基础）→ 辅助聊天（系统4）→ 画像库（系统3）→ AI任务/规划（系统2）→ 自动聊天（系统5）**。每一步都能独立打通、独立验证。

---

## 架构：复用 AutoGLM 执行层 + 本项目能力

- **执行层（操作手机）**：直接复用 AutoGLM `create_agent`（`general-vision` / `glm-async`）的视觉循环（截图→LLM→动作）。
- **配置 / 视觉读屏 / 话术 skills**：全部走本项目 MongoDB 加密运行配置 + `create_llm(vision)` + `create_copywriting_agent`，**不使用 AutoGLM 自带的 config_manager**。
- **自动发现**：`GET /devices`、`/overview` 已包含 AutoGLM 的 mDNS 自动发现结果；后续用 easytier 把远程手机的 adb 接入本机网络，即可被自动发现（无需改本层代码）。公网组网入口优先下载 `/network/easytier/access` 返回的 TOML 配置文件给手机导入。

---

## 19. v2 缺口闭环(实时 / 多步 / 自动 / 组网)

针对「演示能跑、真实使用不完美」的缺口做的增强。下面只列**变化点**。

### 19.1 系统2:多步任务可用(规划+执行)

`POST /api/v1/mobile/agent/run-planned` 行为升级:

- **整轮复用同一执行层 agent**,子任务间上下文(历史截图/动作)自动累积 → 多步任务不再断片。
- **失败自动重规划**:某子任务失败时看当前屏幕重排剩余步骤(`max_replans` 次)。
- **看屏初始规划**:先描述当前界面再拆解(`screen_aware`,默认 true)。
- `plan_id` 即 `/agent/cancel` 的 `task_id`,可取消整轮。

新增请求字段:`{ screen_aware?: bool=true, max_replans?: int=2 }`。
新增 SSE stage:`screen` / `replanning` / `aborted`(其余同前)。

### 19.2 系统3/4:实时性(落库 + 推送 + 身份)

- **统一事件流(SSE)**:`GET /api/v1/mobile/events?device_id=&contact_id=&types=`
  - `types` 逗号分隔,可选:`profile_updated,suggestion,auto_chat,auto_chat_watch`。
  - 先补最近 30 条历史再实时跟;`GET /events/recent` 可纯拉取。
- **画像更新推送**:画像每次 upsert 自动广播 `profile_updated`,前端 SSE 实时刷新画像卡(不再轮询)。
- **建议落库+随时查看**:`/chat-assist/suggest` 结果落库并广播 `suggestion`;
  `GET /api/v1/mobile/suggestions/{key}` 随时取最新(`key` = `contact_id` 或 `device:<id>`),无需重新生成。
- **身份识别**:新增 `parse_chat_state`(读屏→结构化:是否聊天界面/对方昵称/最后一条谁发的/是否未回复),
  `contact_id` 可由「平台:昵称」自动推导 → 画像不再串号。

**前端建议**:进聊天页订阅 `GET /events?contact_id=xxx`,收到 `profile_updated` 刷画像卡、`suggestion` 刷建议区;首屏先 `GET /suggestions/{contact_id}` + `GET /events/recent` 补齐。

### 19.3 系统5:自动聊天闭环(加人即自动聊)

`POST /api/v1/mobile/auto-chat/start` 升级:

- `contact_id` 改为**可选**,新增 `contact_name`、`ensure_chat`。
- **只在「对方发了且未回复」才回**(LLM 判断,替代旧的画面 hash),避免自言自语/连发;同一条消息去重。
- `auto_send=false` = 观察模式:持续沉淀画像 + 产出建议(落库+推送),人来发;`true` = 自动发。
- `ensure_chat=true` 时,不在对话界面会用执行层**自动导航**进对方聊天。
- 每个动作经事件流广播 `auto_chat`;会话快照落库 `auto_chat_sessions`。

**新好友 watcher(真正的「加人后自动聊」)**:

```http
POST /api/v1/mobile/auto-chat/watch/start
{ "device_id":"...", "platform":"微信", "my_background":"...",
  "auto_accept":true, "auto_send":false, "interval":20, "send_button":{"x":..,"y":..} }
```
- 周期性用执行层**检测新好友请求 →(可选)自动通过 → 进入对话**,并为新联系人**自动起一条 auto-chat**。
- 广播 `auto_chat_watch`(`new_contact` 等);`POST /auto-chat/watch/stop { watch_id }` 停止。

### 19.4 系统1:组网闭环 + 唤醒

- **mDNS 自动接入**:`POST /api/v1/mobile/pool/auto-connect` 把发现到的可用设备自动 `adb connect` 纳入池(发现→接入闭环)。
- **独占持久化**:`acquire/release` 写入 `device_reservations`,重启自动恢复(启动时水合进内存池)。
- **唤醒/常亮**:`POST /pool/wake { device_id, stay_on? }`(KEYCODE_WAKEUP)、`POST /pool/stay-awake { device_id, on }`。

### 19.5 仍需注意(诚实声明)

- **远程唤醒非真开机**:手机无 PC 式 Wake-on-LAN。`/pool/wake` 是亮屏+常亮,需配合无线 ADB 常连;彻底断电/断网的手机无法远程唤醒。
- **状态仍在单进程内存**(事件总线、运行任务、auto-chat 会话):多 worker 不共享。预约/会话/建议已落库,但跨 worker 原子锁与事件分发需上 Redis(已在 `events.py` 留迁移点)。
- **watcher 是启发式**:依赖执行层视觉操作通过好友/进对话,平台 UI 改版需调 `nav_task` 提示词;务必先 `auto_send=false` 观察。
- **同设备并发**:同一台手机被多子系统同时驱动仍可能冲突,建议自动聊天/AI任务前先 `pool/acquire` 独占。

---

## 20. v2 详细接口规约(逐端点完整 schema)

> 本节列出 v1 文档未覆盖、或 v2 行为发生变化的端点的**完整请求/响应**。所有 `/api/v1/mobile/**` 端点均需 `Authorization: Bearer <JWT>`(§1)。

### 20.1 mDNS 自动接入(发现 → connect 闭环)

```http
POST /api/v1/mobile/pool/auto-connect
```
**请求体**:无。
**响应**:
```json
{
  "connected": [
    { "serial": "<adb-serial>", "address": "192.168.1.10:5555",
      "ok": true, "message": "Successfully connected to ...", "address": "192.168.1.10:5555" }
  ],
  "errors": [
    { "serial": "...", "address": "...", "ok": false, "message": "connect failed: ..." }
  ],
  "count": 1,
  "scan": {
    "enabled": true,
    "scanned": 253,
    "open": 0,
    "cidr": "10.144.144.0/24",
    "port": 5555,
    "pairing_candidates": 1
  },
  "pairing_candidates": [
    { "hostname": "test1", "ipv4": "10.144.144.2", "cidr": "10.144.144.2/24" }
  ]
}
```
- 仅处理 `state == AVAILABLE_MDNS` 的设备;已 online 的不动。
- 建议在 DevicePool 页加一个「自动接入」按钮调用,完成后再 `GET /pool` 刷新视图。
- `pairing_candidates` 来自 EasyTier peer 列表。它表示手机已经入网但 ADB 尚未配对/连接；`GET /pool` 会把这些手机合并为 `status="pairing_required"`、`pairing_required=true` 的待配对卡片，前端应在该卡片上显示「配对」入口。

### 20.1b Android 无线 ADB 配对

Android 11+ 系统自带“无线调试”需要先配对再连接。所有端点均需管理员 JWT。

```http
GET /api/v1/mobile/adb/wireless/capabilities
```

返回后端 `adb` 版本和是否支持 `pair` / `mdns`。后端镜像应使用 Google 官方 Android SDK Platform Tools，旧版 Debian `android-tools-adb` 不够用。

```http
POST /api/v1/mobile/adb/wireless/pair-code
{
  "ip": "10.144.144.2",
  "pairing_port": 37123,
  "pairing_code": "123456",
  "connect_port": 38797
}
```

使用手机“使用配对码配对设备”页面显示的 EasyTier IP、配对端口和 6 位码执行 `adb pair`。`connect_port` 可选；填写后后端会继续 `adb connect <ip>:<connect_port>`。

```http
POST /api/v1/mobile/adb/wireless/connect
{ "ip": "10.144.144.2", "port": 38797 }
```

连接已配对手机的无线调试连接端口。连接端口和配对端口通常不是同一个端口。

```http
POST /api/v1/mobile/adb/wireless/pair-qr/start
```

生成 Android ADB 专用二维码 payload：

```text
WIFI:T:ADB;S:<serviceName>;P:<password>;;
```

这不是普通网页链接，也不包含手机 IP。手机扫描后会启动 `_adb-tls-pairing._tcp` mDNS 配对服务。

```http
POST /api/v1/mobile/adb/wireless/pair-qr/complete
{
  "service_name": "studio-xxxxxxxxxx",
  "password": "xxxxxxxxxxxxxxxx",
  "timeout_seconds": 60,
  "connect_after_pair": true
}
```

后端等待 mDNS 中出现对应二维码配对服务后执行 `adb pair`。EasyTier 如果不转发 mDNS，二维码完成步骤可能失败；前端应提示改用配对码模式。

### 20.2 唤醒亮屏(KEYCODE_WAKEUP)

```http
POST /api/v1/mobile/pool/wake
{ "device_id": "emulator-5554", "stay_on": false }
```
| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `device_id` | str | 必填 | adb device id |
| `stay_on` | bool | false | 同时设置充电常亮(`svc power stayon true`) |

**响应**:
```json
{
  "ok": true,
  "wake":    { "ok": true, "stderr": "" },
  "stay_on": { "ok": true, "stderr": "" }   // 仅 stay_on=true 时返回
}
```
`ok` = `wake.ok`(亮屏命令是否执行成功)。底层等价 `adb -s <id> shell input keyevent 224`。
**⚠️ 限制**:不是 WOL,无法对完全断电/断网的手机生效。

### 20.3 充电常亮开关

```http
POST /api/v1/mobile/pool/stay-awake
{ "device_id": "emulator-5554", "on": true }
```
**响应**: `{ "ok": true, "stderr": "" }`。
底层 `adb -s <id> shell svc power stayon true|false`。

### 20.4 设备独占持久化(行为变化,无新端点)

`POST /pool/acquire` / `POST /pool/release` 现在同步写入 MongoDB 集合 `device_reservations`(索引 `device_id` unique)。服务重启时自动从 DB 恢复进内存池——之前的占用对前端可见。
- 多 worker 部署时各 worker 内存独立,**强一致的跨 worker 锁需后续上 Redis**(本节作为已知限制保留)。

### 20.5 最新建议查询(系统4 随时查看)

```http
GET /api/v1/mobile/suggestions/{key}
```
| 参数 | 说明 |
|---|---|
| `key` | `contact_id`(如 `wechat:张三`) 或 `device:<device_id>`(设备级建议) |

**响应**:
```json
{
  "key": "wechat:张三",
  "device_id": "emulator-5554",
  "contact_id": "wechat:张三",
  "suggestions": "[场景一] 您好张总,关于上周的方案...",
  "screen_analysis": "聊天界面,对方刚发了...",
  "created_at": "2026-03-30T...", "updated_at": "2026-03-30T..."
}
```
不存在返回 **404**。`suggestions` 是字符串(可能包含多条候选,以分隔符自然分行,由 LLM 决定)。

**触发写入**:`POST /chat-assist/suggest` 完成时、以及 `auto-chat` 观察模式产出建议时。

### 20.6 自动聊天身份/导航(内部能力,通过参数启用)

`/auto-chat/start` 启用的 v2 内部能力:

| 能力 | 启用方式 | 实现 |
|---|---|---|
| 身份自动推导 | `contact_id` 留空 | `parse_chat_state`(LLM 解析)→ `derive_contact_id(platform, name)` → 写入 state 与画像 |
| 自动导航 | `ensure_chat=true` | 不在对话界面时,内部跑执行层任务 `"打开<platform>,进入与「<name>」的聊天对话界面"`(`max_steps=8`) |
| 该不该我回 | 默认开启 | LLM 判断 `last_from=="other" && unreplied` 才回;同 `last_message` 去重 |

事件以 `auto_chat` 类型推到事件总线(§20.8)。

### 20.7 新好友 watcher(加人即自动聊)

#### 启动

```http
POST /api/v1/mobile/auto-chat/watch/start
{ "device_id": "emulator-5554",
  "platform": "微信",
  "my_background": "我是XX商务",
  "auto_accept": true,
  "auto_send": false,
  "interval": 20,
  "send_button": { "x": 1000, "y": 2100 }
}
```
| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `device_id` | str | 必填 | 监控的设备 |
| `platform` | str | `"微信"` | 平台名(注入到 watcher 提示词) |
| `my_background` | str | `""` | 我的背景(传给后续 auto-chat) |
| `auto_accept` | bool | `true` | 是否自动点"通过"接受好友请求 |
| `auto_send` | bool | `false` | 派生出的 auto-chat 是否自动回复(默认仅观察+建议) |
| `interval` | float | `20.0` | 检测周期(秒,最小 8) |
| `send_button` | obj? | null | 自动发送时的发送键坐标 |

**响应**: `{ "ok": true, "watch_id": "watch-abc12345" }`。

#### 停止

```http
POST /api/v1/mobile/auto-chat/watch/stop
{ "watch_id": "watch-abc12345" }
```
**响应**: `{ "ok": true, "watch_id": "..." }`。

#### 行为
每 `interval` 秒:
1. 跑执行层任务"打开<platform>查看是否有新好友请求...";
2. 读屏 → 解析 chat state;
3. 若进入了某新联系人对话(`contact_name not in started_contacts`)→ 自动调 `/auto-chat/start`(`ensure_chat=true`)并广播 `auto_chat_watch.new_contact`。

#### 限制
- 是**启发式**:依赖执行层视觉操作通过好友/进对话。平台 UI 改版需后端调整 watcher 提示词。
- 同一个 watcher 不会为同一 `contact_name` 重复起 auto-chat(进程内 `started_contacts` 集合)。
- 强烈建议先 `auto_send=false` 跑几轮观察画像沉淀,再切 `auto_send=true`。

### 20.8 实时事件流(SSE)

```http
GET /api/v1/mobile/events?device_id=&contact_id=&types=
```
| 查询参数 | 类型 | 说明 |
|---|---|---|
| `device_id` | str? | 过滤:只看该设备(无 `device_id` 的全局事件也会送达) |
| `contact_id` | str? | 过滤:只看该联系人 |
| `types` | str? | 逗号分隔:`profile_updated,suggestion,auto_chat,auto_chat_watch` |

#### 连接行为
1. 先一次性补送**最近 30 条**匹配历史(SSE 数据帧);
2. 然后实时跟流。**（v3）每 ~15s 发送 `: keepalive` 注释行心跳**——保活长连接、同时让服务端及时回收空闲断连的订阅者(修复泄漏)。`EventSource` 会自动忽略注释行,前端无需处理;断网仍按需重连。

#### 事件统一信封
所有事件统一形如:
```json
{ "type": "<type>", "device_id": "<id?>", "contact_id": "<cid?>",
  "ts": 1700000000.123, "data": { ... } }
```

#### 事件类型字典【全】

**`profile_updated`** — 来自 `POST /profiles/analyze` 或 auto-chat 沉淀
```json
{ "type": "profile_updated", "device_id": "...", "contact_id": "wechat:张三",
  "ts": ..., "data": { "name": "张三", "summary": "..." } }
```
前端动作:`GET /profiles/{contact_id}` 刷新画像卡。

**`suggestion`** — 来自 `POST /chat-assist/suggest` 或 auto-chat 观察模式
```json
{ "type": "suggestion", "device_id": "...", "contact_id": "wechat:张三",
  "ts": ..., "data": { "suggestions": "..." } }
```
前端动作:直接渲染 `data.suggestions`(无需再请求,已在 `chat_suggestions` 集合落库)。

**`auto_chat`** — 自动聊天每轮关键动作
```json
{ "type": "auto_chat", "device_id": "...", "contact_id": "wechat:张三",
  "ts": ..., "data": { "task_id": "...", "event": "<...>",
                       "rounds": 12, "replies_sent": 1,
                       /* 视 event 类型可能带额外字段 */
                       "reply": "...", "suggestion": "...",
                       "contact_name": "...", "message": "..." } }
```
`event` 取值字典:
| event | 含义 | 额外字段 |
|---|---|---|
| `started` | 会话启动 | — |
| `observed` | 本轮观察,无需回复(已沉淀画像) | — |
| `not_in_chat` | 当前非对话界面,本轮跳过 | — |
| `navigating` | (`ensure_chat=true`)正在导航至对话 | `contact_name` |
| `suggestion` | 观察模式产出建议(已落库+广播) | `suggestion` |
| `reply_sent` | 自动模式已发送回复 | `reply` |
| `already_handled` | 这条消息上一轮已回过,去重跳过 | — |
| `error` | 单轮异常 | `message` |
| `stopped` | 会话停止 | — |

**`auto_chat_watch`** — 新好友 watcher
```json
{ "type": "auto_chat_watch", "device_id": "...", "contact_id": "<新人 cid?>",
  "ts": ..., "data": { "watch_id": "watch-...", "event": "<...>",
                       "contact_name": "...", "auto_chat_task_id": "...",
                       "message": "..." } }
```
`event` 取值:
| event | 含义 | 额外字段 |
|---|---|---|
| `started` | watcher 启动 | — |
| `new_contact` | 检测到新联系人并已为其起 auto-chat | `contact_name`, `auto_chat_task_id` |
| `error` | 单轮异常(下一轮会继续) | `message` |
| `stopped` | watcher 停止 | — |

#### 背压
后端订阅者 queue 满(默 1000)时**丢弃最旧一条再投递新条**,**不保证零丢失**——关键状态请回 REST 拉(`/profiles/{id}`、`/auto-chat/status`)。

### 20.9 事件历史拉取(首屏补齐用)

```http
GET /api/v1/mobile/events/recent?device_id=&contact_id=&types=&limit=50
```
**响应**:
```json
{ "events": [ { "type": "...", "device_id": "...", "ts": ..., "data": {...} }, ... ] }
```
历史上限 300 条(进程内 ring buffer)。重启即清空——长期审计请回 DAO 集合(`auto_chat_sessions` / `contact_profiles.observations`)。

---

## 21. 工程化约定(前端必读)

### 21.1 时间戳格式(不完全一致)

| 字段来源 | 格式 | 示例 |
|---|---|---|
| Mongo 文档 `created_at` / `updated_at`（画像/建议/会话/预约） | **ISO 8601 字符串**(UTC) | `"2026-03-30T12:34:56.789+00:00"` |
| `observations[].ts` | ISO 8601 字符串 | 同上 |
| 事件总线 `ts`（`/events`） | **unix float**(秒) | `1700000000.123` |
| `/auto-chat/status` 返回的 `started_at` | **unix float**(秒) | `1700000000.0` |
| `/pool` 返回的 `since`(独占起始) | **unix float**(秒) | `1700000000.0` |

前端统一函数处理:
```ts
function toDate(x: string | number | null | undefined): Date | null {
  if (x == null) return null;
  return typeof x === "number" ? new Date(x * 1000) : new Date(x);
}
```

### 21.2 OpenAPI / 交互式文档

- `GET /docs` — Swagger UI(填 token 在线调)
- `GET /redoc` — ReDoc 渲染
- `GET /openapi.json` — schema(生成 TS 类型/SDK 源)

### 21.3 生成 TS 类型

```bash
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/types.d.ts
```
生成后可在前端代码中导入使用:
```ts
import type { paths } from "./api/types";
type StartAutoChatBody = paths["/api/v1/mobile/auto-chat/start"]["post"]["requestBody"]["content"]["application/json"];
```
事件总线 `data` 是开放执行(随事件类型变化)——不会出现在 openapi.json。前端按 §20.8 手动写 union 类型或参考 `MOBILE_FRONTEND_GUIDE.md §3.3`。

### 21.4 CORS / 本地开发
后端默认放行 **本地任意端口**（`allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?"`），前端 dev 服务器无需额外配。生产希望外网可访问需原付后端加 origin 白名单。

### 21.5 Socket.IO 路径
与 REST 并存:**`/socket.io`**(不在 `/api/v1/...` 下)。前端:
```ts
import { io } from "socket.io-client";
const socket = io("http://127.0.0.1:8000", { path: "/socket.io" });
```
当前不鉴权（仅本地/内网），生产需加 token middleware。

---

## 22. 全部端点速查表(48 个 mobile 路由)

| # | Method | Path | 章节 |
|---|---|---|---|
| 1 | POST | `/auth/login` | §1 |
| 1b | POST | `/auth/logout` | §1 |
| 2 | GET | `/mobile/devices` | §2 |
| 3 | GET | `/mobile/devices/{device_id}/health` | §2 |
| 4 | GET | `/mobile/devices/{device_id}/current_app` | §2 |
| 5 | GET | `/mobile/devices/{device_id}/screenshot` | §3b |
| 6 | POST | `/mobile/video/reset` | §3a |
| 7 | POST | `/mobile/devices/{device_id}/tap` | §4 |
| 8 | POST | `/mobile/devices/{device_id}/swipe` | §4 |
| 9 | POST | `/mobile/devices/{device_id}/text` | §4 |
| 10 | POST | `/mobile/devices/{device_id}/key` | §4 |
| 11 | POST | `/mobile/devices/{device_id}/launch` | §4 |
| 12 | POST | `/mobile/agent/task` (SSE) | §10 |
| 13 | POST | `/mobile/agent/cancel` | §10 |
| 14 | POST | `/mobile/chat-assist/suggest` (SSE) | §11/§16 |
| 15 | POST | `/mobile/chat-assist/send` | §11 |
| 16 | GET | `/mobile/overview` | §12 |
| 17 | GET | `/mobile/pool` | §13 |
| 18 | POST | `/mobile/pool/acquire` | §13/§20.4 |
| 19 | POST | `/mobile/pool/release` | §13/§20.4 |
| 20 | POST | `/mobile/pool/connect/wifi` | §13 |
| 21 | POST | `/mobile/pool/connect/usb-to-wifi` | §13 |
| 22 | POST | `/mobile/pool/disconnect` | §13 |
| 23 | POST | `/mobile/pool/remote/discover` | §13 |
| 24 | POST | `/mobile/pool/remote/add` | §13 |
| 25 | POST | `/mobile/pool/remote/remove` | §13 |
| 26 | POST | `/mobile/agent/plan` | §14 |
| 27 | POST | `/mobile/agent/run-planned` (SSE) | §14 |
| 28 | GET | `/mobile/profiles` | §15 |
| 29 | POST | `/mobile/profiles/analyze` | §15 |
| 30 | GET | `/mobile/profiles/{contact_id}` | §15 |
| 31 | PUT | `/mobile/profiles/{contact_id}` | §15 |
| 32 | DELETE | `/mobile/profiles/{contact_id}` | §15 |
| 33 | POST | `/mobile/auto-chat/start` | §17 |
| 34 | POST | `/mobile/auto-chat/stop` | §17 |
| 35 | GET | `/mobile/auto-chat/status` | §17 |
| 36 | POST | `/mobile/auto-chat/watch/start` | §20.7 |
| 37 | POST | `/mobile/auto-chat/watch/stop` | §20.7 |
| 38 | POST | `/mobile/pool/auto-connect` | §20.1 |
| 39 | POST | `/mobile/pool/wake` | §20.2 |
| 40 | POST | `/mobile/pool/stay-awake` | §20.3 |
| 41 | GET | `/mobile/suggestions/{key}` | §20.5 |
| 42 | GET | `/mobile/events` (SSE) | §20.8 |
| 43 | GET | `/mobile/events/recent` | §20.9 |
| 44 | POST | `/mobile/groups` | §13b |
| 45 | GET | `/mobile/groups` | §13b |
| 46 | PATCH | `/mobile/groups/{group_id}` | §13b |
| 47 | DELETE | `/mobile/groups/{group_id}` | §13b |
| 48 | GET | `/mobile/devices/{device_id}/meta` | §13b |
| 49 | PUT | `/mobile/devices/{device_id}/meta` | §13b |
| — | WS | `/socket.io` (`connect-device`/`video-metadata`/`video-data`) | §3a |
| — | WS | `/socket.io` (`control-touch` 低延迟手动控制) | §4.1 |

(mobile 路由 48 条 + `/auth/login`·`/logout` + Socket.IO `connect-device`/`control-touch`。v3 较 v2 新增 6 条：分组 CRUD ×4 + 设备元数据 ×2。)
