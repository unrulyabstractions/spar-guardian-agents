"""Upload a study's runs and their eval logs to the Hugging Face dataset unrulyabstractions/spar-swarm.

Give a token once: run `hf auth login`, or `export HF_TOKEN=<token>`. Then upload:

    make hf-upload STUDY=<study> [RUN=<run>] [DRY_RUN=1]
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # run as a file, `python scripts/upload_runs_to_hf.py`, as well as with -m
    sys.path.insert(0, str(ROOT))

from huggingface_hub import CommitOperationAdd, HfApi, RepoFile, get_token  # noqa: E402

from elicitation.publish import same, secrets  # noqa: E402  (publish also teaches zipfile Inspect's zstd members)

REPO = "unrulyabstractions/spar-swarm"
PROG = "python scripts/upload_runs_to_hf.py"
NO_TOKEN = "no Hugging Face token: run `hf auth login`, or `export HF_TOKEN=<token>`, or pass --token"
EPILOG = """\
runs/<study>/<run>/ goes to <study>/runs/<run>/ in the dataset, and logs/<study>/<run>/ to <study>/logs/<run>/,
beside what the dataset holds. Without --run the whole study goes, except its smoke* folders (mock smoke tests). macOS
files (.DS_Store, ._*) never go. Files already there unchanged are skipped, so a rerun resumes. Nothing on the Hub is
deleted. It refuses to upload a file that holds a key from this shell, the token or the git email.
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


def select(study: str, runs: list[str], root: Path = ROOT) -> tuple[dict[str, list[tuple[str, Path]]], list[str]]:
    """The files to upload by run (or by top-level entry of the study), and what was skipped."""
    runs_dir, logs_dir = root / "runs" / study, root / "logs" / study
    if study == "apart_controls":
        refuse("the apart controls sit at apart_controls/<model>/... on the dataset; this script uploads studies only")
    if not study or Path(study).name != study or not runs_dir.is_dir():
        here = sorted(p.name for p in (root / "runs").glob("study_*") if p.is_dir())
        refuse(f"no study runs/{study}/ here; the studies here: {', '.join(here) or 'none'}")
    items, skipped = {}, []
    if runs:
        for run in runs:
            run = run.strip("/").removeprefix(f"runs/{study}/")
            if not run or Path(run).is_absolute() or ".." in Path(run).parts or not (runs_dir / run).is_dir():
                refuse(f"no run runs/{study}/{run}/")
            items[run] = (files_in(runs_dir / run, f"{study}/runs/{run}")
                          + files_in(logs_dir / run, f"{study}/logs/{run}")
                          + files_in(logs_dir / "screen" / f"{run}.log", f"{study}/logs/screen/{run}.log"))
    else:
        names = sorted({p.name for folder in (runs_dir, logs_dir) if folder.is_dir() for p in folder.iterdir()})
        for name in names:
            if name.startswith("smoke") or junk(Path(name)):
                skipped.append(name)
                continue
            items[name] = files_in(runs_dir / name, f"{study}/runs/{name}") + files_in(logs_dir / name,
                                                                                      f"{study}/logs/{name}")
    return {name: files for name, files in items.items() if files}, skipped


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
        logs = sum(1 for remote, _ in files if remote.startswith(f"{study}/logs/"))
        print(f"  {name:<{width}}  {len(files):>5} files  {megabytes(files):>10}  ({logs} in logs/)")
    if skipped:
        print(f"skipped  {', '.join(skipped)}")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("study", help="a folder of runs/, e.g. study_exploration_1")
    parser.add_argument("--run", action="append", default=[], metavar="NAME",
                        help="one run of the study (repeat for several); without it, the whole study")
    parser.add_argument("--token", help="a Hugging Face token; by default HF_TOKEN, then the saved login")
    parser.add_argument("--repo", default=REPO, help=f"the dataset (default {REPO})")
    parser.add_argument("--dry-run", action="store_true", help="list and check what would go; upload nothing")
    args = parser.parse_args(argv)
    runs = [name for value in args.run for name in value.split()]

    items, skipped = select(args.study, runs, root)
    if not items:
        refuse(f"runs/{args.study}/ holds no file to upload")
    plan(args.repo, args.study, items, skipped, whole=not runs)
    for run in runs:
        if not any(remote.startswith(f"{args.study}/logs/{run}/") for remote, _ in items.get(run, [])):
            print(f"note     {run} has no eval log under logs/{args.study}/{run}/")

    token, source = find_token(args.token)
    if not token and not args.dry_run:
        refuse(NO_TOKEN)
    print(f"token    {'from ' + source if token else 'none found; the real upload needs one'}")

    every = [f for files in items.values() for f in files]
    values = secrets() | ({"the Hugging Face token in use": token.encode()} if token else {})
    found = leaks([path for _, path in every], values)
    if found:
        print("\n".join(found), file=sys.stderr)
        refuse(f"{len(found)} file(s) hold a key, the token or the git email; nothing was uploaded")
    print("check    no key, token or git email in any file")
    if args.dry_run:
        print("dry run: nothing uploaded")
        return 0

    message = f"{args.study}: " + ("the whole study" if not runs else ", ".join(runs[:5])
                                   + (f" and {len(runs) - 5} more" if len(runs) > 5 else ""))
    try:
        api = HfApi(token=token)
        api.create_commit(repo_id=args.repo, repo_type="dataset", commit_message=message,
                          operations=[CommitOperationAdd(path_in_repo=remote, path_or_fileobj=str(path))
                                      for remote, path in every])
        remote = {}
        names = [name for name, _ in every]
        for start in range(0, len(names), 500):
            remote.update({e.path: e for e in api.get_paths_info(args.repo, names[start:start + 500],
                                                                 repo_type="dataset") if isinstance(e, RepoFile)})
    except Exception as ex:  # the library's message, with the token taken out should it ever appear
        refuse(f"the upload failed: {type(ex).__name__}: {str(ex).replace(token, '<token>')}")
    bad = [name for name, path in every if name not in remote or not same(path, remote[name])]
    for name in bad:
        print(f"   not on the Hub with the same bytes: {name}")
    print(f"done     {len(every) - len(bad)} of {len(every)} files on the Hub with the same bytes")
    print(f"url      https://huggingface.co/datasets/{args.repo}/tree/main/{args.study}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
