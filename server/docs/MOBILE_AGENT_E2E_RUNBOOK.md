# Mobile Agent E2E Runbook

本文档用于把远程 Android 手机接入 Sere1nFish，并完成 AI 读屏、操作、截图、日志和人物画像沉淀的端到端验收。

当前实现状态：

- 服务端只需要公网开放 HTTPS `443` 和 EasyTier 固定端口。
- APK 下载走后端鉴权接口，未登录用户无法下载。
- 手机加入 EasyTier 网络后，后端可以通过 mDNS 或 EasyTier 虚拟网段扫描自动发现无线 ADB 设备，不需要在系统里逐台手工登记虚拟 IP。
- 手机操作截图保存为磁盘文件，MongoDB 只保存元数据和关联信息，避免截图大对象打爆数据库。
- Token 观测和运行日志为内存环形缓存，不写入 MongoDB。

## 1. 安全组端口

服务器入站安全组只开放下面端口：

| 协议 | 端口 | 用途 |
| --- | --- | --- |
| TCP | `443` | Web 前端、后端 API、登录后 APK 下载 |
| TCP | `11010` | EasyTier TCP peer |
| TCP | `11011` | EasyTier WebSocket peer |
| TCP | `11012` | EasyTier WSS peer |
| UDP | `11010` | EasyTier UDP peer |
| UDP | `11013` | EasyTier WireGuard peer |

如果云厂商只方便填连续 UDP 段，可以使用：

```text
UDP 11010-11013
```

不要开放下面端口到公网：

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

多台手机、多台 Agent 机器接入时，服务器侧仍然使用这一组固定入站端口。客户端会使用自己的临时出站端口连接服务器的 `11010-11013`，不需要为每台手机增加安全组端口。

## 2. 服务端环境变量

生产环境至少确认这些变量：

```bash
EASYTIER_PUBLIC_HOST=<服务器公网 IP 或域名>
EASYTIER_NETWORK_NAME=sere1nfish-mobile
EASYTIER_NETWORK_SECRET=<高强度随机密钥>
EASYTIER_VIRTUAL_CIDR=10.144.144.0/24
EASYTIER_BACKEND_IPV4=10.144.144.1
EASYTIER_AUTO_SCAN_ENABLED=true
EASYTIER_ANDROID_DOWNLOAD_URL=/api/v1/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk
MOBILE_AGENT_ADB_PORT=5555
```

`EASYTIER_NETWORK_SECRET` 不能使用默认值 `change-me-before-production`。否则拿到网络名和密钥的人都可以尝试加入同一个虚拟网络。

## 3. APK 下载

EasyTier Android APK 已放在服务端本地下载目录：

```text
/root/Sere1nFish/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk
```

来源地址：

```text
https://github.com/EasyTier/EasyTier/releases/download/v2.6.4/app-arm64-release.apk
```

前端下载入口：

```text
/api/v1/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk
```

下载安全边界：

- 后端 `api/routers/downloads.py` 整个路由依赖登录用户。
- 前端 `downloadWithAuth` 只允许同源 `/api/v1/downloads/` 路径，并带 Bearer token 下载。
- Compose 把 `./downloads` 只读挂载到后端 `/srv/downloads`。
- 不要通过 nginx 暴露原始 `downloads/` 静态目录。

未登录访问下载接口应该返回 `401`。

## 4. 启动和端口核查

在服务器上确认 Compose 运行：

```bash
cd /root/Sere1nFish
docker-compose ps
```

确认公网只暴露 HTTPS 和 EasyTier 端口：

```bash
docker ps --format '{{.Names}}\t{{.Ports}}' | rg -i 'sere1nfish|easy|nginx|backend|frontend'
```

预期对宿主机开放：

```text
443/tcp
11010/tcp
11010/udp
11011/tcp
11012/tcp
11013/udp
```

健康检查：

```bash
curl -sk https://127.0.0.1/health
```

预期返回：

```json
{"status":"ok","mongodb":{"ok":true}}
```

## 5. 手机接入

1. 用浏览器登录 Sere1nFish。
2. 打开 `https://<服务器公网地址>/phone-control`。
3. 点击设备池里的“公网 EasyTier 手机接入”。
4. 下载 EasyTier APK，并在远程 Android 手机上安装。
5. 下载并导入标注版 EasyTier TOML 配置文件，让手机加入同一个 EasyTier 网络；配置文件使用 EasyTier DHCP，但通过 `-i 10.144.144.0/24` / `ipv4 = "10.144.144.0/24"` 指定固定 C 段。`[network_identity]` 段不能删除，`network_name` 和 `network_secret` 必须与服务器一致；`[[peer]]` 段也不能全部删除，至少要保留一个可访问的公网 peer，例如 `tcp://114.55.244.3:11010`。如果 Android GUI 只提示导入成功但没有写入配置，需要回到网络编辑页确认保存并启动，或把 TOML 内容粘贴到自定义配置模式。
6. 手机侧确保无线 ADB 或项目 Agent 具备远程控制能力。只开启“USB 调试”不够；自动接入需要手机在 EasyTier 虚拟 IP 上监听 ADB TCP 端口。
7. 回到设备池点击“自动接入”。系统会先尝试接入已开放 ADB 端口的设备；如果手机只是 EasyTier 已入网但 ADB 尚未配对，会在设备池里新增一张“待配对”手机卡片。
8. 在这台“待配对”手机卡片上点击“配对”，用配对码或二维码完成一次 TLS 配对，再连接手机显示的无线调试连接端口。

手机端 EasyTier 最小命令形态：

```bash
easytier-core -d -i 10.144.144.0/24 --network-name sere1nfish-mobile --network-secret <密钥> -p tcp://<服务器公网地址>:11010
```

入网以后，系统通过下面方式发现设备：

- mDNS 发现可用 Android 设备。
- EasyTier 虚拟网段扫描 `MOBILE_AGENT_ADB_PORT`，默认 `5555`。
- 扫描到开放端口后自动执行 `adb connect <虚拟 IP>:5555`。
- Android “USB 调试”只允许 USB 连接；远程自动接入还需要开启“无线调试”，或先通过 USB 执行 `adb tcpip 5555`，让手机在 TCP 端口监听。
- Android 11+ 的系统“无线调试”通常使用随机配对端口和连接端口。首次连接需要先 `adb pair <手机 EasyTier IP>:<配对端口> <6 位配对码>`，再 `adb connect <手机 EasyTier IP>:<连接端口>`。如果连接端口不是 `5555`，需要把 `MOBILE_AGENT_ADB_PORT` 改成当前连接端口，或在前端“接入远程”里手工填写 `10.144.144.x:<端口>`。

因此正常路径不需要手工填写每台手机的虚拟 IP。手机入网后会先出现在设备池里，状态为“待配对”；手工 `ip:port` 接入只作为排障兜底。

### 5.1 Android 无线 ADB 配对

Android 11+ 的“无线调试”是 TLS ADB，不等同于旧版 `adb tcpip 5555`。手机 EasyTier 入网后，还需要完成 ADB 配对和连接：

1. 手机打开“开发者选项 > 无线调试”，允许当前网络。
2. 推荐路径：点“使用配对码配对设备”，记录手机显示的 IP、配对端口和 6 位配对码。前端设备池会展示已入网手机，例如 `test1 / 10.144.144.2`，点击这张卡片的“配对”；手机 IP 会自动带入，配对端口填手机显示的端口，配对码填 6 位数字；如果手机同时显示连接端口，也填到“连接端口”。
3. 二维码路径：前端“ADB 配对 > 二维码 > 生成二维码”，手机点“使用二维码配对设备”扫描前端二维码，然后前端点“完成配对”。二维码内容是 Android ADB 专用 payload：`WIFI:T:ADB;S:<serviceName>;P:<password>;;`，不是普通网页链接，也不会直接写入手机 IP。
4. 二维码配对依赖手机广播 `_adb-tls-pairing._tcp` mDNS 服务。EasyTier 如果不转发 mDNS，二维码完成步骤可能发现不到手机；此时改用配对码模式。
5. 配对只需要做一次。以后手机和后端在同一 EasyTier 网络内时，直接连接手机“无线调试”页面显示的连接端口即可。

后端镜像必须使用 Google 官方 Android SDK Platform Tools。Debian 仓库里的旧 `android-tools-adb` 可能没有 `adb pair` 和 `adb mdns`，不能完成 Android 11+ 无线调试配对。

## 6. 端到端验收

真机在线以后按下面顺序验收。

### 6.1 设备在线

登录后在浏览器或接口里检查：

```text
GET /api/v1/mobile/overview
GET /api/v1/mobile/devices
GET /api/v1/mobile/pool
```

验收标准：

- `/mobile/overview` 中 `devices.total > 0`。
- 至少一个设备 `status == "device"`。
- 设备池里能看到 `online: true`。

### 6.2 截图和读屏

在 `phone-control` 页面选择设备，执行截图或读屏。

验收标准：

- 页面能显示真实手机截图。
- 截图文件写入服务端磁盘。
- MongoDB 只保存截图元数据、路径、项目 ID、设备 ID、任务 ID 等关联字段。
- 截图读取必须走鉴权接口，不能暴露裸文件路径。

### 6.3 AI 操作

选择在线设备后执行一个简单目标，例如：

```text
打开微信，读取当前聊天页面内容，只分析不发送。
```

再执行一个低风险动作目标，例如：

```text
打开设置页面并返回桌面。
```

验收标准：

- Planner 能生成步骤。
- Executor 每步能读屏、截图、执行点击/滑动/输入等动作。
- 失败时能带屏幕状态重规划。
- 操作日志不包含截图 base64 大字段。
- 运行日志可以按项目、任务、设备回查。

### 6.4 人物画像沉淀

在聊天页面执行读屏和人物画像分析。

验收标准：

- 能从聊天上下文沉淀联系人画像。
- 画像与 `project_id`、`contact_id`、设备、任务或会话关联。
- 项目详情页的“手机操作”区域能看到画像、截图、操作记录。

### 6.5 项目关联

从项目详情进入手机操作流程，或执行任务时传入 `project_id`。

验收标准：

- 项目详情页能读取该项目下的手机截图。
- 项目详情页能读取该项目下的操作日志。
- 项目详情页能读取该项目下的人物画像。
- 不同项目的数据不会混在一起。

## 7. 观测验收

Token 观测：

```text
GET /api/v1/observability/turns?limit=20
```

验收标准：

- 返回每轮 `items`、`total`、`limit`。
- 每轮能看到模型、输入 token、输出 token、总 token、费用、耗时或错误。
- 数据来自内存缓存，不写入 MongoDB。

运行日志：

```text
GET /api/v1/observability/logs?limit=100
```

验收标准：

- 能看到近期运行日志。
- 日志来自内存环形缓存，不写入 MongoDB。
- 大截图、视频帧、base64 不进入日志。

## 8. 排障

手机不在线：

- 检查安全组是否放行 `TCP 443`、`TCP 11010-11012`、`UDP 11010`、`UDP 11013`。
- 检查 `EASYTIER_PUBLIC_HOST` 是否是手机能访问到的公网 IP 或域名。
- 检查 `EASYTIER_NETWORK_NAME` 和 `EASYTIER_NETWORK_SECRET` 是否一致。
- 检查手机是否已经加入 EasyTier 网络。
- 检查手机无线 ADB 或项目 Agent 是否可用。
- 点击设备池“自动接入”，不要优先手工填写 IP。

下载失败：

- `401` 表示未登录或 token 失效，重新登录后下载。
- `404` 表示下载路径不在后端白名单内，确认使用 `/api/v1/downloads/mobile/easytier/...`。
- 不要访问原始 `/downloads/...` 静态路径。

ADB 不通：

- 不要把 `5555` 暴露到公网安全组。
- 只允许在 EasyTier 虚拟网络内访问手机的 `5555`。
- 确认 Android 无线调试已开启；Android 11+ 首次需要先完成 `adb pair`，配对端口和连接端口通常不是同一个端口。
- 如果前端二维码配对提示未发现服务，说明 `_adb-tls-pairing._tcp` mDNS 没有穿过当前网络，改用配对码。
- 确认无障碍权限、常亮/解锁策略已经配置。
- 完全关机的手机无法通过网络唤醒。

AI 无法操作：

- 先检查 `/api/v1/mobile/overview` 里的 LLM 配置是否就绪。
- 在“配置管理”里确认 mobile planner、executor、screen、chat 模型已经配置。
- 先跑截图和读屏，再跑带动作的任务。

## 9. 回归命令

后端窄范围测试：

```bash
docker exec sere1nfish_backend_1 pytest -q \
  test_server/tests/test_config_observability.py \
  test_server/tests/test_voice_tts.py \
  test_server/tests/test_bailian_aigc_client.py \
  test_server/tests/test_mobile_project_artifacts.py \
  test_server/tests/test_mobile_agent_pipeline.py
```

后端语法检查：

```bash
docker exec sere1nfish_backend_1 python -m compileall -q api core Sere1nGraph crawler_tools browser_manager scripts
```

Compose 配置检查：

```bash
docker-compose -f /root/Sere1nFish/docker-compose.yml config
```

前端构建：

```bash
cd /root/Sere1nFish/view
npm run build
```

前端页面变更需要使用 Codex `chrome-devtools` MCP 打开页面做浏览器验证，重点检查：

- `/phone-control` 的 EasyTier 弹窗。
- `/settings/config` 的运行配置和 mobile 模型配置。
- `/ai-tools` 的 TTS、图片编辑、视频生成。
- `/dashboard` 的全局观测图表。

## 10. 当前未能自动完成的验收

没有真实手机在线时，只能证明服务端、前端、配置、下载、安全组和单元测试链路可用；不能证明真机端到端已经完成。

真机上线后必须补充下面证据：

- `/api/v1/mobile/overview` 显示 `devices.online > 0`。
- 设备池自动接入成功。
- 真实截图落盘并能通过鉴权接口读取。
- AI 任务能读屏、操作、失败重规划。
- 人物画像、截图、操作日志能在项目详情里按项目读取。
