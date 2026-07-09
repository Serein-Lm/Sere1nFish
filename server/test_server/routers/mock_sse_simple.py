"""
简化版 Mock SSE - 重点展示并行执行和层次结构

核心特点：
1. 并行执行：browser 和 bid 同时输出（交错事件）
2. 层次结构：graph → node → tool（清晰的嵌套路径）
3. 简化内容：最小化文本，突出结构
"""

import json
import asyncio
import time
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()


def check_local(request: Request):
    """检查是否为本地请求"""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(status_code=403, detail="仅允许本地访问")


def _ts() -> int:
    """当前时间戳（毫秒）"""
    return int(time.time() * 1000)


def _id(workflow: str, path: str) -> str:
    """生成节点 ID"""
    return f"{workflow}_{path}_{_ts()}"


def make_event(event_type: str, path: str, workflow: str, agent: str = None, data: dict = None) -> dict:
    """构造 SSE 事件"""
    return {
        "event": event_type,
        "id": _id(workflow, path),
        "path": path,
        "ts": _ts(),
        "data": data or {},
        "workflow": workflow,
        "agent": agent
    }


class MockRequest(BaseModel):
    query: str = Field(..., description="用户查询")
    workflow: str = Field(default="router_copywriting", description="工作流类型")
    delay: float = Field(default=0.1, description="模拟延迟（秒）")


async def generate_simple_mock_stream(workflow: str, query: str, delay: float):
    """
    简化版 Mock 数据流 - 重点展示结构和并行
    
    正确的层次结构：
    graph (router_copywriting)
      ├─ router (子graph)
      │   ├─ classify (node)
      │   ├─ browser (node) [并行]
      │   │   ├─ tool1
      │   │   └─ tool2
      │   ├─ bid (node) [并行]
      │   │   └─ tool1
      │   └─ synthesize (node)
      │
      └─ copywriting (子graph)
          ├─ scenario (node)
          ├─ script (node)
          ├─ objection (node)
          └─ finalize (node)
    """
    
    # ========== Graph 开始 ==========
    yield make_event("start", "graph", workflow, data={
        "type": "graph",
        "name": workflow,
        "displayName": "🚀 信息采集+文案生成",
        "meta": {"total_subgraphs": 2, "parallel_agents": 2}
    })
    await asyncio.sleep(delay)
    
    # ========== Router 子图开始 ==========
    yield make_event("start", "graph.router", workflow, data={
        "type": "subgraph",
        "name": "router",
        "displayName": "📊 Router - 信息采集",
        "meta": {"phase": "router", "total_nodes": 4}
    })
    await asyncio.sleep(delay)
    
    # 1. Classify 节点
    yield make_event("start", "graph.router.classify", workflow, data={
        "type": "node",
        "name": "classify",
        "displayName": "🎯 分析查询",
        "meta": {"subgraph": "router"}
    })
    await asyncio.sleep(delay * 2)
    
    yield make_event("update", "graph.router.classify", workflow, data={
        "description": "已选择: browser, bid",
        "meta": {"agents": ["browser", "bid"], "confidence": 0.92}
    })
    await asyncio.sleep(delay)
    
    yield make_event("end", "graph.router.classify", workflow, data={
        "status": "success",
        "duration": 300
    })
    await asyncio.sleep(delay)
    
    # ========== 并行执行：Browser 和 Bid 交错输出 ==========
    
    # 2. Browser 开始
    yield make_event("start", "graph.router.browser", workflow, agent="browser", data={
        "type": "agent",
        "name": "browser",
        "displayName": "🌐 官网采集",
        "meta": {"subgraph": "router", "parallel": True}
    })
    await asyncio.sleep(delay * 0.5)
    
    # 3. Bid 开始（并行）
    yield make_event("start", "graph.router.bid", workflow, agent="bid", data={
        "type": "agent",
        "name": "bid",
        "displayName": "📋 招投标",
        "meta": {"subgraph": "router", "parallel": True}
    })
    await asyncio.sleep(delay * 0.5)
    
    # Browser Tool 1 开始
    yield make_event("start", "graph.router.browser.tools.get_domain", workflow, agent="browser", data={
        "type": "tool",
        "name": "get_domain",
        "displayName": "查询企业域名"
    })
    await asyncio.sleep(delay)
    
    # Bid Tool 1 开始（交错）
    yield make_event("start", "graph.router.bid.tools.get_bids", workflow, agent="bid", data={
        "type": "tool",
        "name": "get_bids",
        "displayName": "查询招投标"
    })
    await asyncio.sleep(delay)
    
    # Browser Tool 1 结束
    yield make_event("end", "graph.router.browser.tools.get_domain", workflow, agent="browser", data={
        "status": "success",
        "duration": 800,
        "meta": {"result": {"domain": "www.huawei.com"}}
    })
    await asyncio.sleep(delay * 0.5)
    
    # Browser Tool 2 开始
    yield make_event("start", "graph.router.browser.tools.web_scraper", workflow, agent="browser", data={
        "type": "tool",
        "name": "web_scraper",
        "displayName": "抓取网页"
    })
    await asyncio.sleep(delay)
    
    # Bid Tool 1 更新（交错）
    yield make_event("update", "graph.router.bid.tools.get_bids", workflow, agent="bid", data={
        "description": "正在搜索...",
        "meta": {"progress": 0.5, "records_scanned": 150}
    })
    await asyncio.sleep(delay)
    
    # Browser Tool 2 结束
    yield make_event("end", "graph.router.browser.tools.web_scraper", workflow, agent="browser", data={
        "status": "success",
        "duration": 650
    })
    await asyncio.sleep(delay * 0.5)
    
    # Browser 内容输出（Markdown 格式）
    browser_content = """## 🌐 华为技术有限公司 - 官网信息

### 基本信息
- **官方网站**: [www.huawei.com](https://www.huawei.com)
- **ICP备案**: 粤B2-20090212 ✅
- **SSL证书**: 有效 🔒

### 联系方式
| 项目 | 信息 |
|------|------|
| 客服热线 | 400-830-8300 |
| 企业邮箱 | support@huawei.com |
| 工作时间 | 周一至周日 8:00-20:00 |

### 公司规模
- 员工数量: **19.7万+**
- 研发人员占比: **55%**
- 年营收: **7,000亿+** 人民币（2023）

### 核心业务
1. **运营商业务** - 5G网络设备、光传输
2. **企业业务** - 云计算、数据中心
3. **消费者业务** - 智能手机、平板
4. **智能汽车** - 自动驾驶、车载系统

> 💡 华为在5G标准必要专利中占比20%+，技术实力全球领先"""
    
    for char in browser_content:
        yield make_event("content", "graph.router.browser", workflow, agent="browser", data={
            "content": char
        })
        await asyncio.sleep(delay * 0.1)
    
    # Bid Tool 1 结束（交错）
    yield make_event("end", "graph.router.bid.tools.get_bids", workflow, agent="bid", data={
        "status": "success",
        "duration": 1200,
        "meta": {"total_found": 23, "returned": 8}
    })
    await asyncio.sleep(delay * 0.5)
    
    # Browser 结束
    yield make_event("end", "graph.router.browser", workflow, agent="browser", data={
        "status": "success",
        "duration": 3000,
        "meta": {"subgraph": "router", "tools_used": 2}
    })
    await asyncio.sleep(delay * 0.5)
    
    # Bid 内容输出（Markdown 格式）
    bid_content = """## 📋 华为技术有限公司 - 招投标信息

### 统计概览（近12个月）
| 指标 | 数值 |
|------|------|
| 招标项目总数 | 23 个 |
| 中标项目 | 18 个 |
| 中标率 | **78.3%** 🎯 |
| 项目总金额 | **12.5 亿元** 💰 |
| 平均项目金额 | 1.56 亿元 |

### 重点项目 Top 3

#### 1️⃣ 某省政务云平台建设
- **金额**: 5.2 亿元
- **状态**: ✅ 已中标
- **时间**: 2024-11-15
- **内容**: 云计算基础设施、数据中心、安全防护

#### 2️⃣ 某市智慧城市网络设备
- **金额**: 2.8 亿元  
- **状态**: ✅ 已中标
- **时间**: 2024-09-20
- **内容**: 5G基站、路由器、交换机

#### 3️⃣ 某银行数据中心升级
- **金额**: 1.8 亿元
- **状态**: 🔄 进行中
- **截止**: 2025-01-20
- **要求**: 国产化率 ≥60%

### 行业分布
```
政府/公共服务  ████████ 35%
金融          █████ 22%
教育          ████ 17%
制造业        ███ 13%
其他          ███ 13%
```

> 📊 华为在政务云和5G网络领域具有明显优势"""
    
    for char in bid_content:
        yield make_event("content", "graph.router.bid", workflow, agent="bid", data={
            "content": char
        })
        await asyncio.sleep(delay * 0.1)
    
    # Bid 结束
    yield make_event("end", "graph.router.bid", workflow, agent="bid", data={
        "status": "success",
        "duration": 4000,
        "meta": {"subgraph": "router", "tools_used": 1}
    })
    await asyncio.sleep(delay)
    
    # 4. Synthesize 节点
    yield make_event("start", "graph.router.synthesize", workflow, data={
        "type": "node",
        "name": "synthesize",
        "displayName": "📝 汇总结果",
        "meta": {"subgraph": "router", "input_sources": 2}
    })
    await asyncio.sleep(delay)
    
    synthesis_content = """## 📝 综合分析报告

### 企业概况
**华为技术有限公司**是全球领先的ICT基础设施和智能终端提供商，成立于1987年。

#### 核心数据
- 👥 员工规模: 19.7万+（研发占比55%）
- 💰 年营收: 7,000亿+ 人民币
- 🏆 专利数量: 11万+ 件（全球前三）
- 🔬 研发投入: 年均1,600亿+ 人民币

### 市场表现
近12个月参与招投标项目**23个**，成功中标**18个**，中标率达到**78.3%**。

#### 优势领域
1. 政府/公共服务（35%）- 政务云、智慧城市
2. 金融行业（22%）- 数据中心、核心系统  
3. 教育行业（17%）- 智慧校园、教育云

### 竞争优势
- ✅ 5G技术全球领先
- ✅ 国产化能力强（鲲鹏+昇腾）
- ✅ 政务云经验丰富（30+省市）
- ✅ 本地化服务完善

---
*数据来源: 官网、天眼查、招投标平台*"""
    
    for char in synthesis_content:
        yield make_event("content", "graph.router.synthesize", workflow, data={
            "content": char
        })
        await asyncio.sleep(delay * 0.1)
    
    yield make_event("end", "graph.router.synthesize", workflow, data={
        "status": "success",
        "duration": 2000,
        "meta": {"subgraph": "router"}
    })
    await asyncio.sleep(delay)
    
    # ========== Router 子图结束 ==========
    yield make_event("end", "graph.router", workflow, data={
        "status": "success",
        "duration": 9000,
        "meta": {"phase": "router", "nodes_executed": 4}
    })
    await asyncio.sleep(delay)
    
    # ========== Final 第一段：Router 阶段结果 ==========
    yield make_event("final", "graph", workflow, data={
        "section": "router",
        "content": """## 📊 信息采集结果

### 企业基本信息
- **公司**: 华为技术有限公司
- **员工**: 19.7万+
- **营收**: 7,000亿+ 人民币

### 招投标表现
- **中标率**: 78.3%
- **项目额**: 12.5亿元

> ✅ 信息采集完成""",
        "meta": {
            "sectionTitle": "📊 第一阶段：信息采集",
            "phase": "router",
            "timestamp": _ts()
        }
    })
    await asyncio.sleep(delay)
    
    # ========== Copywriting 子图开始 ==========
    yield make_event("start", "graph.copywriting", workflow, data={
        "type": "subgraph",
        "name": "copywriting",
        "displayName": "✍️ Copywriting - 文案生成",
        "meta": {"phase": "copywriting", "total_nodes": 4}
    })
    await asyncio.sleep(delay)
    
    # 5. Scenario 节点
    yield make_event("start", "graph.copywriting.scenario", workflow, data={
        "type": "node",
        "name": "scenario",
        "displayName": "🎬 场景伪造",
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    scenario_content = """## 🎬 销售场景构建

### 目标客户画像
- **客户类型**: 某省级政务服务管理局
- **决策层级**: 局长、信息中心主任、技术负责人
- **预算规模**: 3-5 亿元
- **项目周期**: 2025-2027 年（3年规划）

### 客户痛点

#### 1. 性能瓶颈 ⚠️
- 高峰期响应慢，用户投诉多
- 数据库查询超时，影响办事效率

#### 2. 安全隐患 🔒
- 部分设备已停止维保
- 缺乏统一的安全防护体系

#### 3. 国产化压力 🇨🇳
- 现有系统以国外品牌为主
- 国产化改造技术路线不清晰
- 担心国产设备性能和稳定性

### 客户需求
✅ 高性能云计算平台（支持10万+并发）  
✅ 完善的数据安全防护体系  
✅ 国产化率 ≥70%  
✅ 统一运维管理平台

### 竞争态势
| 竞品 | 优势 | 劣势 |
|------|------|------|
| 阿里云 | 云服务经验丰富 | 国产化能力弱 |
| 腾讯云 | 用户体验好 | 政务经验少 |
| 浪潮 | 关系网强 | 技术代际落后 |

> 💡 **华为优势**: 5G+云+AI全栈能力 + 鲲鹏国产化芯片"""
    
    for char in scenario_content:
        yield make_event("content", "graph.copywriting.scenario", workflow, data={
            "content": char
        })
        await asyncio.sleep(delay * 0.1)
    
    yield make_event("end", "graph.copywriting.scenario", workflow, data={
        "status": "success",
        "duration": 1500,
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    # 6. Script 节点
    yield make_event("start", "graph.copywriting.script", workflow, data={
        "type": "node",
        "name": "script",
        "displayName": "💬 话术生成",
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    script_content = """## 💬 销售话术脚本

### 开场白
> "X局长/X主任，您好！我是华为企业业务的解决方案顾问XXX。"

"非常感谢您抽出时间。我了解到贵局正在规划新一代政务云平台建设，这是一个非常有前瞻性的决策。"

### 需求挖掘（SPIN提问法）

#### 背景问题 (Situation)
- "目前承载了多少个业务系统？"
- "日均访问量大概是多少？"

#### 难点问题 (Problem)  
- "业务高峰期系统响应是否变慢？"
- "国产化改造压力大吗？"

#### 暗示问题 (Implication)
- "如果这些问题持续存在，可能会带来群众投诉增多、数据安全风险..."

#### 需求-效益问题 (Need-payoff)
- "如果我们能提供性能提升3倍、国产化率达70%+的方案，对贵局有帮助吗？"

### 方案呈现（FAB法则）

#### Feature 1: 鲲鹏云基础设施
- **特性**: 基于华为自研鲲鹏处理器，完全自主可控
- **优势**: 性能提升30%，能耗降低20%
- **利益**: 满足国产化要求，降低机房成本

#### Feature 2: 全栈安全防护  
- **特性**: 端到端安全体系，芯片级可信根
- **优势**: 通过等保2.0三级认证
- **利益**: 数据得到全方位保护

### 案例佐证
> 📊 **某省政务云案例**  
> 200+业务系统，日均访问600万+  
> 系统响应速度提升4倍，群众满意度从82%→95%

---
*使用技巧: 根据客户反应灵活调整，避免生硬背诵*"""
    
    for char in script_content:
        yield make_event("content", "graph.copywriting.script", workflow, data={
            "content": char
        })
        await asyncio.sleep(delay * 0.1)
    
    yield make_event("end", "graph.copywriting.script", workflow, data={
        "status": "success",
        "duration": 1800,
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    # 7. Objection 节点
    yield make_event("start", "graph.copywriting.objection", workflow, data={
        "type": "node",
        "name": "objection",
        "displayName": "🛡️ 质疑应对",
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    objection_content = """## 🛡️ 异议处理手册

### 价格异议

#### ❓ "你们的价格太贵了"

**应对策略**: TCO分析

| 项目 | 低价方案 | 华为方案 | 差异 |
|------|---------|---------|------|
| 初期投资 | 3.0亿 | 4.2亿 | +1.2亿 |
| 5年运维 | 1.8亿 | 1.0亿 | **-8000万** ✅ |
| 5年TCO | 4.8亿 | 5.2亿 | +4000万 |

> 💡 虽然初期多投入1.2亿，但5年运维节省8000万，实际只多花4000万

**话术**:  
"我理解您的顾虑。但从TCO角度看，5年可节省8000万运维成本，这4000万的差价换来的是5年的安心和未来的竞争力。"

---

### 产品异议

#### ❓ "华为云不如阿里云、腾讯云成熟"

**应对策略**: 强调政务云差异化

**政务云 vs 互联网云**:
- 核心诉求: 安全、稳定、可控 > 弹性、敏捷
- 国产化: 必须满足 vs 不强制
- 部署方式: 私有云 vs 公有云

**华为优势**:
1. ✅ 服务30+省市政务云（阿里、腾讯主要在互联网）
2. ✅ 国产化率可达75%+（他们只能30-40%）
3. ✅ 芯片级安全防护
4. ✅ 本地化服务响应快

---

### 竞争异议

#### ❓ "浪潮在政务市场做了很多年"

**应对策略**: 技术代际优势

| 维度 | 浪潮 | 华为 |
|------|------|------|
| 技术架构 | Intel x86 | 鲲鹏+昇腾 |
| 国产化率 | 30-40% | **75%+** ✅ |
| AI能力 | 传统IT | AI原生 |

**话术**:  
"浪潮确实是老牌厂商。但选择供应商不仅要看过去，更要看未来。华为的技术更先进，未来5-10年不会落后。"

---

### 时机异议

#### ❓ "我们再看看，不着急"

**应对策略**: 时间窗口提醒

⏰ **关键时间节点**:
- 2025年底前需完成国产化改造（政策要求）
- Q1-Q2是预算执行期（错过可能没预算）
- 邻省已启动，落后会影响竞争力

**建议行动**:
1. 本月: 需求调研和方案设计
2. 下月: POC测试和技术验证  
3. 3月: 完成立项和招标准备

---
*应对原则: 认同感受 → 转化视角 → 提供证据 → 促成行动*"""
    
    for char in objection_content:
        yield make_event("content", "graph.copywriting.objection", workflow, data={
            "content": char
        })
        await asyncio.sleep(delay * 0.1)
    
    yield make_event("end", "graph.copywriting.objection", workflow, data={
        "status": "success",
        "duration": 1600,
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    # 8. Finalize 节点
    yield make_event("start", "graph.copywriting.finalize", workflow, data={
        "type": "node",
        "name": "finalize",
        "displayName": "📄 整合文案",
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    finalize_content = """## 📄 完整销售文案

### 执行摘要

**目标**: 某省政务服务管理局新一代政务云平台建设  
**预算**: 3-5亿元  
**周期**: 2025-2027年（3年）

#### 核心价值主张
1. 🇨🇳 **国产化率75%+** - 超出要求，自主可控
2. ⚡ **性能提升3倍** - 响应时间从2秒→0.6秒
3. 💰 **5年TCO优化** - 运维成本降低30%
4. 🤖 **AI赋能** - 智能客服、智能审批

---

### 方案亮点

#### 技术方案
- 鲲鹏云基础设施（自主可控）
- 全栈安全防护（等保2.0三级）
- AI原生平台（盘古大模型）
- 统一运维管理

#### 商务方案  
- 总投资: 4.2亿元
- 付款方式: 分3年支付
- 质保期: 5年
- 服务承诺: 7×24小时，2小时响应

---

### 竞争优势

| 维度 | 华为 | 竞品A | 竞品B |
|------|------|-------|-------|
| 国产化率 | **75%** ✅ | 30% | 40% |
| 政务云案例 | **30+省市** ✅ | 5个 | 10个 |
| AI能力 | **原生** ✅ | 无 | 弱 |
| 本地服务 | **2小时** ✅ | 4小时 | 远程 |

---

### 行动计划

#### 第一阶段: 需求确认（1-2周）
- [ ] 预约拜访局长/主任
- [ ] 组织需求调研会
- [ ] 现场勘查机房环境

#### 第二阶段: 方案设计（2-3周）
- [ ] 提供详细技术方案
- [ ] 进行TCO分析
- [ ] 安排标杆客户参观

#### 第三阶段: POC验证（1-2个月）
- [ ] 搭建测试环境
- [ ] 迁移2-3个典型系统
- [ ] 性能和稳定性测试

#### 第四阶段: 商务谈判（2-4周）
- [ ] 提交正式报价
- [ ] 协助准备立项材料
- [ ] 配合招投标流程

---

### 预期成果

📊 **商务目标**:
- 中标概率: **60-70%**
- 合同金额: **4.2亿元**
- 签约时间: 2025 Q2

🎯 **战略价值**:
- 树立省级政务云标杆
- 提升华为品牌影响力
- 建立长期合作关系

---

*文案生成时间: 2025-01-05*  
*版本: v2.1.0*  
*有效期: 3个月*"""
    
    for char in finalize_content:
        yield make_event("content", "graph.copywriting.finalize", workflow, data={
            "content": char
        })
        await asyncio.sleep(delay * 0.1)
    
    yield make_event("end", "graph.copywriting.finalize", workflow, data={
        "status": "success",
        "duration": 2000,
        "meta": {"subgraph": "copywriting"}
    })
    await asyncio.sleep(delay)
    
    # ========== Copywriting 子图结束 ==========
    yield make_event("end", "graph.copywriting", workflow, data={
        "status": "success",
        "duration": 6900,
        "meta": {"phase": "copywriting", "nodes_executed": 4}
    })
    await asyncio.sleep(delay)
    
    # ========== Final 第二段：Copywriting 阶段结果 ==========
    yield make_event("final", "graph", workflow, data={
        "section": "copywriting",
        "content": """## 🎯 文案生成结果

### 销售方案要点
1. **场景定位**: 省级政务云项目
2. **核心优势**: 国产化率75%+
3. **价格策略**: TCO优化，5年节省8000万

### 关键话术
- 开场：强调政务云经验（30+省市）
- 应对：TCO分析化解价格异议
- 促成：POC验证+分期付款

> ✅ 文案生成完成，预期中标率：60-70%""",
        "meta": {
            "sectionTitle": "🎯 第二阶段：文案生成",
            "phase": "copywriting",
            "timestamp": _ts()
        }
    })
    await asyncio.sleep(delay)
    
    # ========== Graph 结束 ==========
    yield make_event("end", "graph", workflow, data={
        "status": "success",
        "duration": 15900,
        "meta": {
            "total_subgraphs": 2,
            "total_nodes": 8,
            "total_tools": 3
        }
    })
    await asyncio.sleep(delay)
    
    # ========== Final 第三段：最终总结 ==========
    yield make_event("final", "graph", workflow, data={
        "section": "summary",
        "content": """## 📋 执行总结

### 完成情况
- ✅ 信息采集：2个Agent并行完成
- ✅ 文案生成：4个节点顺序完成
- ✅ 总耗时：15.9秒

### 交付成果
1. 企业信息报告
2. 招投标分析
3. 完整销售方案

### 下一步行动
- 📞 联系客户预约会议
- 📊 准备方案演示PPT
- 🤝 安排POC测试

> 🎉 工作流执行成功！""",
        "meta": {
            "sectionTitle": "📋 执行总结",
            "phase": "summary",
            "timestamp": _ts()
        },
        "summary": {
            "total_duration": 15900,
            "nodes_executed": 8,
            "agents_used": ["browser", "bid"],
            "subgraphs_completed": ["router", "copywriting"],
            "success": True
        }
    })


# ============ API 端点 ============

@router.post("/stream-simple")
async def mock_stream_simple(request: Request, body: MockRequest):
    """
    简化版 Mock SSE - 重点展示并行和层次结构
    
    特点：
    1. 并行执行：browser 和 bid 交错输出事件
    2. 层次结构：graph → node → tool 清晰嵌套
    3. 简化内容：最小化文本，突出结构
    """
    check_local(request)
    
    async def event_generator():
        try:
            async for event in generate_simple_mock_stream(body.workflow, body.query, body.delay):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_event = make_event("error", "graph", body.workflow, data={
                "status": "error",
                "error": str(e)
            })
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/ping-simple")
async def ping_simple(request: Request):
    """健康检查"""
    check_local(request)
    return {"status": "ok", "message": "Simple Mock SSE 服务正常"}
