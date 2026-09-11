"""Office conversion boundaries: isolation, bounded failure and resource cleanup."""
from pathlib import Path
import subprocess

import pytest

from core import office


def test_office_conversion_uses_private_temporary_directories(monkeypatch):
    directories = []
    monkeypatch.setattr(office.shutil, "which", lambda name: "/usr/bin/soffice")

    def convert(command, *, timeout_seconds):
        directory = Path(command[command.index("--outdir") + 1])
        directories.append(directory)
        assert (directory / "source.docx").read_bytes() == b"word-input"
        assert f"-env:UserInstallation={(directory / 'profile').as_uri()}" in command
        assert timeout_seconds == 90
        (directory / "source.pdf").write_bytes(b"%PDF-1.7\nconverted")

    monkeypatch.setattr(office, "_run_conversion", convert)
    for _ in range(2):
        assert office.convert_office_to_pdf(b"word-input", suffix=".docx", timeout_seconds=90).startswith(b"%PDF-")
    assert directories[0] != directories[1]
    assert not any(directory.exists() for directory in directories)


def test_office_failure_cleans_up_and_releases_capacity(monkeypatch):
    monkeypatch.setattr(office.shutil, "which", lambda name: "/usr/bin/soffice")
    directories = []

    def fail(command, **kwargs):
        directories.append(Path(command[-1]).parent)
        raise office.OfficeConversionError("conversion failed")

    monkeypatch.setattr(office, "_run_conversion", fail)
    for _ in range(3):
        with pytest.raises(office.OfficeConversionError, match="conversion failed"):
            office.convert_office_to_pdf(b"input", suffix="docx")
    assert not any(path.exists() for path in directories)


def test_office_rejects_missing_converter_and_invalid_output(monkeypatch):
    monkeypatch.setattr(office.shutil, "which", lambda name: None)
    with pytest.raises(office.OfficeConverterUnavailable, match="LibreOffice"):
        office.convert_office_to_pdf(b"input", suffix=".docx")
    monkeypatch.setattr(office.shutil, "which", lambda name: "/usr/bin/soffice")
    with pytest.raises(ValueError, match="扩展名"):
        office.convert_office_to_pdf(b"input", suffix="../../secret")

    def invalid(command, **kwargs):
        (Path(command[-1]).parent / "source.pdf").write_bytes(b"not a pdf")

    monkeypatch.setattr(office, "_run_conversion", invalid)
    with pytest.raises(office.OfficeConversionError, match="有效的 PDF"):
        office.convert_office_to_pdf(b"input", suffix=".docx")


def test_office_timeout_terminates_process_group(monkeypatch):
    calls = []

    class Process:
        pid = 987654
        returncode = -9

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            if timeout:
                raise subprocess.TimeoutExpired("soffice", timeout)
            return b"", b""

    monkeypatch.setattr(office.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(office.os, "killpg", lambda pid, sig: calls.append(("killpg", pid)))
    with pytest.raises(office.OfficeConversionError, match="超过 1 秒"):
        office._run_conversion(["soffice"], timeout_seconds=1)
    assert calls == [("communicate", 1), ("killpg", 987654), ("communicate", None)]


def test_source_document_parsing_reuses_office_runtime(monkeypatch):
    from api.services.source_documents import resources

    calls = []

    def convert(data, *, suffix, **kwargs):
        calls.append((data, suffix))
        return b"%PDF-1.7\nconverted"

    monkeypatch.setattr(office, "convert_office_to_pdf", convert)
    monkeypatch.setattr(resources, "_extract_pdf_text", lambda data, limit: ("已识别正文", ""))
    assert resources._extract_office_via_pdf(b"input", suffix=".docx", limit=100) == ("已识别正文", "")
    assert calls == [(b"input", ".docx")]


def test_source_document_missing_runtime_keeps_archive_warning(monkeypatch):
    from api.services.source_documents import resources

    def unavailable(*args, **kwargs):
        raise office.OfficeConverterUnavailable("运行环境缺少 LibreOffice")

    monkeypatch.setattr(office, "convert_office_to_pdf", unavailable)
    text, warning = resources._extract_office_via_pdf(b"input", suffix=".docx", limit=100)
    assert text == ""
    assert "已归档但未 OCR" in warning
