"""Prove that every file a box made has a local copy, before the box is destroyed.

    scripts/box/capture_check.sh <instance-id> [--pull] [--accept-docker REASON]
    uv run python -m scripts.box.box_capture check <instance-id> [--pull] [--accept-docker REASON]
    uv run python -m scripts.box.box_capture baseline <instance-id>

check sends box_sweep.py to the box over ssh (logs/boxes/<id>/ssh_config) and holds each line of its sweep against
this machine. A box file under the clone's runs/, logs/ or out/ must be at the same path in this repository, where
watch.sh brings it, or under logs/boxes/<id>/conflicts/ when this machine's copy is newer and differs; any other box
file must be at logs/boxes/<id>/files/<its absolute path>. A file is captured when its size and sha256 match, a link
when the local link has the same target. A file outside the clone that the host image held at first contact (the same
path and bytes, in logs/boxes/<id>/baseline.jsonl) is counted and not required; the baseline never covers the clone.
The check fails on a file missing or different, a path the sweep could not read, a process still producing, a link to
something outside the swept roots, Docker holding containers or volumes (unless --accept-docker gives the reason they
may go), Docker's state unknown, a box whose box_id file names another instance, the clone at a commit origin/main
lacks, a submodule away from the commit the repository records, a sweep cut short, or a sweep that found nothing. It
deletes the old marker and manifest first. When it passes it writes logs/boxes/<id>/manifest.jsonl (every swept file:
its box path, size, sha256 and local copy) and then logs/boxes/<id>/capture.json; fleet.py destroy requires that
marker, fresh, and makes a verified read-only snapshot of the manifest's local copies before it destroys.

--pull first fetches what is missing or different with rsync (Homebrew's: the Mac's /usr/bin/rsync is openrsync) over
the same ssh config, comparing checksums: the outputs into this repository with --update, as watch.sh does, so a newer
local file is never overwritten, then the outputs still different into logs/boxes/<id>/conflicts/; the rest into
logs/boxes/<id>/files/. It then sweeps the box again and holds the new sweep to the same rules. Files under a box run's
runs/<name>/ or out/<name>/ that the box does not have are listed as local only; they do not fail the check.

baseline records the sweep of the box's home and /tmp before box_setup.sh changes anything. It runs once per box: a
later call keeps the first, and a box that already holds the clone or the setup's logs is refused (exit 3), since its
baseline would take the screen's files for the host image's; the capture then requires the host's files too.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scripts.box.fleet_layout import BOXES, REMOTE_REPO, ROOT

SWEEP = Path(__file__).with_name("box_sweep.py")
OUTPUT_DIRS = ("runs", "logs", "out")
HOME, TMP = "/root", "/tmp"
"""The box's home and /tmp, swept with the clone at REMOTE_REPO."""
SETUP_FILES = ("setup.log", "screen.log", "screen.pid")
"""What box_setup.sh and box_run.sh write in the box's home: a baseline that holds one is not of first contact."""
SWEEP_TIMEOUT_S = 3600
SNAPSHOT_MARGIN_BYTES = 5 * 10**9
HOMEBREW_RSYNC = "/opt/homebrew/bin/rsync"


def box_dir(instance_id: int) -> Path:
    return BOXES / str(instance_id)


def marker_path(instance_id: int) -> Path:
    return box_dir(instance_id) / "capture.json"


def manifest_path(instance_id: int) -> Path:
    return box_dir(instance_id) / "manifest.jsonl"


def read_marker(instance_id: int) -> dict | None:
    path = marker_path(instance_id)
    return json.loads(path.read_text()) if path.exists() else None


def ssh_config(instance_id: int) -> Path:
    config = box_dir(instance_id) / "ssh_config"
    if not config.exists():
        raise FileNotFoundError(f"{config} is missing: run `uv run python -m scripts.box.fleet status` first")
    if any(c.isspace() for c in str(config)):
        raise ValueError(f"{config}: rsync's -e cannot take a path with whitespace")
    return config


@functools.cache
def rsync_binary() -> str:
    """Homebrew's rsync when it is there, else the rsync on PATH; refused unless it is rsync 3 (the Mac's
    /usr/bin/rsync is openrsync, which lacks flags the capture and the watch use)."""
    binary = HOMEBREW_RSYNC if os.access(HOMEBREW_RSYNC, os.X_OK) else shutil.which("rsync")
    if not binary:
        raise FileNotFoundError("no rsync: brew install rsync")
    first = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.splitlines()[:1]
    if not first or "version 3." not in first[0]:
        raise FileNotFoundError(f"{binary} is not rsync 3 ({first}): brew install rsync")
    return binary


def under_repo(box_path: str) -> bool:
    return box_path == REMOTE_REPO or box_path.startswith(REMOTE_REPO + "/")


def output_rel(box_path: str) -> str | None:
    """The path under the clone of a box file in runs/, logs/ or out/; None for any other file."""
    if not under_repo(box_path):
        return None
    rel = os.path.relpath(box_path, REMOTE_REPO)
    top, _, rest = rel.partition("/")
    return rel if top in OUTPUT_DIRS and rest else None


def local_path(box_path: str, folder: Path) -> Path:
    """Where this machine keeps a box file: the same path in this repository for an output, else
    <folder>/files/<its absolute path>."""
    rel = output_rel(box_path)
    return ROOT / rel if rel else folder / "files" / box_path.lstrip("/")


def local_paths(box_path: str, folder: Path) -> list[Path]:
    """The local copies that capture a box file: local_path, then for an output its copy under <folder>/conflicts/,
    where --pull puts the box's bytes when this machine's file is newer and differs."""
    rel = output_rel(box_path)
    return [local_path(box_path, folder), *([folder / "conflicts" / rel] if rel else [])]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Report:
    """A sweep held against this machine. swept = captured + image + lost + the excluded files (+ links outside the
    roots, each also a problem)."""

    swept: int = 0
    captured: int = 0
    image: int = 0
    missing: list[tuple[str, str]] = field(default_factory=list)
    """(box path, what it is) of each file with no local copy."""
    differ: list[tuple[str, str]] = field(default_factory=list)
    """(box path, how it differs) of each file whose local copy differs."""
    problems: list[str] = field(default_factory=list)
    excluded: dict[str, dict] = field(default_factory=dict)
    trees: list[dict] = field(default_factory=list)
    special: list[str] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    """The manifest: one line per swept file or link with its local copy."""
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    """(box path, local copy) of each output captured under conflicts/, its repository copy different."""
    docker: dict | None = None
    docker_accepted: dict | None = None
    box_id: str | None = None
    local_only: list[str] = field(default_factory=list)

    @property
    def lost(self) -> int:
        return len(self.missing) + len(self.differ)

    @property
    def ok(self) -> bool:
        return not self.lost and not self.problems and self.swept > 0

    def summary(self) -> str:
        excluded = sum(n["files"] for n in self.excluded.values())
        return (f"swept {self.swept} files: {self.captured} captured ({len(self.conflicts)} under conflicts/), "
                f"{self.image} of the host image, {excluded} excluded as reproducible, {len(self.missing)} missing, "
                f"{len(self.differ)} different")


def docker_problem(state: dict) -> str:
    names = [*state["containers"], *(f"volume {v}" for v in state["volumes"])]
    return (f"Docker holds {len(state['running'])} running and {len(state['containers'])} containers in all, "
            f"{len(state['volumes'])} volumes ({'; '.join(names[:8])}): a hung or stopped run's containers and "
            f"volumes hold its workspace, which the sweep cannot see. Save what matters under /root (docker stop each, "
            f"then cp -a its GraphDriver.Data.UpperDir and each volume's Mountpoint into /root/docker_saved/), pull "
            f"it with --pull, and pass --accept-docker REASON")


def compare(lines: list[dict], locals_for: Callable[[str], list[Path]], baseline: frozenset = frozenset(),
            on_origin: Callable[[str], bool] = lambda sha: False, instance_id: int | None = None,
            accept_docker: str | None = None) -> Report:
    """Hold a sweep's lines against the local copies that locals_for names (the first is the file's own place)."""
    report, done, docker_seen, box_id_seen = Report(), False, False, False
    for line in lines:
        if "file" in line:
            report.swept += 1
            path, size, digest = line["file"], line["size"], line["sha256"]
            if not under_repo(path) and (path, size, digest) in baseline:
                report.image += 1
                report.entries.append({"box": path, "size": size, "sha256": digest, "kind": "image"})
                continue
            candidates = locals_for(path)
            present = [p for p in candidates if p.is_file() and not p.is_symlink()]
            match = next((p for p in present if p.stat().st_size == size and sha256(p) == digest), None)
            if match is not None:
                report.captured += 1
                if match != candidates[0]:
                    report.conflicts.append((path, str(match)))
                report.entries.append({"box": path, "size": size, "sha256": digest, "kind": "file",
                                       "local": str(match)})
            elif present:
                report.differ.append((path, f"box {size} bytes, here {present[0].stat().st_size}"))
            else:
                report.missing.append((path, f"{size} bytes"))
        elif "link" in line or "outside_link" in line:
            report.swept += 1
            path, target = line.get("link") or line["outside_link"], line["target"]
            if not under_repo(path) and (path, "link", target) in baseline:
                report.image += 1
                report.entries.append({"box": path, "target": target, "kind": "image"})
                continue
            if "outside_link" in line:
                report.problems.append(f"{path} links to {line['resolves']}, outside the swept roots: what it points "
                                       f"at is never swept")
                continue
            local = locals_for(path)[0]
            if local.is_symlink() and os.readlink(local) == target:
                report.captured += 1
                report.entries.append({"box": path, "target": target, "kind": "link", "local": str(local)})
            elif local.is_symlink() or local.exists():
                report.differ.append((path, f"a link to {target}"))
            else:
                report.missing.append((path, f"a link to {target}"))
        elif "excluded" in line:
            report.excluded[line["excluded"]] = {"files": line["files"], "bytes": line["bytes"]}
            report.swept += line["files"]
        elif "tree" in line:
            report.trees.append(line)
            if line["recorded"] is None and not on_origin(line["head"]):
                report.problems.append(f"the clone {line['tree']} is at {line['head']}, which origin/main lacks: its "
                                       f"tracked files are not provably elsewhere")
            elif line["recorded"] is not None and line["head"] != line["recorded"]:
                report.problems.append(f"{line['tree']} is at {line['head']}, the repository records "
                                       f"{line['recorded']}")
        elif "unreadable" in line:
            report.problems.append(f"could not read {line['unreadable']}: {line['error']}")
        elif "producing" in line:
            writing = f", writing {', '.join(line['writing'])}" if line.get("writing") else ""
            report.problems.append(f"still producing: pid {line['producing']} {line['command']}{writing}")
        elif "special" in line:
            report.special.append(f"{line['special']} ({line['mode']})")
        elif "docker" in line:
            docker_seen, report.docker = True, line["docker"]
            if line["docker"] is None:
                report.problems.append(f"cannot tell what Docker holds ({line.get('error')}): a container or volume "
                                       f"may hold the only copy of a run's workspace")
            elif line["docker"]["containers"] or line["docker"]["volumes"]:
                if accept_docker:
                    report.docker_accepted = {"reason": accept_docker, "state": line["docker"]}
                else:
                    report.problems.append(docker_problem(line["docker"]))
        elif "box_id" in line:
            box_id_seen, report.box_id = True, line["box_id"]
            if instance_id is not None and line["box_id"] != str(instance_id):
                report.problems.append(f"the box that answered names itself {line['box_id']!r} in /root/box_id, not "
                                       f"{instance_id}: the endpoint reaches another box, or box_setup.sh never ran")
        elif "done" in line:
            done = True
    if not done:
        report.problems.append("the sweep was cut short: no done line")
    if done and not docker_seen:
        report.problems.append("the sweep said nothing of Docker")
    if done and instance_id is not None and not box_id_seen:
        report.problems.append("the sweep said nothing of the box's id")
    if report.swept == 0:
        report.problems.append("the sweep found no files: an empty sweep is a failed check")
    return report


def local_only(lines: list[dict]) -> list[str]:
    """Files under a box run's runs/<name>/ or out/<name>/ here that the box does not have: an earlier export's
    leftovers, or what this machine added (a judge, a figure)."""
    on_box = {line.get("file") or line.get("link") or line.get("outside_link") for line in lines}
    folders = set()
    for path in filter(None, on_box):
        parts = (output_rel(path) or "").split("/")
        if len(parts) >= 3 and parts[0] in ("runs", "out"):
            folders.add((parts[0], parts[1]))
    extras = []
    for top, name in sorted(folders):
        for dirpath, _, filenames in os.walk(ROOT / top / name):
            for filename in filenames:
                rel = (Path(dirpath) / filename).relative_to(ROOT).as_posix()
                if f"{REMOTE_REPO}/{rel}" not in on_box:
                    extras.append(rel)
    return sorted(extras)


def remote_sweep(config: Path, *args: str) -> list[dict]:
    """Run box_sweep.py on the box with the box's python3, the script on ssh's standard input."""
    with open(SWEEP, "rb") as script:
        result = subprocess.run(["ssh", "-F", str(config), "box", "python3", "-", *args], stdin=script,
                                capture_output=True, timeout=SWEEP_TIMEOUT_S)
    lines = [json.loads(text) for text in result.stdout.decode().splitlines() if text.strip()]
    if result.returncode != 0:
        lines.append({"unreadable": "(the sweep)", "error": f"exit {result.returncode}: "
                                                            f"{result.stderr.decode(errors='replace')[-500:]}"})
    return lines


def on_origin(sha: str) -> bool:
    """Whether origin/main, fetched now, holds the commit."""
    if subprocess.run(["git", "-C", str(ROOT), "fetch", "--quiet", "origin"]).returncode != 0:
        return False
    return subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", sha, "origin/main"],
                          capture_output=True).returncode == 0


def read_baseline(instance_id: int) -> frozenset:
    """The host image's files and links at first contact, outside the clone."""
    path = box_dir(instance_id) / "baseline.jsonl"
    if not path.exists():
        return frozenset()
    entries = set()
    for line in (json.loads(text) for text in path.read_text().splitlines() if text.strip()):
        if "file" in line and not under_repo(line["file"]):
            entries.add((line["file"], line["size"], line["sha256"]))
        elif ("link" in line or "outside_link" in line):
            link = line.get("link") or line["outside_link"]
            if not under_repo(link):
                entries.add((link, "link", line["target"]))
    return frozenset(entries)


def rsync(config: Path, names: list[str], source: str, target: Path, extra: list[str]) -> None:
    """rsync exactly these names (relative to source) from the box into target, comparing checksums."""
    if not names:
        return
    target.mkdir(parents=True, exist_ok=True)
    code = subprocess.run([rsync_binary(), "-a", "--checksum", *extra, "--from0", "--files-from=-", "-e",
                           f"ssh -F {config}", source, f"{target}/"], input="\0".join(names).encode()).returncode
    if code not in (0, 24):
        print(f"!! rsync into {target} exited {code}; the sweep after it shows what is still missing")


def fetch(config: Path, box_paths: list[str], folder: Path) -> None:
    """rsync these box files down: outputs into this repository with --update, the rest under folder/files/."""
    outputs = [rel for rel in map(output_rel, box_paths) if rel]
    others = [path.lstrip("/") for path in box_paths if not output_rel(path)]
    rsync(config, outputs, f"box:{REMOTE_REPO}/", ROOT, ["--update"])
    rsync(config, others, "box:/", folder / "files", [])


def fetch_conflicts(config: Path, box_paths: list[str], folder: Path) -> None:
    """rsync these box outputs into folder/conflicts/, beside this machine's newer copies, which stay as they are."""
    rsync(config, [rel for rel in map(output_rel, box_paths) if rel], f"box:{REMOTE_REPO}/", folder / "conflicts", [])


def run_check(instance_id: int, pull: bool, accept_docker: str | None = None) -> tuple[Report, dict | None]:
    """The capture check: its report, and the marker it wrote when it passed, else None."""
    marker, manifest = marker_path(instance_id), manifest_path(instance_id)
    marker.unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)
    config = ssh_config(instance_id)
    folder = box_dir(instance_id)
    baseline = read_baseline(instance_id)

    def sweep_and_compare() -> tuple[list[dict], Report]:
        lines = remote_sweep(config, "sweep", REMOTE_REPO, HOME, TMP)
        return lines, compare(lines, lambda path: local_paths(path, folder), baseline, on_origin, instance_id,
                              accept_docker)

    lines, report = sweep_and_compare()
    if pull and report.lost:
        paths = [path for path, _ in report.missing + report.differ]
        print(f"== pulling {len(paths)} files")
        fetch(config, paths, folder)
        lines, report = sweep_and_compare()
        newer = [path for path, _ in report.differ if output_rel(path)]
        if newer:
            print(f"== {len(newer)} outputs differ and this machine's copy is newer: the box's bytes go to "
                  f"{folder / 'conflicts'}")
            fetch_conflicts(config, newer, folder)
            lines, report = sweep_and_compare()
    report.local_only = local_only(lines)
    for kind, entries in (("MISSING", report.missing), ("DIFFERS", report.differ)):
        for path, detail in entries[:50]:
            print(f"   {kind}  {path} ({detail})")
    if max(len(report.missing), len(report.differ)) > 50:
        print("   (the first 50 of each)")
    for path, copy in report.conflicts:
        print(f"!! CONFLICT {path}: this machine's {local_path(path, folder)} differs; the box's bytes are at {copy}")
    for problem in report.problems:
        print(f"!! {problem}")
    for reason, n in report.excluded.items():
        print(f"   excluded: {n['files']} files, {n['bytes'] / 1e6:.1f} MB: {reason}")
    for rel in report.local_only[:20]:
        print(f"   LOCAL ONLY {rel}")
    if report.docker_accepted:
        print(f"   Docker's containers and volumes accepted as lost: {report.docker_accepted['reason']}")
    print(f"   {len(report.special)} special files, {len(report.local_only)} files local only, Docker "
          f"{report.docker}, baseline of {len(baseline)} host files")
    print(f"== {instance_id}: {report.summary()}")
    if not report.ok:
        print("== the capture check fails: nothing may destroy this box")
        return report, None
    manifest.write_text("".join(json.dumps(entry) + "\n" for entry in report.entries))
    record = {"id": instance_id, "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "epoch": time.time(), "swept": report.swept, "captured": report.captured, "image": report.image,
              "lost": report.lost, "excluded": report.excluded, "trees": report.trees,
              "special": len(report.special), "docker": report.docker, "docker_accepted": report.docker_accepted,
              "box_id": report.box_id, "conflicts": report.conflicts, "local_only": len(report.local_only),
              "manifest": {"path": str(manifest), "lines": len(report.entries), "sha256": sha256(manifest)}}
    marker.write_text(json.dumps(record, indent=1) + "\n")
    print(f"== the capture check passes; manifest {manifest}, marker {marker}")
    return report, record


def check(instance_id: int, pull: bool, accept_docker: str | None = None) -> dict | None:
    """The capture check; the marker it wrote when it passed, else None."""
    return run_check(instance_id, pull, accept_docker)[1]


def snapshot(instance_id: int) -> dict:
    """A read-only gzipped tar of the local copies the manifest names, at their box paths, read back and held to the
    manifest file by file: what the box made, kept apart from the shared working tree's runs/ and logs/."""
    folder, manifest = box_dir(instance_id), manifest_path(instance_id)
    marker = read_marker(instance_id)
    if not marker or not manifest.exists() or sha256(manifest) != marker["manifest"]["sha256"]:
        raise RuntimeError(f"{manifest} is missing or is not the one the capture marker records")
    entries = [json.loads(text) for text in manifest.read_text().splitlines() if text.strip()]
    kept = [entry for entry in entries if entry["kind"] in ("file", "link")]
    size = sum(entry.get("size", 0) for entry in kept)
    free = shutil.disk_usage(folder).free
    if free < size + SNAPSHOT_MARGIN_BYTES:
        raise RuntimeError(f"{free / 1e9:.1f} GB free, the snapshot needs {size / 1e9:.1f} GB and a margin of "
                           f"{SNAPSHOT_MARGIN_BYTES / 1e9:.0f} GB")
    target, temporary = folder / "snapshot.tar.gz", folder / "snapshot.tar.gz.tmp"
    temporary.unlink(missing_ok=True)
    with tarfile.open(temporary, "w:gz") as tar:
        for entry in kept:
            tar.add(entry["local"], arcname=entry["box"].lstrip("/"), recursive=False)
    held = {}
    with tarfile.open(temporary, "r:gz") as tar:
        for member in tar:
            if member.isfile():
                digest = hashlib.sha256()
                with tar.extractfile(member) as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                held[member.name] = (member.size, digest.hexdigest())
            elif member.issym():
                held[member.name] = ("link", member.linkname)
    wrong = [entry["box"] for entry in kept if held.get(entry["box"].lstrip("/")) !=
             ((entry["size"], entry["sha256"]) if entry["kind"] == "file" else ("link", entry["target"]))]
    if wrong or len(held) != len(kept):
        temporary.unlink()
        raise RuntimeError(f"the snapshot does not hold the manifest: {len(wrong)} files differ ({wrong[:5]}), "
                           f"{len(held)} members for {len(kept)} entries")
    os.replace(temporary, target)
    target.chmod(0o444)
    record = {"path": str(target), "bytes": target.stat().st_size, "sha256": sha256(target), "files": len(kept),
              "manifest_sha256": marker["manifest"]["sha256"]}
    (folder / "snapshot.json").write_text(json.dumps(record, indent=1) + "\n")
    print(f"== snapshot {target}: {len(kept)} files read back and matched to the manifest, "
          f"{record['bytes'] / 1e6:.1f} MB")
    return record


def baseline(instance_id: int) -> int:
    """0 when the baseline is recorded or kept, 1 when its sweep failed, 3 when the box is past first contact."""
    path = box_dir(instance_id) / "baseline.jsonl"
    if path.exists():
        print(f"== kept the baseline of first contact, {path}")
        return 0
    lines = remote_sweep(ssh_config(instance_id), "baseline", HOME, TMP)
    if not any("done" in line for line in lines) or any("unreadable" in line for line in lines):
        print("!! the baseline sweep failed: " + "; ".join(str(line) for line in lines if "unreadable" in line))
        return 1
    late = sorted({p for line in lines for p in [line.get("file") or line.get("link") or line.get("outside_link")]
                   if p and (under_repo(p) or p in {f"{HOME}/{name}" for name in SETUP_FILES})})
    if late:
        print(f"refused: the box is past first contact ({len(late)} files of the clone or the setup, such as "
              f"{late[0]}): a baseline now would count the screen's files as the host image's. No baseline is "
              f"recorded, so the capture requires the host image's files as well")
        return 3
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    print(f"== baseline: {sum('file' in line for line in lines)} host files, {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "baseline"])
    parser.add_argument("id", type=int)
    parser.add_argument("--pull", action="store_true", help="fetch what is missing or different, then sweep again")
    parser.add_argument("--accept-docker", metavar="REASON",
                        help="let Docker's containers and volumes go with the box, for this recorded reason")
    args = parser.parse_args(argv)
    try:
        if args.command == "baseline":
            return baseline(args.id)
        return 0 if check(args.id, args.pull, args.accept_docker) else 1
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"!! {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
