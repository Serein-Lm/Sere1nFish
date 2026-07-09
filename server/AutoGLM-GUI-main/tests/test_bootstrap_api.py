"""Bootstrap API tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from AutoGLM_GUI.api import bootstrap as bootstrap_api
from AutoGLM_GUI.config_manager import UnifiedConfigManager, resolve_config_path

pytestmark = pytest.mark.contract


def test_resolve_config_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "custom.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AUTOGLM_CONFIG_PATH", str(cfg))
    assert resolve_config_path() == cfg.resolve()


def test_bootstrap_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:9000/v1",
                "model_name": "test-model",
                "api_key": "secret",
            }
        ),
        encoding="utf-8",
    )
    from AutoGLM_GUI.config_manager import ConfigSource

    manager = UnifiedConfigManager()
    monkeypatch.setattr(manager, "_config_path", config_path)
    manager._file_cache = None
    manager._file_mtime = None
    manager._effective_config = None
    manager._file_layer = manager._file_layer.__class__(source=ConfigSource.FILE)
    manager.load_file_config(force_reload=True)
    monkeypatch.setattr(bootstrap_api, "config_manager", manager)
    monkeypatch.setattr("AutoGLM_GUI.config_manager.config_manager", manager)

    def _init_runtime_config(*, sync_env: bool = True):
        manager.load_env_config()
        manager.load_file_config(force_reload=True)
        return manager.get_effective_config()

    monkeypatch.setattr(bootstrap_api, "init_runtime_config", _init_runtime_config)

    app = FastAPI()
    app.include_router(bootstrap_api.router)
    client = TestClient(app)

    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert body["config_storage"] == "json"
    assert body["config"]["model_name"] == "test-model"
    assert body["endpoints"]["bootstrap"] == "GET /api/bootstrap"
