"""Upload a study's runs and their eval logs, or the apart controls, to the Hugging Face dataset
unrulyabstractions/spar-swarm.

Give a token once: run `hf auth login`, or `export HF_TOKEN=<token>`. Then upload:

    make hf-upload STUDY=<study> [RUN=<run>] [DRY_RUN=1] [DELETE_STALE=1] [LISTING=<file>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # run as a file, `python scripts/upload_runs_to_hf.py`, as well as with -m
    sys.path.insert(0, str(ROOT))

from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi, RepoFile, get_token  # noqa: E402
from huggingface_hub.errors import EntryNotFoundError  # noqa: E402

from elicitation.publish import same, secrets  # noqa: E402  (publish also teaches zipfile Inspect's zstd members)

REPO = "unrulyabstractions/spar-swarm"
PROG = "python scripts/upload_runs_to_hf.py"
APART = "apart_controls"
"""The apart controls: runs/apart_controls/ here is apart_controls/ in the dataset, each run's eval log inside it."""
OLD_LAYOUT = "set_aside_"
"""logs/<study>/set_aside_*/ hold old-layout copies of runs made again (a run folder and its log side by side). They
never go, and --delete-stale deletes the Hub's copies of them when no local file of runs/<study>/ names them."""
KINDS = ("add", "change", "same", "delete", "stale", "keep")
"""What the upload does to a dataset path; compare says which is which."""
NO_TOKEN = "no Hugging Face token: run `hf auth login`, or `export HF_TOKEN=<token>`, or pass --token"
EPILOG = """\
runs/<study>/<run>/ goes to <study>/runs/<run>/ in the dataset, and logs/<study>/<run>/ to <study>/logs/<run>/,
beside what the dataset holds. Without --run the whole study goes, except its smoke* folders (mock smoke tests) and
logs/<study>/set_aside_*/ (old-layout copies). runs/apart_controls/ goes to apart_controls/, whole or one run,
<model>/<task>/<cell>/s<seed>. macOS files (.DS_Store, ._*) never go. Of the .eval files under logs/<study>/, only a
run's own log goes: the one the run.json at the same path under runs/<study>/ names. An aborted start's journal beside
it, or a log folder with no run, stays here and is listed.

The Hub's files under the upload's reach (<study>/runs/ and <study>/logs/, or apart_controls/; with --run, those runs'
paths) are listed first. A file the Hub holds with the same bytes is skipped, so a rerun resumes. A study's own
documents beside runs/ and logs/ (its README, figures, tables) are never touched. Nothing on the Hub is deleted unless
--delete-stale is given: then a Hub file in reach whose local file does not exist is deleted in the same commit, and so
is a Hub copy of a set_aside_ file that no local file names. --listing writes every add, change, delete and kept file.
It refuses to upload a file that holds a key from this shell, the token or the git email.
"""


def refuse(message: str) -> None:
    print(f"{PROG}: error: {message}", file=sys.stderr)
    raise SystemExit(2)


def find_token(given: str | None) -> tuple[str | None, str | None]:
    """The token and where it came from: --token, else HF_TOKEN, else the login `hf auth login` saved."""
    if given:
        return given, "--token"
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"], "HF_TOKEN"
    saved = get_token()
    return (saved, "the saved login (hf auth login)") if saved else (None, None)


def junk(path: Path) -> bool:
    return path.name == ".DS_Store" or path.name.startswith("._")


def files_in(local: Path, remote: str) -> list[tuple[str, Path]]:
    """(path in the dataset, local path) for a file, or for every file under a folder, macOS files left out."""
    if local.is_file():
        return [] if junk(local) else [(remote, local)]
    if not local.is_dir():
        return []
    return [(f"{remote}/{path.relative_to(local).as_posix()}", path) for path in sorted(local.rglob("*"))
            if path.is_file() and not any(junk(Path(part)) for part in path.relative_to(local).parts)]


def named_log(log: Path, runs_dir: Path, logs_dir: Path) -> bool:
    """Whether an .eval file under logs/<study>/ is a run's own log: the run.json at its folder's path under
    runs/<study>/ names a log of its file name (run_directory.RunDir.eval_log finds a log by that name there)."""
    run_json = runs_dir / log.parent.relative_to(logs_dir) / "run.json"
    if not run_json.is_file():
        return False
    recorded = (json.loads(run_json.read_text()) or {}).get("eval_log")
    return bool(recorded) and Path(recorded).name == log.name


def logs_in(logs_dir: Path, runs_dir: Path, rel: str, remote: str) -> tuple[list[tuple[str, Path]], list[Path]]:
    """files_in for logs/<study>/<rel>, without the .eval files no run names (named_log); and those left out."""
    files, stray = [], []
    for name, path in files_in(logs_dir / rel, remote):
        if path.suffix == ".eval" and not named_log(path, runs_dir, logs_dir):
            stray.append(path)
        else:
            files.append((name, path))
    return files, stray


def plain_run(run: str, *prefixes: str) -> str:
    for prefix in prefixes:
        run = run.strip("/").removeprefix(prefix)
    return run.strip("/")


def select_apart(runs: list[str], root: Path) -> tuple[dict[str, list[tuple[str, Path]]], list[str]]:
    """select for the apart controls: each top-level entry of runs/apart_controls/, or each run named."""
    base = root / "runs" / APART
    if not base.is_dir():
        refuse(f"no runs/{APART}/ here")
    items, skipped = {}, []
    if runs:
        for run in runs:
            run = plain_run(run, f"runs/{APART}/", f"{APART}/")
            if not run or Path(run).is_absolute() or ".." in Path(run).parts or not (base / run).is_dir():
                refuse(f"no run runs/{APART}/{run}/")
            items[run] = files_in(base / run, f"{APART}/{run}")
    else:
        for entry in sorted(base.iterdir()):
            if junk(entry):
                skipped.append(entry.name)
                continue
            items[entry.name] = files_in(entry, f"{APART}/{entry.name}")
    return {name: files for name, files in items.items() if files}, skipped


def select(study: str, runs: list[str], root: Path = ROOT) -> tuple[dict[str, list[tuple[str, Path]]], list[str]]:
    """The files to upload by run (or by top-level entry of the study), and what was skipped: the smoke folders, the
    set_aside_ log folders and macOS files by name, and each .eval no run names by its path under logs/<study>/."""
    if study == APART:
        return select_apart(runs, root)
    runs_dir, logs_dir = root / "runs" / study, root / "logs" / study
    if not study or Path(study).name != study or not runs_dir.is_dir():
        here = sorted(p.name for p in (root / "runs").iterdir() if p.is_dir() and p.name != APART)
        refuse(f"no study runs/{study}/ here; the studies here: {', '.join(here) or 'none'}")
    items, skipped = {}, []
    if runs:
        for run in runs:
            run = run.strip("/").removeprefix(f"runs/{study}/")
            if not run or Path(run).is_absolute() or ".." in Path(run).parts or not (runs_dir / run).is_dir():
                refuse(f"no run runs/{study}/{run}/")
            logs, stray = logs_in(logs_dir, runs_dir, run, f"{study}/logs/{run}")
            items[run] = (files_in(runs_dir / run, f"{study}/runs/{run}") + logs
                          + files_in(logs_dir / "screen" / f"{run}.log", f"{study}/logs/screen/{run}.log"))
            skipped += [path.relative_to(logs_dir).as_posix() for path in stray]
    else:
        names = sorted({p.name for folder in (runs_dir, logs_dir) if folder.is_dir() for p in folder.iterdir()})
        for name in names:
            if name.startswith("smoke") or junk(Path(name)):
                skipped.append(name)
                continue
            if name.startswith(OLD_LAYOUT):
                skipped.append(f"{name} (logs/, old layout)")
                logs, stray = [], []
            else:
                logs, stray = logs_in(logs_dir, runs_dir, name, f"{study}/logs/{name}")
            items[name] = files_in(runs_dir / name, f"{study}/runs/{name}") + logs
            skipped += [path.relative_to(logs_dir).as_posix() for path in stray]
    return {name: files for name, files in items.items() if files}, skipped


def reach(study: str, runs: list[str]) -> tuple[list[str], list[str]]:
    """The Hub's paths this upload answers for: folder prefixes (each ends in /) and single files. Never a study's own
    documents beside its runs/ and logs/."""
    if study == APART:
        return ([f"{APART}/{plain_run(run, f'runs/{APART}/', f'{APART}/')}/" for run in runs] or [f"{APART}/"]), []
    if not runs:
        return [f"{study}/runs/", f"{study}/logs/"], []
    runs = [run.strip("/").removeprefix(f"runs/{study}/") for run in runs]
    return ([prefix for run in runs for prefix in (f"{study}/runs/{run}/", f"{study}/logs/{run}/")],
            [f"{study}/logs/screen/{run}.log" for run in runs])


def in_reach(remote: str, folders: list[str], single: list[str]) -> bool:
    return remote in single or any(remote.startswith(prefix) for prefix in folders)


def local_of(remote: str, study: str, root: Path) -> Path | None:
    """The local file a dataset path stands for, the reverse of the upload's mapping (pull_runs_from_hf.local_path)."""
    parts = remote.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if parts[0] == APART and len(parts) > 1:
        return root.joinpath("runs", *parts)
    if len(parts) >= 3 and parts[1] in ("runs", "logs"):
        return root.joinpath(parts[1], parts[0], *parts[2:])
    return None


def hub_tree(api, repo: str, study: str, folders: list[str], single: list[str]) -> dict[str, RepoFile]:
    """The Hub's files in reach, by path: one recursive listing of the collection's folders, kept to the reach."""
    top = [f"{APART}"] if study == APART else [f"{study}/runs", f"{study}/logs"]
    out = {}
    for path in top:
        try:
            out.update({e.path: e for e in api.list_repo_tree(repo, path_in_repo=path, recursive=True,
                                                              repo_type="dataset") if isinstance(e, RepoFile)})
        except EntryNotFoundError:
            pass
    return {path: entry for path, entry in out.items() if in_reach(path, folders, single)}


def named_here(remote: str, study: str, root: Path) -> bool:
    """Whether a local file of runs/<study>/ names a set_aside_ file by its path under logs/<study>/."""
    rel = remote.split("/", 2)[2].encode()
    return any(rel in path.read_bytes() for path in (root / "runs" / study).rglob("*") if path.is_file())


def compare(every: list[tuple[str, Path]], hub: dict[str, RepoFile], study: str, root: Path,
            delete_stale: bool) -> dict[str, list[str]]:
    """Each dataset path by what the upload does to it. add: not on the Hub. change: other bytes there. same: the same
    bytes there. delete (only with delete_stale) and stale (without it): on the Hub, in reach, and either its local file
    is gone or it is a set_aside_ copy no local file names. keep: on the Hub, a local file the upload leaves out."""
    out = {kind: [] for kind in KINDS}
    sent = set()
    for remote, path in every:
        sent.add(remote)
        entry = hub.get(remote)
        out["add" if entry is None else "same" if same(path, entry) else "change"].append(remote)
    for remote in sorted(set(hub) - sent):
        local = local_of(remote, study, root)
        gone = local is None or not local.is_file()
        parts = remote.split("/")
        old = study != APART and parts[1] == "logs" and parts[2].startswith(OLD_LAYOUT)
        if gone or (old and not named_here(remote, study, root)):
            out["delete" if delete_stale else "stale"].append(remote)
        else:
            out["keep"].append(remote)
    return out


def leaks(files: list[Path], values: dict[str, bytes]) -> list[str]:
    """Where a file, or a member of an .eval archive, holds one of the values, by the value's name, never the value."""
    out = []
    for path in files:
        data = path.read_bytes()
        out.extend(f"{path}: holds {name}" for name, value in values.items() if value in data)
        if path.suffix == ".eval" and zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    try:
                        content = archive.read(member)
                    except (NotImplementedError, RuntimeError, zipfile.BadZipFile, OSError) as ex:
                        out.append(f"{path}::{member}: cannot be read ({type(ex).__name__})")
                        continue
                    out.extend(f"{path}::{member}: holds {name}" for name, value in values.items() if value in content)
    return out


def megabytes(files: list[tuple[str, Path]]) -> str:
    return f"{sum(path.stat().st_size for _, path in files) / 1e6:.1f} MB"


def plan(repo: str, study: str, items: dict, skipped: list[str], whole: bool) -> None:
    every = [f for files in items.values() for f in files]
    print(f"repo     {repo} (dataset)")
    print(f"study    {study}" + (" (the whole study)" if whole else ""))
    print(f"{'entries' if whole else 'runs':<8} {len(items)}")
    print(f"files    {len(every)}, {megabytes(every)}")
    width = max(len(name) for name in items)
    for name, files in items.items():
        logs = sum(1 for remote, _ in files if remote.endswith(".eval"))
        print(f"  {name:<{width}}  {len(files):>5} files  {megabytes(files):>10}  ({logs} .eval)")
    if skipped:
        print(f"skipped  {', '.join(skipped)}")


def show(ops: dict[str, list[str]], local: dict[str, Path], hub: dict[str, RepoFile], listing: Path | None) -> None:
    """The counts of each kind, every delete, and with listing a file of every path by kind and both sizes."""
    print(f"hub      {len(hub)} files in reach")
    for kind in KINDS:
        print(f"{kind:<8} {len(ops[kind])}")
    for remote in ops["delete"]:
        print(f"   delete {remote}")
    if ops["stale"]:
        print(f"note     {len(ops['stale'])} stale Hub file(s) stay; --delete-stale deletes them")
    if listing:
        listing.parent.mkdir(parents=True, exist_ok=True)
        with listing.open("w") as out:
            out.write("kind\tpath\tlocal_bytes\thub_bytes\n")
            for kind in KINDS:
                for remote in ops[kind]:
                    size = local[remote].stat().st_size if remote in local else ""
                    out.write(f"{kind}\t{remote}\t{size}\t{hub[remote].size if remote in hub else ''}\n")
        print(f"listing  {listing}")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("study", help=f"a folder of runs/, e.g. ian_baseline_explorations_1 or {APART}")
    parser.add_argument("--run", action="append", default=[], metavar="NAME",
                        help="one run of the study (repeat for several); without it, the whole study")
    parser.add_argument("--token", help="a Hugging Face token; by default HF_TOKEN, then the saved login")
    parser.add_argument("--repo", default=REPO, help=f"the dataset (default {REPO})")
    parser.add_argument("--dry-run", action="store_true", help="list and check what would go; upload nothing")
    parser.add_argument("--delete-stale", action="store_true",
                        help="also delete, in the same commit, the Hub's files in reach whose local file is gone")
    parser.add_argument("--listing", type=Path, metavar="FILE",
                        help="write every path by what the upload does to it (add, change, same, delete, stale, keep)")
    args = parser.parse_args(argv)
    runs = [name for value in args.run for name in value.split()]

    items, skipped = select(args.study, runs, root)
    if not items:
        refuse(f"runs/{args.study}/ holds no file to upload")
    plan(args.repo, args.study, items, skipped, whole=not runs)
    for run in runs:
        if args.study != APART and not any(remote.startswith(f"{args.study}/logs/{run}/")
                                           for remote, _ in items.get(run, [])):
            print(f"note     {run} has no eval log under logs/{args.study}/{run}/")

    token, source = find_token(args.token)
    if not token and not args.dry_run:
        refuse(NO_TOKEN)
    print(f"token    {'from ' + source if token else 'none found; the real upload needs one'}")

    def failed(what: str, ex: Exception, then: str = "") -> None:  # the library's message, the token taken out
        refuse(f"{what} failed: {type(ex).__name__}: {str(ex).replace(token, '<token>') if token else ex}{then}")

    every = [f for files in items.values() for f in files]
    local = dict(every)
    values = secrets() | ({"the Hugging Face token in use": token.encode()} if token else {})
    found = leaks([path for _, path in every], values)
    if found:
        print("\n".join(found), file=sys.stderr)
        refuse(f"{len(found)} file(s) hold a key, the token or the git email; nothing was uploaded")
    print("check    no key, token or git email in any file")

    folders, single = reach(args.study, runs)
    try:
        api = HfApi(token=token or False)
        hub = hub_tree(api, args.repo, args.study, folders, single)
    except Exception as ex:
        failed("listing the Hub", ex)
    ops = compare(every, hub, args.study, root, args.delete_stale)
    show(ops, local, hub, args.listing)
    if args.dry_run:
        print("dry run: nothing uploaded")
        return 0
    operations = ([CommitOperationAdd(path_in_repo=remote, path_or_fileobj=str(local[remote]))
                   for remote in ops["add"] + ops["change"]]
                  + [CommitOperationDelete(path_in_repo=remote) for remote in ops["delete"]])
    if not operations:
        print("done     the Hub already holds every file with the same bytes; nothing to commit")
        return 0

    message = f"{args.study}: " + ("the whole study" if not runs else ", ".join(runs[:5])
                                   + (f" and {len(runs) - 5} more" if len(runs) > 5 else ""))
    message += f" ({len(ops['add'])} added, {len(ops['change'])} changed, {len(ops['delete'])} deleted)"
    try:
        commit = api.create_commit(repo_id=args.repo, repo_type="dataset", commit_message=message,
                                   operations=operations)
    except Exception as ex:  # a gateway timeout can come back while the Hub still applies the commit
        failed("the upload", ex, "; the commit may still land, so look at the dataset's commits, then rerun with "
                                 "--dry-run to see what the Hub holds")
    try:
        after = hub_tree(api, args.repo, args.study, folders, single)
    except Exception as ex:
        failed("listing the Hub after the commit", ex)
    bad = [name for name, path in every if name not in after or not same(path, after[name])]
    left = [name for name in ops["delete"] if name in after]
    for name in bad:
        print(f"   not on the Hub with the same bytes: {name}")
    for name in left:
        print(f"   still on the Hub after its delete: {name}")
    print(f"commit   {getattr(commit, 'oid', None) or 'unknown'}")
    print(f"done     {len(every) - len(bad)} of {len(every)} files on the Hub with the same bytes, "
          f"{len(ops['delete']) - len(left)} of {len(ops['delete'])} deleted")
    print(f"url      https://huggingface.co/datasets/{args.repo}/tree/main/{args.study}")
    return 1 if bad or left else 0


if __name__ == "__main__":
    raise SystemExit(main())
