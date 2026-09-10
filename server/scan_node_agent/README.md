# Sere1nFish Scan Node

独立节点只主动访问主服务 HTTPS，不连接 MongoDB/Redis，也不保存主服务 AK/SK。首次启动使用一次性 bootstrap token，换取的节点令牌保存在权限为 `0600` 的持久卷中。

```bash
export SCAN_CONTROL_PLANE_URL=https://your-domain.example/api/v1
export SCAN_NODE_BOOTSTRAP_TOKEN=snb_xxx
export SCAN_NODE_NAME=hangzhou-01
export SCAN_NODE_LABELS='{"region":"cn-hangzhou"}'
docker compose -f compose.example.yml up -d --build
```

注册成功后应从部署环境删除 `SCAN_NODE_BOOTSTRAP_TOKEN`；后续启动使用持久卷中的身份。自签发证书应挂载 CA 文件并设置 `SCAN_NODE_CA_BUNDLE`，生产环境禁止设置 `SCAN_NODE_INSECURE_TLS=true`。

示例 Compose 的构建上下文仅包含本目录。若从仓库根目录执行，使用：

```bash
docker compose -f server/scan_node_agent/compose.example.yml up -d --build
```

示例会将宿主机大小写两组 `HTTP_PROXY`、`HTTPS_PROXY` 和 `NO_PROXY` 只作为 Docker 构建代理传入，兼容 pip、apt 和 Playwright 从官方源下载依赖；这些预定义代理参数不会写入节点运行环境。

当前 HTTP worker 支持带认证 SOCKS5。浏览器 worker 在 Phase 1 直接使用 Playwright 代理配置；带认证 SOCKS5 必须先用实际代理执行显式测试，生产强制代理模式建议等待 loopback sidecar 验收完成后启用。
