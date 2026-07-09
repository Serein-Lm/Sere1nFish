# Chrome Docker 容器化方案 v2

## 1. 现状分析

### 1.1 当前浏览器使用点

项目中有 4 个独立的浏览器连接点：

| 使用点 | 文件 | 连接方式 |
|--------|------|----------|
| 抖音爬虫 | `crawler_tools/douyin_crawler.py` → `CDPBrowserManager` | CDP 本地启动 Chrome |
| 小红书爬虫 | `crawler_tools/xhs_crawler.py` → `CDPBrowserManager` | CDP 本地启动 Chrome |
| 抖音截图 | `crawler_tools/screenshot_tool.py` | Playwright launch |
| 小红书截图 | `api/services/xhs_vision_tools.py` | Playwright launch |

### 1.2 并发场景（重点：小红书多关键词）

典型的生产场景：

```
任务 1: 小红书 Pipeline - 关键词 "新能源汽车"（搜索 + 打标 + 截图 + 画像）
任务 2: 小红书 Pipeline - 关键词 "智能驾驶"（同一个项目，不同关键词）
任务 3: 小红书 Pipeline - 关键词 "充电桩"（同上）
任务 4: 抖音 Pipeline - 关键词 "新能源"
任务 5: 某用户触发了一个单独的截图请求
```

每个小红书 Pipeline 的浏览器占用时间线：

```
任务1: ████████░░░░░░░░░░░░  搜索阶段（占用 Chrome ~2-5 分钟）
任务2:     ████████░░░░░░░░░  搜索阶段
任务3:         ████████░░░░░  搜索阶段
                              ↑ 三个任务可能同时需要 Chrome
```

关键约束：
- 搜索阶段需要持续占用一个 BrowserContext（维持登录态 + 签名计算依赖 page）
- 同一个 Cookie 账号不能在多个 Context 里同时使用（会被平台检测到多点登录）
- 打标和画像生成阶段不需要浏览器，Chrome 可以释放

### 1.3 核心问题

- 多个小红书关键词任务并发时，需要多个独立的 Chrome 实例
- 当前架构每次都本地启动 Chrome，无法在无 GUI 服务器部署
- 浏览器生命周期和业务代码强耦合

---

## 2. 风控与指纹分析

### 2.1 Docker + Xvfb 环境 vs 真实电脑

这是一个关键问题。结论：**Xvfb + Chrome 在指纹层面和真实桌面几乎无差异，但有几个需要注意的点。**

**和真实电脑一致的部分：**

- UA 头：容器内跑的是完整的 Google Chrome（不是 Chromium），UA 和真实 Chrome 完全一致
- JavaScript API：`navigator.platform` 返回 `Linux x86_64`（如果服务器是 Linux），`navigator.userAgent` 是标准 Chrome UA
- WebGL / Canvas 指纹：Xvfb 提供了完整的 X11 显示服务，Chrome 的渲染管线正常工作，Canvas 指纹和真实环境一致
- 屏幕分辨率：Xvfb 启动时指定 `1920x1080x24`，`screen.width/height` 返回正常值
- 字体指纹：安装了中文字体后，字体枚举结果和真实 Linux 桌面一致

**需要注意的差异：**

| 检测点 | 风险 | 解决方案 |
|--------|------|----------|
| `navigator.platform` | 如果你本地是 macOS，服务器是 Linux，平台字符串会变 | stealth.js 已经在覆盖这个值，问题不大 |
| GPU 信息 | Xvfb 没有真实 GPU，`WebGLRenderer` 会暴露 `SwiftShader` 或 `llvmpipe` | 用 `--disable-gpu` 启动 Chrome，或用 stealth.js 覆盖 WebGL 信息 |
| `navigator.webdriver` | Playwright 通过 CDP 连接时，Chrome 会设置 `navigator.webdriver = true` | 你项目里的 `stealth.min.js` 已经处理了这个，注入后会覆盖为 `false` |
| CDP 检测 | 高级反爬会检测 `Runtime.Enable` 等 CDP 命令的副作用 | 这个和 Docker 无关，本地 CDP 模式也有同样的问题。你现有的 CDPBrowserManager 方案已经在应对 |
| 时区 / 语言 | 容器默认时区可能是 UTC | Dockerfile 里设置 `TZ=Asia/Shanghai`，Playwright context 设置 `locale` 和 `timezone_id` |

**VNC 环境的额外优势：**

开了 VNC 意味着 Chrome 跑在一个有完整 X11 显示的环境里（通过 Xvfb），这和 headless 模式有本质区别：
- Chrome 以 headed 模式运行（`headless=false`），不会触发 headless 检测
- 窗口有真实的尺寸和位置，`window.outerWidth/outerHeight` 返回正常值
- `chrome.runtime` 等 API 行为和真实桌面 Chrome 一致

**总结：Docker + Xvfb + headed Chrome 的指纹特征和一台真实的 Linux 桌面电脑跑 Chrome 是一样的。** 风控层面的主要风险不在容器环境本身，而在于 CDP 协议的副作用（这个本地模式也有）和行为模式（请求频率、操作间隔等）。

### 2.2 Chromium vs Chrome

你项目里用的是 Google Chrome（`CDPBrowserManager` 检测的是 Chrome/Edge 路径），不是 Chromium。这个选择是对的：

- Chrome 和 Chromium 的 UA 不同，部分平台会对 Chromium UA 降权或直接拦截
- Chrome 内置了一些 Chromium 没有的编解码器和 API，指纹差异会被检测到
- 容器里也应该装 `google-chrome-stable`，不要用 `chromium`

### 2.3 建议的反检测配置

容器内 Chrome 启动参数（和你现有的 `CDPBrowserManager` 保持一致）：

```
--disable-blink-features=AutomationControlled
--exclude-switches=enable-automation
--no-first-run
--no-default-browser-check
--disable-infobars
--lang=zh-CN
--window-size=1920,1080
```

加上你项目已有的 `stealth.min.js` 注入，风控风险和本地跑是一样的。

---

## 3. 架构设计：透明代理层

### 3.1 核心设计原则：一层透明代理，业务代码零改动

你提的这个点非常关键——只加一个中间层，业务代码不改或最少改。

思路：**不改 `CDPBrowserManager` 的接口，而是在它下面加一层，让它连接远程 Docker Chrome 而不是本地启动 Chrome。**

```
                    现有架构                              新架构
                    
DouyinCrawler                                DouyinCrawler
    │                                            │
    ▼                                            ▼
CDPBrowserManager                            CDPBrowserManager
    │                                            │
    ▼                                            ▼
BrowserLauncher                              BrowserProvider（新增的透明层）
    │                                            │
    ▼                                        ┌───┴───┐
本地 Chrome 进程                             │       │
                                    LocalProvider  DockerProvider
                                    (原有逻辑)     (新增)
                                        │              │
                                        ▼              ▼
                                    本地 Chrome    Docker Chrome 容器
```

### 3.2 BrowserProvider 透明层设计

新增一个文件：`MediaCrawler/tools/browser_provider.py`

这个文件是唯一需要新增的代码，它提供一个统一接口：

```python
class BrowserProvider:
    """浏览器提供者 - 透明层"""
    
    async def get_cdp_endpoint() -> str:
        """
        返回一个可用的 CDP WebSocket 地址
        
        如果 config 里 docker 模式开启：
            → 从 DockerProvider 获取（动态创建或复用容器）
        否则：
            → 从 LocalProvider 获取（走原有的 BrowserLauncher 逻辑）
        """
    
    async def release_cdp_endpoint(endpoint: str):
        """释放一个 CDP 连接"""
    
    async def shutdown():
        """关闭所有管理的容器"""
```

### 3.3 CDPBrowserManager 的改动（极小）

只改 `launch_and_connect` 方法里的一小段：

```python
# 改动前（当前代码）
async def launch_and_connect(self, playwright, ...):
    browser_path = await self._get_browser_path()
    self.debug_port = self.launcher.find_available_port(config.CDP_DEBUG_PORT)
    await self._launch_browser(browser_path, headless)
    await self._connect_via_cdp(playwright)
    ...

# 改动后
async def launch_and_connect(self, playwright, ...):
    provider = get_browser_provider()  # 获取 provider 单例
    cdp_endpoint = await provider.get_cdp_endpoint()
    
    if cdp_endpoint:
        # Docker 模式：直接连接远程 Chrome
        self.browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
    else:
        # 本地模式：走原有逻辑
        browser_path = await self._get_browser_path()
        self.debug_port = self.launcher.find_available_port(config.CDP_DEBUG_PORT)
        await self._launch_browser(browser_path, headless)
        await self._connect_via_cdp(playwright)
    ...
```

### 3.4 screenshot_tool 的改动（同样极小）

```python
# 改动前
browser = await p.chromium.launch(headless=config.headless, args=[...])

# 改动后
provider = get_browser_provider()
cdp_endpoint = await provider.get_cdp_endpoint()
if cdp_endpoint:
    browser = await p.chromium.connect_over_cdp(cdp_endpoint)
else:
    browser = await p.chromium.launch(headless=config.headless, args=[...])
```

这样改动量极小，而且保留了本地模式的完整兼容——开发时 `docker_mode=false` 走原有逻辑，部署时 `docker_mode=true` 走 Docker。

---

## 4. 动态容器管理

### 4.1 不预启动，按需创建

你说得对，没必要一开始就启动多个容器。`DockerProvider` 的核心逻辑：

```
请求一个 Chrome 实例
    │
    ▼
检查是否有空闲容器
    │
    ├── 有 → 直接返回 CDP 地址
    │
    └── 没有 → 动态创建一个新容器
                │
                ├── docker run chrome-browser
                ├── 等待健康检查通过（CDP 端口可达）
                ├── 返回 CDP 地址
                └── 记录到容器池
```

### 4.2 生命周期与扫描任务对齐

这是关键设计。容器的生命周期应该和扫描任务绑定：

```
场景：3 个小红书关键词并发扫描

时间线：
t0: 任务1 启动 → DockerProvider 创建 container-1 → 分配给任务1
t1: 任务2 启动 → 无空闲容器 → 创建 container-2 → 分配给任务2
t2: 任务3 启动 → 无空闲容器 → 创建 container-3 → 分配给任务3
t3: 任务1 搜索完成 → 释放 container-1（不销毁，标记为空闲）
t4: 任务1 进入打标阶段（不需要浏览器）
t5: 任务1 需要截图 → container-1 空闲 → 复用 container-1
t6: 任务2 搜索完成 → 释放 container-2
t7: 所有任务完成 → 空闲容器超时（如 5 分钟无人使用）→ 自动销毁
```

### 4.3 DockerProvider 内部状态

```python
class DockerProvider:
    """Docker 容器管理器"""
    
    # 容器池
    containers: dict[str, ContainerInfo]  # container_id → info
    
    # 状态
    # idle: 空闲可分配
    # busy: 正在被某个任务使用
    # starting: 正在启动中
    # stopping: 正在销毁中
    
    # 配置
    max_containers: int = 5           # 最大容器数
    idle_timeout: int = 300           # 空闲超时（秒），超时自动销毁
    container_image: str = "chrome-browser:latest"
    
    async def acquire(task_id: str) -> str:
        """获取一个 CDP 地址，关联到 task_id"""
    
    async def release(task_id: str):
        """释放任务占用的容器，标记为空闲"""
    
    async def _create_container() -> ContainerInfo:
        """通过 docker-py 创建新容器"""
    
    async def _destroy_container(container_id: str):
        """销毁容器"""
    
    async def _idle_reaper():
        """后台协程：定期检查并销毁超时的空闲容器"""
```

### 4.4 小红书多关键词并发的具体处理

针对你提到的场景——一个小红书项目有多个关键词需要扫描：

**方案 A：串行复用（简单，推荐初期）**

多个关键词共用一个 Chrome 容器，串行执行搜索：

```
container-1:
  搜索 "新能源汽车" → 完成 → 搜索 "智能驾驶" → 完成 → 搜索 "充电桩"
```

优点：只需要一个容器，一个 Cookie 账号
缺点：慢，总时间 = 各关键词时间之和

**方案 B：并行多容器（快，需要多账号）**

每个关键词分配一个独立容器：

```
container-1: 搜索 "新能源汽车"（Cookie 账号 A）
container-2: 搜索 "智能驾驶"（Cookie 账号 B）
container-3: 搜索 "充电桩"（Cookie 账号 C）
```

优点：快，总时间 = 最慢的那个关键词
缺点：需要多个 Cookie 账号，资源占用多

**方案 C：单容器多 Context + 同账号时间片（推荐生产方案）**

同一个 Cookie 账号在一个容器内，通过时间片轮转多个关键词：

```
container-1（账号 A）:
  Context-1: 搜索 "新能源汽车" 第1页 → 等待
  Context-1: 搜索 "智能驾驶" 第1页 → 等待
  Context-1: 搜索 "充电桩" 第1页 → 等待
  Context-1: 搜索 "新能源汽车" 第2页 → ...
```

这种方式模拟真实用户行为（一个人在多个标签页之间切换搜索），反而比方案 B 更不容易触发风控。但实现复杂度较高，建议后续再做。

**推荐路径：先实现方案 A（串行），再升级到方案 B（多账号并行），最后考虑方案 C。**

---

## 5. Chrome 容器设计

### 5.1 容器内部结构

```
chrome-browser/
├── Dockerfile
├── entrypoint.py          # Python 进程管理 + FastAPI 控制 API
├── chrome.sh              # Chrome 启动参数
└── requirements.txt       # fastapi, uvicorn, psutil
```

### 5.2 Dockerfile 要点

- 基础镜像：`debian:bookworm-slim`（Debian 12，稳定且轻量）
- 安装 `google-chrome-stable`（不是 chromium）
- 安装 Xvfb + TigerVNC + noVNC + 中文字体
- 设置时区 `Asia/Shanghai`
- 非 root 用户运行 Chrome
- Python entrypoint 管理所有子进程

### 5.3 entrypoint.py 职责

**进程管理：**
- subprocess 启动 Xvfb → Chrome → TigerVNC → noVNC
- watchdog 循环监控子进程，挂了自动拉起

**FastAPI 控制 API（端口 8250）：**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | Chrome 进程 + CDP 可达性 |
| `/cdp/info` | GET | 返回 CDP WebSocket URL |
| `/chrome/restart` | POST | 重启 Chrome（清理内存泄漏） |
| `/status` | GET | 子进程状态 + 资源占用 |
| `/vnc/url` | GET | noVNC 访问地址 |

### 5.4 端口

| 端口 | 用途 |
|------|------|
| 9222 | CDP WebSocket |
| 5900 | VNC |
| 6080 | noVNC（浏览器访问桌面） |
| 8250 | 控制 API |

---

## 6. 应用层改动清单

### 6.1 新增文件（2 个）

| 文件 | 说明 |
|------|------|
| `MediaCrawler/tools/browser_provider.py` | 透明代理层，BrowserProvider / DockerProvider / LocalProvider |
| `api/services/browser_pool.py` | 应用层的容器池管理（调用 docker-py） |

### 6.2 修改文件（改动极小）

| 文件 | 改动 | 改动量 |
|------|------|--------|
| `MediaCrawler/tools/cdp_browser.py` | `launch_and_connect` 方法开头加 provider 判断 | ~10 行 |
| `crawler_tools/screenshot_tool.py` | `screenshot_page_stream` 里 launch 改为 provider 获取 | ~5 行 |
| `api/services/xhs_vision_tools.py` | 同上 | ~5 行 |
| `MediaCrawler/config/base_config.py` | 新增 `DOCKER_MODE` 和 `DOCKER_CDP_URL` 配置项 | ~5 行 |
| MongoDB 配置 | 新增 `chrome_docker` 配置段 | 前端配置页或 `/api/v1/config/sections/chrome_docker` |

### 6.3 不需要改动的文件

- `crawler_tools/douyin_crawler.py` — 它调用 `CDPBrowserManager`，CDPBrowserManager 内部透明切换
- `crawler_tools/xhs_crawler.py` — 同上
- `api/services/douyin_pipeline.py` — 不感知浏览器来源
- `api/services/xhs_pipeline.py` — 不感知浏览器来源
- `api/routers/*.py` — 完全不改
- 所有 DAO 层 — 完全不改

### 6.4 chrome_docker 配置段

```json
{
  "config": {
    "enabled": false,
    "image": "chrome-browser:latest",
    "max_containers": 5,
    "idle_timeout": 300,
    "shm_size": "2g",
    "screen_width": 1920,
    "screen_height": 1080,
    "timezone": "Asia/Shanghai",
    "chrome_args": [
      "--disable-blink-features=AutomationControlled",
      "--no-first-run",
      "--lang=zh-CN"
    ]
  }
}
```

`enabled: false` 时走原有本地逻辑，`enabled: true` 时走 Docker。开发环境和生产环境通过这一个开关切换。

---

## 7. 桌面投屏

每个容器内置 noVNC，前端通过以下方式访问：

```
iframe 嵌入: http://<container-host>:6080/vnc.html?autoconnect=true
```

`api/services/browser_pool.py` 提供接口查询所有活跃容器的 VNC 地址：

```python
async def get_active_containers() -> list[dict]:
    """
    返回:
    [
        {
            "container_id": "abc123",
            "task_id": "task-456",
            "task_type": "xhs_search",
            "status": "busy",
            "cdp_url": "ws://172.17.0.2:9222",
            "vnc_url": "http://172.17.0.2:6080",
            "created_at": "2026-03-02T10:00:00",
        }
    ]
    """
```

前端可以：
- 列出所有正在运行的浏览器容器
- 看到每个容器正在执行什么任务
- 点击查看某个容器的实时桌面画面

---

## 8. Docker Compose

```yaml
version: "3.8"

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - CHROME_DOCKER_ENABLED=true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock  # 让应用能管理 Docker 容器
    networks:
      - app-network

  # Chrome 容器不在 compose 里预定义
  # 由应用层的 DockerProvider 通过 docker-py 动态创建和销毁
  # 创建时自动加入 app-network

networks:
  app-network:
    driver: bridge
```

注意：Chrome 容器不写在 docker-compose 里。它们由应用运行时通过 `docker-py` 动态创建，生命周期和扫描任务对齐。compose 里只定义应用服务和网络。

---

## 9. 完整调用链路（以小红书多关键词为例）

```
用户请求: POST /api/xhs/pipeline { keywords: ["新能源汽车", "智能驾驶", "充电桩"] }
    │
    ▼
XhsPipeline.run_pipeline()
    │
    ├── 关键词 1: "新能源汽车"
    │   ├── _get_crawler() → XhsCrawler._init_browser()
    │   │   └── CDPBrowserManager.launch_and_connect()
    │   │       └── BrowserProvider.get_cdp_endpoint()
    │   │           └── DockerProvider.acquire(task_id="xhs-kw1")
    │   │               ├── 无空闲容器 → _create_container() → container-1
    │   │               └── 返回 ws://container-1:9222
    │   ├── 搜索完成 → release container-1（标记空闲）
    │   ├── 打标（不需要浏览器）
    │   ├── 截图 → acquire → 复用空闲的 container-1
    │   └── 截图完成 → release container-1
    │
    ├── 关键词 2: "智能驾驶"（串行模式下复用 container-1）
    │   └── ...同上...
    │
    └── 关键词 3: "充电桩"
        └── ...同上...

所有关键词完成后:
    container-1 空闲 → 5 分钟无人使用 → idle_reaper 自动销毁
```

如果是并行模式（多账号）：

```
    ├── 关键词 1 → acquire → 创建 container-1（账号 A）
    ├── 关键词 2 → acquire → 创建 container-2（账号 B）
    ├── 关键词 3 → acquire → 创建 container-3（账号 C）
    │
    │   三个搜索并行执行
    │
    ├── 全部完成 → release all
    └── 空闲超时 → 自动销毁
```

---

## 10. 分阶段实施

### Phase 1：容器构建 + 透明层（2 天）

- 构建 `chrome-browser/` 目录（Dockerfile + entrypoint.py）
- 新增 `browser_provider.py`（LocalProvider + DockerProvider）
- 改造 `CDPBrowserManager.launch_and_connect`（加 provider 判断，~10 行）
- 改造 `screenshot_tool.py`（~5 行）
- 前端配置页或配置 API 写入 `chrome_docker` 配置
- 验证：单容器跑通抖音搜索 + 小红书搜索 + 截图

### Phase 2：动态容器管理（1-2 天）

- `DockerProvider` 完整实现（docker-py 创建/销毁容器）
- 空闲容器自动回收（idle_reaper 协程）
- 容器健康检查
- 验证：3 个小红书关键词串行扫描，容器自动创建和回收

### Phase 3：并发 + VNC（1-2 天）

- 多容器并发支持（多账号场景）
- noVNC 集成
- `api/routers/browser.py` 管理接口
- 验证：并发扫描 + 前端查看浏览器桌面

### Phase 4：生产加固（后续）

- Chrome 定期重启（防内存泄漏）
- 容器资源限制和监控
- 异常容器自动替换
- 弹性扩缩容策略优化

---

## 11. 关键决策总结

| 决策 | 选择 | 理由 |
|------|------|------|
| 业务代码改动方式 | 透明代理层（BrowserProvider） | 只改 CDPBrowserManager 一个入口点，其他业务代码零改动 |
| 容器启动方式 | 动态按需创建，不预启动 | 资源不浪费，生命周期和任务对齐 |
| 容器管理 | docker-py 库，应用内管理 | 不依赖外部编排工具，简单可控 |
| 浏览器 | Google Chrome（非 Chromium） | 反检测兼容性，和现有代码一致 |
| 多关键词并发 | 初期串行复用，后续多账号并行 | 渐进式，先保证能用再优化速度 |
| 本地/Docker 切换 | MongoDB `chrome_docker.enabled` 单开关 | 开发用本地，部署用 Docker，一键切换 |
| 风控应对 | Xvfb headed 模式 + stealth.js + Chrome（非 Chromium） | 指纹和真实 Linux 桌面一致 |
| 空闲容器处理 | 超时自动销毁（默认 5 分钟） | 避免资源浪费 |
