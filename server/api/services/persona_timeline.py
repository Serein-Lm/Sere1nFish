"""Arithmetic checks for explicit dates in fictional contexts, without filling facts."""
from __future__ import annotations
import re
from datetime import datetime, timezone


def timeline_issues(profile: dict, *, year: int | None = None) -> list[str]:
    year = year or datetime.now(timezone.utc).year
    issues = []
    career = str(profile.get("career_path") or "")
    start = re.match(r"\s*((?:19|20)\d{2})年", career)
    years = re.fullmatch(r"\s*(\d{1,2})\s*年(?:左右)?\s*", str(profile.get("work_years") or ""))
    if start and years and not re.search(r"间断|待业|脱产|离职休|休整|职业中断", career):
        span, stated = year - int(start[1]), int(years[1])
        if abs(span - stated) > 1:
            issues.append(f"career_path 从 {start[1]} 年连续从业，截至 {year} 年约 {span} 年，work_years 却为 {stated} 年；请修订年限及摘要或明确实际间断经历")
    background = str(profile.get("background") or "")
    life = str(profile.get("life_stage") or "")
    for child, label in (("儿子", "育有一子|儿子"), ("女儿", "育有一女|女儿")):
        births = re.findall(r"((?:19|20)\d{2})年" + child + r"(?:出生|诞生)", background)
        ages = re.findall(r"(?:" + label + r")[（(，,：:\s]*(\d{1,2})岁", life)
        if len(births) == len(ages) == 1 and abs(year - int(births[0]) - int(ages[0])) > 1:
            issues.append(f"{child}在 {births[0]} 年出生，与 life_stage 的 {ages[0]} 岁不一致；请按当前 {year} 年统一家庭时间线")
    return issues
