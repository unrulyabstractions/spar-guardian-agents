"""Move run folders into the stored layout, runs/<study>/[<group>/]<run>/processed/ and analysis/, and the Hugging Face
dataset with them.

    uv run python -m scripts.migrate_run_layout local [ROOT ...] [--dry-run]
    uv run python -m scripts.migrate_run_layout hub [--dry-run]

**local.** A ROOT is a folder named runs (default: runs/ and OLD/runs/). A run is a folder with a run.json at its top.
Each entry of it goes where run_directory.FILES names it; tools/board_reads.json to FILES["board_reads.json"] and the
rest of tools/ to analysis/tools/; the older ladders' files (LEGACY) to analysis/; any other entry to processed/. A
run's figures, out/<study>/[<group>/]<run>/ beside the root, go to analysis/figures/, and a run's eval/ folder
(the apart controls) to its log folder, logs/<study>/[<group>/]<run>/. An entry that holds another run (OLD/runs/smoke)
stays: that run moves on its own, and so do processed/ and analysis/ when processing wrote them at the layout already.
macOS files (.DS_Store, ._*) are deleted. Each move is one os.rename on one disk. The plan refuses when a target
exists or two sources share a target.
Before and after, every file under the root, its out/ and the eval folders is listed with its size and sha256; every
file must sit at its new path with the same bytes, and the counts must match. The moves are written to
.backups/migrate_run_layout/<stamp>/moves.tsv, which hub reads.

**hub.** Lists the dataset unrulyabstractions/spar-swarm. study_exploration_0 and _1 (the old names) are deleted in one
commit only when every file there has a twin of the same size and hash at the same path under
ian_baseline_explorations_0 / _1. Then each study (HUB_STUDIES) goes up through scripts/upload_runs_to_hf.py with
--delete-stale, after a check that every Hub file the upload would delete has its bytes at the path its local move
took it to (moves.tsv), a file the upload sends. apart_controls's old folders outside its runs/ and logs/ go in the
same way, by its own commit. Nothing is deleted on the Hub without its twin.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.measures import pipeline_parts, run_directory  # noqa: E402
from src.utils import progress  # noqa: E402

OLD_RUN_JSON = "run.json"
"""A run folder not yet moved holds its run.json at its top."""
LEGACY = ("judge.jsonl", "ladder.jsonl", "ladder_actions.jsonl", "rungs.json", "smoke_results.json")
"""The older ladders' files of the archive: made from the transcripts, so they go to analysis/."""
TOOLS, EVAL, FIGURES = "tools", "eval", "figures"
LAYERS = ("processed", "analysis")
BACKUPS = ROOT / ".backups" / "migrate_run_layout"
HUB_STUDIES = {"apart_controls": "OLD", "ian_baseline_explorations_0": "OLD", "ian_baseline_explorations_1": "OLD",
               "study_orbit2_check": "OLD", "study_smoke": "", "ian_seed_prototyping_0": ""}
"""Each study the dataset holds, by the folder here whose runs/ and logs/ hold it."""
RENAMED = {"study_exploration_0": "ian_baseline_explorations_0", "study_exploration_1": "ian_baseline_explorations_1"}
"""Old study names on the Hub and the names their files live under now."""


def refuse(message: str) -> None:
    print(f"migrate_run_layout: error: {message}", file=sys.stderr)
    raise SystemExit(2)


def junk(path: Path) -> bool:
    return path.name == ".DS_Store" or path.name.startswith("._")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(folders: list[Path]) -> dict[Path, tuple[int, str]]:
    """{file: (size, sha256)} of every file under the folders (symlinks are listed, not followed)."""
    files = sorted({path for folder in folders if folder.is_dir() for path in folder.rglob("*")
                    if (path.is_file() or path.is_symlink()) and path.relative_to(folder).parts[0] not in SKIPPED})
    out = {}
    with progress.Counter(len(files), "hashed", "files") as count:
        for path in files:
            count.tick()
            out[path] = (path.lstat().st_size, "symlink" if path.is_symlink() else sha256(path))
    return out


def holds_run(folder: Path) -> bool:
    """Whether a folder is, or holds, a run in either layout."""
    if not folder.is_dir():
        return False
    return any(True for _ in folder.rglob(OLD_RUN_JSON))


SKIPPED: set[str] = set()
"""Studies the move leaves for a later pass (--skip): one a smoke test still writes, say."""


def old_runs(root: Path) -> list[Path]:
    """Every folder under the root with a run.json at its top, not inside processed/ or analysis/, nor in a study
    SKIPPED names."""
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in LAYERS and not (Path(folder) == root and d in SKIPPED))
        if OLD_RUN_JSON in files:
            found.append(Path(folder))
    return found


def moved_runs(root: Path) -> list[Path]:
    return sorted(path.parent.parent for path in root.rglob(run_directory.FILES["run.json"]))


def layout_paths() -> dict[str, str]:
    """name -> layout path: FILES, and every part's file by its last name (a computed part's file too)."""
    table = dict(run_directory.FILES)
    for part in pipeline_parts.registry().parts.values():
        for path in part.paths:
            if "*" not in path:
                table.setdefault(path.rstrip("/").rsplit("/", 1)[-1], path)
    return table


LAYOUT = layout_paths()


def target_of(run: Path, entry: Path) -> list[tuple[Path, Path]]:
    """(source, target) for one entry of a run folder."""
    name = entry.name
    if name == TOOLS and entry.is_dir():
        tools, cache = run_directory.FILES["tools"], run_directory.FILES["board_reads.json"]
        return [(child, run / (cache if child.name == "board_reads.json" else f"{tools}{child.name}"))
                for child in sorted(entry.iterdir())]
    if name == EVAL and entry.is_dir():
        logs = run_directory.log_dir(run)
        if logs is None:
            refuse(f"{run} sits under no runs/ folder, so its eval/ has no log folder")
        return [(child, logs / child.name) for child in sorted(entry.iterdir())]
    if name in LAYOUT:
        return [(entry, run / LAYOUT[name].rstrip("/"))]
    return [(entry, run / ("analysis" if name in LEGACY else "processed") / name)]


def plan(root: Path) -> tuple[list[tuple[Path, Path]], list[Path], list[Path], dict]:
    """The moves, the macOS files to delete, the folders the moves empty (tools/, eval/) and the counts, for one runs/
    root."""
    root = root.resolve()
    out_root = root.parent / "out"
    moves, deletes, emptied = [], [], []
    counts = {"runs": 0, "already": len(moved_runs(root)), "nested entries": 0, "layer folders": 0,
              "figure folders": 0, "eval files": 0, "catch-all": {}}
    runs = old_runs(root)
    figure_dirs = set()
    for run in runs:
        counts["runs"] += 1
        for entry in sorted(run.iterdir()):
            if junk(entry):
                deletes.append(entry)
            elif entry.name in LAYERS:
                counts["layer folders"] += 1  # written at the layout already (the ledgers, the facts, the labels)
            elif entry.is_dir() and entry.name != EVAL and holds_run(entry):
                counts["nested entries"] += 1
            else:
                if entry.name in (TOOLS, EVAL) and entry.is_dir():
                    emptied.append(entry)
                for source, target in target_of(run, entry):
                    if junk(source):
                        deletes.append(source)
                        continue
                    counts["eval files"] += entry.name == EVAL
                    moves.append((source, target))
                if entry.name not in LAYOUT and entry.name not in (*LEGACY, TOOLS, EVAL):
                    counts["catch-all"][entry.name] = counts["catch-all"].get(entry.name, 0) + 1
        figures = run_directory.mirrored(run, "out")
        if figures is not None and figures.is_dir():
            figure_dirs.add(figures)
            counts["figure folders"] += 1
            for child in sorted(figures.iterdir()):
                if junk(child):
                    deletes.append(child)
                elif not (child.is_dir() and holds_run(run / child.name)):  # a nested run moves its own figures
                    moves.append((child, run / LAYOUT[FIGURES] / child.name))
    if out_root.is_dir():
        for path in sorted(out_root.rglob("*")):
            if path.is_dir() or path.relative_to(out_root).parts[0] in SKIPPED:
                continue
            if junk(path):
                deletes.append(path)
            elif not any(parent in figure_dirs for parent in path.parents):
                refuse(f"{path} belongs to no run under {root}: move it by hand first")
    targets: dict[Path, Path] = {}
    for source, target in moves:
        if target in targets:
            refuse(f"{source} and {targets[target]} both go to {target}")
        if target.exists() or target.is_symlink():
            refuse(f"{target} exists; {source} would replace it")
        targets[target] = source
    return moves, sorted(set(deletes)), emptied, counts


def mapped(path: Path, moves: dict[Path, Path]) -> Path:
    """Where a file sits after the moves ({source: target}): the target of the move whose source is it or holds it."""
    for source in (path, *path.parents):
        if source in moves:
            return moves[source] / path.relative_to(source)
    return path


def remove_empty(folder: Path) -> list[Path]:
    """rmdir every empty folder under folder, bottom up, and folder itself when empty."""
    removed = []
    if not folder.is_dir():
        return removed
    for here, dirs, files in sorted(os.walk(folder), key=lambda item: -len(Path(item[0]).parts)):
        path = Path(here)
        if not any(path.iterdir()):
            path.rmdir()
            removed.append(path)
    return removed


def local(roots: list[Path], dry_run: bool) -> int:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    record = BACKUPS / stamp
    every_move, failures = [], 0
    for root in roots:
        root = root.resolve()
        if root.name != "runs" or not root.is_dir():
            refuse(f"{root} is no folder named runs")
        progress.step(f"Plan {progress.short(root)}")
        moves, deletes, emptied, counts = plan(root)
        for key in ("runs", "already", "nested entries", "layer folders", "figure folders", "eval files"):
            progress.detail(key, counts[key])
        progress.detail("moves", len(moves))
        progress.detail("deletes", f"{len(deletes)} macOS files")
        for name, n in sorted(counts["catch-all"].items()):
            progress.detail("to processed", f"{name}: {n}")
        layer_counts = {}
        for source, target in moves:
            rel = target.relative_to(root) if target.is_relative_to(root) else None
            key = "logs" if rel is None else next((p for p in rel.parts if p in LAYERS), "?")
            layer_counts[key] = layer_counts.get(key, 0) + 1
        progress.detail("by layer", ", ".join(f"{k} {v}" for k, v in sorted(layer_counts.items())))
        if dry_run or not moves:
            continue
        out_root = root.parent / "out"
        watched = [root, out_root] + sorted({target.parent for _, target in moves if not target.is_relative_to(root)})
        progress.step(f"List every file before the move ({progress.short(root)})")
        before = inventory([root, out_root])
        progress.step(f"Move {len(moves)} entries")
        with progress.Counter(len(moves), "moved", "entries") as count:
            for source, target in moves:
                count.tick()
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    refuse(f"{target} appeared during the move; stopped after {count}")
                os.rename(source, target)
        for path in deletes:
            if path.exists():
                path.unlink()
        for folder in emptied:
            folder.rmdir()  # raises when a move left a file in it
        removed = remove_empty(out_root)
        progress.step("List every file after the move and compare")
        after = inventory(watched)
        lost, changed, by_source = [], [], dict(moves)
        for path, (size, digest) in before.items():
            if path in deletes:
                continue
            new = mapped(path, by_source)
            if new not in after:
                lost.append(path)
            elif after[new] != (size, digest):
                changed.append(path)
        expected = len(before) - sum(1 for path in before if path in deletes)
        remaining_old = old_runs(root)
        progress.detail("before", f"{len(before)} files, {len(deletes)} of them macOS files deleted")
        progress.detail("after", f"{expected - len(lost)} of {expected} at their new path")
        progress.detail("lost", len(lost))
        progress.detail("changed", len(changed))
        progress.detail("old layout", f"{len(remaining_old)} runs with run.json at their top")
        out_left = out_root.exists() and any(p.name not in SKIPPED for p in out_root.iterdir())
        progress.detail("out", f"{out_root} still holds files" if out_left else
                        "removed" if not out_root.exists() else "only the skipped studies' figures left")
        progress.detail("emptied", f"{len(removed) + len(emptied)} emptied folders removed")
        for path in (lost + changed)[:20]:
            progress.detail("", f"{path} -> {mapped(path, by_source)}", err=True)
        if lost or changed or remaining_old or out_left:
            failures += 1
        record.mkdir(parents=True, exist_ok=True)
        with (record / "moves.tsv").open("a") as handle:
            for path, (size, digest) in before.items():
                if path not in deletes:
                    handle.write(f"{path}\t{mapped(path, by_source)}\t{size}\t{digest}\n")
        every_move += moves
    if dry_run:
        progress.done("dry run: nothing moved")
        return 0
    if failures:
        progress.failed(f"{failures} root(s) failed the check; see {record}")
        return 1
    progress.done(f"moved {len(every_move)} entries; every file at its new path with its bytes",
                  out=record if every_move else None)
    return 0


# --- the Hub ---------------------------------------------------------------------------------------------------------


def hub_listing(api, repo: str) -> dict:
    from huggingface_hub import RepoFile
    return {entry.path: entry for entry in api.list_repo_tree(repo, repo_type="dataset", recursive=True)
            if isinstance(entry, RepoFile)}


def identity(entry) -> tuple:
    """A Hub file's size and hash: its LFS sha256, else its git blob id."""
    lfs = entry.lfs
    sha = (lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)) if lfs else None
    return entry.size, sha or entry.blob_id


def latest_moves() -> dict[str, tuple[str, int, str]]:
    """{old path from the checkout: (new path from the checkout, size, sha256)} from every moves.tsv."""
    out = {}
    for record in sorted(BACKUPS.glob("*/moves.tsv")):
        for line in record.read_text().splitlines():
            old, new, size, digest = line.split("\t")
            if not Path(old).is_relative_to(ROOT):
                continue  # a move of a copy elsewhere (the dry rehearsal)
            out[Path(old).relative_to(ROOT).as_posix()] = (Path(new).relative_to(ROOT).as_posix(), int(size), digest)
    return out


def hub_to_local(remote: str, base: str) -> str | None:
    """A Hub path's local path from the checkout, before the move: <study>/runs/X is <base>/runs/<study>/X."""
    parts = remote.split("/")
    if len(parts) >= 3 and parts[1] in ("runs", "logs"):
        return "/".join([p for p in (base,) if p] + [parts[1], parts[0], *parts[2:]])
    if parts[0] == "apart_controls" and len(parts) > 1:  # the old apart layout: apart_controls/<run>/...
        return "/".join([p for p in (base,) if p] + ["runs", *parts])
    return None


def local_to_hub(local: str, study: str, base: str) -> str:
    rel = Path(local).relative_to(Path(base)) if base else Path(local)
    top, _, *rest = rel.parts
    return "/".join([study, top, *rest])


def hub(dry_run: bool) -> int:
    from huggingface_hub import CommitOperationDelete, HfApi

    from elicitation.publish import same
    from scripts import upload_runs_to_hf as upload
    token, _ = upload.find_token(None)
    if not token and not dry_run:
        refuse(upload.NO_TOKEN)
    api = HfApi(token=token or False)
    progress.step(f"List {upload.REPO}")
    listing = hub_listing(api, upload.REPO)
    progress.detail("files", len(listing))

    progress.step("Old study names")
    deletes = []
    for old, new in RENAMED.items():
        files = {path: entry for path, entry in listing.items() if path.startswith(f"{old}/")}
        missing = [path for path, entry in files.items()
                   if identity(listing.get(new + path[len(old):], entry)) != identity(entry)
                   or new + path[len(old):] not in listing]
        progress.detail(old, f"{len(files)} files, {len(files) - len(missing)} with a twin under {new}")
        for path in missing[:10]:
            progress.detail("", f"no twin: {path}", err=True)
        if missing:
            refuse(f"{old} holds {len(missing)} files with no twin under {new}; nothing deleted")
        deletes += list(files)
    if deletes and not dry_run:
        api.create_commit(repo_id=upload.REPO, repo_type="dataset",
                          commit_message=f"Delete {', '.join(RENAMED)}: every file has its twin under "
                                         f"{', '.join(RENAMED.values())}",
                          operations=[CommitOperationDelete(path_in_repo=path) for path in deletes])
        progress.detail("deleted", f"{len(deletes)} files")

    moves = latest_moves()
    failures = 0
    for study, base in HUB_STUDIES.items():
        progress.step(f"Check the deletes of {study}")
        root = ROOT / base if base else ROOT
        items, _ = upload.select(study, [], root)
        sent = {remote: path for files in items.values() for remote, path in files}
        old_files = [path for path in listing if path.startswith(f"{study}/") and path not in sent
                     and hub_to_local(path, base) is not None]
        orphans, twinned = [], []
        for remote in old_files:
            local_old = hub_to_local(remote, base)
            if (ROOT / local_old).is_file():
                continue  # a local file the upload leaves out (a smoke folder, a study document): it stays
            new = moves.get(local_old)
            twin = new and local_to_hub(new[0], study, base)
            if not new or twin not in sent or not same(sent[twin], listing[remote]):
                orphans.append(remote)
            else:
                twinned.append(remote)
        progress.detail("hub files", sum(1 for path in listing if path.startswith(f"{study}/")))
        progress.detail("to send", len(sent))
        progress.detail("moved", f"{len(twinned)} Hub files whose bytes moved here, each sent at its new path")
        for remote in orphans[:10]:
            progress.detail("", f"no local twin: {remote}", err=True)
        if orphans:
            failures += 1
            progress.detail("refused", f"{len(orphans)} Hub files have no moved local copy: pull them first")
            continue
        argv = [study, "--delete-stale"] + (["--dry-run"] if dry_run else [])
        if upload.main(argv, root=root) != 0:
            failures += 1
            continue
        if study == "apart_controls":
            outside = [path for path in twinned if not path.startswith(("apart_controls/runs/",
                                                                        "apart_controls/logs/"))]
            progress.detail("old layout", f"{len(outside)} files of apart_controls/<model>/ to delete")
            if outside and not dry_run:
                api.create_commit(repo_id=upload.REPO, repo_type="dataset",
                                  commit_message="apart_controls: the old layout, every file now under runs/ and logs/",
                                  operations=[CommitOperationDelete(path_in_repo=path) for path in outside])
    if failures:
        progress.failed(f"{failures} stud(ies) refused or failed")
        return 1
    progress.done("dry run: nothing changed on the Hub" if dry_run else "the Hub holds the stored layout")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.migrate_run_layout", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    move = sub.add_parser("local", help="move the run folders here")
    move.add_argument("roots", nargs="*", type=Path, default=[ROOT / "runs", ROOT / "OLD" / "runs"])
    move.add_argument("--dry-run", action="store_true")
    move.add_argument("--skip", action="append", default=[], metavar="STUDY",
                      help="a study to leave for a later pass (repeat for several)")
    hub_parser = sub.add_parser("hub", help="bring the dataset to the stored layout")
    hub_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "local":
        SKIPPED.update(args.skip)
        return local(args.roots, args.dry_run)
    return hub(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
