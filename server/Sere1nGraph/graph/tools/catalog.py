"""Auditable catalog for tools exposed to AI Hub specialists."""
from __future__ import annotations

from typing import Any


def _name(tool: Any) -> str:
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", "unknown"))


def _description(tool: Any) -> str:
    return str(getattr(tool, "description", None) or getattr(tool, "__doc__", "") or "").strip()


def _tool_groups() -> dict[str, list[Any]]:
    from .analysis_tools import ANALYSIS_TOOLS
    from .artifact_tools import ARTIFACT_QUERY_TOOLS
    from .context_tools import CONTEXT_TOOLS
    from .persona_tools import PERSONA_TOOLS
    from .project_data_tools import PROJECT_DATA_TOOLS
    from .read_tools import READ_TOOLS
    from .skill_tools import SKILL_TOOLS
    from .word_tools import PAYLOAD_WORD_TOOLS, WORD_TOOLS

    data = (
        list(READ_TOOLS)
        + list(ANALYSIS_TOOLS)
        + list(PROJECT_DATA_TOOLS)
        + list(ARTIFACT_QUERY_TOOLS)
    )
    persona = list(PERSONA_TOOLS) + list(CONTEXT_TOOLS) + [
        tool
        for tool in READ_TOOLS
        if _name(tool) in {"list_contact_profiles", "get_contact_profile", "list_mobile_operations"}
    ]
    content = list(SKILL_TOOLS) + list(WORD_TOOLS) + list(PERSONA_TOOLS) + list(ARTIFACT_QUERY_TOOLS)
    payload = (
        list(READ_TOOLS)
        + list(ANALYSIS_TOOLS)
        + list(PERSONA_TOOLS)
        + list(CONTEXT_TOOLS)
        + list(PROJECT_DATA_TOOLS)
        + list(ARTIFACT_QUERY_TOOLS)
        + list(SKILL_TOOLS)
        + list(PAYLOAD_WORD_TOOLS)
    )
    return {"data": data, "persona": persona, "content": content, "payload": payload}


def get_hub_tool_catalog(*, chrome_configured: bool = False) -> dict[str, Any]:
    groups = _tool_groups()
    assignments: dict[str, list[str]] = {
        agent: sorted({_name(tool) for tool in tools}) for agent, tools in groups.items()
    }

    all_tools: dict[str, dict[str, Any]] = {}
    for agent, tools in groups.items():
        for tool in tools:
            name = _name(tool)
            item = all_tools.setdefault(
                name,
                {
                    "name": name,
                    "description": _description(tool),
                    "kind": "builtin",
                    "agents": [],
                },
            )
            item["agents"].append(agent)

    from .analysis_tools import ANALYSIS_TOOLS
    from .artifact_tools import ARTIFACT_QUERY_TOOLS
    from .context_tools import CONTEXT_TOOLS
    from .persona_tools import PERSONA_TOOLS
    from .project_data_tools import PROJECT_DATA_TOOLS
    from .read_tools import READ_TOOLS

    query_names = {
        _name(tool)
        for tool in (
            list(READ_TOOLS)
            + list(ANALYSIS_TOOLS)
            + list(PERSONA_TOOLS)
            + list(CONTEXT_TOOLS)
            + list(PROJECT_DATA_TOOLS)
            + list(ARTIFACT_QUERY_TOOLS)
        )
    }
    exposed_names = {name for names in assignments.values() for name in names}
    missing_queries = sorted(query_names - exposed_names)

    return {
        "agents": [
            {
                "name": "data",
                "prompt": "hub/data",
                "tools": assignments["data"],
            },
            {
                "name": "persona",
                "prompt": "hub/persona",
                "tools": assignments["persona"],
            },
            {
                "name": "content",
                "prompt": "hub/content",
                "tools": assignments["content"],
            },
            {
                "name": "payload",
                "prompt": "hub/payload",
                "tools": assignments["payload"],
                "mcp_servers": ["chrome-devtools"],
            },
        ],
        "tools": sorted(all_tools.values(), key=lambda item: item["name"]),
        "mcp": [
            {
                "name": "chrome-devtools",
                "purpose": "公网检索与来源核验",
                "configured": chrome_configured,
                "agents": ["payload"],
            }
        ],
        "audit": {
            "query_interfaces": len(query_names),
            "registered_query_interfaces": len(query_names) - len(missing_queries),
            "missing_query_interfaces": missing_queries,
            "complete": not missing_queries,
        },
    }
