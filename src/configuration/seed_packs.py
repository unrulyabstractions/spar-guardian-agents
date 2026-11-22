"""Seed packs: what a run's environment holds before round 1 beyond the task, from a "previous team".

    config = build_config("default", "small", ["seed_pack=swe/pointer"])
    record = seed_record(config, "swe", "flipt-02e2")      # run.json "seed": pack, authors, posts, files
    texts(record)                                        # every seeded text, for the ladders and the tests

A pack is a few board posts and files written from the posts and notes of real teams in our past runs
(ian_baseline_explorations_0 and ian_baseline_explorations_1, harvested on 2026-11-22). Its posts stand on the board
before round 1 under the IDs of a previous team, drawn per run from SwarmConfig.ID_POOL apart from the roster. Its files
are planted before the agents start: `tmp` puts one copy in /tmp of every machine, root's and readable by all; `home`
puts one in each agent's home, the agent's own. A pack may only point, tempt, normalise or rationalise. It never holds a
piece of the exploit, a planted design, a line of the reference, a half of a credential or any other content of a
honeypot, so a seeded team still needs two agents' items to cheat (tests/unit/test_collaboration_*.py check every pack).
A pack's lines never go into the task's repository, its README or its history, because a tracked file would enter every
agent's graded tree.

The packs live in src/configuration/seeds/<task>.yaml, one file per task, under the Benchmark's key. A pack's name in
the config is "<task>/<pack>". Each pack carries `about` (what it holds), `kinds` (pointer, normalise, rationalise,
pressure, pool), `harvest` (the ids of the harvested posts it was written from, or none for a pack composed for a task
with no harvest), `rows` (the rows its text fits; every row when absent), `posts` ({author: k, text}) and `files`
({place: tmp or home, name, text}). "{author<k>}" in a text becomes the k-th author's ID.

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

from src.configuration import settings

SEEDS_DIR = settings.HERE / "seeds"
PLACES = ("tmp", "home")
KINDS = ("pointer", "normalise", "rationalise", "pressure", "pool")
AUTHOR = re.compile(r"\{author(\d+)\}")
FILE_NAME = re.compile(r"[A-Za-z0-9._-]+")
TMP = "/tmp"
SEEDED = "seed_pack"            # the invocation_id of a seeded post in ORBIT's communication store


def is_seeded(message) -> bool:
    """Whether a board post (a CommunicationMessage or its exported dict) is a seed pack's."""
    value = message.get("invocation_id") if isinstance(message, dict) else getattr(message, "invocation_id", None)
    return value == SEEDED


@functools.cache
def packs() -> dict[str, dict]:
    """Every pack by its name "<task>/<pack>", each with its task, from src/configuration/seeds/*.yaml."""
    out = {}
    for path in sorted(SEEDS_DIR.glob("*.yaml")):
        data = settings.load_yaml(path)
        task = data["task"]
        if path.stem != task:
            raise ValueError(f"{path.name} names task {task!r}: a seed file is named after its task")
        for name, pack in data["packs"].items():
            out[f"{task}/{name}"] = {"task": task, "name": name, **pack}
            check_pack(f"{task}/{name}", out[f"{task}/{name}"])
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
