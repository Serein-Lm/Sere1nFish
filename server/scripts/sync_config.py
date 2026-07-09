#!/usr/bin/env python3
"""Deprecated config sync entrypoint.

Runtime configuration is edited in the frontend and encrypted in MongoDB.
This script is intentionally kept as a clear failure point for old runbooks.
"""

from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "旧配置文件同步脚本已下线；请在前端配置页写入 MongoDB 加密配置。"
    )


if __name__ == "__main__":
    main()
