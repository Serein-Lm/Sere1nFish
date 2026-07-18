# Chrome Docker 容器化 — 实现文档

## 1. 概述

本项目将所有浏览器操作（爬虫、截图）从本地 Chrome 迁移到 Docker 容器化的 Chrome 实例。通过一个透明代理层（`browser_manager`），业务代码几乎零改动即可在本地模式和 Docker 模式之间切换。

核心目标：
- 无 GUI 服务器可部署（Xvfb 提供虚拟显示）
- 多任务并发时动态创建多个 Chrome 容器
- 容器生命周期与扫描任务对齐，空闲自动回收
- VNC 远程查看浏览器桌面（带鉴权）
- 前端配置页 / MongoDB `chrome_docker` 配置段切换本地/Docker 模式

---

## 2. 项目结构

```
项目根目录/
├── browser_manager/                  # 透明代理层（独立于 MediaCrawler）
│   ├── __init__.py                   # 导出 get_browser_provider, shutdown_provider 等
│   └── provider.py                   # BrowserProvider / DockerProvider / LocalProvider
│
├── chrome-browser/                   # Docker 镜像构建目录
│   ├── Dockerfile                    # 基于 debian:bookworm-slim + Google Chrome
│   ├── entrypoint.py                 # Python 进程管理 + FastAPI 控制 API
│   ├── chrome.sh                     # Chrome 启动参数脚本
│   ├── requirements.txt              # 容器内 Python 依赖
│   └── .dockerignore
│
├── api/routers/config.py             # chrome_docker 配置段通过 MongoDB 加密配置维护
│
├── MediaCrawler/tools/cdp_browser.py # CDPBrowserManager（改动约 20 行）
├── crawler_tools/screenshot_tool.py  # 截图工具（改动约 15 行）
├── api/services/xhs_vision_tools.py  # 小红书截图（改动约 15 行）
├── api/routers/browser.py            # 容器池管理 API
├── api/main.py                       # 注册 browser router + shutdown 清理
│
└── test_server/tests/
    └── test_docker_chrome.py         # Docker 容器化测试（6 个测试项）
```

---

## 3. 架构设计

### 3.1 透明代理层

```
DouyinCrawler / XhsCrawler / screenshot_tool / xhs_vision_tools
        │
        ▼
CDPBrowserManager.launch_and_connect()
        │
        ▼
BrowserProvider.get_cdp_endpoint()     ← 透明层入口
        │
   ┌────┴────┐
   │         │
LocalProvider  DockerProvider
(返回 None,    (docker-py 动态
 走原有逻辑)    创建/管理容器)
   │              │
   ▼              ▼
本地 Chrome    Docker Chrome 容器
```


### 3.2 业务代码改动点

| 文件 | 改动内容 | 改动量 |
|------|----------|--------|
| `MediaCrawler/tools/cdp_browser.py` | `launch_and_connect()` 开头加 `_try_get_remote_cdp()` 判断 | ~20 行 |
| `crawler_tools/screenshot_tool.py` | `screenshot_page_stream()` 里 launch 前尝试 Docker 连接 | ~15 行 |
| `api/services/xhs_vision_tools.py` | `screenshot_user_profile_stream()` 同上 | ~15 行 |
| `api/main.py` | 注册 browser router + lifespan 里 shutdown_provider | ~5 行 |

不需要改动的文件：
- `crawler_tools/douyin_crawler.py` — 调用 CDPBrowserManager，内部透明切换
- `crawler_tools/xhs_crawler.py` — 同上
- 所有 Pipeline / DAO / Router — 完全不感知浏览器来源

### 3.3 容器生命周期

```
请求 CDP 端点
    │
    ├── 有空闲容器 → 复用（标记 busy）
    │
    └── 无空闲容器
         ├── 未达上限 → docker run 创建新容器 → 等待健康检查 → 返回 CDP URL
         └── 已达上限 → 等待空闲容器（最多 60s）

任务完成 → release_cdp_endpoint() → 容器标记 idle

后台 idle_reaper 协程：
    每 30s 检查一次
    空闲超过 idle_timeout（默认 300s）→ 自动 stop + remove
```

---

## 4. Docker 镜像

### 4.1 构建

```bash
docker build -t chrome-browser:latest ./chrome-browser
```

### 4.2 Dockerfile 要点

- 基础镜像：`debian:bookworm-slim`（Debian 12）
- 浏览器：`google-chrome-stable`（不是 Chromium，反检测兼容性更好）
- 显示：Xvfb 虚拟显示 + Fluxbox 窗口管理器
- 远程桌面：TigerVNC + noVNC（浏览器访问桌面）
- 中文支持：`fonts-wqy-zenhei` + `fonts-noto-cjk` + `zh_CN.UTF-8` locale
- 时区：`Asia/Shanghai`
- 入口：Python FastAPI 进程管理器

### 4.3 层优化

- 系统依赖 + locale + 时区合并为单个 RUN 层
- noVNC 用 `wget` + `tar` 安装（不依赖 git）
- Python 依赖独立层（利用 Docker 层缓存）
- 应用文件 COPY 放在最后（变更频率最高）
- `.dockerignore` 排除无关文件

### 4.4 容器内进程

entrypoint.py 管理 5 个子进程：

| 进程 | 说明 |
|------|------|
| Xvfb | 虚拟 X11 显示服务（:99） |
| Fluxbox | 窗口管理器（所有窗口自动最大化，隐藏工具栏） |
| Chrome | Google Chrome（headed 全屏模式，CDP 端口 9222） |
| TigerVNC | VNC 服务（端口 5900，支持密码鉴权） |
| noVNC | WebSocket VNC 代理（端口 6080，浏览器访问桌面） |

watchdog 协程每 3 秒检查子进程状态，挂了自动拉起（最多重启 10 次）。

### 4.5 桌面环境

容器内只跑 Chrome，桌面环境做了以下优化：

- Fluxbox `apps` 配置：所有窗口自动最大化
- Fluxbox `init` 配置：隐藏工具栏、去掉窗口装饰（标题栏/边框）
- Chrome 启动参数：`--start-maximized --start-fullscreen`
- VNC 连进去看到的是纯粹的全屏 Chrome，没有桌面、没有任务栏

### 4.6 容器端口

| 端口 | 用途 | 宿主机映射 |
|------|------|-----------|
| 9222 | CDP WebSocket | 动态分配（从 cdp_port_start 递增） |
| 5900 | VNC | 动态分配（从 vnc_port_start 递增） |
| 6080 | noVNC（浏览器访问桌面） | 动态分配（从 novnc_port_start 递增） |
| 8250 | 控制 API | 动态分配（从 api_port_start 递增） |

### 4.7 容器控制 API

FastAPI 服务运行在容器内 8250 端口：

| 接口 | 方法 | 说明 | 鉴权 |
|------|------|------|------|
| `/health` | GET | Chrome 进程 + CDP 可达性检查 | 免鉴权 |
| `/cdp/info` | GET | 返回 CDP WebSocket URL、Browser 版本、UA | 需 Token |
| `/chrome/restart` | POST | 重启 Chrome 进程（清理内存泄漏） | 需 Token |
| `/status` | GET | 所有子进程状态 + CPU/内存占用 | 需 Token |
| `/vnc/url` | GET | 返回 VNC/noVNC 访问地址 | 需 Token |

---

## 5. 安全鉴权

### 5.1 VNC 鉴权

- 通过环境变量 `VNC_PASSWORD` 设置 VNC 密码
- entrypoint.py 启动时调用 `tigervncpasswd` 生成密码文件
- TigerVNC 使用 `VncAuth` 安全类型，连接时必须输入密码
- noVNC 页面会弹出密码输入框（底层连接的是带密码的 VNC）
- 不设置密码时使用 `SecurityTypes=None`（仅开发环境）

### 5.2 控制 API 鉴权

- 通过环境变量 `API_TOKEN` 设置 Bearer Token
- 启用后，除 `/health` 外所有接口需要 `Authorization: Bearer <token>` 头
- 不设置 Token 时不鉴权（仅开发环境）

### 5.3 配置

MongoDB `chrome_docker` 配置段：

```json
{
  "config": {
    "vnc_password": "chrome@2026",
    "api_token": ""
  }
}
```

`DockerProvider` 创建容器时自动将密码和 Token 通过环境变量传入容器。

---

## 6. browser_manager 模块

### 6.1 ChromeDockerConfig

从 MongoDB `chrome_docker` 配置段加载，配置由前端配置页或 `/api/v1/config/sections/chrome_docker` 写入：

```json
{
  "config": {
    "enabled": true,
    "image": "chrome-browser:latest",
    "max_containers": 5,
    "reserved_non_bulk_containers": 2,
    "idle_timeout": 300,
    "shm_size": "2g",
    "screen_width": 1920,
    "screen_height": 1080,
    "timezone": "Asia/Shanghai",
    "cdp_port_start": 9222,
    "api_port_start": 8250,
    "vnc_port_start": 5900,
    "novnc_port_start": 6080,
    "vnc_password": "chrome@2026",
    "api_token": ""
  }
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `enabled` | 是否启用 Docker 模式 | `true` |
| `image` | Docker 镜像名 | `chrome-browser:latest` |
| `max_containers` | 最大容器数 | `5` |
| `reserved_non_bulk_containers` | 为公众号归档、学者采集等非批量链路保留的容器数 | `2` |
| `idle_timeout` | 空闲超时（秒） | `300` |
| `shm_size` | 共享内存大小 | `2g` |
| `screen_width/height` | 虚拟屏幕分辨率 | `1920x1080` |
| `timezone` | 时区 | `Asia/Shanghai` |
| `cdp_port_start` | CDP 端口起始值 | `9222` |
| `vnc_password` | VNC 密码 | `chrome@2026` |
| `api_token` | 控制 API Token | 空（不鉴权） |


### 6.2 DockerProvider 核心方法

```python
class DockerProvider(BrowserProvider):
    async def get_cdp_endpoint(task_id: str) -> str
        # 获取 CDP WebSocket URL，必要时动态创建容器

    async def release_cdp_endpoint(task_id: str)
        # 释放任务占用的容器，标记为 idle

    async def shutdown()
        # 销毁所有容器

    async def get_pool_status() -> list[dict]
        # 获取容器池状态
```

### 6.3 端口分配

每个新容器的端口 = 起始端口 + 偏移量（递增）：

```
容器 0: CDP=9222, API=8250, VNC=5900, noVNC=6080
容器 1: CDP=9223, API=8251, VNC=5901, noVNC=6081
容器 2: CDP=9224, API=8252, VNC=5902, noVNC=6082
...
```

### 6.4 全局单例

```python
from browser_manager import get_browser_provider, shutdown_provider

# 获取 provider（根据 MongoDB chrome_docker 配置自动选择 Docker/Local）
provider = get_browser_provider()

# 应用退出时调用
await shutdown_provider()
```

---

## 7. 日志体系

连接链路的每个关键步骤都有详细日志，出问题时可以快速定位：

```
[CDPBrowserManager] 尝试获取远程 CDP | provider=DockerProvider | task_id=xhs-kw1
[DockerProvider] 请求 CDP 端点 | task_id=xhs-kw1
[DockerProvider] 无空闲容器 | 活跃容器数=0/5
[DockerProvider] 为 task xhs-kw1 创建新容器...
[DockerProvider] 开始创建容器 chrome-a1b2c3d4 | 镜像=chrome-browser:latest | 端口分配: CDP=9222, API=8250, VNC=5900, noVNC=6080
[DockerProvider] VNC 鉴权已启用 (密码长度=11)
[DockerProvider] 容器 chrome-a1b2c3d4 已创建 (id=abc123def456) | docker run 耗时 1.2s
[DockerProvider] 容器 chrome-a1b2c3d4 健康检查通过 | 尝试 8 次 | 耗时 8.3s
[DockerProvider] 容器 chrome-a1b2c3d4 就绪 | 健康检查耗时 8.3s | 总启动耗时 9.5s
[DockerProvider] 获取 CDP WS URL | 容器=chrome-a1b2c3d4 | CDP 地址=http://localhost:9222
[DockerProvider] CDP WS URL 获取成功 | Browser=Chrome/126.0.6478.126 | WS=ws://localhost:9222/devtools/browser/xxx
[DockerProvider] 新容器 chrome-a1b2c3d4 已分配给 task xhs-kw1 | CDP=9222, VNC=5900, noVNC=6080
[CDPBrowserManager] 远程 CDP 端点获取成功: ws://localhost:9222/devtools/browser/xxx
[CDPBrowserManager] Docker 模式: 连接远程 Chrome → ws://localhost:9222/devtools/browser/xxx
[CDPBrowserManager] Docker Chrome 连接成功 | version=126.0.6478.126 | contexts=1
```

日志级别：
- `INFO`：正常流程（创建、连接、释放、销毁）
- `DEBUG`：健康检查轮询、忽略的释放请求
- `WARNING`：fallback 到本地模式、销毁失败
- `ERROR`：超时、连接失败

---

## 8. 应用层集成

### 8.1 容器池管理 API

`api/routers/browser.py` 提供 3 个接口：

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/browser/pool/status` | GET | 容器池状态（容器列表、busy/idle 数量） |
| `/api/v1/browser/pool/config` | GET | Docker Chrome 配置 |
| `/api/v1/browser/pool/shutdown` | POST | 关闭所有容器 |

### 8.2 应用退出清理

`api/main.py` 的 lifespan 中，应用关闭时自动调用 `shutdown_provider()` 销毁所有容器。

---

## 9. 反检测

### 9.1 容器内 Chrome 启动参数

```bash
--disable-blink-features=AutomationControlled
--exclude-switches=enable-automation
--disable-infobars
--no-first-run
--no-default-browser-check
--lang=zh-CN
--window-size=1920,1080
--start-maximized
--start-fullscreen
```

### 9.2 指纹特征

- UA：完整 Google Chrome UA（不是 Chromium）
- `navigator.webdriver`：stealth.min.js 覆盖为 `false`
- `navigator.platform`：`Linux x86_64`（stealth.js 可覆盖）
- Canvas/WebGL：Xvfb 提供完整 X11 显示，渲染管线正常
- 屏幕分辨率：Xvfb 指定 1920x1080，`screen.width/height` 正常
- 字体：安装了中文字体，字体枚举和真实 Linux 桌面一致
- 运行模式：headed（非 headless），不触发 headless 检测

### 9.3 和真实电脑的差异

| 检测点 | 风险 | 应对 |
|--------|------|------|
| GPU 信息 | Xvfb 无真实 GPU，WebGLRenderer 暴露 SwiftShader | `--disable-gpu` + stealth.js 覆盖 |
| CDP 检测 | `Runtime.Enable` 等 CDP 命令副作用 | 和本地 CDP 模式相同风险，非 Docker 特有 |
| 时区/语言 | 容器默认 UTC | Dockerfile 设置 `TZ=Asia/Shanghai`，Playwright context 设置 locale |

---

## 10. 测试

### 10.1 测试文件

`test_server/tests/test_docker_chrome.py`，交互式菜单，6 个测试项：

| 编号 | 测试项 | 说明 |
|------|--------|------|
| 1 | Docker 镜像检查 | 验证 chrome-browser 镜像是否已构建 |
| 2 | 容器生命周期 | 创建 → 健康检查 → 释放 → 复用 → 销毁 |
| 3 | Playwright 连接 | connect_over_cdp 连接 Docker Chrome，访问百度，检查指纹 |
| 4 | Docker 截图 | 注入 Cookie + stealth.js，访问抖音首页截图 |
| 5 | 多容器并发 | 同时创建 3 个容器，验证并发能力 |
| 6 | DouyinCrawler 搜索 | 完整链路：DouyinCrawler → CDPBrowserManager → DockerProvider → Cookie 登录 → 关键词搜索 |

### 10.2 运行测试

```bash
# 前置：构建镜像
docker build -t chrome-browser:latest ./chrome-browser

# 前置：安装依赖
pip install docker

# 运行测试
python test_server/tests/test_docker_chrome.py

# 直接运行全部测试
# 在菜单中输入 0
```

### 10.3 前置条件

- Docker 已安装并运行
- `chrome-browser:latest` 镜像已构建
- `test_server/tests/douyin_cookie.txt` 存在（测试 4、6 需要）
- MongoDB `chrome_docker.enabled: true`

---

## 11. 快速开始

### 11.1 构建镜像

```bash
docker build -t chrome-browser:latest ./chrome-browser
```

### 11.2 安装依赖

```bash
pip install docker psutil
```

### 11.3 配置

确认前端配置页或 `/api/v1/config/sections/chrome_docker` 中：

```json
{
  "config": {
    "enabled": true,
    "vnc_password": "你的VNC密码"
  }
}
```

### 11.4 验证

```bash
# 运行测试
python test_server/tests/test_docker_chrome.py
# 选择 2（容器生命周期）验证基本功能
# 选择 3（Playwright 连接）验证 CDP 连接
```

### 11.5 查看浏览器桌面

容器启动后，通过 noVNC 在浏览器中查看：

```
http://localhost:6080/vnc.html?autoconnect=true
```

输入 VNC 密码即可看到全屏 Chrome。

### 11.6 切换回本地模式

```json
{
  "chrome_docker": {
    "enabled": false
  }
}
```

所有业务代码自动回退到本地 Chrome 启动逻辑，无需任何代码改动。

---

## 12. 文件清单

### 12.1 新增文件

| 文件 | 说明 |
|------|------|
| `browser_manager/__init__.py` | 模块导出 |
| `browser_manager/provider.py` | BrowserProvider / DockerProvider / LocalProvider |
| `chrome-browser/Dockerfile` | Chrome 容器镜像 |
| `chrome-browser/entrypoint.py` | 容器入口（进程管理 + FastAPI API） |
| `chrome-browser/chrome.sh` | Chrome 启动参数 |
| `chrome-browser/requirements.txt` | 容器 Python 依赖 |
| `chrome-browser/.dockerignore` | Docker 构建排除 |
| `api/routers/browser.py` | 容器池管理 API |
| `test_server/tests/test_docker_chrome.py` | Docker 容器化测试 |
| `docs/CHROME_DOCKER_SOLUTION.md` | 方案设计文档 |
| `docs/CHROME_DOCKER_IMPLEMENTATION.md` | 本文档（实现文档） |

### 12.2 修改文件

| 文件 | 改动 |
|------|------|
| `MediaCrawler/tools/cdp_browser.py` | 新增 `_try_get_remote_cdp()`，`launch_and_connect()` 加 Docker 分支 |
| `crawler_tools/screenshot_tool.py` | `screenshot_page_stream()` 加 Docker 连接逻辑 |
| `api/services/xhs_vision_tools.py` | `screenshot_user_profile_stream()` 加 Docker 连接逻辑 |
| `api/main.py` | 注册 browser router，lifespan 加 shutdown_provider |
| MongoDB 配置 | 新增 `chrome_docker` 配置段 |
| `requirements.txt` | 新增 `docker`、`psutil` 依赖 |
