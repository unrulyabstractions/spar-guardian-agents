"""Stockfish on the host, where the game and the endpoint run: CheatBench's pinned sf_19 release, installed by our
build under build/chess/stockfish/ and checked against CheatBench's engine signature before first use.

CheatBench's build (tasks/chess/stockfish.py) downloads the release into its own task folder and supports Linux only.
Ours keeps its two Linux assets and digests, adds the release's macOS universal asset by the digest GitHub publishes
for it, and writes into our build, so the submodule stays read only. As in CheatBench, STOCKFISH names another
binary, which must pass the same signature.

    ensure_stockfish()       # the build: download, check and publish the engine when it is missing
    binary = require()       # at setup: the verified engine, or an error naming the build
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from src.domains.chess._chess_cheatbench import STOCKFISH
from src.domains.chess._chess_settings import MAC_ASSET, STOCKFISH_DIR

BUILD_HINT = "uv run python -m src.domains.chess.chess_rows_build"
CACHE = STOCKFISH_DIR / STOCKFISH.VERSION


def asset() -> tuple[str, str]:
    """(the release asset's name, its archive's SHA-256) for this host."""
    system, machine = platform.system(), platform.machine()
    if system == "Darwin":
        return MAC_ASSET
    if system == "Linux" and machine in STOCKFISH.ASSETS:
        return STOCKFISH.ASSETS[machine]
    raise RuntimeError(f"no pinned Stockfish {STOCKFISH.VERSION} for {system} {machine}; set STOCKFISH to a binary")


def engine_path() -> Path:
    """The engine the game and the endpoint start: STOCKFISH, else the build's copy."""
    return Path(os.environ.get("STOCKFISH") or CACHE / "stockfish")


def require() -> Path:
    """The engine, checked against the signature (once per binary and process); an error naming the build when it is
    missing."""
    binary = engine_path()
    if not binary.is_file():
        raise FileNotFoundError(f"{binary} missing: build the chess task first: {BUILD_HINT}")
    STOCKFISH.verify(binary)
    return binary


def ensure_stockfish(log=print) -> Path:
    """Download the pinned release once, check its digest and the engine's signature before publishing it, and reuse
    it offline afterwards (CheatBench's ensure_stockfish, for this host and our build)."""
    if os.environ.get("STOCKFISH"):
        return require()
    name, digest = asset()
    CACHE.mkdir(parents=True, exist_ok=True)
    binary = CACHE / "stockfish"
    with (CACHE / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binary.is_file():
            STOCKFISH.verify(binary)
            return binary
        url = f"https://github.com/official-stockfish/Stockfish/releases/download/{STOCKFISH.VERSION}/{name}.tar.gz"
        log(f"Stockfish: downloading pinned {STOCKFISH.VERSION} from {url}")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=CACHE) as temporary:
            stage = Path(temporary)
            archive = stage / "download.tar.gz"
            checksum = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    checksum.update(chunk)
                    out.write(chunk)
            if checksum.hexdigest() != digest:
                raise RuntimeError(f"{name}.tar.gz: SHA-256 {checksum.hexdigest()} is not the pinned {digest}")
            # named regular members only; never extract archive paths or links
            with tarfile.open(archive, "r:gz") as bundle:
                for member_name, target in ((name, "stockfish"), ("Copying.txt", "Copying.txt")):
                    member = bundle.getmember(f"stockfish/{member_name}")
                    if not member.isfile():
                        raise RuntimeError(f"Stockfish archive member is not a regular file: {member.name}")
                    with bundle.extractfile(member) as source, (stage / target).open("wb") as out:
                        shutil.copyfileobj(source, out)
            candidate = stage / "stockfish"
            candidate.chmod(0o755)
            STOCKFISH.verify(candidate)
            (stage / "Copying.txt").replace(CACHE / "Copying.txt")
            candidate.replace(binary)
    return binary
