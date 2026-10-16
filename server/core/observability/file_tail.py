"""Bound recent-log restoration by bytes and rows, reading newest files first."""
import json
from pathlib import Path
from typing import Any


def load_recent_jsonl(paths: list[Path], *, limit: int, max_bytes: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    budget = max(0, max_bytes)
    if limit <= 0:
        return rows
    for path in sorted(paths, reverse=True):
        if budget <= 0:
            break
        try:
            with path.open("rb") as stream:
                position = stream.seek(0, 2)
                partial = b""
                while position and budget:
                    size = min(position, budget, 65536)
                    position -= size
                    budget -= size
                    stream.seek(position)
                    lines = (stream.read(size) + partial).split(b"\n")
                    partial = lines.pop(0)
                    if not position:
                        lines.insert(0, partial)
                    for line in reversed(lines):
                        try:
                            value = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if isinstance(value, dict):
                            rows.append(value)
                            if len(rows) >= limit:
                                return list(reversed(rows))
        except FileNotFoundError:
            continue
    return list(reversed(rows))
