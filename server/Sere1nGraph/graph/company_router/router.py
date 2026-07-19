"""
公司信息路由器 (Company Router)

核心 LLM Agent，负责:
1. 解析公司名称，生成标准化和口语化名称
2. 判断公司行业、业务性质
3. 生成搜索策略和关键词
4. 决定启用哪些下游节点
"""

from __future__ import annotations

import json
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
from core.logger import get_logger


logger = get_logger("company_router")


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
            self._llm = create_llm(self.app_config, streaming=False)
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
            from ..prompts.loader import load_prompt

            llm = self._get_llm()
            from api.services.search_terms import get_keyword_skill_context
            from api.utils.json_extract import extract_json_object

            keyword_context = get_keyword_skill_context(["xhs", "weixin"])
            system_prompt = load_prompt("company_router/company_router")
            if keyword_context:
                system_prompt = f"{system_prompt}\n\n# 当前场景渐进加载的搜索 Skill\n\n{keyword_context}"
            schema = json.dumps(
                CompanyRouterOutput.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            system_prompt = (
                f"{system_prompt}\n\n"
                "只输出一个 JSON 对象，不要使用 Markdown。JSON 必须符合以下 schema：\n"
                f"{schema}"
            )
            json_llm = llm.bind(response_format={"type": "json_object"})
            result: CompanyRouterOutput | None = None
            last_error = ""
            raw_output = ""
            for attempt in range(2):
                correction = ""
                if attempt:
                    correction = (
                        "\n\n上一次输出未通过 JSON/schema 校验。"
                        f"错误：{last_error[:500]}。"
                        "请修正字段类型和缺失字段，只返回完整 JSON。"
                    )
                    if raw_output:
                        correction += f"\n上一次输出：{raw_output[:2_000]}"
                try:
                    response = await json_llm.ainvoke(
                        [
                            SystemMessage(content=system_prompt),
                            HumanMessage(
                                content=(
                                    "请分析以下公司并生成搜索策略：\n\n"
                                    f"{company_name}{correction}"
                                )
                            ),
                        ]
                    )
                    content = getattr(response, "content", "")
                    if isinstance(content, str):
                        raw_output = content
                    elif isinstance(content, list):
                        raw_output = "\n".join(
                            str(item.get("text") or item)
                            if isinstance(item, dict)
                            else str(item)
                            for item in content
                        )
                    else:
                        raw_output = str(content or "")
                    result = CompanyRouterOutput.model_validate(
                        extract_json_object(raw_output)
                    )
                    break
                except Exception as exc:
                    from core.llm_capacity import LLMCapacityUnavailableError

                    if isinstance(exc, LLMCapacityUnavailableError):
                        raise
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "CompanyRouter 输出校验失败，注入纠正提示后重试 | company=%s attempt=%s/2 error=%s",
                        company_name,
                        attempt + 1,
                        last_error,
                    )
            if result is None:
                raise RuntimeError(last_error or "CompanyRouter 未返回有效 JSON")
            
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
            from core.llm_capacity import LLMCapacityUnavailableError

            if isinstance(e, LLMCapacityUnavailableError):
                raise
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
            if node_name in {"xhs", "weixin"}:
                from api.services.search_terms import get_keyword_templates

                default_keywords = list(
                    dict.fromkeys([*default_keywords, *get_keyword_templates(node_name)])
                )
            
            # 合并关键词（去重）
            all_keywords = list(dict.fromkeys(node_config.keywords + default_keywords))
            all_focus = list(dict.fromkeys(node_config.focus_points + default_focus))
            
            # 替换公司名占位符
            search_names = list(
                dict.fromkeys(
                    [
                        *[name.strip() for name in profile.colloquial_names if name.strip()],
                        profile.icp_name.strip(),
                    ]
                )
            )[:3]
            
            expanded_keywords = []
            for kw in all_keywords:
                if "{company}" in kw or "公司名" in kw:
                    for search_name in search_names:
                        expanded_keywords.append(
                            kw.replace("{company}", search_name).replace("公司名", search_name)
                        )
                else:
                    expanded_keywords.append(kw)
            
            node_config.keywords = list(dict.fromkeys(expanded_keywords))
            node_config.focus_points = all_focus
            
            # 特殊处理：web_tagging 添加爬取节点
            if node_name == "web_tagging":
                crawl_points = self.keyword_library.get_crawl_points(industry)
                if crawl_points:
                    node_config.params["crawl_points"] = crawl_points
            
            # 特殊处理：bidding 根据行业决定是否启用
            if node_name == "bidding" and not node_config.enabled:
                node_config.enabled = self.keyword_library.is_bidding_enabled(industry)

            # 公众号是项目采集的基础词源；有词库时始终产出策略，执行仍由任务开关控制。
            if node_name == "weixin" and node_config.keywords:
                node_config.enabled = True
        
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
