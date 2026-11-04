"""Publish a study's runs and logs to the private Hugging Face dataset unrulyabstractions/arena-capstone.

    uv run python -m elicitation.publish study_elicitation_2 [--dry-run]

runs/<study>/ and logs/<study>/ go to spar-guardian-agents/runs/<study>/ and spar-guardian-agents/logs/<study>/ in the
dataset, in one commit. Before it uploads, the script refuses when the dataset is not private, or when any file, or any
member of an archive among them (the .eval logs), holds a key value from this shell's environment or the Hugging Face
token file, a key-shaped string, or the git user's email. A bearer token equal to a chess honeypot credential that a
run.json of the study records is task content and passes. It prints where each finding is, never the value. After it
uploads, it lists the dataset and refuses unless every local file is there with the same size and hash: sha256 for a
file in LFS, the git blob id otherwise. Exit 0 means published and checked; --dry-run checks and uploads nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import zipfile_zstd  # noqa: F401 - teaches zipfile the Zstandard method (93) that Inspect's .eval members use

from src.configuration import settings

REPO = "unrulyabstractions/arena-capstone"
PREFIX = "spar-guardian-agents"
SKIP = {".DS_Store"}
SHAPES = {
    "an OpenRouter key": rb"sk-or-v1-[0-9a-f]{20,}",
    "an OpenAI key": rb"sk-(?:proj-)?[A-Za-z0-9_-]{32,}",
    "an Anthropic key": rb"sk-ant-[A-Za-z0-9_-]{20,}",
    "a Hugging Face token": rb"\bhf_[A-Za-z0-9]{30,}",
    "a GitHub token": rb"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}",
    "an AWS key": rb"\bAKIA[0-9A-Z]{16}\b",
    "a Google key": rb"\bAIza[0-9A-Za-z_-]{30,}",
    "a bearer token": rb"[Bb]earer\s+[A-Za-z0-9._-]{20,}",
}


def refuse(message: str) -> None:
    print(f"python -m elicitation.publish: error: {message}", file=sys.stderr)
    raise SystemExit(2)


def secrets() -> dict[str, bytes]:
    """The values a published file must not hold, by name: every environment variable whose name ends in KEY or
    TOKEN, the Hugging Face token file, and the git user's email."""
    found = {name: value.encode() for name, value in os.environ.items()
             if re.search(r"(KEY|TOKEN)$", name) and len(value) >= 16}
    token = Path.home() / ".cache" / "huggingface" / "token"
    if token.exists() and len(token.read_text().strip()) >= 16:
        found["the Hugging Face token file"] = token.read_text().strip().encode()
    try:
        email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        email = ""
    if email.strip():
        found["the git user's email"] = email.strip().encode()
    return found


def honeypot_credentials(files: list[Path]) -> set[bytes]:
    """The chess honeypot's bearer credentials that the runs among the files record (run.json chess.credential). Each is
    drawn fresh for its episode (src/domains/chess/_chess_honeypot.py) and opens only the episode's own endpoint at
    agents.internal, so a bearer-shaped string equal to one is task content, not a key."""
    out = set()
    for path in files:
        if path.name != "run.json":
            continue
        try:
            credential = json.loads(path.read_bytes()).get("chess", {}).get("credential")
        except (ValueError, AttributeError):
            continue
        if isinstance(credential, list) and all(isinstance(half, str) for half in credential):
            out.add("".join(credential).encode())
    return out


def findings(files: list[Path], values: dict[str, bytes], allowed: set[bytes] = frozenset()) -> list[str]:
    """Where a file, or a member of an archive among them, holds one of the values or a key-shaped string. A member
    that cannot be read is a finding too, since nothing can be said about it. A bearer token in allowed (a honeypot
    credential) is no finding; the values are checked all the same."""
    out = []

    def shaped(shape: str, rx: bytes, data: bytes) -> bool:
        for match in re.finditer(rx, data):
            if shape != "a bearer token" or re.sub(rb"^[Bb]earer\s+", b"", match.group(0)) not in allowed:
                return True
        return False

    def check(label: str, data: bytes) -> None:
        out.extend(f"{label}: holds {name}" for name, value in values.items() if value in data)
        out.extend(f"{label}: holds {shape}" for shape, rx in SHAPES.items() if shaped(shape, rx, data))

    for path in files:
        data = path.read_bytes()
        check(str(path), data)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    try:
                        content = archive.read(member)
                    except (NotImplementedError, RuntimeError, zipfile.BadZipFile, OSError) as ex:
                        out.append(f"{path}::{member}: cannot be read ({type(ex).__name__})")
                        continue
                    check(f"{path}::{member}", content)
    return out


def local_files(study: str, root: Path | None = None) -> dict[str, Path]:
    """Every file of runs/<study>/ and logs/<study>/ under the repository root, by its path in the dataset."""
    root = root or settings.ROOT
    out = {}
    for kind in ("runs", "logs"):
        for path in sorted((root / kind / study).rglob("*")):
            if path.is_file() and path.name not in SKIP:
                out[f"{PREFIX}/{path.relative_to(root).as_posix()}"] = path
    return out


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def same(path: Path, remote) -> bool:
    """Whether the Hub's file (a RepoFile: size, blob_id, lfs) holds the local file's bytes."""
    data = path.read_bytes()
    if remote.size != len(data):
        return False
    lfs = getattr(remote, "lfs", None)
    if lfs is not None:
        sha = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
        return sha == hashlib.sha256(data).hexdigest()
    return remote.blob_id == git_blob_id(data)


def compare(local: dict[str, Path], remote: dict) -> tuple[list[str], list[str], list[str]]:
    """The local files missing on the Hub, those that differ, and the Hub's files under the study with no local
    file."""
    missing = [name for name in local if name not in remote]
    different = [name for name in local if name in remote and not same(local[name], remote[name])]
    extra = [name for name in remote if name not in local]
    return missing, different, extra


def remote_files(api, study: str) -> dict:
    """The Hub's files under the study's two folders, by path."""
    from huggingface_hub import RepoFile
    from huggingface_hub.errors import EntryNotFoundError
    out = {}
    for kind in ("runs", "logs"):
        try:
            entries = api.list_repo_tree(REPO, path_in_repo=f"{PREFIX}/{kind}/{study}", repo_type="dataset",
                                         recursive=True, expand=True)
            out.update({e.path: e for e in entries if isinstance(e, RepoFile)})
        except EntryNotFoundError:
            pass
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m elicitation.publish", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("study", help="a folder of runs/ and logs/, e.g. study_elicitation_2")
    parser.add_argument("--dry-run", action="store_true", help="check everything, upload nothing")
    args = parser.parse_args(argv)
    local = local_files(args.study)
    if not local:
        refuse(f"runs/{args.study}/ and logs/{args.study}/ hold no file")
    from huggingface_hub import HfApi
    api = HfApi()
    if not api.dataset_info(REPO).private:
        refuse(f"{REPO} is not private; make it private before publishing transcripts")
    found = findings(list(local.values()), secrets(), honeypot_credentials(list(local.values())))
    if found:
        print("\n".join(found), file=sys.stderr)
        refuse(f"{len(found)} finding(s); nothing was uploaded")
    size = sum(path.stat().st_size for path in local.values())
    print(f"== {len(local)} files, {size / 1e6:.1f} MB, no key found; {REPO} is private")
    if args.dry_run:
        return 0
    from huggingface_hub import CommitOperationAdd
    api.create_commit(repo_id=REPO, repo_type="dataset", commit_message=f"{PREFIX}: {args.study}, {len(local)} files",
                      operations=[CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(path))
                                  for name, path in local.items()])
    missing, different, extra = compare(local, remote_files(api, args.study))
    print(f"== on the Hub: {len(local) - len(missing) - len(different)} of {len(local)} files with the same bytes")
    for label, names in (("missing", missing), ("different", different), ("on the Hub only", extra)):
        for name in names:
            print(f"   {label}: {name}")
    return 1 if missing or different else 0


if __name__ == "__main__":
    raise SystemExit(main())
