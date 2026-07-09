"""
公司路由器数据结构定义

结构化输出 schema，用于 LLM 解析和后续节点调用
"""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


class IndustryType(str, Enum):
    """行业类型"""
    INTERNET = "internet"           # 互联网
    FINANCE = "finance"             # 金融
    AIRPORT = "airport"             # 机场/航空
    GOVERNMENT = "government"       # 政府/事业单位
    EDUCATION = "education"         # 教育
    HEALTHCARE = "healthcare"       # 医疗
    MANUFACTURING = "manufacturing" # 制造业
    RETAIL = "retail"               # 零售
    REAL_ESTATE = "real_estate"     # 房地产
    ENERGY = "energy"               # 能源
    LOGISTICS = "logistics"         # 物流
    TELECOM = "telecom"             # 电信
    MEDIA = "media"                 # 传媒
    CONSULTING = "consulting"       # 咨询
    HOTEL = "hotel"                 # 酒店
    FOOD = "food"                   # 餐饮
    OTHER = "other"                 # 其他


class BusinessNature(str, Enum):
    """业务性质"""
    TO_C = "to_c"       # 面向消费者
    TO_B = "to_b"       # 面向企业
    TO_G = "to_g"       # 面向政府
    MIXED = "mixed"     # 混合


class CompanyScale(str, Enum):
    """公司规模"""
    LARGE = "large"         # 大型（>10000人）
    MEDIUM = "medium"       # 中型（1000-10000人）
    SMALL = "small"         # 小型（100-1000人）
    STARTUP = "startup"     # 初创（<100人）
    UNKNOWN = "unknown"     # 未知


# ── LLM 输出容错工具 ──

def _coerce_str_to_list(v: Any) -> list:
    """LLM 可能把数组写成空格/逗号分隔的字符串"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return []
        # 尝试逗号分隔，再尝试空格分隔
        if "," in v or "，" in v:
            return [s.strip() for s in v.replace("，", ",").split(",") if s.strip()]
        return [s.strip() for s in v.split() if s.strip()]
    return []


def _coerce_str_to_dict(v: Any) -> dict:
    """LLM 可能把 dict 写成字符串"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return {}
        # 尝试 key: value 格式
        import json
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            pass
        # 尝试 "key: value" 格式
        result = {}
        for part in v.split(","):
            part = part.strip()
            if ":" in part:
                k, val = part.split(":", 1)
                result[k.strip()] = val.strip()
            elif part:
                result[part] = True
        return result if result else {}
    return {}


def _coerce_business_nature(v: Any) -> str:
    """LLM 可能输出 'to_c/to_b/mixed' 这种斜杠分隔"""
    if isinstance(v, str) and "/" in v:
        # 取第一个有效值，或者如果包含多个就是 mixed
        parts = [p.strip() for p in v.split("/")]
        valid = {"to_c", "to_b", "to_g", "mixed"}
        valid_parts = [p for p in parts if p in valid]
        if len(valid_parts) > 1:
            return "mixed"
        if valid_parts:
            return valid_parts[0]
    return v


class CompanyProfile(BaseModel):
    """公司画像"""
    
    icp_name: str = Field(
        description="ICP备案的标准公司名称，如：上海宽娱数码科技有限公司"
    )
    
    colloquial_names: list[str] = Field(
        default_factory=list,
        description="口语化名称列表，如：['b站', 'bilibili', '哔哩哔哩']"
    )
    
    industry: IndustryType = Field(
        default=IndustryType.OTHER,
        description="主要行业类型"
    )
    
    sub_industries: list[str] = Field(
        default_factory=list,
        description="细分行业标签，如：['视频平台', '二次元', 'UGC']"
    )
    
    business_nature: BusinessNature = Field(
        default=BusinessNature.MIXED,
        description="业务性质：to_c/to_b/to_g/mixed"
    )
    
    main_business: list[str] = Field(
        default_factory=list,
        description="主营业务，如：['视频内容', '直播', '游戏']"
    )
    
    tags: list[str] = Field(
        default_factory=list,
        description="特征标签，如：['实习较多', '校招活跃', '技术驱动']"
    )
    
    scale: CompanyScale = Field(
        default=CompanyScale.UNKNOWN,
        description="公司规模"
    )
    
    is_listed: bool = Field(
        default=False,
        description="是否上市公司"
    )
    
    headquarters: Optional[str] = Field(
        default=None,
        description="总部所在地"
    )

    @field_validator("colloquial_names", "sub_industries", "main_business", "tags", mode="before")
    @classmethod
    def coerce_list(cls, v):
        return _coerce_str_to_list(v)

    @field_validator("business_nature", mode="before")
    @classmethod
    def coerce_business_nature(cls, v):
        return _coerce_business_nature(v)


class NodeConfig(BaseModel):
    """节点配置"""
    
    enabled: bool = Field(
        default=False,
        description="是否启用该节点"
    )
    
    priority: int = Field(
        default=5,
        ge=1,
        le=10,
        description="优先级，1最高，10最低"
    )
    
    keywords: list[str] = Field(
        default_factory=list,
        description="搜索关键词列表"
    )
    
    focus_points: list[str] = Field(
        default_factory=list,
        description="关注重点"
    )
    
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="节点特定参数"
    )

    @field_validator("keywords", "focus_points", mode="before")
    @classmethod
    def coerce_list(cls, v):
        return _coerce_str_to_list(v)

    @field_validator("params", mode="before")
    @classmethod
    def coerce_dict(cls, v):
        return _coerce_str_to_dict(v)


class SearchStrategy(BaseModel):
    """搜索策略 - 各节点配置"""
    
    xhs: NodeConfig = Field(
        default_factory=NodeConfig,
        description="小红书搜索配置"
    )
    
    douyin: NodeConfig = Field(
        default_factory=NodeConfig,
        description="抖音搜索配置"
    )
    
    web_tagging: NodeConfig = Field(
        default_factory=NodeConfig,
        description="官网爬取配置"
    )
    
    bidding: NodeConfig = Field(
        default_factory=NodeConfig,
        description="招投标搜索配置"
    )
    
    paper: NodeConfig = Field(
        default_factory=NodeConfig,
        description="论文搜索配置（暂不启用）"
    )
    
    weixin: NodeConfig = Field(
        default_factory=NodeConfig,
        description="微信公众号搜索配置"
    )


class CompanyRouterOutput(BaseModel):
    """LLM 结构化输出"""
    
    company_profile: CompanyProfile = Field(
        description="公司画像"
    )
    
    search_strategy: SearchStrategy = Field(
        description="搜索策略"
    )
    
    reasoning: str = Field(
        default="",
        description="决策推理过程"
    )


# ============ 节点调用参数 ============

class XhsSearchParams(BaseModel):
    """小红书搜索参数"""
    keywords: list[str]
    max_notes: int = 20
    sort_by: str = "general"  # general/hot/time
    note_type: str = "all"    # all/video/image


class DouyinSearchParams(BaseModel):
    """抖音搜索参数"""
    keywords: list[str]
    max_videos: int = 20
    sort_by: str = "general"


class WebTaggingParams(BaseModel):
    """官网爬取参数"""
    url: Optional[str] = None
    crawl_points: list[str] = Field(default_factory=list)
    extract_contacts: bool = True


class BiddingSearchParams(BaseModel):
    """招投标搜索参数"""
    keywords: list[str]
    regions: list[str] = Field(default_factory=list)
    date_range_days: int = 365
