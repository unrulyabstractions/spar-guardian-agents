"""The build's rows read with their paths at the folders a run stages.

The build's rows and files hold CheatBench's two folders (CHEATBENCH_FOLDERS). A row is read with every path moved to
the folders a run stages: FOLDERS for a new run, the run's record for a stored one (folders_of). The paths inside a
staged file move with them (moved_bytes). The trees, the files, their modes and the deal stay as CheatBench's.

    row = load_row("alfalfa_chi", FOLDERS)      # its keys, values and instruction at FOLDERS, row["folders"] says so
    data = built(row, row["payload_file"])      # a staged file's bytes as the build wrote them
"""
from __future__ import annotations

import hashlib
import re

from src.domains.protein._protein_settings import BUILD, CHEATBENCH_FOLDERS, Folders
from src.sandbox import cheatbench_task_files as task_files

BUILT = {CHEATBENCH_FOLDERS.plant: "plant", CHEATBENCH_FOLDERS.project: "project"}     # the build's folders, by field
BUILT_PATH = re.compile("|".join(re.escape(path) for path in BUILT) + r"(?![\w.-])")
BUILT_BYTES = re.compile(BUILT_PATH.pattern.encode())


def moved(text: str, folders: Folders) -> str:
    """The text with the build's two folders at `folders`."""
    return BUILT_PATH.sub(lambda m: getattr(folders, BUILT[m.group()]), text)


def moved_bytes(data: bytes, folders: Folders) -> bytes:
    """A staged file's bytes with the build's two folders at `folders`."""
    return BUILT_BYTES.sub(lambda m: getattr(folders, BUILT[m.group().decode()]).encode(), data)


def moved_row(row: dict, folders: Folders) -> dict:
    """A build row with every path at `folders`: its keys and its values, the work dir, the staged files'
    destinations and the payload's, and the instruction. Its "folders" records them."""
    def move(value):
        if isinstance(value, str):
            return moved(value, folders)
        if isinstance(value, dict):
            return {move(key): move(item) for key, item in value.items()}
        return [move(item) for item in value] if isinstance(value, list) else value
    return {**move(row), "folders": folders.record()}


def load_row(row_id: str, folders: Folders, hint: str = "") -> dict:
    """A row of the build by its id, its paths at `folders` (moved_row)."""
    return moved_row(task_files.load_row(BUILD, row_id, hint=hint), folders)


def built(row: dict, dest: str) -> bytes:
    """A staged file's bytes as the build wrote them (build/protein_design/environment/), at the build's folders."""
    return (BUILD / "environment" / row["files_abs"][dest]).read_bytes()


def staged_mtimes(row: dict, files: dict[str, bytes]) -> dict[str, int | None]:
    """Each staged file's time as an epoch: the row's, or the candidates file's for a file the row does not date."""
    mtimes = task_files.row_mtimes(row)
    return {dest: mtimes.get(dest, mtimes.get(row["payload_file"])) for dest in files}


def sha256s(files: dict[str, bytes]) -> dict[str, str]:
    """{path: sha256} of staged files, as run.json records them (protein.agents.<agent>.staged, side_copies)."""
    return {dest: hashlib.sha256(data).hexdigest() for dest, data in files.items()}
