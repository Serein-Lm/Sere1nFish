"""采集分析 — 对截图做结构化视觉提取 + 相关性打分。

三种能力(均复用 Sere1nGraph create_llm + runtime 模型配置 mobile_screen_model,
用 observation_context 归因 token,不绑定单一供应商):
- triage_screenshot: 对**列表页**截图,识别所有可见条目,给出每条的结构化字段、
  相关性分(0-100)、打分理由,以及可点击中心点坐标(0-1000 归一化),用于「列表全收 + 详情选采」;
- analyze_detail: 对**详情页**(点进后可多张截图)做综合结构化,产出单条富记录 + 分数;
- analyze_screenshot: 不深采时的列表整屏结构化(每条带分数),或无字段时的整屏摘要。

统一返回记录形状:
  {"fields": dict, "score": int|None, "score_reason": str, "tap_x": int|None, "tap_y": int|None}
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, create_model

from Sere1nGraph.graph.agents.runtime import create_llm
from api.services.runtime_config import get_runtime_app_config
from core.observability import observation_context

from api.models.mobile_collect import ExtractField


_PY_TYPE = {
    "string": (str | None, None),
    "number": (float | None, None),
    "boolean": (bool | None, None),
    "list": (list[str], ...),
}

# schema 内固定追加的字段名(与业务字段区分,便于从结果里剥离)
_SCORE_KEYS = {
    "subject_match",
    "relevance_score",
    "score_reason",
    "tap_x",
    "tap_y",
    "source_url",
}


def _build_item_model(
    fields: list[ExtractField], *, with_coords: bool
) -> type[BaseModel]:
    """构造单条目模型:业务字段 + relevance_score/score_reason(+可选 tap 坐标)。"""
    item_field_defs: dict[str, Any] = {}
    for f in fields:
        annotation, _default = _PY_TYPE.get(f.type, (str | None, None))
        item_field_defs[f.name] = (
            annotation,
            Field(
                default_factory=list if f.type == "list" else (lambda: None),
                description=f.description,
            ),
        )
    item_field_defs["subject_match"] = (
        int,
        Field(
            default=0,
            description=(
                "主体对应程度(0-100):该条目的主体是否就是搜索词所指的目标主体。"
                "90-100=完全就是目标主体本身;70-89=直接相关(目标主体的项目/公告/子事项);"
                "40-69=间接相关(同行业/关联方/提及);0-39=不同主体或无关。"
            ),
        ),
    )
    item_field_defs["relevance_score"] = (
        int,
        Field(default=0, description="相关性/价值分(0-100),依据搜索词与内容价值,越相关越高"),
    )
    item_field_defs["score_reason"] = (
        str,
        Field(default="", description="简短打分理由"),
    )
    item_field_defs["source_url"] = (
        str | None,
        Field(
            default=None,
            description="若画面中可见该条目的原文链接/URL(http/https)则填写,看不到就留空,不要臆造",
        ),
    )
    if with_coords:
        item_field_defs["tap_x"] = (
            int | None,
            Field(default=None, description="该条目可点击中心点的横坐标(0-1000 归一化)"),
        )
        item_field_defs["tap_y"] = (
            int | None,
            Field(default=None, description="该条目可点击中心点的纵坐标(0-1000 归一化)"),
        )
    return create_model("CollectItem", **item_field_defs)  # type: ignore[call-overload]


def _build_records_model(
    fields: list[ExtractField], *, with_coords: bool = False
) -> type[BaseModel]:
    """由 extract_fields 动态构造 CollectRecords 结构化模型(条目带分数)。"""
    item_model = _build_item_model(fields, with_coords=with_coords)
    return create_model(
        "CollectRecords",
        items=(
            list[item_model],  # type: ignore[valid-type]
            Field(default_factory=list, description="从当前截图识别到的结构化条目列表"),
        ),
    )


def _clamp_score(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _split_record(data: dict[str, Any]) -> dict[str, Any]:
    """从条目 dict 中剥离评分/坐标/链接,返回统一记录形状。"""
    score = _clamp_score(data.get("relevance_score"))
    subject_match = _clamp_score(data.get("subject_match"))
    reason = str(data.get("score_reason") or "")
    tap_x = data.get("tap_x")
    tap_y = data.get("tap_y")
    raw_url = data.get("source_url")
    source_url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None
    fields = {k: v for k, v in data.items() if k not in _SCORE_KEYS}
    return {
        "fields": fields,
        "score": score,
        "subject_match": subject_match,
        "score_reason": reason,
        "tap_x": tap_x if isinstance(tap_x, int) else None,
        "tap_y": tap_y if isinstance(tap_y, int) else None,
        "source_url": source_url,
    }


def _has_content(fields: dict[str, Any]) -> bool:
    return any(v not in (None, "", [], {}) for v in fields.values())


def _get_vision_llm(app_config: Any):
    vision_model = app_config.runtime.models.mobile_screen_model
    return create_llm(app_config, model_name=vision_model, streaming=False)


def _fields_desc(fields: list[ExtractField]) -> str:
    return "、".join(f"{f.name}({f.description})" for f in fields) or "关键条目"


async def triage_screenshot(
    image_base64: str,
    *,
    fields: list[ExtractField],
    app_name: str,
    keyword: str,
    project_id: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """列表页分诊:识别所有条目 + 结构化字段 + 相关性分 + 可点击坐标。"""
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)
    records_model = _build_records_model(fields, with_coords=True)
    structured = llm.with_structured_output(records_model)

    system = (
        f"你是手机列表页信息分诊助手。当前应用: {app_name}, 搜索词: {keyword or '无'}。\n"
        f"【目标主体】以搜索词「{keyword or '无'}」为唯一目标主体。逐条判断每个条目的主体"
        "是否就是该目标主体,并用 subject_match 打出主体对应程度。只对真正属于/围绕目标主体的"
        "条目给高分;其他主体(其他公司/机构/无关话题)给低 subject_match,不要混入。\n"
        f"请识别当前列表页中所有可见条目,并为每个条目提取字段: {_fields_desc(fields)}。\n"
        "同时给出:\n"
        "- subject_match: 0-100 主体对应程度。90-100=完全就是目标主体本身;70-89=直接相关"
        "(目标主体的项目/公告/子事项);40-69=间接相关(同行业/关联方/仅提及);0-39=不同主体或无关;\n"
        "- relevance_score: 0-100 的相关性/价值分,依据与目标主体的相关度与内容价值判断,越相关越高;"
        "与目标主体强相关、且含招标/中标/公告/联系方式等高价值信息的条目应给更高分;\n"
        "- score_reason: 简短打分理由,说明主体是谁、为何这样评级;\n"
        "- source_url: 若画面能看到该条目的原文链接/URL(http/https)则填入,看不到就留空,不要臆造;\n"
        "- tap_x / tap_y: 该条目在屏幕上可点击中心点的坐标,使用 0-1000 归一化坐标系"
        "(左上角为 0,0,右下角为 1000,1000)。\n"
        "严格依据画面内容,不臆测;无法确定的字段留空。若画面无有效条目, items 返回空数组。"
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": "请分诊当前列表页并按 schema 输出。"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            },
        ]
    )
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_triage",
        agent="collect",
    ):
        result = await structured.ainvoke([SystemMessage(content=system), message])

    items = getattr(result, "items", []) or []
    out: list[dict[str, Any]] = []
    for item in items:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        rec = _split_record(data)
        if _has_content(rec["fields"]):
            out.append(rec)
    return out


async def analyze_detail(
    image_base64s: list[str],
    *,
    fields: list[ExtractField],
    app_name: str,
    keyword: str,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any] | None:
    """详情页深采:对点进后的一张或多张截图做综合结构化,产出单条富记录 + 分数。"""
    if not image_base64s:
        return None
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)
    item_model = _build_item_model(fields, with_coords=False)
    structured = llm.with_structured_output(item_model)

    system = (
        f"你是手机详情页信息提取助手。当前应用: {app_name}, 搜索词: {keyword or '无'}。\n"
        f"【目标主体】以搜索词「{keyword or '无'}」为目标主体, 只围绕该主体提取信息, 不要混入无关主体内容。\n"
        f"以下是同一条内容详情页的一张或多张截图。请综合所有截图,提取字段: {_fields_desc(fields)}。\n"
        "务必尽力捕捉画面中出现的联系方式(手机号/座机/邮箱/微信号/QQ)与项目背景信息,"
        "把它们填入对应字段(如 contact / background),便于后续联系与背景分析。\n"
        "并给出 relevance_score(0-100 相关性/价值分)与 score_reason(简短理由);"
        "若画面可见原文链接/URL 则填 source_url,看不到留空。\n"
        "严格依据画面内容,不臆测;无法确定的字段留空。"
    )
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "请综合以下详情页截图提取单条结构化记录并按 schema 输出。"}
    ]
    for b64 in image_base64s:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        )
    message = HumanMessage(content=content)
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_detail",
        agent="collect",
    ):
        result = await structured.ainvoke([SystemMessage(content=system), message])

    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    rec = _split_record(data)
    if not _has_content(rec["fields"]):
        return None
    return rec


async def analyze_screenshot(
    image_base64: str,
    *,
    fields: list[ExtractField],
    app_name: str,
    keyword: str,
    project_id: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """不深采时的列表整屏结构化(每条带分数);无字段时退化为整屏摘要单条记录。"""
    app_config = await get_runtime_app_config()
    llm = _get_vision_llm(app_config)

    if not fields:
        prompt = (
            f"你正在看应用「{app_name}」的截图(搜索词: {keyword or '无'})。"
            "请用简体中文简要描述当前画面的关键信息(50-150字)。"
        )
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                },
            ]
        )
        with observation_context(
            project_id=project_id,
            task_id=task_id,
            phase="mobile_collect_analyze",
            agent="collect",
        ):
            resp = await llm.ainvoke([message])
        summary = resp.content if isinstance(resp.content, str) else str(resp.content)
        summary = summary.strip()
        if not summary:
            return []
        return [
            {
                "fields": {"summary": summary},
                "score": None,
                "subject_match": 0,
                "score_reason": "",
                "tap_x": None,
                "tap_y": None,
                "source_url": None,
            }
        ]

    records_model = _build_records_model(fields, with_coords=False)
    structured = llm.with_structured_output(records_model)
    system = (
        f"你是手机截图信息提取助手。当前应用: {app_name}, 搜索词: {keyword or '无'}。\n"
        f"请从截图中识别所有可见条目,并为每个条目提取字段: {_fields_desc(fields)}。\n"
        "同时给出 relevance_score(0-100 相关性分)与 score_reason(简短理由)。\n"
        "严格依据画面内容,不臆测;无法确定的字段留空。若画面无有效条目, items 返回空数组。"
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": "请提取截图中的条目并按 schema 输出。"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            },
        ]
    )
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_collect_analyze",
        agent="collect",
    ):
        result = await structured.ainvoke([SystemMessage(content=system), message])
    items = getattr(result, "items", []) or []
    out: list[dict[str, Any]] = []
    for item in items:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        rec = _split_record(data)
        if _has_content(rec["fields"]):
            out.append(rec)
    return out
