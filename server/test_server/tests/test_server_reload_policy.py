from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize("reload", [False, True])
def test_boot_reload_is_explicit_and_scripts_excluded(monkeypatch, reload):
    from api.config import Settings
    import api.config
    import uvicorn
    captured = {}
    settings = Settings(_env_file=None, SERVER_RELOAD=reload, SERVER_WATCHDOG_ENABLED=False)
    monkeypatch.setattr(api.config, "get_settings", lambda: settings)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs))
    runpy.run_path(str(Path(__file__).parents[2] / "run.py"), run_name="__main__")
    assert captured["reload"] is reload
    if reload:
        assert str(Path(__file__).parents[2] / "scripts") in captured["reload_excludes"]
    else:
        assert "reload_dirs" not in captured


def test_production_reload_defaults_off(monkeypatch):
    from api.config import Settings
    monkeypatch.delenv("SERVER_RELOAD", raising=False)
    assert not Settings(_env_file=None).SERVER_RELOAD
