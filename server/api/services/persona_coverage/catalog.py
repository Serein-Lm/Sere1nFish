"""Official industry dimensions, sourced and versioned independently of personas."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads(Path(__file__).with_name("industry_catalog.json").read_text(encoding="utf-8"))


def ordered_divisions() -> list[dict]:
    """Breadth first across sectors, then fill the more numerous divisions."""
    groups = {sector["code"]: [] for sector in catalog()["sectors"]}
    for division in catalog()["divisions"]:
        groups[division["sector_code"]].append(division)
    return [items[offset] for offset in range(max(map(len, groups.values()))) for items in groups.values() if offset < len(items)]


def default_background(industries: list[str] | None = None) -> str:
    scope = "、".join(industries or [sector["name"] for sector in catalog()["sectors"]])
    return f"基于公开网络资料研究以下行业及典型岗位，自动生成职业和生活经历完整、身份明确虚构的人设：{scope}。公司行业背景应有可追溯来源，人物不得冒用真实自然人的身份或联系方式。覆盖不同岗位、职级、地区与职业阶段，不需要用户提供资料。"
