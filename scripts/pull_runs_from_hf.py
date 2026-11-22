"""Pull a study's runs and their eval logs from the Hugging Face dataset unrulyabstractions/spar-swarm.

The dataset is public, so no token is needed. Pull one run, some runs or a whole study:

    make hf-pull STUDY=<study> [RUN=<run>] [DRY_RUN=1] [FORCE=1] [DEST=<dir>]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # run as a file, `python scripts/pull_runs_from_hf.py`, as well as with -m
    sys.path.insert(0, str(ROOT))

from huggingface_hub import HfApi, RepoFile, RepoFolder, snapshot_download  # noqa: E402
from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError  # noqa: E402

from elicitation.publish import same  # noqa: E402
from scripts.upload_runs_to_hf import REPO, find_token, junk  # noqa: E402
from src.utils import progress  # noqa: E402

PROG = "python scripts/pull_runs_from_hf.py"
MANIFEST = "apart_controls"
"""The manifest collection: its runs keep their eval logs inside each run, and the whole folder is runs/apart_controls/
here."""
EXACT_PATTERNS = 500
"""Up to this many files, the download asks for each file by name; past it, for each run's folder."""
EPILOG = """\
<study>/runs/<run>/ in the dataset comes to runs/<study>/<run>/, <study>/logs/<run>/ to logs/<study>/<run>/ and
<study>/logs/screen/<run>.log to logs/<study>/screen/<run>.log, the reverse of scripts/upload_runs_to_hf.py. The apart
controls' folder apart_controls/ comes to runs/apart_controls/; a run there is <model>/<task>/<cell>/s<seed>, e.g.
--run ds/binder/cheat/s1. A run comes with its study's INDEX.jsonl (manifest.jsonl for the apart controls) when that
file is not here yet, since the viewer reads each run's valid flag from it. A study's own documents (its README,
figures and tables) have no place under runs/ or logs/ and stay on the Hub. A local file that differs from the Hub's
stops the pull before anything downloads; --force replaces it. Files already here with the Hub's bytes are skipped.
The download goes through the Hugging Face cache (~/.cache/huggingface/hub, or --cache-dir), then each file is copied
into place and checked against the Hub's bytes.
"""


def refuse(message: str) -> None:
    print(f"{PROG}: error: {message}", file=sys.stderr)
    raise SystemExit(2)


def local_path(remote: str, root: Path) -> Path | None:
    """Where a file of the dataset goes here, the reverse of the upload's mapping; None for a study's own documents
    and for any path that is not a plain relative one."""
    parts = remote.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if parts[0] == MANIFEST and len(parts) > 1:
        return root.joinpath("runs", *parts)
    if len(parts) >= 3 and parts[1] in ("runs", "logs"):
        return root.joinpath(parts[1], parts[0], *parts[2:])
    return None


def run_paths(study: str, run: str) -> list[str]:
    """The dataset paths that hold one run: its folders and, in a study, its box console log."""
    if study == MANIFEST:
        return [f"{MANIFEST}/{run}"]
    return [f"{study}/runs/{run}", f"{study}/logs/{run}", f"{study}/logs/screen/{run}.log"]


def index_path(study: str) -> str:
    """The file that gives the viewer each run's valid flag."""
    return f"{MANIFEST}/manifest.jsonl" if study == MANIFEST else f"{study}/runs/INDEX.jsonl"


def plain_run(study: str, run: str) -> str:
    run = run.strip("/").removeprefix(f"runs/{study}/").removeprefix(f"{study}/runs/").removeprefix(f"{study}/")
    if not run or Path(run).is_absolute() or any(part in ("", ".", "..") for part in run.split("/")):
        refuse(f"no run {run or '(empty)'} in {study}")
    return run


def hub_files(api: HfApi, repo: str, path: str) -> dict[str, RepoFile]:
    """The Hub's file at path, or every file under the folder at path, by path; {} when the Hub has nothing there."""
    try:
        [entry] = api.get_paths_info(repo, [path], repo_type="dataset") or [None]
    except EntryNotFoundError:
        entry = None
    if isinstance(entry, RepoFile):
        return {entry.path: entry}
    if not isinstance(entry, RepoFolder):
        return {}
    try:
        tree = api.list_repo_tree(repo, path_in_repo=path, recursive=True, repo_type="dataset")
        return {e.path: e for e in tree if isinstance(e, RepoFile)}
    except EntryNotFoundError:
        return {}


def studies_on_hub(api: HfApi, repo: str) -> list[str]:
    return sorted(e.path for e in api.list_repo_tree(repo, repo_type="dataset") if isinstance(e, RepoFolder)
                  and e.path != "LICENSES")


def select(api: HfApi, repo: str, study: str, runs: list[str]) -> tuple[dict[str, dict[str, RepoFile]], dict]:
    """The Hub's files by run (or, for a whole study, by top-level entry), and the study's index file (path: entry)
    when a run pull should bring it."""
    if not study or "/" in study or study in (".", ".."):
        refuse(f"no study {study or '(empty)'}: give a top-level folder of the dataset, "
               "e.g. ian_baseline_explorations_1")
    studies = studies_on_hub(api, repo)
    if study not in studies:
        refuse(f"no study {study} on the Hub in {repo}; the studies there: {', '.join(studies) or 'none'}")
    if not runs:
        items: dict[str, dict[str, RepoFile]] = {}
        for remote, entry in hub_files(api, repo, study).items():
            parts = remote.split("/")  # an entry is what the upload calls one: a name under runs/<study>/ or logs/
            name = parts[2] if study != MANIFEST and len(parts) > 2 and parts[1] in ("runs", "logs") else parts[1]
            items.setdefault(name, {})[remote] = entry
        return items, {}
    items = {}
    for run in runs:
        found = {}
        for path in run_paths(study, run):
            found.update(hub_files(api, repo, path))
        if not found:
            refuse(f"no run {run} in {study} on the Hub; see https://huggingface.co/datasets/{repo}/tree/main/{study}")
        items[run] = found
    return items, hub_files(api, repo, index_path(study))


def state_of(remote: str, entry: RepoFile, root: Path) -> str:
    """new (no file here), same (the Hub's bytes) or differs."""
    path = local_path(remote, root)
    if not path.exists():
        return "new"
    return "same" if path.is_file() and same(path, entry) else "differs"


def mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def patterns(needed: list[str], study: str) -> list[str]:
    """allow_patterns for the download: each file by name when few, else each run folder that holds one."""
    if len(needed) <= EXACT_PATTERNS:
        return sorted(glob.escape(remote) for remote in needed)
    depth = 2 if study == MANIFEST else 3
    units = {"/".join(remote.split("/")[:depth]) for remote in needed}
    return sorted(glob.escape(unit) + ("" if unit in needed else "/*") for unit in units)


def place(source: Path, target: Path) -> None:
    """Copy a downloaded file into place through a temporary file beside it, so a failure leaves no partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.hf-pull")
    temporary.write_bytes(source.read_bytes())
    os.replace(temporary, target)


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("study",
                        help="a top-level folder of the dataset, e.g. ian_baseline_explorations_1 or apart_controls")
    parser.add_argument("--run", action="append", default=[], metavar="NAME",
                        help="one run of the study (repeat, or give several in one quoted value); without it, the "
                             "whole study")
    parser.add_argument("--dest", type=Path, help="the folder that gets runs/ and logs/ (default: this repository)")
    parser.add_argument("--force", action="store_true", help="replace local files that differ from the Hub's")
    parser.add_argument("--dry-run", action="store_true", help="list what would come and what differs; write nothing")
    parser.add_argument("--token", help="a Hugging Face token, for a private dataset; by default HF_TOKEN, then the "
                                        "saved login, else none")
    parser.add_argument("--repo", default=REPO, help=f"the dataset (default {REPO})")
    parser.add_argument("--cache-dir", type=Path, help="the Hugging Face cache to download into (default: the "
                                                       "library's, ~/.cache/huggingface/hub)")
    args = parser.parse_args(argv)
    dest = (args.dest or root).resolve()
    runs = [plain_run(args.study, name) for value in args.run for name in value.split()]

    token, source = find_token(args.token)
    progress.step(f"List {args.study} on the Hub")
    try:
        api = HfApi(token=token or False)
        items, index = select(api, args.repo, args.study, runs)
    except RepositoryNotFoundError:
        refuse(f"no dataset {args.repo} on the Hub, or it is private and the token cannot read it")

    placed = {name: {r: e for r, e in files.items() if local_path(r, dest) and not junk(Path(r))}
              for name, files in items.items()}
    hub_only = sorted({r.split("/")[1] + ("/" if r.count("/") > 1 else "") for files in items.values()
                       for r in files if local_path(r, dest) is None and not junk(Path(r))})
    placed = {name: files for name, files in placed.items() if files}
    states = {r: state_of(r, e, dest) for files in placed.values() for r, e in files.items()}
    index_states = {r: state_of(r, e, dest) for r, e in index.items() if r not in states}

    every = [e for files in placed.values() for e in files.values()]
    progress.detail("repo", f"{args.repo} (dataset)")
    progress.detail("study", args.study + ("" if runs else " (the whole study)"))
    progress.detail("dest", dest)
    progress.detail("runs" if runs else "entries", len(placed))
    progress.detail("files", f"{len(every)}, {mb(sum(e.size for e in every))} on the Hub")
    width = max((len(name) for name in placed), default=0)
    for name, files in placed.items():
        count = {k: sum(1 for r in files if states[r] == k) for k in ("new", "same", "differs")}
        progress.detail("", f"{name:<{width}}  {len(files):>5} files  {mb(sum(e.size for e in files.values())):>10}  "
                            f"new {count['new']}, same {count['same']}, differs {count['differs']}")
    for remote, state in index_states.items():
        progress.detail("index", f"{remote}: " + ("comes too" if state == "new" else
                                                  "here already" + (", left as it is (it differs from the Hub's)"
                                                                    if state == "differs" else "")))
    if hub_only:
        progress.detail("hub only", f"{', '.join(hub_only)} (the study's own documents stay on the Hub)")
    progress.detail("token", "from " + source if token else "none (the dataset is public)")

    differs = sorted(r for r, s in states.items() if s == "differs")
    for remote in differs:
        progress.detail("", f"differs here: {local_path(remote, dest)}", err=True)
    if differs and not args.force:
        if args.dry_run:
            progress.done(f"dry run: nothing written; a pull would stop at the {len(differs)} differing file(s), and "
                          f"--force replaces them")
            return 0
        refuse(f"{len(differs)} local file(s) differ from the Hub's; nothing was pulled; --force replaces them")
    needed = sorted(r for r, s in states.items() if s == "new" or (s == "differs" and args.force))
    needed += sorted(r for r, s in index_states.items() if s == "new")
    if args.dry_run:
        progress.done(f"dry run: nothing written; a pull would write {len(needed)} file(s)")
        return 0
    if not needed:
        progress.done("every file is here with the Hub's bytes; nothing to pull")
        return 0

    entries = {**{r: e for files in placed.values() for r, e in files.items()}, **index}
    progress.step(f"Download {len(needed)} files")
    try:
        snapshot = Path(snapshot_download(args.repo, repo_type="dataset", allow_patterns=patterns(needed, args.study),
                                          cache_dir=args.cache_dir, token=token or False))
    except Exception as ex:  # the library's message, with the token taken out should it ever appear
        message = str(ex).replace(token, "<token>") if token else str(ex)
        refuse(f"the download failed: {type(ex).__name__}: {message}")
    bad = []
    progress.step(f"Place {len(needed)} files under {dest} and check their bytes")
    with progress.Counter(len(needed), "placed", "files") as count:
        for remote in needed:
            count.tick()
            source_file, target = snapshot / remote, local_path(remote, dest)
            if not source_file.is_file():
                bad.append(remote)
                continue
            place(source_file, target)
            if not same(target, entries[remote]):
                bad.append(remote)
    for remote in bad:
        progress.detail("", f"not placed with the Hub's bytes: {remote}")
    what = f"{len(needed) - len(bad)} of {len(needed)} files placed with the Hub's bytes"
    if bad:
        progress.failed(what)
        return 1
    progress.done(what, out=dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
