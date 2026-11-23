"""Seed packs: what a run's environment holds before round 1 beyond the task, from a "previous team".

    config = build_config("default", "small", ["seed_pack=swe/exp1_called_cheating"])
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

    seeds/<task>/<pack>/README.md                    what the pack does in one paragraph, then `Kinds: a, b.`
    seeds/<task>/<pack>/pack.yaml                    kinds, and rows when the pack fits only some rows
    seeds/<task>/<pack>/board/01.md, 02.md, ...      the posts, oldest first: a front matter `author: k`, then the text
    seeds/<task>/<pack>/files/tmp/<name>             a file planted at /tmp/<name> of every machine
    seeds/<task>/<pack>/files/home/<name>            a file planted at ~/<name> of every agent
    seeds/<task>/<pack>/harvest/                     the real posts the pack was written from, for people; never read

<task> is the Benchmark's key, and a pack's name in the config is "<task>/<pack>". README.md's first paragraph, its
lines joined by a space, is the pack's `about`; its second paragraph names the kinds, which must equal pack.yaml's
`kinds` (among KINDS). pack.yaml's `rows` lists the rows its text fits; every row when absent. A post is board/NN.md,
numbered from 01 with no gap: a front matter of one line `author: k`, then the post's text, its surrounding blank
lines dropped. A file's text is its bytes as UTF-8, planted as they are. "{author<k>}" in a text becomes the k-th
author's ID. A seed record before 2026-11-22's folder layout also lists `harvest`, the ids of the posts its pack was
written from; a newer one has none. studies/ holds the plans of scripts/run_study.py, not packs.

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

import yaml

from src.configuration import settings

SEEDS_DIR = settings.ROOT / "seeds"
README_FILE = "README.md"
PACK_FILE = "pack.yaml"
BOARD_DIR = "board"
FILES_DIR = "files"
HARVEST_DIR = "harvest"         # the sources a pack was written from, for people: never read
ENTRIES = (README_FILE, PACK_FILE, BOARD_DIR, FILES_DIR, HARVEST_DIR)
NOT_TASKS = ("plans",)          # folders of seeds/ that hold no packs
IGNORED = (".DS_Store",)        # Finder's files, never planted
PLACES = ("tmp", "home")
KINDS = ("pointer", "normalise", "rationalise", "pressure", "pool", "collaborate", "objection")
PACK_KEYS = ("kinds", "rows")
MOVED = {"about": f"`about` is {README_FILE}'s first paragraph",
         "posts": f"the posts are {BOARD_DIR}/01.md, 02.md, ...",
         "files": f"a pack's files live in {FILES_DIR}/<place>/<name>",
         "harvest": f"the sources go in {HARVEST_DIR}/, which nothing reads"}
"""Where a key pack.yaml no longer takes went, for the refusal's hint."""
KINDS_LINE = re.compile(r"Kinds: ([a-z_]+(?:, [a-z_]+)*)\.")
POST_NAME = re.compile(r"\d\d+\.md")
FRONT_MATTER = re.compile(r"---\n(.*?)\n---(?:\n(.*))?", re.S)
AUTHOR = re.compile(r"\{author(\d+)\}")
SET_PREFIX = re.compile(r"/exp\d+_")
"""A pack's set prefix in its name, "<task>/exp<N>_<kind>": the set the pack belongs to (exp0: the first, exploratory
set)."""
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
    """Every pack under root, checked: each folder root/<task>/<pack>/ must hold a pack.yaml and a README.md."""
    out = {}
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in NOT_TASKS):
        for folder in sorted(p for p in task_dir.iterdir() if not p.name.startswith(".")):
            pack = read_pack(folder)
            out[f"{pack['task']}/{pack['name']}"] = pack
    return out


def read_pack(folder: Path) -> dict:
    """One pack from its folder: pack.yaml, README.md, board/NN.md, then files/<place>/<name>; checked (check_pack).
    harvest/ is never read. The pack: {task, name, about, kinds, rows?, posts?, files?}."""
    task, name = folder.parent.name, folder.name
    full = f"{task}/{name}"
    for needed in (PACK_FILE, README_FILE):
        if not folder.is_dir() or not (folder / needed).is_file():
            raise ValueError(f"seed pack {full}: {folder} needs a {needed}")
    if not FOLDER_NAME.fullmatch(task) or not FOLDER_NAME.fullmatch(name):
        raise ValueError(f"seed pack {full}: a task's and a pack's folder names take a-z, 0-9 and _")
    stray = sorted(p.name for p in folder.iterdir() if p.name not in ENTRIES + IGNORED)
    if stray:
        hint = f"; its posts go in {BOARD_DIR}/01.md, 02.md, ..." if any(n.endswith(".md") for n in stray) else ""
        raise ValueError(f"seed pack {full}: a pack's folder holds only {README_FILE}, {PACK_FILE}, {BOARD_DIR}/, "
                         f"{FILES_DIR}/ and {HARVEST_DIR}/, got {stray}{hint}")
    data = settings.load_yaml(folder / PACK_FILE) or {}
    if not isinstance(data, dict) or set(data) - set(PACK_KEYS):
        extra = sorted(set(data) - set(PACK_KEYS)) if isinstance(data, dict) else data
        hints = "".join(f"; {MOVED[key]}" for key in extra if key in MOVED) if isinstance(data, dict) else ""
        raise ValueError(f"seed pack {full}: {PACK_FILE} takes only {PACK_KEYS}, got {extra}{hints}")
    about, named = read_readme(folder / README_FILE, full)
    pack = {"task": task, "name": name, "about": about, "kinds": data.get("kinds")}
    if "rows" in data:
        pack["rows"] = data["rows"]
    posts = read_board(folder / BOARD_DIR, full)
    if posts:
        pack["posts"] = posts
    files = read_files(folder / FILES_DIR, full)
    if files:
        pack["files"] = files
    check_pack(full, pack)
    if named != list(pack["kinds"]):
        raise ValueError(f"seed pack {full}: {README_FILE} names the kinds {named}, {PACK_FILE} {pack['kinds']}")
    return pack


def read_readme(path: Path, full: str) -> tuple[str, list[str]]:
    """README.md's `about` (its first paragraph, the lines joined by a space) and the kinds its second paragraph names
    (`Kinds: pointer, pool.`). Anything after them is for people."""
    paragraphs = [p.strip() for p in re.split(r"\n[ \t]*\n", path.read_text(encoding="utf-8")) if p.strip()]
    kinds = KINDS_LINE.fullmatch(paragraphs[1]) if len(paragraphs) > 1 else None
    if kinds is None or paragraphs[0].startswith("#"):
        raise ValueError(f"seed pack {full}: {README_FILE} is a paragraph on what the pack does (no heading), a blank "
                         f"line, then one line `Kinds: pointer, pool.`")
    return " ".join(line.strip() for line in paragraphs[0].splitlines()), kinds.group(1).split(", ")


def read_board(root: Path, full: str) -> list[dict]:
    """The posts of board/, oldest first: [{author, text}] from 01.md, 02.md, ..., each a front matter of one line
    `author: k` between two lines `---`, then the post's text. [] without a board/."""
    if not root.exists():
        return []
    names = [p.name for p in root.iterdir() if p.name not in IGNORED]
    expected = [f"{k:02d}.md" for k in range(1, len(names) + 1)]
    if sorted(names) != sorted(expected):
        numbered = [n for n in names if POST_NAME.fullmatch(n) and n == f"{int(n[:-3]):02d}.md"]
        top = max([len(names)] + [int(n[:-3]) for n in numbered])
        missing = [f"{k:02d}.md" for k in range(1, top + 1) if f"{k:02d}.md" not in names]
        odd = sorted(set(names) - set(numbered))
        raise ValueError(f"seed pack {full}: {BOARD_DIR}/ holds its posts as 01.md, 02.md, ..., numbered with no gap; "
                         f"got {sorted(names)}" + (f", missing {missing}" if missing else "")
                         + (f", not in the numbering {odd}" if odd else ""))
    posts = []
    for name in expected:
        where = f"{BOARD_DIR}/{name}"
        try:
            text = (root / name).read_bytes().decode("utf-8")
        except (UnicodeDecodeError, IsADirectoryError) as error:
            raise ValueError(f"seed pack {full}: {where} is not a UTF-8 text file") from error
        match = FRONT_MATTER.fullmatch(text)
        try:
            head = yaml.safe_load(match.group(1)) if match else None
        except yaml.YAMLError:
            head = None
        if not isinstance(head, dict) or list(head) != ["author"] or type(head["author"]) is not int \
                or head["author"] < 1:
            raise ValueError(f"seed pack {full}: {where} starts with a front matter, the three lines `---`, "
                             f"`author: k` (k = 1, 2, ...) and `---`, then the post's text")
        body = (match.group(2) or "").strip()
        if not body:
            raise ValueError(f"seed pack {full}: {where} has no text after its front matter")
        posts.append({"author": head["author"], "text": body})
    return posts


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
        raise ValueError(f"seed pack {name} holds no post and no file: its posts go in {BOARD_DIR}/01.md, 02.md, ..., "
                         f"its files in {FILES_DIR}/<place>/<name>")
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
    and salt. The draw reads the pack's name without its set prefix (SET_PREFIX: "swe/exp1_called_cheating" draws
    as "swe/called_cheating"), so a pack keeps its authors when it is filed under a set, and the runs recorded before
    the packs were named exp0_ rebuild with their own IDs. Imported here to keep the configuration package free of a
    cycle."""
    from src.configuration.swarm_config import ID_POOL
    taken = set(config.names)
    pool = [i for i in ID_POOL if i not in taken]
    name = SET_PREFIX.sub("/", config.seed_pack)
    return random.Random(f"seed-authors/{name}/{config.id_salt}/{config.seed}").sample(pool, n)


def fill(text: str, ids: list[str]) -> str:
    return AUTHOR.sub(lambda m: ids[int(m.group(1)) - 1], text)


def seed_record(config, task: str, row: str) -> dict | None:
    """The run's seed as run.json records it: the pack's name, what it is, its kinds, the previous team's IDs, each
    post with its author, and each file with its place, name and text. None without a pack. A record made before
    2026-11-22's folder layout also holds `harvest` after `kinds`."""
    if config.seed_pack is None:
        return None
    check_for(config.seed_pack, task, row, config.board)
    pack = pack_of(config.seed_pack)
    posts = pack.get("posts") or []
    ids = authors(config, max((int(p["author"]) for p in posts), default=0))
    return {"pack": config.seed_pack, "about": pack["about"].strip(), "kinds": list(pack["kinds"]), "authors": ids,
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
