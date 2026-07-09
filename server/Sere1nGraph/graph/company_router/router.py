"""
公司信息路由器 (Company Router)

核心 LLM Agent，负责:
1. 解析公司名称，生成标准化和口语化名称
2. 判断公司行业、业务性质
3. 生成搜索策略和关键词
4. 决定启用哪些下游节点
"""

from __future__ import annotations

from typing import Any, Optional
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from .schemas import (
    CompanyProfile,
    SearchStrategy,
    NodeConfig,
    CompanyRouterOutput,
    IndustryType,
)
from .keywords import KeywordLibrary


@dataclass
class CompanyRouterResult:
    """路由结果"""
    success: bool
    company_profile: Optional[CompanyProfile] = None
    search_strategy: Optional[SearchStrategy] = None
    reasoning: str = ""
    error: Optional[str] = None
    
    # 便捷访问
    enabled_nodes: list[str] = field(default_factory=list)
    all_keywords: dict[str, list[str]] = field(default_factory=dict)


ROUTER_SYSTEM_PROMPT = """你是一个公司信息分析专家，负责分析目标公司并生成搜索策略。

## 任务

根据用户提供的公司名称，完成以下分析：

1. **公司识别**
   - 识别 ICP 备案的标准公司名（如：上海宽娱数码科技有限公司）
   - 识别口语化名称（如：b站、bilibili、哔哩哔哩）
   - 如果不确定标准名，保持用户输入

2. **行业判断**
   - 判断公司所属行业（internet/finance/airport/government/education/healthcare/manufacturing/retail/real_estate/energy/logistics/telecom/media/other）
   - 识别细分行业标签

3. **业务性质**
   - 判断是 to_c（面向消费者）、to_b（面向企业）、to_g（面向政府）还是 mixed（混合）
   - 识别主营业务

4. **特征标签**
   - 根据公司特点生成标签（如：实习较多、校招活跃、技术驱动等）

5. **搜索策略**
   根据行业特性决定各节点配置：
   
   - **xhs（小红书）**: 互联网公司重点关注实习、内推、跳槽、招聘；机场关注招商、广告
   - **douyin（抖音）**: 类似小红书，关注员工动态、公司文化
   - **web_tagging（官网）**: 关注商务合作、联系方式、招聘入口
   - **bidding（招投标）**: 机场、政府、医疗等行业启用；互联网一般不启用
   - **paper（论文）**: 暂不启用
   - **weixin（微信公众号）**: 根据需要启用

## 输出格式

请严格按照以下 JSON 格式输出：

```json
{
  "company_profile": {
    "icp_name": "标准公司名",
    "colloquial_names": ["口语名1", "口语名2"],
    "industry": "行业类型",
    "sub_industries": ["细分行业1"],
    "business_nature": "to_c/to_b/to_g/mixed",
    "main_business": ["主营业务1"],
    "tags": ["标签1", "标签2"],
    "scale": "large/medium/small/startup/unknown",
    "is_listed": true/false
  },
  "search_strategy": {
    "xhs": {
      "enabled": true,
      "priority": 1,
      "keywords": ["公司名+实习", "公司名+内推"],
      "focus_points": ["员工动态", "招聘信息"],
      "params": {}
    },
    "douyin": {
      "enabled": true,
      "priority": 2,
      "keywords": ["公司名+实习"],
      "focus_points": ["员工生活"],
      "params": {}
    },
    "web_tagging": {
      "enabled": true,
      "priority": 1,
      "keywords": [],
      "focus_points": ["商务合作", "联系方式"],
      "params": {"crawl_points": ["商务合作页", "关于我们"]}
    },
    "bidding": {
      "enabled": false,
      "priority": 5,
      "keywords": [],
      "focus_points": [],
      "params": {}
    },
    "paper": {
      "enabled": false,
      "priority": 10,
      "keywords": [],
      "focus_points": [],
      "params": {}
    },
    "weixin": {
      "enabled": false,
      "priority": 5,
      "keywords": [],
      "focus_points": [],
      "params": {}
    }
  },
  "reasoning": "决策推理过程说明"
}
```

## 行业特定规则

### 互联网行业
- xhs/douyin: 重点关注 实习、内推、跳槽、招聘、offer、面试
- web_tagging: 关注 商务合作、API文档、招聘入口
- bidding: 一般不启用

### 机场/航空行业
- xhs/douyin: 关注 招商、广告投放、机场广告
- web_tagging: 关注 招商信息、广告服务
- bidding: 启用，关注广告招标、媒体运营

### 政府/事业单位
- xhs/douyin: 关注 公务员、事业编、考试
- web_tagging: 关注 招标公告、政务公开
- bidding: 启用，关注政府采购

### 金融行业
- xhs/douyin: 关注 实习、校招、工作体验
- web_tagging: 关注 招聘信息、网点信息
- bidding: 启用，关注IT系统招标

## 注意事项

1. 关键词要包含公司的口语化名称，便于搜索
2. 优先级数字越小越优先（1最高，10最低）
3. 根据公司特点灵活调整，不要机械套用模板
4. reasoning 字段简要说明决策依据
"""


class CompanyRouter:
    """
    公司信息路由器
    
    使用 LLM 分析公司信息，生成结构化的搜索策略
    """
    
    def __init__(
        self,
        app_config: Any,
        keyword_library: Optional[KeywordLibrary] = None,
    ):
        self.app_config = app_config
        self.keyword_library = keyword_library or KeywordLibrary()
        self._llm = None
    
    def _get_llm(self):
        """获取 LLM 实例"""
        if self._llm is None:
            from ..agents.runtime import create_llm
            self._llm = create_llm(self.app_config)
        return self._llm
    
    async def route(self, company_name: str) -> CompanyRouterResult:
        """
        分析公司并生成搜索策略
        
        Args:
            company_name: 公司名称（可以是标准名或口语名）
        
        Returns:
            CompanyRouterResult: 包含公司画像和搜索策略
        """
        try:
            llm = self._get_llm()
            
            # 使用结构化输出
            structured_llm = llm.with_structured_output(CompanyRouterOutput)
            
            result = await structured_llm.ainvoke([
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=f"请分析以下公司并生成搜索策略：\n\n{company_name}"),
            ])
            
            # 后处理：合并关键词库的默认配置
            search_strategy = self._enhance_strategy(result.company_profile, result.search_strategy)
            
            # 构建结果
            enabled_nodes = self._get_enabled_nodes(search_strategy)
            all_keywords = self._collect_all_keywords(search_strategy)
            
            return CompanyRouterResult(
                success=True,
                company_profile=result.company_profile,
                search_strategy=search_strategy,
                reasoning=result.reasoning,
                enabled_nodes=enabled_nodes,
                all_keywords=all_keywords,
            )
            
        except Exception as e:
            return CompanyRouterResult(
                success=False,
                error=str(e),
            )
    
    def _enhance_strategy(
        self,
        profile: CompanyProfile,
        strategy: SearchStrategy,
    ) -> SearchStrategy:
        """
        使用关键词库增强搜索策略
        
        合并 LLM 生成的关键词和默认关键词库
        """
        industry = profile.industry
        
        # 增强各节点配置
        for node_name in ["xhs", "douyin", "web_tagging", "bidding", "weixin"]:
            node_config: NodeConfig = getattr(strategy, node_name)
            
            # 获取默认关键词
            default_keywords = self.keyword_library.get_keywords(industry, node_name)
            default_focus = self.keyword_library.get_focus_points(industry, node_name)
            
            # 合并关键词（去重）
            all_keywords = list(set(node_config.keywords + default_keywords))
            all_focus = list(set(node_config.focus_points + default_focus))
            
            # 替换公司名占位符
            primary_name = profile.colloquial_names[0] if profile.colloquial_names else profile.icp_name
            
            expanded_keywords = []
            for kw in all_keywords:
                if "{company}" in kw or "公司名" in kw:
                    # 使用口语化名称替换
                    expanded_keywords.append(kw.replace("{company}", primary_name).replace("公司名", primary_name))
                else:
                    expanded_keywords.append(kw)
            
            node_config.keywords = expanded_keywords
            node_config.focus_points = all_focus
            
            # 特殊处理：web_tagging 添加爬取节点
            if node_name == "web_tagging":
                crawl_points = self.keyword_library.get_crawl_points(industry)
                if crawl_points:
                    node_config.params["crawl_points"] = crawl_points
            
            # 特殊处理：bidding 根据行业决定是否启用
            if node_name == "bidding" and not node_config.enabled:
                node_config.enabled = self.keyword_library.is_bidding_enabled(industry)
        
        return strategy
    
    def _get_enabled_nodes(self, strategy: SearchStrategy) -> list[str]:
        """获取启用的节点列表"""
        enabled = []
        for node_name in ["xhs", "douyin", "web_tagging", "bidding", "paper", "weixin"]:
            node_config: NodeConfig = getattr(strategy, node_name)
            if node_config.enabled:
                enabled.append(node_name)
        
        # 按优先级排序
        enabled.sort(key=lambda n: getattr(strategy, n).priority)
        return enabled
    
    def _collect_all_keywords(self, strategy: SearchStrategy) -> dict[str, list[str]]:
        """收集所有节点的关键词"""
        keywords = {}
        for node_name in ["xhs", "douyin", "web_tagging", "bidding", "weixin"]:
            node_config: NodeConfig = getattr(strategy, node_name)
            if node_config.enabled and node_config.keywords:
                keywords[node_name] = node_config.keywords
        return keywords


async def create_company_router(app_config: Any) -> CompanyRouter:
    """创建公司路由器实例"""
    return CompanyRouter(app_config)
