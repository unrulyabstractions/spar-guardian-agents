"""Download MAST's taxonomy definitions and examples into tools/failures/mast_taxonomy/ and check their sha256.

    uv run --project tools python -m tools.failures.fetch_mast_taxonomy

The MAST repository has no licence and weighs 1.9 GB, so the two text files are fetched at a pinned commit instead of
vendored. The command exits 0 when both files match their hashes, 1 on a download failure or a mismatch.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MAST_COMMIT = "a70542e541b2104ef8fcd785778179e173fb8d70"
RAW_BASE = f"https://raw.githubusercontent.com/multi-agent-systems-failure-taxonomy/MAST/{MAST_COMMIT}"
TAXONOMY_DIR = Path(__file__).resolve().parent / "mast_taxonomy"
TAXONOMY_FILES = {
    "definitions.txt": "bfefa4f2c788fa1658f6879afa8a2b4577a027f224ce3e3b6ba0927dfd82f9ce",
    "examples.txt": "3cf84f024eccecbb1f51deddae889bd8d14d55b539002665210555f00fe60964",
}
FETCH_COMMAND = "uv run --project tools python -m tools.failures.fetch_mast_taxonomy"
TIMEOUT_SECONDS = 60


def sha256_of(data: bytes) -> str:
    """The hex sha256 of some bytes."""
    return hashlib.sha256(data).hexdigest()


def taxonomy_present(directory: Path = TAXONOMY_DIR) -> bool:
    """True when both files are on disk with their pinned hashes."""
    for name, digest in TAXONOMY_FILES.items():
        path = directory / name
        if not path.is_file() or sha256_of(path.read_bytes()) != digest:
            return False
    return True


def fetch_taxonomy(directory: Path = TAXONOMY_DIR) -> int:
    """Download both files at the pinned commit and verify them; returns 0 or 1."""
    directory.mkdir(parents=True, exist_ok=True)
    status = 0
    for name, digest in TAXONOMY_FILES.items():
        url = f"{RAW_BASE}/taxonomy_definitions_examples/{name}"
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
                data = response.read()
        except OSError as error:
            print(f"fetch_taxonomy: {url}: {error}", file=sys.stderr)
            status = 1
            continue
        actual = sha256_of(data)
        if actual != digest:
            print(f"fetch_taxonomy: {name}: sha256 {actual} is not the pinned {digest}; not written", file=sys.stderr)
            status = 1
            continue
        (directory / name).write_bytes(data)
        print(f"fetch_taxonomy: {name}: {len(data)} bytes, sha256 ok, written to {directory / name}")
    return status


if __name__ == "__main__":
    sys.exit(fetch_taxonomy())
