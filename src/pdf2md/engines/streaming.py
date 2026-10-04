"""Stream an engine subprocess's output so a long run reports progress and a silent stage still beats."""

from __future__ import annotations

import queue
import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable

from pdf2md.logging import Progress

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
Counter = tuple[str, int, int, str]  # label, completed, total, unit


def capture_output(
    process: subprocess.Popen[str],
    progress: Progress,
    parse: Callable[[str], Counter | None],
    *,
    still_working: str,
    heartbeat_seconds: float,
    debug: Callable[[str], None] = lambda _message: None,
) -> deque[str]:
    """Read the process's merged output line by line, report the counters `parse` finds, and heartbeat when silent.

    Returns the last 50 lines for an error message. Carriage-return updates (a progress bar redrawing itself) are
    split into separate messages.
    """
    tail: deque[str] = deque(maxlen=50)
    if process.stdout is None:
        return tail

    pending: queue.Queue[str | None] = queue.Queue()

    def read_stdout() -> None:
        try:
            for line in process.stdout:
                pending.put(line)
        finally:
            pending.put(None)

    threading.Thread(target=read_stdout, daemon=True).start()
    while True:
        try:
            line = pending.get(timeout=heartbeat_seconds)
        except queue.Empty:
            progress.stage(still_working)
            continue
        if line is None:
            return tail
        for message in filter(None, re.split(r"[\r\n]+", line)):
            message = message.strip()
            if not message:
                continue
            tail.append(message)
            debug(message)
            counter = parse(message)
            if counter is not None:
                label, completed, total, unit = counter
                progress.count(label, completed, total, unit=unit)
