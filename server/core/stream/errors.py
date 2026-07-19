"""Control-flow errors shared by stream stages and runtime adapters."""
from __future__ import annotations


class PipelineAbortError(RuntimeError):
    """Stop the current pipeline without converting the active item to a DLQ row."""
