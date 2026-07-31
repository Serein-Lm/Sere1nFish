"""Bounded subprocess lifecycle helpers for the media runtime."""

from __future__ import annotations

import asyncio


async def terminate_subprocess(
    process: asyncio.subprocess.Process | None,
    *,
    graceful_timeout: float = 2.0,
    kill_timeout: float = 1.0,
) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=graceful_timeout)
        return
    except TimeoutError:
        pass
    try:
        process.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=kill_timeout)
    except TimeoutError:
        # A subprocess with an unread PIPE can remain blocked in asyncio's
        # transport teardown even after SIGKILL. Closing the transport is the
        # final bounded fallback and lets the event loop reap it asynchronously.
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()
