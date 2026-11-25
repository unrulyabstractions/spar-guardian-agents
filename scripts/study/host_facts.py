"""The facts of the host a study launches on, the Mac or Linux, picked once from the platform as HOST.

    from scripts.study.host_facts import HOST
    HOST.free_memory_pct()            # free memory in percent
    HOST.memory_gb()                  # the host's memory in GB
    HOST.docker_disk()                # (size, free) in GB of the disk that holds Docker's data
    HOST.process_lines()              # the command line of every process
    HOST.cell_process(line, mock)     # (argv, env) that run one cell's shell line
    HOST.key_problem(variable)        # why a paid launch would find no key in the variable, or None
    HOST.grow_disk(size, grow, free)  # the line that says how Docker's disk grows to `grow` GB

    python -m scripts.study.host_facts   # print this host's facts

Each fact is None when the host cannot say. The Mac reads `memory_pressure`, `sysctl hw.memsize`, `colima ssh --
df -BG /var/lib/docker` (Docker runs in Colima's VM) and `ps -axo command=`, and runs a cell under `zsh -lic`, so the
cell's key comes from ~/.zshrc. Linux reads /proc/meminfo (MemAvailable over MemTotal), `df -BG` on Docker's data root
(`docker info`) and /proc/<pid>/cmdline, and runs a cell under `bash -c` in the launcher's own environment, which must
hold OPENROUTER_API_KEY. A mock launch runs every cell under `bash -c` without any variable named *_API_KEY, on both
hosts, so no key is read. The module uses Python's standard library alone, so it runs on a bare Linux image.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

GB = 2 ** 30
KEY = "OPENROUTER_API_KEY"
"""The key a paid launch on Linux takes from its own environment."""
KEY_MARK = "_API_KEY"
"""A variable whose name holds this is a key (OPENROUTER_API_KEY, OPENROUTER_API_KEY_OWN); a mock launch passes none to
its cells."""
COLIMA_DOCKER_ROOT = "/var/lib/docker"
"""Docker's data root inside Colima's VM."""
PROC = Path("/proc")
"""Linux's process and memory facts."""


def output(argv: list[str]) -> str | None:
    """What a command prints, None when it cannot run or takes over a minute."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def memory_pressure_pct(text: str) -> int | None:
    """The free percentage `memory_pressure` prints."""
    found = re.search(r"free percentage:\s*(\d+)%", text)
    return int(found.group(1)) if found else None


def meminfo_kb(text: str) -> dict[str, int]:
    """The fields of /proc/meminfo in kB."""
    return {m[1]: int(m[2]) for m in re.finditer(r"^(\w+):\s+(\d+) kB$", text, re.MULTILINE)}


def meminfo_pct(text: str) -> int | None:
    """The free memory in percent of /proc/meminfo: MemAvailable over MemTotal, rounded down."""
    fields = meminfo_kb(text)
    if not fields.get("MemTotal") or "MemAvailable" not in fields:
        return None
    return fields["MemAvailable"] * 100 // fields["MemTotal"]


def df_disk(text: str) -> tuple[int, int] | None:
    """(size, free) in GB from the last line of `df -BG <path>`."""
    lines = text.strip().splitlines()
    fields = [field.rstrip("G") for field in lines[-1].split()] if len(lines) > 1 else []
    if len(fields) >= 4 and fields[1].isdigit() and fields[3].isdigit():
        return int(fields[1]), int(fields[3])
    return None


def cmdline_text(raw: bytes) -> str:
    """A process's command line from /proc/<pid>/cmdline, its arguments joined by spaces."""
    return raw.rstrip(b"\0").replace(b"\0", b" ").decode(errors="replace")


def keyless(env: dict[str, str]) -> dict[str, str]:
    """The environment without any key."""
    return {name: value for name, value in env.items() if KEY_MARK not in name}


class Host:
    """What both hosts share: a mock cell runs under bash without any key."""

    name = "the host"

    def keyed_argv(self, line: str) -> list[str]:
        raise NotImplementedError

    def cell_process(self, line: str, mock: bool) -> tuple[list[str], dict[str, str] | None]:
        """The argv and environment that run one cell's shell line; None keeps the launcher's environment."""
        return (["bash", "-c", line], keyless(dict(os.environ))) if mock else (self.keyed_argv(line), None)


class Mac(Host):
    name = "the Mac"

    def free_memory_pct(self) -> int | None:
        text = output(["memory_pressure"])
        return memory_pressure_pct(text) if text else None

    def memory_gb(self) -> float | None:
        text = (output(["sysctl", "-n", "hw.memsize"]) or "").strip()
        return int(text) / GB if text.isdigit() else None

    def docker_disk(self) -> tuple[int, int] | None:
        return df_disk(output(["colima", "ssh", "--", "df", "-BG", COLIMA_DOCKER_ROOT]) or "")

    def process_lines(self) -> list[str]:
        return (output(["ps", "-axo", "command="]) or "").splitlines()

    def keyed_argv(self, line: str) -> list[str]:
        return ["zsh", "-lic", line]

    def key_problem(self, variable: str = KEY) -> str | None:
        return None                                     # `zsh -lic` reads the key from ~/.zshrc

    def grow_disk(self, size: int, grow: int, host_free_gb: float) -> str:
        fits = "fits" if grow - size < host_free_gb else "does not fit"
        return (f"colima stop && colima start --disk {grow}   ({grow - size} GB more, which {fits} in the Mac's free "
                f"disk; colima stop ends every container, so never while runs are live)")


class Linux(Host):
    def free_memory_pct(self) -> int | None:
        return meminfo_pct(read_text(PROC / "meminfo"))

    def memory_gb(self) -> float | None:
        total = meminfo_kb(read_text(PROC / "meminfo")).get("MemTotal")
        return total * 1024 / GB if total else None

    def docker_root(self) -> str | None:
        """Docker's data root, as the daemon reports it."""
        return (output(["docker", "info", "--format", "{{.DockerRootDir}}"]) or "").strip() or None

    def docker_disk(self) -> tuple[int, int] | None:
        root = self.docker_root()
        return df_disk(output(["df", "-BG", root]) or "") if root else None

    def process_lines(self) -> list[str]:
        lines = []
        for path in PROC.glob("[0-9]*/cmdline"):
            try:
                lines.append(cmdline_text(path.read_bytes()))
            except OSError:
                continue                                # the process ended while we read
        return [line for line in lines if line]

    def keyed_argv(self, line: str) -> list[str]:
        return ["bash", "-c", line]

    def key_problem(self, variable: str = KEY) -> str | None:
        return None if os.environ.get(variable) else f"{variable} is not set: a launch on Linux takes the key from " \
            "its own environment (or add --mock, which reads no key)"

    def grow_disk(self, size: int, grow: int, host_free_gb: float) -> str:
        return f"this host's disk of {size} GB does not grow here: launch on a host with {grow} GB or more"


def read_text(path: Path) -> str:
    """A file's text, empty when it cannot be read."""
    try:
        return path.read_text()
    except OSError:
        return ""


HOST: Host = Mac() if sys.platform == "darwin" else Linux()


def main() -> int:
    print(f"host: {type(HOST).__name__}")
    print(f"free memory: {HOST.free_memory_pct()}%")
    print(f"memory: {HOST.memory_gb()} GB")
    print(f"docker disk (size, free) GB: {HOST.docker_disk()}")
    print(f"processes: {len(HOST.process_lines())}")
    for mock in (False, True):
        print(f"a {'mock' if mock else 'paid'} cell runs under: {HOST.cell_process('<line>', mock)[0][:-1]}")
    print(f"key: {HOST.key_problem() or 'ok'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
