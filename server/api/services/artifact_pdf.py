"""PDF artifact adapter using the same Word layout and shared Office runtime."""
from __future__ import annotations

from typing import Any


def generate_pdf(*, title: str, content: str) -> dict[str, Any]:
    from api.services.artifact_word import generate_docx
    from core.office import convert_office_to_pdf

    word = generate_docx(title=title, content=content)
    data = convert_office_to_pdf(word["data"], suffix=".docx", timeout_seconds=90)
    return {
        **word,
        "kind": "pdf",
        "filename": word["filename"].removesuffix(".docx") + ".pdf",
        "data": data,
        "size": len(data),
        "content_type": "application/pdf",
    }
