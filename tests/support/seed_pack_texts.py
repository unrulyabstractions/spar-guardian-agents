"""The seed packs' texts as the collaboration tests read them (src/configuration/seed_packs.py).

    for name in task_packs("swe"): text = joined(name)        # every post and file of the pack, one string

A pack is seeded before round 1 for every agent, so every agent's items plus the seed are its items plus these texts.
The tests of each task prove that the texts hold none of its exploit, so a seeded team still needs two agents' items.
"""
from __future__ import annotations

import re

from src.configuration import seed_packs

task_packs = seed_packs.task_packs


def joined(name: str) -> str:
    """Every text of the pack, posts then files, one per line, its author placeholders left in."""
    return "\n".join(seed_packs.pack_texts(name))


def fits(name: str, row: str) -> bool:
    """Whether the pack fits the row (its `rows`, or every row when it names none)."""
    rows = seed_packs.pack_of(name).get("rows")
    return not rows or row in rows


def word_runs(text: str, n: int) -> set[tuple[str, ...]]:
    """The text's runs of n words, lower-cased, punctuation dropped."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}
