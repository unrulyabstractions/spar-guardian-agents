"""Start and stop the results browser as a background process.

    start(8765, open_browser=True)
    stop(8765)

`python -m src ui` calls these. The process they start is `python -m src.ui` (_server.py).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from src.configuration import settings

MODULE = "src.ui"
WAIT_TRIES = 100
WAIT_STEP_S = 0.1


def _python() -> str:
    venv_python = settings.ROOT / ".venv" / "bin" / "python"
    return str(venv_python) if os.access(venv_python, os.X_OK) else sys.executable


def listener_pid(port: int) -> int | None:
    result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    lines = result.stdout.split()
    return int(lines[0]) if lines else None


def is_browser(pid: int | None) -> bool:
    if pid is None:
        return False
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return MODULE in result.stdout


def _read_pid_file(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def server_answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "api/runs", timeout=30):
            return True
    except OSError:
        return False


def _files(port: int) -> tuple[Path, Path]:
    settings.LOGS.mkdir(parents=True, exist_ok=True)
    return settings.LOGS / f"ui-{port}.pid", settings.LOGS / f"ui-{port}.log"


def start(port: int, *, open_browser: bool = True) -> int:
    """Start the browser in the background unless something already listens on the port; print its URL."""
    pid_file, log_file = _files(port)
    url = f"http://127.0.0.1:{port}/"
    if listener_pid(port) is None:
        with open(log_file, "ab") as log:
            process = subprocess.Popen([_python(), "-m", MODULE, "--port", str(port)], cwd=settings.ROOT,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        pid_file.write_text(f"{process.pid}\n")
        print(f"started results browser on port {port} (pid {process.pid}, log logs/{log_file.name})")
        for _ in range(WAIT_TRIES):
            if server_answers(url):
                break
            time.sleep(WAIT_STEP_S)
        if not server_answers(url):
            print(f"server did not answer on {url}; see logs/{log_file.name}", file=sys.stderr)
            return 1
    else:
        print(f"results browser already listening on {url}")
    print(url)
    if open_browser:
        webbrowser.open(url)
    return 0


def stop(port: int) -> int:
    """Stop the browser on the port, found by its pid file or its listener; never kill another program."""
    pid_file, _ = _files(port)
    pid = _read_pid_file(pid_file)
    if not is_browser(pid):
        pid = listener_pid(port)
    if is_browser(pid):
        os.kill(pid, signal.SIGTERM)
        print(f"stopped results browser on port {port} (pid {pid})")
    elif pid is not None:
        print(f"pid {pid} listens on port {port} but is not the results browser; not killed", file=sys.stderr)
        return 1
    else:
        print(f"nothing listens on port {port}")
    pid_file.unlink(missing_ok=True)
    return 0
