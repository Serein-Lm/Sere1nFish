# 声音复刻 API 文档

## 概述

基于阿里云 DashScope CosyVoice 的声音复刻服务。支持音色创建、管理、语音合成和合成历史回顾。

**Base URL:** `/api/v1/voice`

**认证方式:** Bearer Token（所有接口需登录，DELETE 需管理员权限）

**配置位置:** 前端配置页或 `/api/v1/config/sections/cosyvoice`；通用模型密钥在 `/api/v1/config/llm` 或 `/api/v1/config/sections/runtime` 中维护。敏感字段在 MongoDB 中加密保存，接口返回脱敏值。

## 核心流程

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  上传音频到   │────▶│ POST /voices │────▶│  获取 voice_id│
│  OSS/CDN    │     │  创建音色     │     │  保存到前端   │
└─────────────┘     └──────────────┘     └──────┬───────┘
                                                │
                    ┌──────────────┐             │
                    │POST /synthesize│◀───────────┘
                    │  传入 text +  │
                    │  voice_id    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  返回 MP3 流  │
                    │  前端直接播放  │
                    └──────────────┘
```

## 配置

```json
{
  "config": {
    "api_key": "sk-xxxx",
    "model": "cosyvoice-v3.5-plus",
    "region": "beijing",
    "prefix": "sere1nfish",
    "language_hints": ["zh"],
    "max_prompt_audio_length": 10.0,
    "enable_preprocess": false
  }
}
```

示例:

```bash
curl -X POST http://localhost:8000/api/v1/config/sections/cosyvoice \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "config": {
      "api_key": "sk-xxxx",
      "model": "cosyvoice-v3.5-plus",
      "region": "beijing",
      "prefix": "sere1nfish",
      "language_hints": ["zh"],
      "max_prompt_audio_length": 10.0,
      "enable_preprocess": false
    }
  }'
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `api_key` | string | 否 | DashScope API Key。不填时回退到 LLM/runtime 配置 |
| `model` | string | 是 | 语音合成模型。可选: `cosyvoice-v3.5-plus`、`cosyvoice-v3.5-flash`、`cosyvoice-v3-plus`、`cosyvoice-v3-flash` |
| `region` | string | 否 | 服务地域。`beijing`(默认) 或 `singapore`(仅 v3) |
| `prefix` | string | 否 | 默认音色名前缀，仅字母数字，≤10字符 |
| `language_hints` | string[] | 否 | 默认语种: zh/en/fr/de/ja/ko/ru/pt/th/id/vi |
| `max_prompt_audio_length` | number | 否 | 参考音频最大时长，3.0-30.0 秒 |
| `enable_preprocess` | boolean | 否 | 降噪+增强+音量规整。有噪音建议开启 |

---

## 一、音色管理

### 1.0 上传音频文件

```
POST /api/v1/voice/upload
```

上传本地音频文件，返回可直接传给创建音色接口的公网绝对 URL。该 URL 会根据反向代理的 `Host` 和 `X-Forwarded-Proto` 生成，确保阿里云百炼可以拉取音频。

#### Request

`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | **是** | 音频文件。支持 wav/mp3/flac/m4a/ogg/aac/wma，≤50MB |

#### Response `200`

```json
{
  "filename": "148120f9af764215a27949e39f7b0017.mp3",
  "original_name": "sample.mp3",
  "size": 123456,
  "url": "https://example.com/api/v1/voice/files/148120f9af764215a27949e39f7b0017.mp3",
  "relative_url": "/api/v1/voice/files/148120f9af764215a27949e39f7b0017.mp3"
}
```

| 字段 | 说明 |
|------|------|
| `url` | 公网绝对 URL，直接作为 `POST /voices` 的 `url` 参数 |
| `relative_url` | 站内相对路径，用于本系统访问或排障 |

### 1.1 创建音色

```
POST /api/v1/voice/voices
```

传入音频公网 URL 创建专属音色。返回 `voice_id` 供后续合成使用。

**⚠️ 注意:** 每次调用创建新音色，达到配额上限后无法再创建，请勿频繁调用。

#### Request Body

```json
{
  "url": "https://oss.example.com/audio/sample.wav",
  "prefix": "user01",
  "language_hints": ["zh"],
  "max_prompt_audio_length": 15.0,
  "enable_preprocess": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | **是** | 音频文件公网 URL。支持 wav/mp3/flac/m4a/ogg，建议 3-30 秒安静环境录制 |
| `prefix` | string | 否 | 音色名前缀。不传用 config 默认值 |
| `language_hints` | string[] | 否 | 语种提示。不传用 config 默认值 |
| `max_prompt_audio_length` | number | 否 | 参考音频最大时长(秒)，3.0-30.0 |
| `enable_preprocess` | boolean | 否 | 音频预处理开关 |

#### Response `200`

```json
{
  "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
  "model": "cosyvoice-v3.5-plus",
  "request_id": "req-xxxx-yyyy-zzzz"
}
```

#### cURL

```bash
curl -X POST http://localhost:8000/api/v1/voice/voices \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://oss.example.com/audio/sample.wav","prefix":"user01"}'
```

#### TypeScript

```typescript
const { data } = await axios.post<{
  voice_id: string;
  model: string;
  request_id: string | null;
}>('/api/v1/voice/voices', {
  url: audioOssUrl,
  prefix: 'user01',
});

// 保存 voice_id 供后续合成使用
const voiceId = data.voice_id;
```

---

### 1.2 查询音色列表

```
GET /api/v1/voice/voices
```

分页查询已创建的音色记录。

#### Query Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prefix` | string | 否 | 按前缀筛选 |
| `status` | string | 否 | 按状态筛选: `active` / `deleted` |
| `page` | int | 否 | 页码，从 0 开始（默认 0） |
| `page_size` | int | 否 | 每页条数，1-100（默认 20） |

#### Response `200`

```json
{
  "items": [
    {
      "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
      "model": "cosyvoice-v3.5-plus",
      "prefix": "user01",
      "url": "https://oss.example.com/audio/sample.wav",
      "language_hints": ["zh"],
      "status": "active",
      "request_id": "req-xxxx",
      "created_at": 1717142400.0,
      "updated_at": 1717142400.0
    }
  ],
  "total": 1,
  "page": 0,
  "page_size": 20
}
```

#### TypeScript

```typescript
const { data } = await axios.get<PageResp>('/api/v1/voice/voices', {
  params: { prefix: 'user01', page: 0, page_size: 20 },
});
```

---

### 1.3 查询音色详情

```
GET /api/v1/voice/voices/{voice_id}
```

返回本地记录 + DashScope 远端详情。

#### Response `200`

```json
{
  "local": {
    "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
    "model": "cosyvoice-v3.5-plus",
    "prefix": "user01",
    "status": "active",
    "created_at": 1717142400.0
  },
  "remote": {
    "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
    "status": "ready"
  }
}
```

#### 错误码

| 状态码 | 说明 |
|--------|------|
| 404 | 音色不存在 |

---

### 1.4 更新音色

```
PUT /api/v1/voice/voices/{voice_id}
```

用新音频替换已有音色。`voice_id` 不变。

#### Request Body

```json
{
  "url": "https://oss.example.com/audio/new_sample.wav",
  "language_hints": ["en"],
  "max_prompt_audio_length": 20.0
}
```

#### Response `200`

```json
{
  "ok": true,
  "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
  "request_id": "req-xxxx"
}
```

---

### 1.5 删除音色

```
DELETE /api/v1/voice/voices/{voice_id}
```

永久删除音色（管理员权限）。同步删除 DashScope 远端和本地记录。

#### 权限

需要管理员权限（`role: admin`）。

#### Response `200`

```json
{
  "ok": true,
  "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4"
}
```

#### 错误码

| 状态码 | 说明 |
|--------|------|
| 403 | 无管理员权限 |
| 404 | 音色不存在 |

---

## 二、语音合成

### 2.1 文本转语音

```
POST /api/v1/voice/synthesize
```

使用复刻音色将文本合成为 MP3 音频。**返回二进制音频流**（不是 JSON）。

#### Request Body

```json
{
  "text": "你好，这是一段测试语音",
  "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
  "model": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | **是** | 待合成文本，1-5000 字符 |
| `voice_id` | string | **是** | 音色 ID（从创建音色接口获取） |
| `model` | string | 否 | 合成模型，需与创建音色时一致。不传用 config 默认值 |

#### Response `200`

```
Content-Type: audio/mpeg
Content-Disposition: inline; filename="speech.mp3"
X-Request-Id: req-xxxx-yyyy
X-Record-Id: syn-a1b2c3d4e5f6
X-First-Package-Delay-Ms: 350
Body: <MP3 二进制数据>
```

#### Response Headers

| Header | 说明 |
|--------|------|
| `X-Request-Id` | DashScope 请求 ID |
| `X-Record-Id` | 本地合成记录 ID（可用于查询进度） |
| `X-First-Package-Delay-Ms` | 首包延迟(毫秒) |

#### cURL

```bash
curl -X POST http://localhost:8000/api/v1/voice/synthesize \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，这是测试","voice_id":"cosyvoice-v3.5-plus-user01-xxxxxxxx"}' \
  --output speech.mp3
```

#### TypeScript — fetch + 播放

```typescript
const response = await fetch('/api/v1/voice/synthesize', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  },
  body: JSON.stringify({
    text: '你好，这是一段测试语音',
    voice_id: voiceId,
  }),
});

if (response.ok) {
  const blob = await response.blob();
  const audioUrl = URL.createObjectURL(blob);
  const audio = new Audio(audioUrl);
  audio.play();

  // 可从 header 获取记录 ID
  const recordId = response.headers.get('X-Record-Id');
}
```

#### TypeScript — axios + 下载

```typescript
const { data, headers } = await axios.post(
  '/api/v1/voice/synthesize',
  { text: '你好', voice_id: voiceId },
  { responseType: 'blob' },
);

// 创建下载链接
const url = URL.createObjectURL(data);
const a = document.createElement('a');
a.href = url;
a.download = 'speech.mp3';
a.click();
```

#### React 组件示例

```tsx
function VoicePlayer({ voiceId }: { voiceId: string }) {
  const [text, setText] = useState('');
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const synthesize = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/v1/voice/synthesize', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ text, voice_id: voiceId }),
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      setAudioUrl(URL.createObjectURL(blob));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <textarea value={text} onChange={e => setText(e.target.value)} />
      <button onClick={synthesize} disabled={loading || !text}>
        {loading ? '合成中...' : '合成语音'}
      </button>
      {audioUrl && <audio src={audioUrl} controls autoPlay />}
    </div>
  );
}
```

---

## 三、进度回顾（合成历史）

### 3.1 合成记录列表

```
GET /api/v1/voice/records
```

分页查询语音合成历史。

#### Query Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `voice_id` | string | 否 | 按音色 ID 筛选 |
| `status` | string | 否 | 按状态筛选: `processing` / `completed` / `failed` |
| `page` | int | 否 | 页码（默认 0） |
| `page_size` | int | 否 | 每页条数（默认 20） |

#### Response `200`

```json
{
  "items": [
    {
      "record_id": "syn-a1b2c3d4e5f6",
      "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
      "text": "你好，这是一段测试语音",
      "text_length": 10,
      "model": "cosyvoice-v3.5-plus",
      "status": "completed",
      "audio_bytes": 45678,
      "first_pkg_delay_ms": 350,
      "request_id": "req-xxxx",
      "error": null,
      "created_at": 1717142500.0,
      "completed_at": 1717142502.0
    }
  ],
  "total": 1,
  "page": 0,
  "page_size": 20
}
```

#### TypeScript

```typescript
interface SynthesisRecord {
  record_id: string;
  voice_id: string;
  text: string;
  text_length: number;
  model: string;
  status: 'processing' | 'completed' | 'failed';
  audio_bytes: number;
  first_pkg_delay_ms: number;
  request_id: string | null;
  error: string | null;
  created_at: number;
  completed_at: number | null;
}

const { data } = await axios.get<PageResp<SynthesisRecord>>(
  '/api/v1/voice/records',
  { params: { voice_id: voiceId, status: 'completed', page: 0 } },
);
```

---

### 3.2 合成记录详情

```
GET /api/v1/voice/records/{record_id}
```

查询单条合成记录的详细信息。

#### Response `200`

```json
{
  "record_id": "syn-a1b2c3d4e5f6",
  "voice_id": "cosyvoice-v3.5-plus-user01-a1b2c3d4",
  "text": "你好，这是一段测试语音",
  "text_length": 10,
  "model": "cosyvoice-v3.5-plus",
  "status": "completed",
  "audio_bytes": 45678,
  "first_pkg_delay_ms": 350,
  "request_id": "req-xxxx",
  "error": null,
  "created_at": 1717142500.0,
  "completed_at": 1717142502.0
}
```

#### 错误码

| 状态码 | 说明 |
|--------|------|
| 404 | 记录不存在 |

---

## TypeScript 类型参考

```typescript
// ===== 通用分页响应 =====
interface PageResp<T = Record<string, any>> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ===== 请求类型 =====
interface VoiceCreateReq {
  url: string;
  prefix?: string;
  language_hints?: string[];
  max_prompt_audio_length?: number;
  enable_preprocess?: boolean;
}

interface VoiceUpdateReq {
  url: string;
  language_hints?: string[];
  max_prompt_audio_length?: number;
  enable_preprocess?: boolean;
}

interface SynthesizeReq {
  text: string;
  voice_id: string;
  model?: string;
}

// ===== 响应类型 =====
interface VoiceCreateResp {
  voice_id: string;
  model: string;
  request_id: string | null;
}

interface VoiceClone {
  voice_id: string;
  model: string;
  prefix: string;
  url: string;
  language_hints: string[];
  status: 'active' | 'deleted';
  request_id: string | null;
  created_at: number;
  updated_at: number;
}

interface VoiceDetail {
  local: VoiceClone | null;
  remote: Record<string, any> | null;
}

interface SynthesisRecord {
  record_id: string;
  voice_id: string;
  text: string;
  text_length: number;
  model: string;
  status: 'processing' | 'completed' | 'failed';
  audio_bytes: number;
  first_pkg_delay_ms: number;
  request_id: string | null;
  error: string | null;
  created_at: number;
  completed_at: number | null;
}
```

---

## 错误处理

所有接口统一错误格式：

```json
{
  "detail": "错误描述信息",
  "path": "/api/v1/voice/voices"
}
```

| 状态码 | 场景 |
|--------|------|
| 401 | 未认证或 Token 过期 |
| 403 | 无权限（如非管理员执行删除） |
| 404 | 音色/记录不存在 |
| 422 | 请求参数校验失败 |
| 500 | DashScope 服务异常或内部错误 |

---

## MongoDB 集合

| 集合名 | 用途 |
|--------|------|
| `voice_clones` | 音色记录（与 DashScope 同步） |
| `voice_synthesis_records` | 合成历史记录 |

服务启动时自动创建索引。

---

## API 端点总览

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `POST` | `/api/v1/voice/upload` | 上传本地音频并返回公网 URL | 登录用户 |
| `POST` | `/api/v1/voice/voices` | 创建复刻音色 | 登录用户 |
| `GET` | `/api/v1/voice/voices` | 音色列表（分页） | 登录用户 |
| `GET` | `/api/v1/voice/voices/{voice_id}` | 音色详情 | 登录用户 |
| `PUT` | `/api/v1/voice/voices/{voice_id}` | 更新音色音频 | 登录用户 |
| `DELETE` | `/api/v1/voice/voices/{voice_id}` | 删除音色 | **管理员** |
| `POST` | `/api/v1/voice/synthesize` | 语音合成（返回 MP3 流） | 登录用户 |
| `GET` | `/api/v1/voice/records` | 合成记录列表 | 登录用户 |
| `GET` | `/api/v1/voice/records/{record_id}` | 合成记录详情 | 登录用户 |
