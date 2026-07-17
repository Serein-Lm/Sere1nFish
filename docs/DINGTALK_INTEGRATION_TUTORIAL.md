# Sere1nFish 钉钉接入与测试教程

本文按当前 Sere1nFish 实现编写，覆盖 AI 中枢双向问答、流式 AI Card、执行进度、多格式产物下载、主动通知 Webhook，以及白名单和安全组要求。

最后核验日期：2026-07-17。

## 1. 先选择正确的接入方式

当前系统支持三条彼此独立的钉钉链路：

| 使用目的 | 钉钉侧类型 | Sere1nFish 配置 | 推荐级别 |
| --- | --- | --- | --- |
| 在钉钉中向 AI 中枢提问并获得流式回答 | 企业内部应用机器人，Stream Mode | `Client ID`、`Client Secret` | 推荐 |
| 由任务、告警或业务事件主动向群发送通知 | 群自定义机器人 Webhook | `Access Token`、加签 `Secret` | 按需启用 |
| 兼容旧版群机器人 HTTP 回调 | Outgoing/Webhook 回调 | `outgoing_app_secret` 和公网回调 URL | 仅兼容旧系统 |

推荐组合：

1. 先配置 Stream Mode，验证 AI 中枢双向问答。
2. 需要主动告警时，在同一个 `default` 配置中补充 Webhook Token 和加签 Secret。
3. 不新建旧 HTTP 回调；现有系统迁移时才使用。

应用机器人和群自定义机器人不是同一种机器人。只创建群自定义机器人，无法获得当前系统的 Stream AI 对话能力。

## 2. 当前钉钉输出能力

Stream 接入后，一次 AI 中枢任务会按以下方式展示：

1. AI Card 先展示当前阶段、已完成阶段数、工具调用数和耗时。
2. 工作流执行期间持续更新阶段输出，避免长任务只显示转圈。
3. 最终以结构化 Markdown 展示结论、执行统计和交付产物清单。
4. 当前 AI 中枢可生成并保存 `Word`、`Markdown`、`TXT`、`JSON`、`CSV` 产物。
5. 产物文件写入统一 OSS，MongoDB 保存元信息，通过登录鉴权后下载。
6. 钉钉卡片创建失败时自动回退为普通 Markdown 回复。

卡片展示的是可审计的计划、阶段、工具状态和阶段结论，不展示模型内部不可验证的隐式推理文本。

## 3. 前置条件

开始前确认：

- 你有目标钉钉组织的内部应用创建和发布权限。
- Sere1nFish 后端运行正常。
- 后端容器可以出站访问互联网 `443/tcp`。
- AI 中枢的 LLM 配置可用，否则 Stream 会连接成功但无法生成回答。
- 需要从钉钉打开产物时，准备一个指向 Sere1nFish `443` 的公网域名和可信 HTTPS 证书。

当前服务器基础检查：

```bash
curl -k https://127.0.0.1/health
```

预期：

```json
{"status":"ok","mongodb":{"ok":true}}
```

## 4. 创建 Stream 应用机器人

### 4.1 创建企业内部应用

1. 打开[钉钉开发者后台](https://open-dev.dingtalk.com/)。
2. 登录后选择机器人要归属的组织。
3. 进入“应用开发”，创建企业内部应用。
4. 填写应用名称和描述并保存。

不要从旧的独立“机器人”入口创建。钉钉当前推荐先创建应用，再在应用内开启机器人能力。

### 4.2 获取应用凭证

在应用详情的“应用信息”中复制：

| 钉钉字段 | Sere1nFish 字段 |
| --- | --- |
| Client ID / AppKey | `Client ID（AppKey）` |
| Client Secret / AppSecret | `Client Secret（AppSecret）` |

不要把凭证写入 Git、教程、群消息或截图。

### 4.3 开启并发布机器人

1. 在应用左侧进入“机器人与消息推送”。
2. 开启“机器人配置”。
3. 消息接收模式选择 **Stream Mode**。
4. 完成机器人名称、头像和简介等基础信息。
5. 发布应用和机器人配置。

官方步骤见[创建 Stream 机器人应用](https://opensource.dingtalk.com/developerpedia/docs/explore/tutorials/stream/bot/nodejs/create-bot/)。

## 5. 在 Sere1nFish 中配置 Stream

1. 使用管理员账号登录 Sere1nFish。
2. 打开 `https://<你的域名或服务器地址>/settings/config`。
3. 进入“钉钉机器人”页签。
4. 点击“添加机器人”。
5. 按下表填写并保存。

| 页面字段 | 建议值 | 说明 |
| --- | --- | --- |
| 机器人名称 | `default` | 通知 Hook 和旧回调默认使用该名称 |
| 启用 Stream Mode | 开启 | AI 中枢双向交互必需 |
| Client ID（AppKey） | 钉钉应用 Client ID | 不是 Webhook Token |
| Client Secret（AppSecret） | 钉钉应用 Client Secret | 加密存储 |
| AI Card 流式回答 | 开启 | 展示执行阶段、进度、实时输出和最终产物 |
| 公网访问地址 | 首次可留空 | 产物按钮需要，例如 `https://fish.example.com` |
| 断线重连间隔 | `5` 秒 | 允许范围 `2-60` 秒 |
| Access Token / 签名密钥 / 关键词 | 首次留空 | 仅用于主动通知 Webhook |
| 旧回调 App Secret | 留空 | Stream Mode 不使用 |

保存后，后端会立即重载该机器人，不需要重启容器。点击列表中的刷新按钮，状态应从“Stream 未连接”变为“Stream 已连接”。

如果状态仍为“未连接”或“重连中”，先查看列表中显示的错误，再执行：

```bash
docker logs --since 10m sere1nfish_backend_1 2>&1 | grep -E "钉钉|DingTalk|dingtalk"
```

连接成功时日志包含：

```text
钉钉 Stream 已连接 bot=default
```

## 6. 测试 Stream AI 对话

### 6.1 单聊测试

1. 在钉钉客户端搜索机器人名称。
2. 在搜索结果的“功能”分类中进入机器人单聊。
3. 发送：

```text
请只回复“Stream 接入成功”，并展示本次执行进度。
```

### 6.2 群聊测试

1. 使用机器人应用所属组织的企业内部群。
2. 在“群设置 -> 机器人 -> 添加机器人”中添加该应用机器人。
3. 在群内发送：

```text
@机器人 请只回复“群聊接入成功”。
```

群聊必须 `@机器人`，否则钉钉不会把消息投递给应用；单聊不需要 `@`。官方测试说明见[体验聊天机器人](https://opensource.dingtalk.com/developerpedia/docs/explore/tutorials/stream/bot/go/test-bot/)。

### 6.3 多格式产物测试

依次发送以下请求：

```text
生成一份“钉钉接入测试报告”，包含当前结论和检查项，输出 Word。
```

```text
把本次测试结果同时整理为 Markdown 和 JSON 两个可下载产物。
```

预期结果：

- 卡片执行中持续显示阶段和工具状态。
- 最终卡片出现“交付产物”清单。
- 配置了“公网访问地址”时，卡片出现“打开/下载 Word”等按钮。
- 点击按钮先进入 AI 中枢；未登录时会跳转登录页，登录成功后自动返回对应产物，随后仍需通过所有权校验才能下载。

需要结合公网检索生成有来源的载荷 Word 时，可以发送：

```text
检索公开资料，核验来源后生成一份带来源清单的 Word 调研报告。
```

## 7. 配置主动通知 Webhook

Stream 负责接收提问并回复；任务失败、扫描完成、告警等主动消息由群自定义机器人 Webhook 发送。

### 7.1 在钉钉群创建自定义机器人

1. 打开目标群的机器人管理。
2. 添加“自定义机器人”。
3. 安全设置选择 **加签**，复制以 `SEC` 开头的 Secret。
4. 复制完整 Webhook URL。
5. 从 URL 的 `access_token=` 后提取 Access Token。

官方入口见[创建自定义机器人](https://open.dingtalk.com/document/orgapp/custom-robot-access)。

当前 Sere1nFish Webhook 适配器要求 `Access Token` 和加签 `Secret` 同时存在，因此不要只配置 Token。

### 7.2 可选安全项

- 自定义关键词：可以额外启用；在钉钉和 Sere1nFish 中填写完全相同的关键词。系统会自动把关键词加入通知正文。
- IP 地址段：可以额外启用，但不是必需。启用后填写后端容器的实际公网出口 IP。

当前后端容器直连公网出口实测为：

```text
114.55.244.3
```

等价的单地址 CIDR 是 `114.55.244.3/32`。钉钉页面若只接受单个 IP，则填写 `114.55.244.3`。

部署、NAT、EIP 或代理策略变化后，使用下面的命令重新确认，不能用浏览器访问看到的 IP 或宿主机代理出口代替：

```bash
docker exec sere1nfish_backend_1 curl -fsS https://ifconfig.me/ip
```

### 7.3 写入 Sere1nFish

编辑 `default` 机器人，在“主动通知 Webhook”区域填写：

| 页面字段 | 值 |
| --- | --- |
| Access Token | Webhook URL 的 `access_token` 参数 |
| 签名密钥（Secret） | 钉钉安全设置中的 `SEC...` |
| 关键词 | 仅在钉钉启用了关键词时填写 |

保存后，点击机器人列表中的发送按钮。该按钮只测试 Webhook 主动通知，不测试 Stream。成功时目标群会收到“钉钉机器人配置测试”。

## 8. 白名单和安全组结论

### 8.1 推荐 Stream Mode

| 项目 | 是否需要 | 正确配置 |
| --- | --- | --- |
| 新增公网入站端口 | 不需要 | 保持项目原有 `443` 和 EasyTier 端口即可 |
| 钉钉回调 URL | 不需要 | Stream 是后端主动建立反向 TLS 长连接 |
| 钉钉侧 IP 白名单 | 不需要 | Stream 官方定义为无需防火墙白名单 |
| 出站域名白名单 | 仅在服务器限制出站时需要 | 放行下面两个域名的 `443/tcp` |

出站域名：

```text
api.dingtalk.com:443
wss-open-connection.dingtalk.com:443
```

不要把这两个域名当前解析出的 IP 固化到白名单。钉钉采用多节点并会调整接入 IP，官方不提供固定 IP 列表。详见 [Stream Mode 概述](https://opensource.dingtalk.com/developerpedia/docs/learn/stream/overview/) 和 [Stream Mode 常见问题](https://opensource.dingtalk.com/developerpedia/docs/learn/stream/faq/)。

当前后端容器连通性检查：

```bash
docker exec sere1nfish_backend_1 sh -lc \
  "curl -sS -o /dev/null -w 'api=%{http_code} tls=%{time_appconnect}s\n' https://api.dingtalk.com/; \
   curl -sS -o /dev/null -w 'wss=%{http_code} tls=%{time_appconnect}s\n' https://wss-open-connection.dingtalk.com/"
```

`wss-open-connection.dingtalk.com` 的普通 HTTPS 根路径返回 `404` 是正常的；重点是 DNS、TCP 和 TLS 可以建立。

### 8.2 主动通知 Webhook

| 项目 | 是否需要 | 正确配置 |
| --- | --- | --- |
| 新增公网入站端口 | 不需要 | 后端只向钉钉发出 HTTPS 请求 |
| 出站访问 | 需要 | `oapi.dingtalk.com:443` |
| 钉钉机器人 IP 地址段 | 可选 | 启用时填后端直连出口 `114.55.244.3` |
| 加签 Secret | 当前实现必需 | 使用 `SEC...` Secret |

建议使用“加签 + 可选关键词”；出口 IP 稳定且你愿意维护变更时，再叠加 IP 地址段。

### 8.3 旧 HTTP 回调

旧模式的回调地址是：

```text
https://<可信公网域名>/api/v1/dingtalk/callback
```

它需要公网入站 `443/tcp`、可信域名证书和 `outgoing_app_secret` 验签。当前 nginx 证书仅包含 `localhost` 和 `127.0.0.1`，不能作为公网钉钉回调证书，因此当前环境不要启用旧回调模式。

### 8.4 产物打开地址

“公网访问地址”不参与 Stream 建连，只用于卡片中的产物按钮。要求：

- 使用 `https://`。
- 指向 Sere1nFish 的 `443`。
- 使用可信 CA 签发且与域名匹配的证书。
- 不在 URL 中携带 Token、Secret 或 OSS AK/SK。
- 产物仍经过 Sere1nFish 登录和所有权鉴权，不暴露永久公开 OSS URL。

## 9. 配置和凭证安全

- `Client Secret`、Webhook `Access Token`、加签 `Secret` 和旧回调 Secret 写入 MongoDB 前会加密。
- 普通配置查询只返回脱敏值。
- 只有管理员通过二级密码临时解锁后才能查看明文。
- 编辑已有配置时，Secret 留空会保留数据库中的原值。
- 不要把钉钉凭证放入 `.env`、代码、Git commit、日志或聊天记录。
- 凭证发生泄露时，应立即在钉钉后台轮换，并在系统配置中更新。

## 10. 常见故障

### Stream 一直未连接

按顺序检查：

1. `Client ID` 和 `Client Secret` 是否属于同一个应用。
2. 应用内机器人是否已开启、选择 Stream Mode 并发布。
3. Sere1nFish 机器人总开关和 Stream 开关是否都开启。
4. 后端容器是否能访问两个钉钉域名的 `443`。
5. 刷新状态并读取 `stream_last_error` 和后端日志。

### 已连接但收不到群消息

- 群必须属于应用所在组织。
- 应用机器人必须已经添加到该群。
- 群消息必须 `@机器人`。
- 单聊时从搜索结果的“功能”分类进入机器人。

### 卡片有进度但最终回答失败

- 检查 LLM API Key、模型配置和模型网关。
- 查看 Dashboard/Observability 中 `dingtalk_hub` 的 token 与错误归因。
- AI Card 更新失败时系统会尝试回退 Markdown；同时检查钉钉应用机器人权限和发布状态。

### 产物已生成但没有下载按钮

- 在 Sere1nFish 机器人配置中填写“公网访问地址”。
- 地址必须是浏览器和手机均可访问的 HTTPS 地址。
- 当前 `localhost` 自签证书不能用于公网产物入口。
- 用户打开产物页后仍需登录；普通用户只能访问归属于自己的产物，管理员可以按权限访问。
- 从钉钉 WebView 首次打开时，系统会保留产物深链，登录成功后自动回到原产物抽屉。

### Webhook 测试按钮不可用或测试失败

- 发送按钮只在已保存 Access Token 时启用。
- 当前后端还要求加签 Secret；两者必须同时配置。
- 若钉钉启用了关键词，确认系统填写了完全相同的关键词。
- 若钉钉启用了 IP 地址段，确认填写的是容器直连出口 `114.55.244.3`。
- Webhook 机器人只能主动向其所在群发消息，不能替代 Stream 应用机器人接收 AI 问答。

## 11. 可选 API 验证

以下接口都需要登录 Token；不要把真实 Token 写入脚本仓库。

```bash
curl -k 'https://127.0.0.1/api/v1/config/dingtalk' \
  -H 'Authorization: Bearer <JWT>'
```

```bash
curl -k 'https://127.0.0.1/api/v1/config/dingtalk/default/status' \
  -H 'Authorization: Bearer <JWT>'
```

```bash
curl -k -X POST 'https://127.0.0.1/api/v1/config/dingtalk/default/test' \
  -H 'Authorization: Bearer <JWT>'
```

第三个接口测试的是 Webhook 主动通知，不是 Stream 接收连接。

## 12. 最短验收清单

- [ ] 企业内部应用已创建并发布机器人。
- [ ] 消息接收模式为 Stream Mode。
- [ ] Sere1nFish 已保存 `default` 的 Client ID 和 Client Secret。
- [ ] 页面状态显示“Stream 已连接”。
- [ ] 单聊可以收到带阶段进度的 AI Card。
- [ ] 群内 `@机器人` 可以收到回答。
- [ ] Word 和 JSON 测试产物均已生成。
- [ ] 配置可信公网地址后，钉钉产物按钮可以打开并下载。
- [ ] 如需主动通知，Webhook Token 和加签 Secret 测试成功。
- [ ] 安全组没有新增 `8000`、`5173`、`9222`、`27017`、`6379` 等公网端口。
