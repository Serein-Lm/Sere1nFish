"""
关键词库

按行业维护搜索关键词、关注重点、爬取节点等配置
可独立维护，方便扩展
"""

from typing import Optional
from .schemas import IndustryType


class KeywordLibrary:
    """
    关键词库
    
    按行业维护：
    - 搜索关键词模板
    - 关注重点
    - 爬取节点
    - 是否启用招投标
    """
    
    # ============ 小红书关键词模板 ============
    # {company} 会被替换为公司口语化名称
    
    XHS_KEYWORDS = {
        IndustryType.INTERNET: [
            "{company}实习",
            "{company}内推",
            "{company}跳槽",
            "{company}招聘",
            "{company}offer",
            "{company}面试",
            "{company}工作体验",
            "{company}员工",
        ],
        IndustryType.FINANCE: [
            "{company}实习",
            "{company}校招",
            "{company}工作体验",
            "{company}面试",
            "{company}offer",
            "{company}待遇",
        ],
        IndustryType.AIRPORT: [
            "{company}招商",
            "{company}广告",
            "{company}广告投放",
            "{company}机场广告",
            "{company}媒体",
        ],
        IndustryType.GOVERNMENT: [
            "{company}公务员",
            "{company}事业编",
            "{company}考试",
            "{company}招聘",
        ],
        IndustryType.EDUCATION: [
            "{company}老师",
            "{company}招聘",
            "{company}工作",
            "{company}待遇",
        ],
        IndustryType.HEALTHCARE: [
            "{company}医生",
            "{company}护士",
            "{company}招聘",
            "{company}工作体验",
        ],
        IndustryType.CONSULTING: [
            "{company}实习",
            "{company}校招",
            "{company}面试",
            "{company}工作体验",
        ],
        IndustryType.HOTEL: [
            "{company}招商",
            "{company}加盟",
            "{company}合作",
        ],
        IndustryType.RETAIL: [
            "{company}招商",
            "{company}加盟",
            "{company}合作",
            "{company}入驻",
        ],
    }
    
    # ============ 抖音关键词模板 ============
    
    DOUYIN_KEYWORDS = {
        IndustryType.INTERNET: [
            "{company}实习",
            "{company}员工",
            "{company}工作日常",
            "{company}公司文化",
        ],
        IndustryType.FINANCE: [
            "{company}实习",
            "{company}员工",
            "{company}工作",
        ],
        IndustryType.AIRPORT: [
            "{company}广告",
            "{company}招商",
        ],
    }
    
    # ============ 关注重点 ============
    
    XHS_FOCUS = {
        IndustryType.INTERNET: ["员工动态", "招聘信息", "实习体验", "内推机会"],
        IndustryType.FINANCE: ["校招信息", "实习体验", "薪资待遇"],
        IndustryType.AIRPORT: ["招商信息", "广告投放", "媒体合作"],
        IndustryType.GOVERNMENT: ["招考信息", "工作体验"],
        IndustryType.HOTEL: ["招商加盟", "合作机会"],
        IndustryType.RETAIL: ["招商入驻", "加盟信息"],
    }
    
    DOUYIN_FOCUS = {
        IndustryType.INTERNET: ["员工生活", "公司文化", "招聘信息"],
        IndustryType.FINANCE: ["员工日常", "工作环境"],
        IndustryType.AIRPORT: ["广告展示", "招商信息"],
    }
    
    WEB_FOCUS = {
        IndustryType.INTERNET: ["商务合作", "联系方式", "招聘入口", "API文档"],
        IndustryType.FINANCE: ["招聘信息", "网点信息", "联系方式"],
        IndustryType.AIRPORT: ["招商信息", "广告服务", "商务合作"],
        IndustryType.GOVERNMENT: ["招标公告", "政务公开", "联系方式"],
        IndustryType.HOTEL: ["招商加盟", "联系方式"],
        IndustryType.RETAIL: ["招商入驻", "联系方式"],
    }

    
    # ============ 官网爬取节点 ============
    
    CRAWL_POINTS = {
        IndustryType.INTERNET: ["商务合作", "关于我们", "联系我们", "招聘", "API"],
        IndustryType.FINANCE: ["关于我们", "联系我们", "招聘", "网点查询"],
        IndustryType.AIRPORT: ["招商信息", "广告服务", "商务合作", "联系我们"],
        IndustryType.GOVERNMENT: ["招标公告", "政务公开", "联系方式"],
        IndustryType.HOTEL: ["招商加盟", "联系我们", "关于我们"],
        IndustryType.RETAIL: ["招商入驻", "联系我们", "关于我们"],
        IndustryType.HEALTHCARE: ["联系我们", "科室介绍", "专家团队"],
        IndustryType.EDUCATION: ["联系我们", "招生信息", "师资力量"],
    }
    
    # ============ 招投标启用行业 ============
    
    BIDDING_ENABLED_INDUSTRIES = {
        IndustryType.AIRPORT,
        IndustryType.GOVERNMENT,
        IndustryType.HEALTHCARE,
        IndustryType.EDUCATION,
        IndustryType.FINANCE,  # 金融IT系统招标
        IndustryType.ENERGY,
        IndustryType.TELECOM,
    }
    
    # ============ 招投标关键词 ============
    
    BIDDING_KEYWORDS = {
        IndustryType.AIRPORT: [
            "{company}广告",
            "{company}媒体运营",
            "{company}广告位",
        ],
        IndustryType.GOVERNMENT: [
            "{company}采购",
            "{company}招标",
        ],
        IndustryType.HEALTHCARE: [
            "{company}设备采购",
            "{company}信息化",
        ],
        IndustryType.FINANCE: [
            "{company}IT系统",
            "{company}信息化",
        ],
    }
    
    def __init__(self):
        """初始化关键词库"""
        pass
    
    def get_keywords(
        self,
        industry: IndustryType,
        node: str,
    ) -> list[str]:
        """
        获取指定行业和节点的关键词模板
        
        Args:
            industry: 行业类型
            node: 节点名称 (xhs/douyin/bidding)
        
        Returns:
            关键词模板列表
        """
        keyword_map = {
            "xhs": self.XHS_KEYWORDS,
            "douyin": self.DOUYIN_KEYWORDS,
            "bidding": self.BIDDING_KEYWORDS,
        }
        
        if node not in keyword_map:
            return []
        
        return keyword_map[node].get(industry, [])
    
    def get_focus_points(
        self,
        industry: IndustryType,
        node: str,
    ) -> list[str]:
        """获取关注重点"""
        focus_map = {
            "xhs": self.XHS_FOCUS,
            "douyin": self.DOUYIN_FOCUS,
            "web_tagging": self.WEB_FOCUS,
        }
        
        if node not in focus_map:
            return []
        
        return focus_map[node].get(industry, [])
    
    def get_crawl_points(self, industry: IndustryType) -> list[str]:
        """获取官网爬取节点"""
        return self.CRAWL_POINTS.get(industry, ["联系我们", "关于我们"])
    
    def is_bidding_enabled(self, industry: IndustryType) -> bool:
        """判断该行业是否启用招投标搜索"""
        return industry in self.BIDDING_ENABLED_INDUSTRIES
    
    def expand_keywords(
        self,
        templates: list[str],
        company_name: str,
    ) -> list[str]:
        """
        展开关键词模板
        
        Args:
            templates: 关键词模板列表
            company_name: 公司名称（口语化）
        
        Returns:
            展开后的关键词列表
        """
        return [
            t.replace("{company}", company_name)
            for t in templates
        ]


# ============ 自定义关键词扩展 ============

class CustomKeywordLibrary(KeywordLibrary):
    """
    可扩展的关键词库
    
    支持从配置文件或数据库加载自定义关键词
    """
    
    def __init__(self, custom_config: Optional[dict] = None):
        super().__init__()
        self.custom_config = custom_config or {}
        self._load_custom_keywords()
    
    def _load_custom_keywords(self):
        """加载自定义关键词配置"""
        if "xhs_keywords" in self.custom_config:
            for industry_str, keywords in self.custom_config["xhs_keywords"].items():
                try:
                    industry = IndustryType(industry_str)
                    self.XHS_KEYWORDS[industry] = keywords
                except ValueError:
                    pass
        
        if "douyin_keywords" in self.custom_config:
            for industry_str, keywords in self.custom_config["douyin_keywords"].items():
                try:
                    industry = IndustryType(industry_str)
                    self.DOUYIN_KEYWORDS[industry] = keywords
                except ValueError:
                    pass
    
    def add_keywords(
        self,
        industry: IndustryType,
        node: str,
        keywords: list[str],
    ):
        """动态添加关键词"""
        keyword_map = {
            "xhs": self.XHS_KEYWORDS,
            "douyin": self.DOUYIN_KEYWORDS,
            "bidding": self.BIDDING_KEYWORDS,
        }
        
        if node in keyword_map:
            existing = keyword_map[node].get(industry, [])
            keyword_map[node][industry] = list(set(existing + keywords))
