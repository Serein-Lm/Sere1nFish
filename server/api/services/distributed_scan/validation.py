"""Bounded protocol validation shared by control-plane entry points."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit


SUPPORTED_CAPABILITIES = frozenset({"http_probe", "browser_probe"})
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_LABEL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,63}$")


def normalize_capabilities(
    values: dict[str, str] | list[str],
    *,
    allowed: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    source = values if isinstance(values, dict) else {value: "1" for value in values}
    result: dict[str, str] = {}
    for raw_name, raw_version in source.items():
        name = str(raw_name or "").strip().casefold()
        if not _CAPABILITY_RE.fullmatch(name):
            raise ValueError(f"能力名称无效: {raw_name}")
        if name not in SUPPORTED_CAPABILITIES:
            raise ValueError(f"暂不支持的能力: {name}")
        if allowed is not None and name not in allowed:
            raise ValueError(f"注册凭据未授权能力: {name}")
        version = str(raw_version or "1").strip()[:32] or "1"
        result[name] = version
    if not result:
        raise ValueError("至少声明一个能力")
    return result


def normalize_capacity(values: dict[str, int], capabilities: set[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for capability in capabilities:
        raw = values.get(f"{capability}_slots", values.get(capability, 1))
        try:
            result[f"{capability}_slots"] = max(1, min(int(raw), 500))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{capability} 容量必须是整数") from exc
    return result


def normalize_usage(values: dict[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for raw_key, raw_value in list(values.items())[:50]:
        key = str(raw_key or "").strip()
        if not _LABEL_RE.fullmatch(key):
            continue
        try:
            result[key] = max(0, min(int(raw_value), 1_000_000))
        except (TypeError, ValueError):
            continue
    return result


def normalize_labels(values: dict[str, str], *, limit: int = 30) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in list(values.items())[:limit]:
        key = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if _LABEL_RE.fullmatch(key) and value:
            result[key] = value[:200]
    return result


def _normalize_urls(value: Any, *, maximum: int) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    urls: list[str] = []
    for raw in values:
        url = str(raw or "").strip()
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        normalized = url[:4096]
        if normalized in urls:
            continue
        if len(urls) >= maximum:
            raise ValueError(f"单个工作项最多包含 {maximum} 个 URL")
        urls.append(normalized)
    if not urls:
        raise ValueError("工作项至少需要一个有效 HTTP(S) URL")
    return list(dict.fromkeys(urls))


def validate_work_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "http_probe":
        urls = _normalize_urls(payload.get("urls") or payload.get("url"), maximum=50)
        return {
            "urls": urls,
            "timeout": max(1.0, min(float(payload.get("timeout") or 10), 60.0)),
            "concurrency": max(1, min(int(payload.get("concurrency") or 20), 50)),
            "verify_tls": bool(payload.get("verify_tls", False)),
        }
    if kind == "browser_probe":
        include_html = bool(payload.get("include_html", False))
        urls = _normalize_urls(
            payload.get("urls") or payload.get("url"),
            maximum=1 if include_html else 10,
        )
        return {
            "urls": urls,
            "timeout": max(5.0, min(float(payload.get("timeout") or 30), 120.0)),
            "include_html": include_html,
            "ignore_https_errors": bool(payload.get("ignore_https_errors", True)),
        }
    raise ValueError(f"暂不支持的工作类型: {kind}")


def _result_number(value: Any, *, integer: bool, minimum: float, maximum: float) -> int | float:
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError):
        parsed = minimum
    bounded = max(minimum, min(parsed, maximum))
    return int(bounded) if integer else round(float(bounded), 3)


def _result_url(value: Any, fallback: str = "") -> str:
    candidate = str(value or fallback).strip()[:4096]
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return fallback
    return candidate if parsed.scheme in {"http", "https"} and parsed.hostname else fallback


def validate_work_result(
    kind: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Reject result injection and keep the remote/local probe contract stable."""

    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, dict):
        raise ValueError("工作结果缺少 items 对象")
    expected_urls = [str(value) for value in payload.get("urls") or []]
    if set(items) != set(expected_urls):
        raise ValueError("工作结果 URL 集合与工作参数不一致")
    normalized: dict[str, dict[str, Any]] = {}
    for requested_url in expected_urls:
        source = items.get(requested_url)
        if not isinstance(source, dict):
            raise ValueError(f"工作结果条目格式无效: {requested_url}")
        status_code = source.get("status_code")
        if status_code is not None:
            status_code = _result_number(
                status_code,
                integer=True,
                minimum=0,
                maximum=999,
            )
        common: dict[str, Any] = {
            "url": _result_url(source.get("url"), requested_url),
            "is_alive": bool(source.get("is_alive", False)),
            "status_code": status_code,
            "title": str(source.get("title") or "")[:500],
            "content_length": _result_number(
                source.get("content_length"),
                integer=True,
                minimum=0,
                maximum=1_000_000_000_000,
            ),
            "response_time": _result_number(
                source.get("response_time"),
                integer=False,
                minimum=0,
                maximum=10_000,
            ),
            "error": str(source.get("error") or "")[:1000] or None,
        }
        if kind == "http_probe":
            selected_url = _result_url(source.get("selected_url"), common["url"])
            common.update(
                {
                    "requested_url": requested_url,
                    "selected_url": selected_url,
                    "transport_fallback_used": bool(
                        source.get("transport_fallback_used", selected_url != requested_url)
                    ),
                    "is_content_accessible": bool(source.get("is_content_accessible", False)),
                    "probe_attempts": _result_number(
                        source.get("probe_attempts"), integer=True, minimum=1, maximum=5
                    ),
                    "probe_recovered": bool(source.get("probe_recovered", False)),
                }
            )
        elif kind == "browser_probe":
            common["final_url"] = _result_url(source.get("final_url"), "")
            if payload.get("include_html") and isinstance(source.get("html"), str):
                common["html"] = source["html"][: 512 * 1024]
        else:
            raise ValueError(f"暂不支持的工作类型: {kind}")
        normalized[requested_url] = common
    return {"items": normalized}


def bounded_json(value: Any, *, max_bytes: int, label: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是可序列化 JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label}超过 {max_bytes // 1024} KiB 限制")
    return encoded


def normalize_artifacts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in values[:50]:
        object_id = str(item.get("storage_object_id") or item.get("object_id") or "").strip()
        if not object_id:
            raise ValueError("产物缺少 storage_object_id")
        result.append(
            {
                "storage_object_id": object_id[:128],
                "sha256": str(item.get("sha256") or "")[:128],
                "size": max(0, int(item.get("size") or 0)),
                "content_type": str(item.get("content_type") or "application/octet-stream")[:200],
            }
        )
    return result


def validate_proxy_endpoint(value: str, *, dns_mode: str) -> str:
    endpoint = str(value or "").strip()
    if "://" not in endpoint:
        endpoint = f"socks5://{endpoint}"
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("SOCKS5 endpoint 格式无效") from exc
    if parsed.scheme not in {"socks5", "socks5h"} or not parsed.hostname or not port:
        raise ValueError("SOCKS5 endpoint 必须包含主机和端口")
    scheme = "socks5h" if dns_mode == "remote" else "socks5"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"
