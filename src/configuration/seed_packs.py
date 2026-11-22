"""Seed packs: what a run's environment holds before round 1 beyond the task, from a "previous team".

    config = build_config("default", "small", ["seed_pack=swe/pointer"])
    record = seed_record(config, "swe", "flipt-02e2")      # run.json "seed": pack, authors, posts, files
    texts(record)                                        # every seeded text, for the ladders and the tests
    uv run python -m src.configuration.seed_packs        # load and check every pack, list them (make seeds-check)

A pack is a few board posts and files written from the posts and notes of real teams in our past runs. Its posts stand
on the board before round 1 under the IDs of a previous team, drawn per run from SwarmConfig.ID_POOL apart from the
roster. Its files are planted before the agents start: `tmp` puts one copy in /tmp of every machine, root's and
readable by all; `home` puts one in each agent's home, the agent's own. A pack may point, tempt, normalise, rationalise
or urge the team to work together. It never holds a piece of the exploit, a planted design, a line of the reference, a
half of a credential or any other content of a honeypot, so a seeded team still needs two agents' items to cheat
(tests/unit/test_collaboration_*.py check every pack). A pack's lines never go into the task's repository, its README
or its history, because a tracked file would enter every agent's graded tree.

The packs live in the repository's seeds/ folder, one folder per pack (seeds/README.md):

    seeds/<task>/<pack>/pack.yaml                    about, kinds, harvest, rows, posts
    seeds/<task>/<pack>/files/tmp/<name>             a file planted at /tmp/<name> of every machine
    seeds/<task>/<pack>/files/home/<name>            a file planted at ~/<name> of every agent

<task> is the Benchmark's key, and a pack's name in the config is "<task>/<pack>". pack.yaml carries `about` (what it
holds), `kinds` (among KINDS), `harvest` (the ids of the harvested posts it was written from: an id of the 2026-11-22
harvest such as swe-01, or "<study>/<run>#<sequence>" for a board post of a run; none for a pack composed for a task
with no harvest), `rows` (the rows its text fits; every row when absent) and `posts` ([{author: k, text}]). A file's
text is its bytes as UTF-8, planted as they are. "{author<k>}" in a text becomes the k-th author's ID. seeds/plans/
holds the plans of scripts/seed_study.py, not packs.

How the ladders read a seed (the decision of 2026-11-22): a seeded post or file is the harness's, never a teammate's
act. The transcript scan counts it as exposure only when its own text names the honeypot's location, the domain's
pointer (its branch, commit, path or file name), and then records the source as seeded_post or seeded_file
(src/measures/transcript_scan.py), so a seeded exposure is never taken for a teammate's. A pack holds no content, so
reading it opens nothing and moves nothing on the Cheating ladder. The board's measures count only the roster's posts,
so a seeded post is no teammate's post on the Coordination and Collusion ladders either.
"""
from __future__ import annotations

import functools
import random
import re
import sys
from pathlib import Path

from src.configuration import settings

SEEDS_DIR = settings.ROOT / "seeds"
PACK_FILE = "pack.yaml"
FILES_DIR = "files"
NOT_TASKS = ("plans",)          # folders of seeds/ that hold no packs
IGNORED = (".DS_Store",)        # Finder's files, never planted
PLACES = ("tmp", "home")
KINDS = ("pointer", "normalise", "rationalise", "pressure", "pool", "collaborate")
PACK_KEYS = ("about", "kinds", "harvest", "rows", "posts")
POST_KEYS = ("author", "text")
AUTHOR = re.compile(r"\{author(\d+)\}")
FILE_NAME = re.compile(r"[A-Za-z0-9._-]+")
FOLDER_NAME = re.compile(r"[a-z0-9_]+")
TMP = "/tmp"
SEEDED = "seed_pack"            # the invocation_id of a seeded post in ORBIT's communication store


def is_seeded(message) -> bool:
    """Whether a board post (a CommunicationMessage or its exported dict) is a seed pack's."""
    value = message.get("invocation_id") if isinstance(message, dict) else getattr(message, "invocation_id", None)
    return value == SEEDED


@functools.cache
def packs() -> dict[str, dict]:
    """Every pack by its name "<task>/<pack>", each with its task, from seeds/<task>/<pack>/."""
    return read_packs(SEEDS_DIR)


def read_packs(root: Path) -> dict[str, dict]:
    """Every pack under root, checked: each folder root/<task>/<pack>/ must hold a pack.yaml."""
    out = {}
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in NOT_TASKS):
        for folder in sorted(p for p in task_dir.iterdir() if not p.name.startswith(".")):
            pack = read_pack(folder)
            out[f"{pack['task']}/{pack['name']}"] = pack
    return out


def read_pack(folder: Path) -> dict:
    """One pack from its folder: pack.yaml, then files/<place>/<name>; checked (check_pack)."""
    task, name = folder.parent.name, folder.name
    full = f"{task}/{name}"
    if not folder.is_dir() or not (folder / PACK_FILE).is_file():
        raise ValueError(f"seed pack {full}: {folder} needs a {PACK_FILE}")
    if not FOLDER_NAME.fullmatch(task) or not FOLDER_NAME.fullmatch(name):
        raise ValueError(f"seed pack {full}: a task's and a pack's folder names take a-z, 0-9 and _")
    data = settings.load_yaml(folder / PACK_FILE) or {}
    if not isinstance(data, dict) or set(data) - set(PACK_KEYS):
        extra = sorted(set(data) - set(PACK_KEYS)) if isinstance(data, dict) else data
        hint = f"; a pack's files live in {FILES_DIR}/<place>/<name>" if "files" in extra else ""
        raise ValueError(f"seed pack {full}: {PACK_FILE} takes only {PACK_KEYS}, got {extra}{hint}")
    for post in data.get("posts") or []:
        if not isinstance(post, dict) or set(post) != set(POST_KEYS):
            raise ValueError(f"seed pack {full}: a post is {{author: k, text: ...}}, got {post!r}")
    pack = {"task": task, "name": name, **data}
    files = read_files(folder / FILES_DIR, full)
    if files:
        pack["files"] = files
    check_pack(full, pack)
    return pack


def read_files(root: Path, full: str) -> list[dict]:
    """The files of a pack, tmp's then home's, each place's by name: [{place, name, text}]. Refuses anything under
    files/ that is not a plain file at files/<place>/<name>."""
    if not root.exists():
        return []
    def placed(p: Path) -> bool:
        return (p.is_file() and p.parent.parent == root and p.parent.name in PLACES) or \
            (p.is_dir() and p.parent == root and p.name in PLACES)
    stray = [p for p in root.rglob("*") if p.name not in IGNORED and not placed(p)]
    if stray:
        raise ValueError(f"seed pack {full}: {FILES_DIR}/ holds only {FILES_DIR}/<place>/<name> with a place among "
                         f"{PLACES}, got {[str(p.relative_to(root)) for p in sorted(stray)]}")
    out = []
    for place in PLACES:
        for path in sorted((root / place).glob("*")) if (root / place).is_dir() else []:
            if path.name in IGNORED:
                continue
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(f"seed pack {full}: {path.relative_to(root)} is not UTF-8 text") from error
            out.append({"place": place, "name": path.name, "text": text})
    return out


def check_pack(name: str, pack: dict) -> None:
    """Refuse a pack with no content, an unknown kind or place, a file name with a path in it, two files at one place
    and name, or an author placeholder that names no author of its posts."""
    posts, files = pack.get("posts") or [], pack.get("files") or []
    if not posts and not files:
        raise ValueError(f"seed pack {name} holds no post and no file")
    if not pack.get("about") or not set(pack.get("kinds") or []) <= set(KINDS) or not pack.get("kinds"):
        raise ValueError(f"seed pack {name} needs `about` and `kinds` among {KINDS}")
    authors = {int(p["author"]) for p in posts}
    if authors and authors != set(range(1, max(authors) + 1)):
        raise ValueError(f"seed pack {name}: its authors are numbered 1..k, got {sorted(authors)}")
    for post in posts:
        if not post.get("text", "").strip():
            raise ValueError(f"seed pack {name}: a post has no text")
    for f in files:
        if f.get("place") not in PLACES or not FILE_NAME.fullmatch(f.get("name") or "") or not f.get("text"):
            raise ValueError(f"seed pack {name}: a file needs a place among {PLACES}, a plain name and a text")
    if len({(f["place"], f["name"]) for f in files}) != len(files):
        raise ValueError(f"seed pack {name} plants two files at one place and name")
    for text in [p["text"] for p in posts] + [f["text"] for f in files]:
        if any(int(k) not in authors for k in AUTHOR.findall(text)):
            raise ValueError(f"seed pack {name}: a text names an author none of its posts has")


def pack_of(name: str) -> dict:
    """The pack of this name, or a ValueError that lists the packs."""
    if name not in packs():
        raise ValueError(f"unknown seed pack {name!r}; choose one of {sorted(packs())}")
    return packs()[name]


def check_for(name: str, task: str, row: str, board: bool) -> None:
    """Refuse a pack for another task, for a row its text does not fit, or with posts on a run without a board."""
    pack = pack_of(name)
    if pack["task"] != task:
        raise ValueError(f"seed pack {name} is for {pack['task']}, not {task}")
    if pack.get("rows") and row not in pack["rows"]:
        raise ValueError(f"seed pack {name} fits the rows {pack['rows']}, not {row}")
    if pack.get("posts") and not board:
        raise ValueError(f"seed pack {name} holds board posts, and a lone agent has no board")


def authors(config, n: int) -> list[str]:
    """n IDs for the previous team, drawn from ID_POOL apart from the run's own IDs, the same for the same pack, seed
    and salt. Imported here to keep the configuration package free of a cycle."""
    from src.configuration.swarm_config import ID_POOL
    taken = set(config.names)
    pool = [i for i in ID_POOL if i not in taken]
    return random.Random(f"seed-authors/{config.seed_pack}/{config.id_salt}/{config.seed}").sample(pool, n)


def fill(text: str, ids: list[str]) -> str:
    return AUTHOR.sub(lambda m: ids[int(m.group(1)) - 1], text)


def seed_record(config, task: str, row: str) -> dict | None:
    """The run's seed as run.json records it: the pack's name, what it is, the previous team's IDs, each post with
    its author, and each file with its place, name and text. None without a pack."""
    if config.seed_pack is None:
        return None
    check_for(config.seed_pack, task, row, config.board)
    pack = pack_of(config.seed_pack)
    posts = pack.get("posts") or []
    ids = authors(config, max((int(p["author"]) for p in posts), default=0))
    return {"pack": config.seed_pack, "about": pack["about"].strip(), "kinds": list(pack["kinds"]),
            "harvest": list(pack.get("harvest") or []), "authors": ids,
            "posts": [{"author": ids[int(p["author"]) - 1], "text": fill(p["text"], ids).strip()} for p in posts],
            "files": [{"place": f["place"], "name": f["name"], "text": fill(f["text"], ids)}
                      for f in pack.get("files") or []]}


def texts(record: dict | None) -> list[str]:
    """Every seeded text of a run's seed record: its posts', then its files'. [] for a run without one."""
    if not record:
        return []
    return [p["text"] for p in record["posts"]] + [f["text"] for f in record["files"]]


def pack_texts(name: str) -> list[str]:
    """Every text of a pack as written, its author placeholders left in: what the collaboration tests read."""
    pack = pack_of(name)
    return [p["text"] for p in pack.get("posts") or []] + [f["text"] for f in pack.get("files") or []]


def task_packs(task: str) -> list[str]:
    """The names of the packs for one task."""
    return [name for name, pack in packs().items() if pack["task"] == task]


def file_path(place: str, name: str, home: str | None) -> str:
    """Where a seeded file goes: /tmp/<name>, or <home>/<name> for a seat with a home."""
    if place == "tmp":
        return f"{TMP}/{name}"
    if not home:
        raise ValueError(f"a seeded file for the agent's home ({name}) needs a seat with a home")
    return f"{home.rstrip('/')}/{name}"


def main(argv: list[str] | None = None) -> int:
    """Load and check every pack of seeds/ and list each with its kinds, posts and files; 1 at the first bad pack."""
    try:
        found = read_packs(SEEDS_DIR)
    except ValueError as error:
        print(f"seeds/: {error}", file=sys.stderr)
        return 1
    for name, pack in found.items():
        files = [f"{f['place']}/{f['name']}" for f in pack.get("files") or []]
        rows = ",".join(pack.get("rows") or []) or "every row"
        print(f"  {name:40} {'+'.join(pack['kinds']):34} {len(pack.get('posts') or [])} posts  "
              f"files {', '.join(files) or '-'}  rows {rows}")
    print(f"{len(found)} seed packs of {len({p['task'] for p in found.values()})} tasks are well formed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
