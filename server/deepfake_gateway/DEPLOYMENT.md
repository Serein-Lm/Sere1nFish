# GPU 节点部署与恢复手册

本文档是 Sere1nFish 远端换脸与实时变声节点的唯一部署入口。新节点不依赖旧 GPU
磁盘，也不依赖历史对话中的临时命令。

## 下次需要提供的信息

只需要提供以下内容：

1. GPU 公网 IP、SSH 端口和 SSH 用户。
2. 主服务器可读取的 SSH 私钥路径，默认是
   `/root/.ssh/sere1nfish_gpu_ed25519`。
3. 是否需要通过主服务器的 HTTP 代理下载官方依赖；需要时提供
   `HOST:PORT`，不提供账号密码形式的代理 URL。
4. 是否启用 OBS WHIP。默认浏览器源方案不需要 WHIP。

不要通过聊天或命令行参数提供 GPU API Token、MeanVC Token、TLS 私钥、OSS AK/SK。
部署入口会生成新的运行时 Token 和私有 CA，并通过数据库配置服务加密保存应用侧凭据。

## 节点要求

- Ubuntu 22.04 或 24.04，`x86_64`。
- NVIDIA T4 16GB 是当前已验证基线；V100 等卡可部署，但不会复用 T4 的
  `sm75` TensorRT 缓存。
- 云镜像必须已经安装可用的 NVIDIA 驱动，并能执行 `nvidia-smi`。驱动必须支持
  CUDA 12.8 容器；脚本不会冒险替换宿主机驱动。
- 建议至少 8 vCPU、32GB 内存和 150GB 系统盘。
- 主服务器到 GPU 的 SSH 必须稳定可达。

安全组：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `22/tcp` | 仅主服务器和管理 IP | 部署、私网隧道 |
| `443/tcp` | 使用 OBS 浏览器源时的客户端；首次签发公网证书时需允许 CA 访问 | HTTPS/WSS |
| `8189/tcp+udp` | 仅启用 WHIP 时的客户端 | WebRTC ICE |

禁止公网开放 `8443`、`8766`、`8554`、`8889`、`9997`、`9998`。这些模型、
RTSP、MediaMTX API 和监控端口均应只监听 GPU loopback。

Docker Engine 按 Docker 官方 Ubuntu 仓库安装；NVIDIA Container Toolkit 按
NVIDIA 官方仓库安装，并通过 `nvidia-ctk runtime configure --runtime=docker`
接入 Docker：

- <https://docs.docker.com/engine/install/ubuntu/>
- <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>

## 一键部署

在 `/root/Sere1nFish` 主服务器执行：

```bash
sudo server/deepfake_gateway/deploy/deploy-from-main.sh \
  --host <GPU_PUBLIC_IP> \
  --port 22 \
  --user root \
  --identity /root/.ssh/sere1nfish_gpu_ed25519
```

GPU 无法直连官方软件源时增加：

```bash
  --proxy-target <HTTP_PROXY_HOST>:<HTTP_PROXY_PORT>
```

脚本按以下顺序执行：

1. 固定 SSH host key，校验 NVIDIA 驱动并读取 GPU compute capability。
2. 生成新的 API Token、MeanVC Token、私有 CA 和节点证书；秘密只进入权限为
   `0600` 的临时目录和 GPU `secrets/`。
3. 建立临时反向隧道，安装 Docker、Compose 和 NVIDIA Container Toolkit。
4. 从主服务器的已校验缓存恢复模型；本地缺失时才通过统一
   `ObjectStorageService` 从私有 OSS 下载。
5. 从固定 commit 构建 FaceFusion、Gateway 和 MeanVC 镜像，启动 MediaMTX 与
   Caddy。
6. 在 GPU loopback 验证公网域名证书、Caddy、鉴权 API 和私有端口监听范围；主
   服务器不在公网 443 白名单时只告警，不阻断部署。
7. 仅在新节点健康后切换主服务器隧道，并通过配置 DAO 加密写入新的 Token 与
   CA。切换失败会恢复原 tunnel service。
8. 将成功版本原子更新到 `/opt/sere1nfish/deepfake/current`，历史 release 保留供
   回滚。

默认公网域名是 `<IP_WITH_DASHES>.sslip.io`。如已有正式域名，使用
`--public-host <HOSTNAME>`。

## 模型包与 OSS

版本清单位于 `model-bundle.manifest.json`，记录模型兼容性、对象 ID、大小和
SHA-256。当前 T4 包包含：

- FaceFusion 模型资产。
- MeanVC 模型与必要的 Torch Hub 运行代码。
- 仅适用于 compute capability `7.5` 的 TensorRT engine/timing cache。

本地归档位于 Git 忽略的
`server/data/gpu-node-bundles/<bundle_version>/`。验证本地副本：

```bash
docker-compose -f docker-compose.yml exec -T backend \
  python -m scripts.manage_gpu_node bundle-local-verify \
  --manifest /app/deepfake_gateway/model-bundle.manifest.json \
  --archive-dir /app/data/gpu-node-bundles/t4-sm75-20260817-v1 \
  --include-optional
```

同步到私有 OSS：

```bash
docker-compose -f docker-compose.yml exec -T backend \
  python -m scripts.manage_gpu_node bundle-upload \
  --manifest /app/deepfake_gateway/model-bundle.manifest.json \
  --archive-dir /app/data/gpu-node-bundles/t4-sm75-20260817-v1

docker-compose -f docker-compose.yml exec -T backend \
  python -m scripts.manage_gpu_node bundle-verify \
  --manifest /app/deepfake_gateway/model-bundle.manifest.json
```

OSS Bucket 必须保持私有读写。CLI 只能通过 `ObjectStorageService` 访问对象；不得
在脚本中调用 OSS SDK、拼接对象 key 或记录 AK/SK。本地缓存验证通过但 OSS 暂时
不可用时，仍可重建 GPU；此时不能同时释放主服务器或删除本地 bundle。

## 验证与排障

应用侧状态：

```bash
docker-compose -f docker-compose.yml exec -T backend \
  python -m scripts.manage_gpu_node status
curl -k https://127.0.0.1/health
systemctl --no-pager --full status sere1nfish-gpu-tunnel.service
```

GPU 侧状态：

```bash
cd /opt/sere1nfish/deepfake/current
docker compose --project-name sere1nfish-deepfake \
  --env-file .env -f deepfake_gateway/compose.example.yaml ps
docker compose --project-name sere1nfish-deepfake \
  --env-file .env -f deepfake_gateway/compose.example.yaml logs --tail 200
ss -lntup
```

`8443`、`8766`、`8554`、`8889`、`9997`、`9998` 出现在
`0.0.0.0` 或 `[::]` 时视为部署失败。公网证书未签发时，先检查 `443/tcp` 安全组、
DNS 和 Caddy 日志。

## 释放旧 GPU

释放前至少保证以下任一条件成立：

1. `bundle-verify` 已确认私有 OSS 中的全部对象大小和 SHA-256；或
2. `bundle-local-verify --include-optional` 已通过，并且主服务器磁盘会继续保留。

释放节点后停止指向旧 IP 的 tunnel，避免 systemd 反复重连：

```bash
sudo systemctl disable --now \
  sere1nfish-gpu-tunnel.service \
  sere1nfish-media-output-relay.service \
  sere1nfish-gpu-proxy-tunnel.service
```

不要删除数据库中的 Deepfake 配置。下一次一键部署会在新节点完全健康后原子更新
连接信息。
