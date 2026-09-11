"""Bounded Office-to-PDF runtime shared by resource parsing and artifacts."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading


_CONVERSION_SLOTS = threading.BoundedSemaphore(2)
_MAX_PDF_BYTES = 200 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 300


class OfficeConversionError(RuntimeError):
    pass


class OfficeConverterUnavailable(OfficeConversionError):
    pass


def _run_conversion(command: list[str], *, timeout_seconds: int) -> None:
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate()
            raise OfficeConversionError(f"Office 转 PDF 超过 {timeout_seconds} 秒上限") from exc
        if process.returncode:
            detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
            raise OfficeConversionError(f"Office 转 PDF 失败: {detail or f'退出码 {process.returncode}'}"[:2000])


def convert_office_to_pdf(
    data: bytes,
    *,
    suffix: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Convert one in-memory document with an isolated profile and temporary files.

    This synchronous adapter belongs in a tool/executor thread, never directly
    in an async router. A bounded process group and shared capacity limit cover
    both source parsing and document generation.
    """
    converter = shutil.which("soffice") or shutil.which("libreoffice")
    if not converter:
        raise OfficeConverterUnavailable("运行环境缺少 LibreOffice")
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", safe_suffix):
        raise ValueError("Office 文件扩展名无效")
    timeout = max(1, min(int(timeout_seconds), DEFAULT_TIMEOUT_SECONDS))
    if not _CONVERSION_SLOTS.acquire(timeout=min(timeout, 30)):
        raise OfficeConversionError("Office 转 PDF 繁忙，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="sere1nfish-office-") as directory:
            root = Path(directory)
            source = root / f"source{safe_suffix}"
            source.write_bytes(data)
            command = [
                converter,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={(root / 'profile').as_uri()}",
                "--convert-to", "pdf", "--outdir", directory, str(source),
            ]
            _run_conversion(command, timeout_seconds=timeout)
            output = root / "source.pdf"
            if not output.is_file():
                raise OfficeConversionError("Office 转 PDF 失败: 未生成 PDF 文件")
            if output.stat().st_size > _MAX_PDF_BYTES:
                raise OfficeConversionError("Office 转换后的 PDF 超过 200 MiB 安全上限")
            result = output.read_bytes()
            if not result.startswith(b"%PDF-"):
                raise OfficeConversionError("Office 转 PDF 失败: 输出不是有效的 PDF 文件")
            return result
    except OSError as exc:
        raise OfficeConversionError(f"Office 转 PDF 失败: {exc}") from exc
    finally:
        _CONVERSION_SLOTS.release()
