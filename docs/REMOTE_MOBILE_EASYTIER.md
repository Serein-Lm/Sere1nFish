# 远程手机 EasyTier 接入说明

## 目标

使用当前公网服务器作为 EasyTier 私有共享节点。手机安装 EasyTier 与项目 Mobile Agent 后，通过前端二维码加入同一个虚拟网络；后端 peer 会同时加入该虚拟网络，并由「自动接入」通过 mDNS 或 EasyTier 虚拟网段 ADB 扫描纳入现有云手机资源池，实现远程操控、资源申请、分组隔离和唤醒/解锁操作。

参考文档：https://easytier.cn/guide/network/host-public-server.html

## 服务端配置

当前服务器已配置公网地址：

```text
https://114.55.244.3/
```

生产环境必须设置：

```env
EASYTIER_PUBLIC_HOST=<公网 IPv4 或域名>
EASYTIER_NETWORK_NAME=sere1nfish-mobile
EASYTIER_NETWORK_SECRET=<高强度随机密钥>
EASYTIER_BACKEND_IPV4=10.144.144.1
EASYTIER_AUTO_SCAN_ENABLED=true
MOBILE_AGENT_ANDROID_URL=<可选，项目 Mobile Agent APK 的站内下载地址>
DOWNLOADS_DIR=/root/Sere1nFish/downloads
```

公网安全组入站最小放行：

- `443/tcp`：网页、API、登录后 APK 下载。
- `11010/tcp`：EasyTier TCP peer，手机端最小命令默认使用这个端口。
- `11010/udp`：EasyTier UDP peer，提高 NAT/P2P 连接成功率。
- `11011/tcp`：EasyTier WebSocket peer。
- `11012/tcp`：EasyTier WSS peer。
- `11013/udp`：EasyTier WireGuard peer。

如果云厂商安全组按端口段填写：

- TCP：`443`、`11010-11012`
- UDP：最小 `11010`、`11013`；如果只能填连续端口段，可填 `11010-11013`，其中 `11011/udp` 和 `11012/udp` 当前未使用。

不要对公网开放：

- `5555/tcp`：无线 ADB 只应在 EasyTier 虚拟网络内访问。
- `8000/tcp`、`5173/tcp`、`27017/tcp`、`6379/tcp`：后端、前端开发服务、MongoDB、Redis 都只走 Docker 内网或 nginx 代理。

启动：

```sh
docker-compose -f docker-compose.yml up -d easytier-server backend frontend nginx
```

完整远程手机链路建议启动：

```sh
docker-compose -f docker-compose.yml up -d \
  easytier-server easytier-backend-peer backend frontend nginx
```

本机登录：

```text
用户名：admin
密码：admin123
访问密钥：从 MongoDB system_config.login_key 读取，当前已在前端验证可用
```

配置检查：

```sh
docker-compose -f docker-compose.yml config
curl -k https://127.0.0.1/health
```

## 高配置服务器调优

当前 Compose 已按高配置服务器做默认调优：

- 后端、前端、EasyTier、nginx、MongoDB、Redis 的 `nofile` 提高到 `1048576`。
- 后端和前端容器的 `/dev/shm` 提高到 `1g`，减少浏览器/截图/构建相关共享内存瓶颈。
- 后端运行时 `RLIMIT_NOFILE` 目标提高到 `1048576`。
- nginx `worker_connections` 提高到 `8192`。
- Chrome Docker 预热池默认提高到 `2`，可通过 `CHROME_DOCKER_WARM_POOL_SIZE` 环境变量覆盖。

## 前端操作

入口：`云手机操控台 -> 公网组网`

页面会展示：

- EasyTier 下载页
- 项目 Agent APK 下载链接
- 服务端启动参数
- 手机端入网参数
- Android 无线 ADB 配对入口
- 当前配置警告
- 远程唤醒/解锁限制说明

手机接入时应完成：

1. 导入 EasyTier 网络名、密钥和公网 peer。
2. 启动 EasyTier，加入虚拟网络。
3. Mobile Agent 开启无线 ADB 或远程控制桥接，并通过 mDNS/Agent 服务发现暴露设备。只打开 Android“USB 调试”不够，后端自动接入需要手机在 EasyTier 虚拟 IP 上监听 ADB TCP 端口。
4. 在前端点击「自动接入」。已开放 ADB 端口的手机会直接纳入资源池；只完成 EasyTier 入网但尚未 ADB 配对的手机，会以“待配对”卡片出现在设备池。
5. Android 11+ 系统自带“无线调试”需要点击这台手机卡片上的「配对」，完成 TLS 配对后再连接无线调试连接端口。
6. 给设备设置显示名、标签和分组。
7. 申请占用后进入操控台。

`Agent 组网` 和 `远程接入` 仍保留为兜底入口，用于自动发现被系统权限、ADB 授权或网络策略挡住时排障。

## 自动发现闭环

Compose 中有两个 EasyTier 角色：

- `easytier-server`：公网共享节点，负责让手机从公网入网。
- `easytier-backend-peer`：共享 backend 容器的网络命名空间，默认虚拟 IP 为 `10.144.144.1`，让后端进程可以直接访问手机的 EasyTier 虚拟 IP。

`POST /api/v1/mobile/pool/auto-connect` 会做两件事：

- 接入 AutoGLM mDNS 中状态为 `AVAILABLE_MDNS` 的设备。
- 扫描 `EASYTIER_VIRTUAL_CIDR` 中开放 `MOBILE_AGENT_ADB_PORT` 的主机，默认是 `10.144.144.0/24` 的 `5555/tcp`，扫描并发默认 `128`，单地址超时默认 `0.25s`。

手机端需要满足：

- EasyTier 已加入同一个网络。
- 手机端 Agent 或无线 ADB 已暴露 ADB TCP 端口。USB 调试只对 USB 线生效；远程自动接入需要先开启 Android“无线调试”，或通过 USB 执行 `adb tcpip 5555`。
- Android 11+ 的“无线调试”通常使用随机配对端口和连接端口。首次需要先 `adb pair <手机 EasyTier IP>:<配对端口> <6 位配对码>`，再 `adb connect <手机 EasyTier IP>:<连接端口>`。如果连接端口不是 `5555`，需要把 `MOBILE_AGENT_ADB_PORT` 改成当前连接端口，或在前端“接入远程”手工填写 `10.144.144.x:<端口>`。
- 首次 ADB 授权已经在手机上确认。

## Android 无线 ADB 配对

前端「ADB 配对」提供两种路径：

- 配对码：手机打开“开发者选项 > 无线调试 > 使用配对码配对设备”，前端在设备池里点击这台“待配对”手机的「配对」，手机 EasyTier IP 会自动带入；再填写手机显示的配对端口和 6 位配对码。这个模式不依赖 mDNS，最适合 EasyTier 远程网络。
- 二维码：前端生成 Android ADB 专用二维码，手机打开“使用二维码配对设备”扫描，再点前端“完成配对”。二维码内容形如 `WIFI:T:ADB;S:<serviceName>;P:<password>;;`，不是普通网页链接，也不包含手机 IP。手机扫描后会启动 `_adb-tls-pairing._tcp` mDNS 配对服务；如果 EasyTier 不转发 mDNS，后端可能发现不到服务，需要改用配对码模式。

配对完成后，手机“无线调试”页面会显示连接端口。这个连接端口和配对端口通常不同；自动扫描默认看 `MOBILE_AGENT_ADB_PORT=5555`，如果手机使用随机端口，需要手工连接一次或把环境变量改成当前连接端口。

公网 peer：

```text
tcp://114.55.244.3:11010
udp://114.55.244.3:11010
ws://114.55.244.3:11011
wss://114.55.244.3:11012
wg://114.55.244.3:11013
```

手机端最小命令：

```sh
easytier-core -d \
  --network-name sere1nfish-mobile \
  --network-secret <读取 /root/Sere1nFish/.env 中的 EASYTIER_NETWORK_SECRET> \
  -p tcp://114.55.244.3:11010
```

如果使用项目 Agent APK，把文件放到下载根目录：

```text
/root/Sere1nFish/downloads/mobile-agent.apk
```

然后把 `MOBILE_AGENT_ANDROID_URL` 设置为 `/api/v1/downloads/mobile-agent.apk`，前端会在登录后的「公网组网」弹窗中显示下载按钮。

EasyTier Android APK 已下载到本机随机路径：

```text
/root/Sere1nFish/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk
```

登录网页后，在「公网组网」弹窗点击 `EasyTier 下载`。对应的站内鉴权接口是：

```text
https://114.55.244.3/api/v1/downloads/mobile/easytier/f4d0f795c2dc283fff573e29690cec54/easytier-v2.6.4-arm64.apk
```

SHA256：

```text
53444ada74838e91504ae8d3ee2688bc91ee9cf43f36b969c07de6af696f723c
```

`/downloads/` 旧静态直出已删除，nginx 返回 `410 Gone`；下载走 `/api/v1/downloads/...` 后端接口，并复用登录态 Bearer Token 鉴权。未登录用户直接访问会返回 `401`，知道完整路径也不能下载。

下载映射安全边界：

- Compose 只把下载目录以只读方式挂载到后端 `/srv/downloads`。
- 当前这台服务器的运行目录默认是 `${DOWNLOADS_DIR:-./downloads}`，实际路径为 `/root/Sere1nFish/downloads`。
- 后端仓库的 `deploy/docker-compose.yml` 默认值是 `${DOWNLOADS_DIR:-../downloads}`。
- nginx 不再暴露 `/downloads/` 静态目录。
- 后端下载接口有登录鉴权和白名单，仅允许 `mobile-agent.apk`、EasyTier APK 与对应 SHA256 文件。
- EasyTier APK 放在随机目录 `mobile/easytier/f4d0f795c2dc283fff573e29690cec54/` 下，降低被枚举命中的概率；安全性仍主要依赖登录鉴权和白名单。

Chrome DevTools MCP 调试依赖官方 Chrome。本机已通过 Google 官方下载地址安装 Chrome Stable，MCP 使用 `/opt/google/chrome/chrome` 启动隔离浏览器实例。

## 资源分割

建议分组：

- `客服组`：用于 IM / 微信 / 客服场景。
- `内容组`：用于内容平台观察、发布和互动。
- `测试组`：用于新 Agent、新 ROM 和自动化流程验证。
- `保留组`：用于高优先级任务或手工排障。

资源申请规则：

- 操作前必须先「接入控制」申请占用。
- 占用基于稳定 `device_key`，USB/WiFi/虚拟 IP 重连后仍会对应同一设备。
- 释放后其他用户才能接管。
- 分组和备注按 Mongo 元数据保存，不依赖当前连接地址。

## 唤醒和解锁边界

已实现：

- `POST /api/v1/mobile/pool/wake`：亮屏，可设置充电常亮。
- `POST /api/v1/mobile/pool/wake-unlock`：亮屏、滑开锁屏，可选一次性 PIN。
- 前端设备卡片提供「唤醒解锁」入口。

限制：

- 手机完全关机时无法通过网络唤醒。
- 黑屏但系统在线、EasyTier/Agent/ADB 在线时可以远程亮屏。
- 有锁屏密码时，PIN 只作为一次性请求参数发送，不保存。
- 生物识别、强安全锁、企业管控或系统策略拦截时，需要 Mobile Agent 持有系统权限、设备管理员权限或无障碍权限配合，不能绕过 Android 安全模型。

## 验收清单

- `docker-compose -f docker-compose.yml config` 通过。
- 前端 `npm run build` 通过。
- 后端 `python3 -m py_compile server/api/routers/mobile.py server/api/routers/downloads.py server/core/mobile/easytier.py server/core/mobile/pool.py` 通过。
- `/api/v1/mobile/network/easytier/access` 登录后返回非默认公网 host、非默认网络密钥和二维码 payload。
- 手机扫码后 EasyTier peer 可连通。
- 手机虚拟 IP 可以从后端容器访问，`easytier-backend-peer` 日志正常。
- 前端「自动接入」成功发现并把手机纳入资源池；仅在自动发现不可用时才使用「Agent 组网」或「远程接入」。
- 设备可申请、释放、分组、备注。
- 黑屏在线设备可唤醒。
- 带可自动输入 PIN 的设备可完成一次性唤醒解锁。
