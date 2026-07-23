# 百炼实时语音复刻 API

## 能力边界

- 默认模型：`qwen-audio-3.0-tts-flash`。
- 高质量备选：`qwen-audio-3.0-tts-plus`。
- 接口前缀：`/api/v1/voice`。
- 所有接口要求 Bearer Token；删除音色要求管理员权限。
- 新建复刻音色前必须确认已获得声音所有者授权。
- 音色创建模型与合成模型必须完全一致。历史音色继续使用其创建时记录的模型，不随默认模型切换。
- 实时链路使用预热 WebSocket 连接池，服务端输出 24kHz、单声道、16-bit little-endian PCM；前端边接收边播放，并在完成后封装为 WAV 供回放。
- 完整 MP3 接口保留，用于不需要实时播放的兼容场景。

官方资料：

- [语音合成模型选型](https://help.aliyun.com/zh/model-studio/tts-model)
- [实时语音合成](https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide)
- [声音复刻](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)
- [Qwen-Audio-TTS 音色列表](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list)

## 运行配置

配置由 MongoDB `cosyvoice` 配置段托管。敏感字段通过项目配置加密层保存，不写入环境文件、日志或 Git。

```json
{
  "config": {
    "model": "qwen-audio-3.0-tts-flash",
    "region": "beijing",
    "workspace_id": "llm-example",
    "base_http": "https://llm-example.cn-beijing.maas.aliyuncs.com/api/v1",
    "base_ws": "wss://llm-example.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
    "prefix": "sere1nfish",
    "language_hints": ["zh"],
    "max_prompt_audio_length": 20,
    "enable_preprocess": false,
    "pool_size": 4,
    "stream_sample_rate": 24000
  }
}
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | 回退到 `bailian` 或 `runtime` | 百炼 API Key，按敏感字段加密 |
| `model` | `qwen-audio-3.0-tts-flash` | 新建音色与默认合成模型 |
| `region` | `beijing` | `beijing` 或 `singapore` |
| `workspace_id` | 空 | 专属 Workspace ID |
| `base_http` / `base_ws` | 按地域生成 | 专属或公共百炼端点 |
| `prefix` | `sere1nfish` | 1-10 位小写字母或数字 |
| `language_hints` | `["zh"]` | 参考音频语种；当前百炼只处理首项 |
| `max_prompt_audio_length` | `20` | 3-60 秒 |
| `enable_preprocess` | `false` | 是否启用降噪、增强和音量规整 |
| `pool_size` | `2` | WebSocket 预热连接数，服务端限幅为 1-8 |
| `stream_sample_rate` | `24000` | 可选 16000、24000、48000 |

当前部署使用 `pool_size=4` 与 `stream_sample_rate=24000`。

## 参考音频上传

```http
POST /api/v1/voice/upload
Content-Type: multipart/form-data
```

限制：

- 格式：WAV、MP3、M4A。
- 大小：不超过 10MB。
- 建议：10-20 秒清晰、单人、无混响人声。

上传内容通过 `ObjectStorageService` 写入私有对象存储。接口返回百炼可短时访问的签名 URL，业务集合不保存 AK/SK。

## 音色管理

### 创建音色

```http
POST /api/v1/voice/voices
Content-Type: application/json
```

```json
{
  "url": "https://signed.example.com/reference.wav",
  "prefix": "user01",
  "language_hints": ["zh"],
  "max_prompt_audio_length": 20,
  "enable_preprocess": false,
  "authorized_use": true
}
```

`authorized_use` 必须为 `true`。服务端同时记录确认账号与确认时间。

```json
{
  "voice_id": "qwen-audio-3.0-tts-flash-user01-xxxxxxxx",
  "model": "qwen-audio-3.0-tts-flash",
  "request_id": "req-xxxxxxxx"
}
```

### 查询、更新与删除

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/voices` | 分页查询本地音色 |
| `GET` | `/voices/{voice_id}` | 查询本地记录和百炼远端状态 |
| `PUT` | `/voices/{voice_id}` | 使用新参考音频更新音色 |
| `DELETE` | `/voices/{voice_id}` | 管理员删除远端与本地音色 |

百炼当前更新接口只接受 `voice_id` 和新音频 URL。请求模型中保留的其他可选字段用于兼容旧客户端，但不会改变已创建音色的模型。

## 实时合成

```http
POST /api/v1/voice/synthesize/stream
Content-Type: application/json
```

```json
{
  "text": "你好，这是一段实时语音。",
  "voice_id": "qwen-audio-3.0-tts-flash-user01-xxxxxxxx",
  "instruction": "自然、平静、清晰"
}
```

响应：

```text
Content-Type: audio/pcm;rate=24000;channels=1
Cache-Control: no-store
X-Accel-Buffering: no
X-Record-Id: syn-xxxxxxxxxxxx
X-Voice-Model: qwen-audio-3.0-tts-flash
X-Audio-Encoding: pcm_s16le
X-Audio-Sample-Rate: 24000
X-Audio-Channels: 1
X-Synthetic-Media: true
```

响应体是分片 PCM，不是 WAV 文件。客户端必须按响应头中的采样率解码。浏览器实现位于：

- `view/src/services/voiceService.ts`
- `view/src/pages/AITools/VoiceClone.tsx`

客户端断开或主动停止时，服务端调用百炼取消方法、释放连接池对象，并将记录标记为 `cancelled`。

## 完整 MP3 合成

```http
POST /api/v1/voice/synthesize
Content-Type: application/json
```

请求体与实时接口相同。响应为完整 `audio/mpeg`，兼容原有调用方：

```text
X-Request-Id: req-xxxxxxxx
X-Record-Id: syn-xxxxxxxxxxxx
X-First-Package-Delay-Ms: 300
X-Voice-Model: qwen-audio-3.0-tts-flash
X-Synthetic-Media: true
```

## 合成记录与指标

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/records` | 分页查询记录 |
| `GET` | `/records/{record_id}` | 查询单条记录 |

主要字段：

| 字段 | 说明 |
| --- | --- |
| `status` | `processing`、`completed`、`failed`、`cancelled` |
| `streaming` | 是否走实时 PCM 链路 |
| `audio_format` | `pcm_s16le` 或 `mp3` |
| `sample_rate` | 采样率 |
| `first_pkg_delay_ms` | 百炼服务首音频延迟 TTFA |
| `total_latency_ms` | 服务端本次合成总耗时 |
| `audio_duration_ms` | PCM 音频时长 |
| `rtf` | 总耗时 / 音频时长；小于 1 表示生成快于实时 |

前端另行测量“端到端首包”，即浏览器发起请求至收到首个 PCM 分片的耗时。该值包含 HTTPS、nginx、后端编排与百炼网络耗时，因此通常高于服务 TTFA。

2026-07-23 在当前北京专属 Workspace 上使用系统音色 `longanhuan_v3.6` 验证：

| 指标 | 实测 |
| --- | --- |
| 服务 TTFA | 286-337ms |
| 端到端首包 | 297-349ms |
| RTF | 0.17-0.32 |

官方对 Flash 的“小于 200ms”属于平台模型目标，部署验收以本系统记录的实际端到端数据为准。

## 错误语义

| 状态码 | 场景 |
| --- | --- |
| `401` | 未登录或 Token 过期 |
| `403` | 未确认声音授权，或无管理员权限 |
| `404` | 音色或记录不存在 |
| `409` | 请求模型与音色创建模型不一致 |
| `422` | 参数校验失败 |
| `500` | 本地运行配置错误 |
| `502` | 百炼创建、查询或合成失败 |

## 持久化

| 集合 | 用途 |
| --- | --- |
| `voice_clones` | 音色、创建模型、来源、授权确认与远端请求 ID |
| `voice_synthesis_records` | 文本、模型、状态、TTFA、RTF、格式与取消记录 |

应用启动时幂等创建索引，并预热语音 WebSocket 连接池；应用关闭时释放连接和维护线程。
