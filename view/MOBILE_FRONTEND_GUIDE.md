# Mobile AI 前端集成指南

> 与 `MOBILE_API.md` 互补:那一份是**接口参考**,这一份是**前端工程师的工程化指南**——用户旅程、页面拆解、状态管理、实时层、错误处理、组件代码片段。

适用版本:后端 v2(42 路由,含事件总线 + 自动聊天闭环 + 组网闭环 + 多步任务可用)。

---

## 0. TL;DR(三件事必看)

1. **实时层 = SSE `GET /api/v1/mobile/events`**。画像更新、建议、自动聊天动作、watcher 新好友——全部通过这一条流推过来。前端只订阅一次,按 `type` 分发。
2. **坐标系**:基础控制(`/devices/{id}/tap` 等)用**设备真实像素**;AI 执行层内部用 0-1000 相对坐标(对前端透明)。从画面点击 → 真实像素的换算见 §5。
3. **核心卖点闭环 = "watcher → start → events"**:`POST /auto-chat/watch/start` 一次启动后,新好友被自动通过+进对话+起会话;前端只需要订阅 `events?types=auto_chat_watch,auto_chat,profile_updated,suggestion` 实时渲染。

---

## 0.5 5 分钟 Quickstart(粘贴即跑)

从 0 到「看到 watcher 发现新人 + 画像/建议事件推过来」:

```ts
const BASE = "http://127.0.0.1:8000";
const API  = `${BASE}/api/v1`;

// 1) 拿 token
const { access_token } = await fetch(`${API}/auth/login`, {
  method:"POST", headers:{"Content-Type":"application/json"},
  body: JSON.stringify({ username:"admin", password:"admin123" })
}).then(r => r.json());
const H = { Authorization: `Bearer ${access_token}` };

// 2) mDNS 自动接入 + 选第一台在线设备
await fetch(`${API}/mobile/pool/auto-connect`, { method:"POST", headers:H });
const { devices } = await fetch(`${API}/mobile/pool`, { headers:H }).then(r=>r.json());
const device_id = devices.find((d:any) => d.online)?.device_id;
if (!device_id) throw new Error("没有在线设备,插模拟器或 USB");

// 3) 独占占用
await fetch(`${API}/mobile/pool/acquire`, {
  method:"POST", headers:{...H, "Content-Type":"application/json"},
  body: JSON.stringify({ device_id, note:"frontend quickstart" })
});

// 4) 起一条 watcher(观察模式,不自发)
const { watch_id } = await fetch(`${API}/mobile/auto-chat/watch/start`, {
  method:"POST", headers:{...H, "Content-Type":"application/json"},
  body: JSON.stringify({ device_id, platform:"微信", auto_accept:true, auto_send:false })
}).then(r=>r.json());

// 5) 一条 SSE 长连,所有动作实时收
const resp = await fetch(
  `${API}/mobile/events?types=auto_chat_watch,auto_chat,profile_updated,suggestion`,
  { headers:H }
);
const reader = resp.body!.getReader();
const dec = new TextDecoder(); let buf = "";
while (true) {
  const { value, done } = await reader.read(); if (done) break;
  buf += dec.decode(value, { stream:true });
  let i; while ((i = buf.indexOf("\n\n")) >= 0) {
    const chunk = buf.slice(0,i); buf = buf.slice(i+2);
    for (const line of chunk.split("\n"))
      if (line.startsWith("data: ")) console.log(JSON.parse(line.slice(6)));
  }
}
```

跑起来后让手机端"通过一个好友请求"或加新人,控制台会看到:
```json
{ "type":"auto_chat_watch", "data":{ "event":"new_contact", "contact_name":"...", "auto_chat_task_id":"..." } }
{ "type":"profile_updated", "contact_id":"微信:xxx", "data":{ "name":"...", "summary":"..." } }
{ "type":"suggestion", "data":{ "suggestions":"..." } }
```

**到此五大系统全接通**——剩下只是把这些事件漂亮地渲染成页面(见 §8 组件树)。

---

## 1. 前置约定

### 1.1 baseURL / 鉴权

```ts
const BASE = "http://127.0.0.1:8000";
const API  = `${BASE}/api/v1`;

// 登录(默认 admin/admin123,生产改 JWT 配置)
const { access_token } = await fetch(`${API}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "admin", password: "admin123" }),
}).then(r => r.json());

// 所有 /mobile/** 请求加 Header
const H = { Authorization: `Bearer ${access_token}` };
```

> Socket.IO(视频流)当前不鉴权,内网/本地用;生产前接 token 中间件。

### 1.2 SSE 解析(所有流式接口)

```ts
async function sse(url: string, onEvent: (ev: any) => void, init?: RequestInit) {
  const resp = await fetch(url, { ...init, headers: { ...H, ...(init?.headers || {}) }});
  const reader = resp.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try { onEvent(JSON.parse(line.slice(6))); } catch {}
        }
      }
    }
  }
}
```

### 1.3 错误约定

| 码 | 含义 | 前端处理 |
|---|---|---|
| 401 | 未登录/token 过期 | 跳登录 |
| 403 | 设备不归我(release) | 提示"非占用人" |
| 404 | 资源不存在(画像/建议) | 静默或空状态 |
| 409 | 设备已被他人占用(acquire) | 显示 owner,置灰操作 |
| 502 | 设备/LLM 调用失败 | 显示 detail 字段,提供重试 |

### 1.4 时间/标识

- `device_id`:`emulator-5554` / `192.168.1.10:5555` / `remote:...` 三种格式都可能,前端不要解析。
- `contact_id`:推荐 `平台:昵称`(后端 `derive_contact_id`),也可手填。
- `key`(建议库):`contact_id` 或 `device:<device_id>`(设备级建议)。
- **时间戳两种**:Mongo 文档是 ISO 8601 字符串,**事件总线 `ts` / `/auto-chat/status.started_at` / `/pool.since` 是 unix 秒(float)**。统一函数:
```ts
export const toDate = (x?: string | number | null) =>
  x == null ? null : typeof x === "number" ? new Date(x*1000) : new Date(x);
```

### 1.5 JWT 过期 / 401 统一拦截

- TTL = **24 小时**。本项目不提供 refresh token——任何 `401` 一律跳登录页重试。
- 主动登出:`POST /api/v1/auth/logout`(服务端会即刻失效 token)。
- 推荐 fetch 拦截器统一处理:
```ts
export async function http(input: RequestInfo, init: RequestInit = {}) {
  const r = await fetch(input, { ...init, headers: { ...H, ...(init.headers||{}) }});
  if (r.status === 401) { location.href = "/login"; throw new Error("unauthorized"); }
  if (!r.ok) {
    const body = await r.json().catch(()=>({detail:r.statusText}));
    throw Object.assign(new Error(typeof body.detail==="string"?body.detail:r.statusText), { status:r.status, body });
  }
  return r;
}
```

### 1.6 OpenAPI / 一键生成 TS 类型

- **在线调**:`http://127.0.0.1:8000/docs`(Swagger),填 token 右上角锁。
- **生成类型**(推荐,避免手写请求/响应接口):
  ```bash
  npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/types.d.ts
  ```
  使用:
  ```ts
  import type { paths } from "@/api/types";
  type StartBody  = paths["/api/v1/mobile/auto-chat/start"]["post"]["requestBody"]["content"]["application/json"];
  type StatusResp = paths["/api/v1/mobile/auto-chat/status"]["get"]["responses"][200]["content"]["application/json"];
  ```
- **事件总线** `data` 随 `type` 变化,**不在 OpenAPI 里**——手写 union,见 §3.3。

---

## 2. 五大功能 → 端点速查

| 我要... | 调谁 | 备注 |
|---|---|---|
| 看池里有什么手机 | `GET /pool` | mDNS 发现的会以 `online=false, status=available` 出现 |
| 把发现的全自动接入 | `POST /pool/auto-connect` | 闭环按钮 |
| 我占用 X(独占) | `POST /pool/acquire` | 409=他人占用 |
| 唤醒亮屏(灭屏后无法操作) | `POST /pool/wake` | `stay_on=true` 充电常亮 |
| 看 X 的画面(实时) | Socket.IO `connect-device` | 见 §6 |
| 点画面让 X 点 (x,y) | `POST /devices/{id}/tap` | 真实像素 |
| 让 AI 自己完成"打开微信发消息" | `POST /agent/run-planned` (SSE) | 看屏规划+多步上下文 |
| 中途停止 AI 任务 | `POST /agent/cancel` | task_id 来自首事件 |
| 给我聊天回复建议 | `POST /chat-assist/suggest` (SSE) | 传 `contact_id` 自动注入画像 |
| 发我选定的话 | `POST /chat-assist/send` | 可选 `send_button` 自动点发送 |
| 给我 X 最新建议(无需重新生成) | `GET /suggestions/{key}` | 落库快照 |
| 给我 X 的画像 | `GET /profiles/{contact_id}` | |
| 让画像自动沉淀(挂着自动学) | `POST /auto-chat/start` `auto_send=false` | 观察模式 |
| 全自动回 X 的消息 | `POST /auto-chat/start` `auto_send=true` `ensure_chat=true` | |
| 加人就自动聊(终极模式) | `POST /auto-chat/watch/start` | 监控新好友 |
| 实时知道发生了啥 | `GET /events` (SSE) | 一条流搞定 |

---

## 3. API 全量索引

> 详细字段见 `MOBILE_API.md`;这里只列**前端最常用的 21 个 + SSE 事件类型**。

### 3.1 必须接(MVP 集)

```
# 设备
GET  /api/v1/mobile/pool                              -> 设备池全景(含 mDNS 发现)
POST /api/v1/mobile/pool/auto-connect                 -> 自动接入闭环
POST /api/v1/mobile/pool/acquire   {device_id,note}   -> 独占
POST /api/v1/mobile/pool/release   {device_id}        -> 释放
POST /api/v1/mobile/pool/wake      {device_id,stay_on?} -> 唤醒+常亮
GET  /api/v1/mobile/devices/{id}/health               -> 健康
GET  /api/v1/mobile/devices/{id}/screenshot           -> PNG(轮询低帧预览也行)

# 基础控制
POST /api/v1/mobile/devices/{id}/{tap|swipe|text|key|launch}

# AI 操作手机
POST /api/v1/mobile/agent/plan         {goal}                        -> 预览步骤(可让用户确认)
POST /api/v1/mobile/agent/run-planned  {device_id,goal,screen_aware?,max_replans?}  (SSE)
POST /api/v1/mobile/agent/cancel       {task_id}

# 画像
GET  /api/v1/mobile/profiles?device_id=
GET  /api/v1/mobile/profiles/{contact_id}
POST /api/v1/mobile/profiles/analyze   {device_id,contact_id,name?,platform?}

# 建议
POST /api/v1/mobile/chat-assist/suggest {device_id,contact_id?,my_background,...} (SSE)
POST /api/v1/mobile/chat-assist/send    {device_id,text,send_button?}
GET  /api/v1/mobile/suggestions/{key}                  -> 随时查看最新

# 自动聊天 + watcher
POST /api/v1/mobile/auto-chat/start   {device_id,contact_id?,contact_name?,ensure_chat?,auto_send?,...}
POST /api/v1/mobile/auto-chat/stop    {task_id}
GET  /api/v1/mobile/auto-chat/status?task_id=
POST /api/v1/mobile/auto-chat/watch/start {device_id,platform,auto_accept?,auto_send?,...}
POST /api/v1/mobile/auto-chat/watch/stop  {watch_id}

# 实时
GET  /api/v1/mobile/events?device_id=&contact_id=&types= (SSE)
GET  /api/v1/mobile/events/recent?...&limit=
```

### 3.2 事件流类型(SSE `events` 推送的 `type`)

| type | 何时触发 | 关键字段 |
|---|---|---|
| `profile_updated` | 画像 upsert | `device_id, contact_id, data.{name,summary}` |
| `suggestion` | suggest/auto-chat 产出建议 | `device_id, contact_id, data.suggestions` |
| `auto_chat` | 自动聊天每轮关键事件 | `device_id, contact_id, data.{task_id,event,rounds,replies_sent,reply?,suggestion?}` |
| `auto_chat_watch` | watcher 启停/发现新人/错误 | `device_id, contact_id?, data.{watch_id,event,contact_name?,auto_chat_task_id?}` |

`auto_chat.data.event` 取值:`started / observed / suggestion / reply_sent / not_in_chat / navigating / already_handled / error / stopped`。

### 3.3 TypeScript 类型清单(手写部分)

OpenAPI 生成的类型覆盖了请求/响应,下面指明事件总线的开放类型与常用 union:

```ts
// 事件统一信封
export interface MobileEventBase<T extends string, D> {
  type: T;
  device_id?: string;
  contact_id?: string;
  ts: number;          // unix float秒
  data: D;
}

export type ProfileUpdatedEvent = MobileEventBase<"profile_updated", {
  name?: string;
  summary?: string;
}>;

export type SuggestionEvent = MobileEventBase<"suggestion", {
  suggestions: string;  // 可能含多条候选,原文字符串
}>;

export type AutoChatEventName =
  | "started" | "observed" | "not_in_chat" | "navigating"
  | "suggestion" | "reply_sent" | "already_handled" | "error" | "stopped";

export type AutoChatEvent = MobileEventBase<"auto_chat", {
  task_id: string;
  event: AutoChatEventName;
  rounds: number;
  replies_sent: number;
  reply?: string;
  suggestion?: string;
  contact_name?: string;
  message?: string;     // error event 时带
}>;

export type AutoChatWatchEventName = "started" | "new_contact" | "error" | "stopped";

export type AutoChatWatchEvent = MobileEventBase<"auto_chat_watch", {
  watch_id: string;
  event: AutoChatWatchEventName;
  contact_name?: string;
  auto_chat_task_id?: string;
  message?: string;
}>;

export type MobileEvent =
  | ProfileUpdatedEvent | SuggestionEvent | AutoChatEvent | AutoChatWatchEvent;

// 端误型守例
export const isAutoChat = (e: MobileEvent): e is AutoChatEvent => e.type === "auto_chat";
export const isWatch    = (e: MobileEvent): e is AutoChatWatchEvent => e.type === "auto_chat_watch";
```

run-planned SSE stage union:
```ts
export type RunPlannedStage =
  | "planning" | "screen" | "screen_error" | "plan"
  | "subtask_start" | "exec" | "subtask_done"
  | "replanning" | "aborted" | "cancelled" | "done" | "error";

export interface RunPlannedFrame { stage: RunPlannedStage; data: any; }
```

画像文档结构(与 Mongo 一致):
```ts
export interface ContactProfile {
  contact_id: string;
  name?: string;
  platform?: string;
  device_id?: string;
  persona: {
    background?: string; personality?: string;
    communication_style?: string; summary?: string;
    interests: string[]; tags: string[];
  };
  observations: { ts: string; content: string; source: string }[];
  created_at: string; updated_at: string;
}
```

---

## 4. 端到端用户旅程(7 条核心 flow)

### Flow A:首次启动 → 设备就绪

```
GET /pool                       → 看到几台?有 status=available 的吗
POST /pool/auto-connect         → 自动 connect 闭环
POST /pool/acquire {device_id}  → 占用要操作的那台(独占)
POST /pool/wake {device_id, stay_on:true}  → 灭屏自动亮 + 常亮
GET /devices/{id}/health        → 全绿
启 Socket.IO connect-device     → 上画面
```

### Flow B:手动远程操作(画面 + 点击/输入)

```
Socket.IO 连上 → 拿到 video-metadata(width/height)
画面 onClick → 按 §5 把触点换算为设备真实像素 → POST /devices/{id}/tap {x,y}
键盘 → POST /devices/{id}/text {text}
返回/桌面 → POST /devices/{id}/key {key:'back'|'home'}
```

### Flow C:AI 自己完成多步任务

```
// 1) 让用户预览步骤(可选)
const { subtasks } = await POST(/agent/plan, { goal: '帮我在微信约张三周五开会' })
显示子任务清单,用户确认

// 2) 执行(SSE)
sse(/agent/run-planned, ev => {
  switch(ev.stage){
    case 'plan':          renderPlan(ev.data.subtasks)  // 含 replanned:true 标记
    case 'subtask_start': highlight(ev.data.index)
    case 'exec':          // ev.data.event.{type=step|thinking|done} 透传渲染
                          if (ev.data.event.type==='step') showStep(ev.data.event.data) // 含 screenshot
    case 'subtask_done':  check(ev.data.index, ev.data.success)
    case 'replanning':    toast('AI 正在调整方案...')
    case 'aborted':       error(ev.data.reason)
    case 'done':          success(`完成 ${ev.data.completed}/${ev.data.subtasks}`)
    case 'cancelled':     info('已取消')
  }
}, { method:'POST', body: JSON.stringify({ device_id, goal }) })

// 3) 用户按"停止"
POST /agent/cancel { task_id: plan_id }  // plan_id 在 'planning' 事件里
```

### Flow D:聊天辅助(IDE 模式,我自己挑话)

```
POST /chat-assist/suggest (SSE) { device_id, contact_id, my_background }
sse → 收 'suggestion_chunk' 增量渲染 → 'done' 完整 suggestions
用户挑一条 → POST /chat-assist/send { device_id, text, send_button?:{x,y} }
```

### Flow E:画像沉淀(挂着自动学,人来发)

```
POST /auto-chat/start { device_id, contact_id, contact_name, platform:'微信',
                        auto_send:false, ensure_chat:true }
→ task_id

订阅 events?contact_id=xxx&types=profile_updated,suggestion,auto_chat
画像卡:收到 profile_updated → GET /profiles/{contact_id} 拉新数据刷新
建议区:收到 suggestion → 渲染 ev.data.suggestions
活动日志:收到 auto_chat → push 一条事件
```

### Flow F:全自动聊天(对方说话我自动回)

```
POST /auto-chat/start { ..., auto_send:true, ensure_chat:true,
                        send_button:{x:1000,y:2100} /* 一次性给键位置 */ }
其它同 Flow E,日志里会看到 auto_chat.event='reply_sent' 含 reply 文本
```

### Flow G:终极 — 加好友即自动聊

```
POST /auto-chat/watch/start { device_id, platform:'微信',
                              my_background, auto_accept:true, auto_send:false }
→ watch_id

订阅 events?types=auto_chat_watch,auto_chat,profile_updated,suggestion
auto_chat_watch.event='new_contact' → 表里新增一个联系人卡(含 contact_name, auto_chat_task_id)
之后该联系人的 auto_chat / suggestion / profile_updated 都会陆续推过来,前端只挂列表

停止 watcher: POST /auto-chat/watch/stop { watch_id }
```

---

## 5. 坐标换算(必看)

视频流和截图返回的尺寸 = **设备真实像素**(meta.width × meta.height)。前端画布常被缩放,触点需还原:

```ts
const deviceX = Math.round(touchClientX / canvas.clientWidth  * meta.width);
const deviceY = Math.round(touchClientY / canvas.clientHeight * meta.height);
await fetch(`${API}/mobile/devices/${id}/tap`, {
  method:"POST",
  headers:{ ...H, "Content-Type":"application/json" },
  body: JSON.stringify({ x: deviceX, y: deviceY })
});
```

> AI 执行层内部用 0-1000 相对坐标(`ActionHandler` 自动换算成真实像素),前端**完全不用关心**;前端只关心自己用户的点击。

---

## 6. Socket.IO 视频流

```ts
import { io } from "socket.io-client";

const socket = io(BASE, { path:"/socket.io" });

socket.on("connect", () => {
  socket.emit("connect-device", { device_id, maxSize: 1280, bitRate: 4_000_000 });
});

let meta: { width:number; height:number; codec:string };
socket.on("video-metadata", m => { meta = m; });

// H264 Annex-B 流;最快方案 jmuxer,低延迟方案 WebCodecs VideoDecoder
socket.on("video-data", pkt => {
  // pkt = { type:'config'|'data', data:ArrayBuffer, keyframe?, pts? }
  decoder.feed(pkt);
});

socket.on("error", console.error);

// 同设备只能有一路视频流(后端按 device 加锁,新连接顶替旧的)
// 切设备: socket.emit('connect-device', { device_id: 'another', ... })
// 完全停: POST /api/v1/mobile/video/reset?device_id=...
```

---

## 7. 实时事件 SSE 集中接

**关键设计**:全前端只维护**一条 SSE 长连接**(到 `/events`),按 `type` 分发到各页面的 store。这是整个实时层的源头。

```ts
// hooks/useMobileEvents.ts
export function useMobileEvents(opts?: { device_id?: string; contact_id?: string; types?: string[] }) {
  const params = new URLSearchParams();
  if (opts?.device_id)  params.set("device_id", opts.device_id);
  if (opts?.contact_id) params.set("contact_id", opts.contact_id);
  if (opts?.types?.length) params.set("types", opts.types.join(","));
  const url = `${API}/mobile/events?${params}`;

  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    let stop = false;
    const ctrl = new AbortController();
    (async () => {
      // 1) 首屏先拉历史补齐
      const recent = await fetch(`${API}/mobile/events/recent?${params}`, { headers: H })
        .then(r => r.json()).then(r => r.events ?? []);
      if (!stop) setEvents(prev => [...recent, ...prev]);
      // 2) 实时跟
      await sse(url, ev => setEvents(prev => [...prev, ev]), { signal: ctrl.signal });
    })();
    return () => { stop = true; ctrl.abort(); };
  }, [url]);
  return events;
}
```

页面侧:

```tsx
function ChatPage({ contactId }) {
  const events = useMobileEvents({ contact_id: contactId, types:["profile_updated","suggestion","auto_chat"] });
  const lastProfile = events.findLast(e => e.type==="profile_updated");
  const lastSugg    = events.findLast(e => e.type==="suggestion");
  const acTimeline  = events.filter (e => e.type==="auto_chat");
  // 收到 profile_updated 时拉详情
  useEffect(() => { if (lastProfile) refetchProfile(contactId); }, [lastProfile?.ts]);
  // 收到 suggestion 时刷建议区(直接用 data.suggestions,无需再请求)
  ...
}
```

---

## 8. 前端 5 个核心页面(组件树)

```
App
├── /devices      DevicePoolPage          系统1
│   ├── PoolToolbar       (auto-connect, mass wake)
│   └── DeviceGrid → DeviceCard(online/owner/操作按钮)
│       └── 操作按钮: 选我占用 / 释放 / 唤醒 / 远程接入弹窗
├── /devices/:id  DeviceConsolePage       基础 + 系统2
│   ├── VideoCanvas       (Socket.IO + 触摸→tap/swipe)
│   ├── ControlBar        (back/home/text/launch)
│   ├── AITaskPanel       (输入 goal → /agent/plan 预览 → /agent/run-planned 执行)
│   └── StepList          (子任务进度 + exec 步骤 + screenshot)
├── /chat/:contactId  ChatAssistPage      系统3+4
│   ├── ChatPreview       (截图 + read_screen 分析)
│   ├── SuggestionPanel   (suggest_stream 流式 / 落库 GET / SSE 自动刷)
│   ├── ProfileCard       (GET /profiles/{id} + profile_updated 自动刷)
│   └── SendButton        (chat-assist/send)
├── /profiles     ProfileLibraryPage      系统3
│   ├── ProfileTable      (GET /profiles, sort by updated_at)
│   └── ProfileDrawer     (详情 / 手动 PUT / DELETE)
└── /auto-chat    AutoChatPanelPage       系统5(终极)
    ├── WatcherCard       (watch/start | stop, 当前 watch_id 状态)
    ├── SessionTable      (status: rounds/replies/last_reply/last_state, 控制 stop)
    └── ActivityLog       (events 流: auto_chat / auto_chat_watch 时间线)
```

---

## 9. 状态管理建议

### 9.1 三层 store

| 层 | 来源 | 用法 |
|---|---|---|
| **设备/会话/画像/建议(权威)** | REST | 进页面/触发动作时拉,放 react-query / SWR |
| **实时事件(增量)** | 一条 `/events` SSE | 全局 store,按 type 分桶;触发对应 REST 重拉详情 |
| **流式动作的过程帧** | 各 SSE 接口本地状态 | 用完即弃(`agent/run-planned`, `chat-assist/suggest`) |

> 不要把 SSE 事件本身当成完整数据源 —— 它们是"刷新通知",真实数据回 REST 拉(`GET /profiles/{id}` 等),避免事件丢失带来不一致。

### 9.2 一条会话两个 ID

- `task_id`(`auto-chat/start` 返回)= 这次会话的句柄,用来 stop / status / 时间线过滤(`auto_chat.data.task_id`)。
- `contact_id` = 这个人的画像/建议 key,用来 `/profiles/{id}`、`/suggestions/{id}`、`events?contact_id=`。

页面常把这两个**绑在一张联系人卡上**。

### 9.3 设备独占
- 在 DevicePoolPage 顶部显示当前用户已占用的设备数;`auto-chat/start` 前 UI 强制提示"先 acquire"。
- 收到 `409` → 弹"已被 ${owner} 占用,需要他释放"。
- 离开页面/退出 → 友好提示是否 release(可选,不强制)。

---

## 10. 错误 / 取消 / 重连

### 10.1 SSE 重连

`fetch` 流断了不会自动重连。封装 `sse()` 时加指数退避:

```ts
async function sseWithRetry(url, onEv, signal, base=1000, max=30000) {
  let wait = base;
  while (!signal.aborted) {
    try { await sse(url, onEv, { signal }); wait = base; }
    catch (e) { if (signal.aborted) return; await new Promise(r => setTimeout(r, wait)); wait = Math.min(wait*2, max); }
  }
}
```

### 10.2 取消正在进行的任务

| 任务 | 取消方式 |
|---|---|
| `/agent/run-planned`(SSE) | `POST /agent/cancel { task_id: plan_id }`(plan_id 在第一条 `planning` 事件) |
| `/agent/task`(单步 AI) | 同上,task_id 在 `task_start` 事件 |
| `/auto-chat/start`(后台循环) | `POST /auto-chat/stop { task_id }` |
| `/auto-chat/watch/start` | `POST /auto-chat/watch/stop { watch_id }` |
| `/chat-assist/suggest`(SSE) | 前端 AbortController 关连接即可(后端无副作用) |

### 10.3 自动聊天的"二次确认"

强烈建议 UI 上**默认 `auto_send=false`**,显式开关切到 `true`(给个红色标识)。配合事件流的 `auto_chat.event='reply_sent'` 在界面提示"刚刚发了一句",可即时回滚业务认知。

---

## 11. 性能与配额

- 每轮 `auto-chat` ≈ 2-4 次 LLM 调用 (视觉读屏 + chat_state + 画像 + 回复)。`interval` 建议 8-15s。
- `run-planned` 每子任务可能 5-30 步,每步 1 次 LLM + 1-2 次截图;`max_steps_per_subtask` 默 None 会用全局 watchdog(60min/12 重复动作/20 无进展)。
- 同一台手机**只能同时有一路视频流 + 一路 ADB 操作**;并发跑 auto-chat + AI 任务 + 人工操作会抢键盘/焦点。**用 `pool/acquire` 标记后,前端把其它路置灰**。
- 一条 `/events` SSE 连接背压:后端 queue 满会丢最旧的,前端不要假设零丢失,关键状态必须回 REST。

---

## 12. 测试 checklist(交付前过一遍)

- [ ] 登录拿 token,所有 `/mobile/**` 带 Authorization。
- [ ] `GET /pool` 看到设备;插个 USB / 开个模拟器能看到;mDNS 设备 status=available。
- [ ] `POST /pool/auto-connect` 后,available 变 online。
- [ ] `POST /pool/wake` 灭屏的手机能亮(发到模拟器至少不报错)。
- [ ] Socket.IO `connect-device` 出画面;触摸 → tap 同位响应。
- [ ] `POST /agent/plan` 返回 3-8 个子任务;`/agent/run-planned` SSE 看到 plan / exec(含 screenshot)/ done。失败时看到 replanning。
- [ ] `POST /chat-assist/suggest` 流式收到 suggestion_chunk;`GET /suggestions/{key}` 能拉到刚才那次。
- [ ] `POST /auto-chat/start auto_send=false` 后,`GET /events?contact_id=xxx` 持续推 observed / suggestion;`/auto-chat/status` 计数递增。
- [ ] `POST /auto-chat/watch/start` 后,在手机端"通过一个新好友请求 → 进对话"模拟,观察 SSE `auto_chat_watch.new_contact`,然后该联系人的 `auto_chat` 事件接力出现。
- [ ] `POST /agent/cancel`、`/auto-chat/stop`、`/auto-chat/watch/stop` 都能立即停止对应流。
- [ ] 重启后端 → 之前 `acquire` 过的设备 `GET /pool` 仍显示已被自己占用(持久化生效)。

---

## 13. 残留限制(产品口径)

| 项 | 影响 | 缓解 |
|---|---|---|
| 手机无 WOL,不能真"远程开机" | 完全断电/断网的手机无解 | 文案"远程唤醒"=灭屏亮屏+常亮;搭配充电+无线 ADB 常连 |
| 事件总线在单进程内存 | 多 worker 部署事件不跨进程 | 单 worker 部署(`uvicorn --workers 1`),或后续接 Redis pub/sub |
| watcher 是启发式 | 平台 UI 改版可能识别失败 | watcher 提示词后端可改;UI 显示"最近 N 次 watcher 失败"提示运维 |
| 同设备并发会抢 | 同时人工+AI+autochat 操作会乱 | 前端按 `pool.reserved/owner` 置灰其它入口 |
| Socket.IO 视频无鉴权 | 仅本地/内网可用 | 上线前接 token middleware |
| 画像合并是 last-write-wins | 极端可能覆盖 | 提供 `PUT /profiles/{id}` 手工修正入口 |

---

## 14. 上线最小动作清单

1. 在前端配置页或 `/api/v1/config/llm` 配 `runtime.base_url / api_key / models.{default: qwen3-max, vision: qwen-vl-max}`，配置由 MongoDB 加密保存。
2. `uv run python run.py`(单 worker)。
3. 配 Mongo,启动会自动建索引并恢复独占预约。
4. 前端按 §1.1 拿 token → §7 起一条 SSE → §8 渲染 5 个页面。
5. 接 Socket.IO 视频流(§6)。
6. **首推流程**:Flow A → Flow G(watcher 开起来就完事了,其它流自动跟随)。

---

## 附录:与 `MOBILE_API.md` 的关系

- `MOBILE_API.md`:**接口字典**(每个端点的入参/出参/SSE 字段/错误码)。
- 本文(`MOBILE_FRONTEND_GUIDE.md`):**集成手册**(怎么把这些拼成产品)。
- 文档间链接:遇到字段不确定时回 `MOBILE_API.md` 查,本文不再重复字段表。
