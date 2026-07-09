"""主项目 bootstrap / 坐标 / 配置加载单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mobile.coordinates import resolve_tap
from Sere1nGraph.graph.config.loader import load_config


def test_load_config_mobile_and_sampling(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "base_url": "http://localhost:9999/v1",
                    "api_key": "test-key",
                    "max_tokens": 4096,
                    "temperature": 0.1,
                    "models": {"default": "m1", "vision": "vl1"},
                },
                "mobile": {
                    "adb_timeout": 45,
                    "video": {
                        "max_size": 1280,
                        "bit_rate": 6_000_000,
                        "max_fps": 30,
                        "downsize_on_error": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    app = load_config(str(cfg_path))
    assert app.runtime.base_url == "http://localhost:9999/v1"
    assert app.runtime.max_tokens == 4096
    assert app.runtime.temperature == 0.1
    assert app.runtime.models.mobile_planner_model == "m1"
    assert app.runtime.models.mobile_executor_model == "vl1"
    assert app.runtime.models.mobile_screen_model == "vl1"
    assert app.mobile.adb_timeout == 45
    assert app.mobile.video.max_size == 1280
    assert app.mobile.video.downsize_on_error is True


def test_load_config_mobile_model_overrides(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "models": {
                        "default": "default-model",
                        "vision": "vision-model",
                        "mobile_planner": "planner-model",
                        "mobile_executor": "executor-model",
                        "mobile_screen": "screen-model",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    app = load_config(str(cfg_path))
    assert app.runtime.models.mobile_planner_model == "planner-model"
    assert app.runtime.models.mobile_executor_model == "executor-model"
    assert app.runtime.models.mobile_screen_model == "screen-model"


def test_resolve_tap_pixel_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.mobile.coordinates.get_display_size",
        lambda device_id, adb_path="adb": (1080, 2400),
    )
    assert resolve_tap(540, 1200, device_id="dev1", coord_space="pixel") == (
        540,
        1200,
    )


def test_resolve_tap_normalized_1000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.mobile.coordinates.get_display_size",
        lambda device_id, adb_path="adb": (1000, 2000),
    )
    px, py = resolve_tap(500, 500, device_id="dev1", coord_space="normalized_1000")
    assert px == 500
    assert py == 1000


def test_bootstrap_router_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from api.routers import bootstrap as bootstrap_api

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "base_url": "http://llm/v1",
                    "api_key": "abcdefghijklmnop",
                    "models": {"vision": "qwen-vl-max"},
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        bootstrap_api,
        "load_config",
        lambda: load_config(str(cfg_path)),
    )
    monkeypatch.setattr(bootstrap_api, "get_config_path", lambda: cfg_path)

    import asyncio

    body = asyncio.get_event_loop().run_until_complete(
        bootstrap_api.frontend_bootstrap()
    )
    assert body["config_storage"] == "json"
    assert body["runtime"]["llm_configured"] is True
    assert body["mobile"]["video_defaults"]["maxSize"] == 1920
    assert "mobile_devices" in body["endpoints"]
    assert body["runtime"]["api_key_masked"].startswith("abcd")
