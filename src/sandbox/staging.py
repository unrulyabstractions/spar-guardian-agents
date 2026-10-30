"""Files written on the host for a sandbox: atomic writes, compose files, and staging tarballs with CheatBench's
ownership and times (core/stage.py).

    compose = write_compose(path, {"default": compose_service(working_dir="/workspace", image="cbtask:python311")})
    tar = stage_tar(files, out_path, mtimes=mtimes, dirs=parent_dirs(files, mtimes, stop=IMAGE_DIRS, default=now()))

Every file is written through a private temporary name and renamed into place: several task builds may run at once,
and a reader must never see a partial file.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml


def write_atomic(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def write_yaml(path: Path, document: dict) -> Path:
    """A YAML document in its own key order, written atomically."""
    return write_atomic(path, yaml.safe_dump(document, sort_keys=False).encode())


def compose_service(*, working_dir: str, image: str | None = None, **extra) -> dict:
    """One compose service without a network: an init process, an idle command and a one-second stop. `image` names a
    local image; `extra` adds or replaces keys (a build, capabilities, limits)."""
    service = {"init": True, "command": ["tail", "-f", "/dev/null"], "working_dir": working_dir,
               "network_mode": "none", "stop_grace_period": "1s"}
    if image:
        service.update({"image": image, "x-local": True})
    return {**service, **extra}


def write_compose(path: Path, services: dict[str, dict], volumes: dict | None = None) -> Path:
    """A compose file of these services and volumes, written atomically."""
    return write_yaml(path, {"services": services, **({"volumes": volumes} if volumes else {})})


def source_path(env_dir: Path, src: str) -> Path:
    """A row's source file under the build's environment directory; anything outside it or not a regular file is
    refused."""
    path = (env_dir / src).resolve()
    if not path.is_relative_to(env_dir.resolve()) or not path.is_file():
        raise ValueError(f"bad source {src!r}")
    return path


def check_destination(dest: str, *, absolute: bool) -> None:
    """A staging destination: absolute or relative as the layout wants, and never climbing with `..`."""
    if dest.startswith("/") != absolute or ".." in Path(dest).parts:
        raise ValueError(f"bad destination {dest!r}")


def parse_mtime(value) -> int:
    """CheatBench core/stage.py: 'YYYY-MM-DD[ HH:MM[:SS]]' or an epoch, read as UTC."""
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc).timestamp())
        except ValueError:
            continue
    raise ValueError(f"unparseable file_mtimes value {value!r}")


def now() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp())


@dataclass(frozen=True)
class Member:
    """One file of a staging tarball."""
    name: str                   # the path inside the archive, without a leading slash
    data: bytes
    mode: int = 0o644
    mtime: int = 0


def parent_dirs(dests: Iterable[str], mtimes: dict[str, int], *, stop: frozenset[str], default: int) -> dict[str, int]:
    """The directories a tarball has to create for `dests`, each with the newest mtime of what lies beneath it
    (`mtimes`, else `default`), walking up until a directory of `stop` (the image's own, or "." for relative
    paths)."""
    dirs: dict[str, int] = {}
    for dest in dests:
        parent = str(Path(dest).parent)
        while parent not in stop:
            dirs[parent] = max(dirs.get(parent, 0), mtimes.get(dest, default))
            parent = str(Path(parent).parent)
    return dirs


def write_tar(members: list[Member], out_path: Path, *, dirs: dict[str, int] | None = None, uid: int = 0,
              uname: str = "root") -> Path:
    """A gzipped tarball of `dirs` ({path: mtime}, created 0755, shallowest first) and `members`, everything owned by
    uid/gid `uid` as `uname`, written atomically."""
    def owned(info: tarfile.TarInfo, mode: int, mtime: int) -> tarfile.TarInfo:
        info.mode, info.mtime = mode, mtime
        info.uid = info.gid = uid
        info.uname = info.gname = uname
        return info

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for d in sorted(dirs or {}, key=lambda p: (p.count("/"), p)):
            info = tarfile.TarInfo(d.lstrip("/"))
            info.type = tarfile.DIRTYPE
            tar.addfile(owned(info, 0o755, dirs[d]))
        for m in members:
            info = tarfile.TarInfo(m.name.lstrip("/"))
            info.size = len(m.data)
            tar.addfile(owned(info, m.mode, m.mtime), io.BytesIO(m.data))
    return write_atomic(out_path, buffer.getvalue())


def stage_tar(files: dict[str, bytes], out_path: Path, *, mtimes: dict[str, int] | None = None,
              modes: dict[str, int] | None = None, dirs: dict[str, int] | None = None, uid: int = 0,
              uname: str = "root") -> Path:
    """A staging tarball of `files` ({destination: bytes}): each file 0644 unless `modes` says otherwise, its mtime
    now unless `mtimes` says otherwise, and the directories `dirs` (parent_dirs) created first."""
    when = now()
    members = [Member(dest, files[dest], (modes or {}).get(dest, 0o644), (mtimes or {}).get(dest, when))
               for dest in sorted(files)]
    return write_tar(members, out_path, dirs=dirs, uid=uid, uname=uname)
