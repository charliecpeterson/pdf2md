"""Logging setup. The library attaches only a NullHandler; the CLI is the single
place that installs a real handler."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
import logging
import threading
import time

_ROOT = "pdf2md"

logging.getLogger(_ROOT).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_ROOT}.{name}")


class DuplicateWarningFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.counts: Counter[tuple[str, int, str]] = Counter()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING:
            return True
        key = (record.name, record.levelno, record.getMessage())
        self.counts[key] += 1
        return self.counts[key] == 1

    @property
    def repeat_count(self) -> int:
        return sum(count - 1 for count in self.counts.values())


@contextmanager
def collapse_repeated_warnings(
    logger_names: tuple[str, ...], *, report_to: logging.Logger, stage: str
) -> Iterator[DuplicateWarningFilter]:
    """Keep the first copy of each warning and report exact repeat counts at exit."""
    duplicate_filter = DuplicateWarningFilter()
    sources = [logging.getLogger(name) for name in logger_names]
    for source in sources:
        source.addFilter(duplicate_filter)
    try:
        yield duplicate_filter
    finally:
        for source in sources:
            source.removeFilter(duplicate_filter)
        for (name, _level, message), count in duplicate_filter.counts.items():
            if count > 1:
                report_to.warning(
                    "%s: suppressed %d repeated warning(s) from %s: %s",
                    stage,
                    count - 1,
                    name,
                    message,
                )


class Progress:
    """Emit throttled, line-oriented progress that remains readable when redirected."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.started_at = time.monotonic()
        self._counters: dict[str, tuple[float, int, float, int]] = {}

    def stage(self, message: str, *args: object) -> None:
        self.logger.info("progress %s | " + message, _duration(self.elapsed), *args)

    @contextmanager
    def heartbeat(
        self, message: str, *, interval_seconds: float = 60.0
    ) -> Iterator[None]:
        """Report elapsed time while a blocking stage exposes no useful counters."""
        stopped = threading.Event()

        def report() -> None:
            while not stopped.wait(interval_seconds):
                self.stage(message)

        thread = threading.Thread(target=report, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join()

    def count(
        self,
        label: str,
        completed: int,
        total: int,
        *,
        unit: str,
        detail: str | None = None,
        force: bool = False,
    ) -> None:
        if total <= 0 or completed < 0:
            return
        completed = min(completed, total)
        now = time.monotonic()
        state = self._counters.get(label)
        if state is None or completed < state[1] or total != state[3]:
            counter_started, last_completed, last_reported = now, -1, 0.0
        else:
            counter_started, last_completed, last_reported, _ = state
        percent_step = max(1, total // 20)
        should_report = (
            force
            or completed == total
            or last_completed < 0
            or completed - last_completed >= percent_step
            or now - last_reported >= 30.0
        )
        if not should_report or completed <= last_completed:
            return

        self._counters[label] = (counter_started, completed, now, total)
        remaining = total - completed
        percent = round(completed / total * 100)
        suffix = f", {detail}" if detail else ""
        eta = _eta(now - counter_started, completed, remaining)
        self.logger.info(
            "progress %s | %s: %d/%d %s (%d%%, %d remaining%s)%s",
            _duration(now - self.started_at),
            label,
            completed,
            total,
            unit,
            percent,
            remaining,
            eta,
            suffix,
        )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at


def _eta(elapsed: float, completed: int, remaining: int) -> str:
    if completed <= 0 or remaining <= 0 or elapsed < 1.0:
        return ""
    return f", ETA {_duration(elapsed / completed * remaining)}"


def _duration(seconds: float) -> str:
    rounded = max(0, round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def configure_cli_logging(verbose: bool = False) -> None:
    """Install a stderr handler on the pdf2md logger. Called once, from the CLI."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger(_ROOT)
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.NullHandler)]
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
