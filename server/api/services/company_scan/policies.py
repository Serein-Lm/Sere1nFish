"""Pure completion and resource policies for company scans."""
from __future__ import annotations

from typing import Any


def requires_initial_core_lease(
    *,
    enabled_core_modules: dict[str, bool],
    target_id: str,
    refresh_target_identity: bool,
    enable_wechat: bool,
    wechat_target_selection_mode: str,
    checkpoint_modules: set[str] | None = None,
    restore_core_context: bool = False,
) -> bool:
    checkpoints = checkpoint_modules or set()
    if any(
        enabled and module not in checkpoints
        for module, enabled in enabled_core_modules.items()
    ):
        return True
    if not restore_core_context and (
        not str(target_id or "").strip() or refresh_target_identity
    ):
        return True
    return bool(
        not restore_core_context
        and enable_wechat
        and str(wechat_target_selection_mode or "auto").strip().casefold() != "all"
    )


def should_checkpoint_module(kind: str, outcome: Any) -> bool:
    """Only cache modules whose enabled work reached a complete terminal state."""
    if not isinstance(outcome, dict):
        return False
    if kind == "asset_url":
        from api.services.target_scan_profile import coverage_status_from_result

        assets = dict(outcome.get("assets") or {})
        providers = assets.get("providers")
        provider_results = [
            provider
            for provider in (
                providers.values() if isinstance(providers, dict) else []
            )
            if isinstance(provider, dict)
        ]
        providers_failed = bool(provider_results) and all(
            provider.get("errors") for provider in provider_results
        )
        return (
            not providers_failed
            and coverage_status_from_result("website", outcome) == "completed"
        )
    payload = (
        dict(outcome.get("result") or {})
        if kind == "control_structure"
        else outcome
    )
    status = str(payload.get("status") or "").strip().lower()
    return status in {"completed", "skipped", "disabled"}


def incomplete_collection_sources(result: dict[str, Any]) -> list[str]:
    """Return enabled collection sources that did not finish completely."""
    incomplete: list[str] = []
    terminal_statuses = {"completed", "skipped", "disabled"}
    for source in (
        "control_structure",
        "url_scan",
        "website_documents",
        "bidding",
        "wechat",
        "scholar",
    ):
        section = result.get(source)
        if not isinstance(section, dict) or section.get("enabled") is False:
            continue
        if str(section.get("status") or "pending").strip().lower() not in terminal_statuses:
            incomplete.append(source)

    xhs = result.get("xhs")
    if isinstance(xhs, dict) and xhs.get("enabled") is not False and xhs.get("root_selected"):
        if str(xhs.get("status") or "pending").strip().lower() not in terminal_statuses:
            incomplete.append("xhs")

    control = result.get("control_structure")
    if isinstance(control, dict) and control.get("enabled") is not False and control.get("errors"):
        incomplete.append("control_structure")
    if isinstance(control, dict):
        summary = dict(control.get("scan_summary") or {})
        if int(summary.get("partial") or 0) or int(summary.get("failed") or 0):
            incomplete.append("control_structure")

    scholar = result.get("scholar")
    if isinstance(scholar, dict) and scholar.get("descendant_status"):
        if str(scholar.get("descendant_status") or "").lower() not in terminal_statuses:
            incomplete.append("scholar")

    assets = result.get("assets")
    if isinstance(assets, dict) and assets.get("enabled") is not False:
        providers = assets.get("providers")
        provider_results = [
            provider
            for provider in (
                providers.values() if isinstance(providers, dict) else []
            )
            if isinstance(provider, dict)
        ]
        if provider_results and all(item.get("errors") for item in provider_results):
            incomplete.append("asset_intelligence")
    if result.get("sub_errors"):
        incomplete.append("subtasks")
    return list(dict.fromkeys(incomplete))
