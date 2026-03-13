"""Lightweight wall-clock timing for pipeline stages.

Usage::

    with stage_timer("scan/hashing", items_label="files") as t:
        results = process_files(files)
        t.items = len(results)

    print(t.format())  # "45.23s  (110 files/s)"

For deep profiling (function-level), use external tools:
  - py-spy record -o profile.svg -- uv run photoapp scan ./photos
  - python -m cProfile -o out.prof -m photoapp scan ./photos && snakeviz out.prof
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator


@dataclass
class StageResult:
    """Timing result for a single pipeline stage."""

    stage: str
    duration_s: float
    items: int
    items_label: str

    @property
    def rate(self) -> float | None:
        """Items per second, or None if not applicable."""
        if self.items > 0 and self.duration_s > 0:
            return self.items / self.duration_s
        return None

    def format(self) -> str:
        """Human-readable timing string, e.g. '45.23s  (110 files/s)'."""
        s = f"{self.duration_s:.2f}s"
        if self.rate is not None:
            s += f"  ({self.rate:.0f} {self.items_label}/s)"
        return s


@contextmanager
def stage_timer(stage: str, items_label: str = "items") -> Generator[StageResult, None, None]:
    """Context manager for timing a named pipeline stage.

    The yielded StageResult is mutable — update ``result.items`` inside the
    block after processing completes. ``duration_s`` is set automatically on exit.

    Args:
        stage: Dot-path stage name, e.g. ``"scan/hashing"``.
        items_label: Unit name shown in the rate string, e.g. ``"files"``.
    """
    result = StageResult(stage=stage, duration_s=0.0, items=0, items_label=items_label)
    t0 = time.perf_counter()
    try:
        yield result
    finally:
        result.duration_s = time.perf_counter() - t0
