import json
import logging
import time
from datetime import datetime, timedelta, timezone

from core.log_rotation import DailySizeRotatingFileHandler
from core.log_safety import RuntimeLogFormatter, process_identity, redact_log_data, redact_log_text
from core.observability.file_tail import load_recent_jsonl


def test_plain_and_structured_logs_redact_credentials_without_losing_context(monkeypatch):
    monkeypatch.setenv("SEREINFISH_RUN_KIND", "test")
    source = {"task_id": "task-a", "tokens": 42, "nested": {"api_key": "secret-value"},
              "url": "https://example.com/?token=secret-token&record_id=r1"}
    safe = redact_log_data(source)
    assert safe["task_id"] == "task-a" and safe["tokens"] == 42
    assert safe["nested"]["api_key"] == "[REDACTED_SECRET]"
    assert "secret-token" not in safe["url"] and "record_id=r1" in safe["url"]
    assert source["nested"]["api_key"] == "secret-value"
    for message in ["failed Bearer auth-value", "Cookie: sid=private", "Authorization: private",
                    "redis://user:private@host", "api_key=private", "x-oss-signature=private"]:
        assert "private" not in redact_log_text(message)
        assert "auth-value" not in redact_log_text(message)
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "task=t failed token=%s", ("secret",), None)
    line = RuntimeLogFormatter("%(run_kind)s %(process_id)s %(runtime_id)s %(message)s").format(record)
    assert line.startswith("test ") and "task=t failed" in line and "secret" not in line
    assert process_identity()["runtime_id"] == process_identity()["runtime_id"]


def test_application_log_rolls_on_day_and_size_with_bounded_backups(tmp_path):
    handler = DailySizeRotatingFileHandler(tmp_path / "application.log", maxBytes=100, backupCount=2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    first = logging.LogRecord("test", logging.ERROR, __file__, 1, "first failure", (), None)
    handler.emit(first)
    next_day = logging.LogRecord("test", logging.ERROR, __file__, 1, "next day failure", (), None)
    next_day.created = first.created + 86400
    handler.emit(next_day)
    assert "first failure" in (tmp_path / "application.log.1").read_text()
    for _ in range(6):
        row = logging.LogRecord("test", logging.ERROR, __file__, 1, "X" * 80, (), None)
        row.created = next_day.created
        handler.emit(row)
    handler.close()
    assert len(list(tmp_path.iterdir())) == 3


def test_recent_restore_reads_tail_with_utf8_and_skips_partial_rows(tmp_path):
    old = tmp_path / "2026-01-01.jsonl"
    old.write_bytes(b"x" * 200000 + b"\n" + json.dumps({"i": 1}).encode() + b"\n")
    new = tmp_path / "2026-01-02.jsonl"
    new.write_text('\n'.join(json.dumps({"i": i, "text": "中文"}, ensure_ascii=False) for i in range(2, 7)) + '\n{incomplete')
    rows = load_recent_jsonl([old, new], limit=3, max_bytes=65536)
    assert [row["i"] for row in rows] == [4, 5, 6]
    assert all(row["text"] == "中文" for row in rows)
    rows = load_recent_jsonl([old, new], limit=100, max_bytes=300)
    assert [row["i"] for row in rows] == [1, 2, 3, 4, 5, 6]
    assert load_recent_jsonl([old], limit=100, max_bytes=4) == []


def test_observation_sink_identity_retention_and_business_failure_survive(tmp_path, monkeypatch):
    from core.observability.logs import LocalJsonlLogStore, ObservabilityLogger
    monkeypatch.setenv("OBS_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("OBS_LOG_RETENTION_DAYS", "30")
    old_day = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d")
    old = tmp_path / (old_day + ".jsonl")
    old.write_text('{"old":true}\n')
    preserved = tmp_path / "manual-evidence.jsonl"
    preserved.write_text('{"evidence":true}\n')
    logger = ObservabilityLogger()
    logger.log("storage failed token=secret", task_id="task-a", level="error", data={"password": "private"})
    rows = LocalJsonlLogStore(tmp_path).load_recent(10)
    row = next(r for r in rows if r.get("task_id") == "task-a")
    assert row["level"] == "error" and "storage failed" in row["message"]
    assert row["run_kind"] == "test" and row["process_id"] > 0 and row["runtime_id"]
    assert row["data"]["password"] == "[REDACTED_SECRET]"
    assert not old.exists() and preserved.exists()


def test_noisy_library_debug_suppressed_but_warnings_enabled():
    from core.logger import get_logger
    for name in ["PIL.PngImagePlugin", "websockets.client", "watchdog.observers", "httpcore2.http11"]:
        logger = get_logger(name)
        assert not logger.isEnabledFor(logging.DEBUG)
        assert logger.isEnabledFor(logging.WARNING)
