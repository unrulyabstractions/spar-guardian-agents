"""The date that gives a replayed commit its recorded id: git's object id, the date rewrite, the window search, the
recorded ids and the parents of a rewritten commit's child. No Docker, no git."""
from __future__ import annotations

from src.replay.commit_dates import (
    RecordedIds,
    find_date,
    object_id,
    renamed_text,
    reparent,
    retime,
    variants,
)

TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
PARENT = "85bb23a3571794c7ba01e61904bac6913c3d9729"


def commit(author: str, a_time: int, committer: str, c_time: int, tz: str = "+0000", parent: str = PARENT) -> bytes:
    return (f"tree {TREE}\nparent {parent}\nauthor {author} <{author}@team.internal> {a_time} {tz}\n"
            f"committer {committer} <{committer}@team.internal> {c_time} {tz}\n\nfix(config): redis TLS\n").encode()


def test_object_id_is_gits():
    assert object_id("blob", b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert object_id("tree", b"") == TREE


def test_retime_moves_the_committer_and_with_author_too_the_author():
    body = commit("k7q2m", 10, "k7q2m", 10)
    assert retime(body, 99, b"+0000", False) == commit("k7q2m", 10, "k7q2m", 99)
    assert retime(body, 99, b"+0000", True) == commit("k7q2m", 99, "k7q2m", 99)


def test_variants_move_the_author_only_for_one_person_at_one_date():
    assert variants(commit("k7q2m", 10, "k7q2m", 10)) == [(b"+0000", True), (b"+0000", False)]
    cherry = commit("Mark Phelps", 5, "k7q2m", 10)
    assert variants(cherry) == [(b"+0000", False)]
    assert variants(commit("k7q2m", 10, "k7q2m", 10, tz="-0700"))[-1] == (b"+0000", False)


def test_find_date_learns_the_second_in_the_window_that_gives_the_recorded_id():
    replayed = commit("Mark Phelps", 5, "k7q2m", 1_900_000_000)             # today's clock
    recorded = object_id("commit", commit("Mark Phelps", 5, "k7q2m", 1_793_560_912))
    ids = RecordedIds([f"[k7q2m {recorded[:7]}] fix(config): redis TLS"])
    found = find_date(replayed, ids.matching, 1_793_560_900.2, 1_793_560_910.8)
    assert found is not None
    assert (found.seconds, found.tz, found.author_too, found.sha, found.recorded) == \
        (1_793_560_912, "+0000", False, recorded, recorded[:7])
    assert find_date(replayed, ids.matching, 1_793_560_000, 1_793_560_100) is None     # outside the window


def test_a_new_commit_moves_both_dates():
    replayed = commit("k7q2m", 1_900_000_000, "k7q2m", 1_900_000_000)
    recorded = object_id("commit", commit("k7q2m", 1_793_561_689, "k7q2m", 1_793_561_689))
    found = find_date(replayed, RecordedIds([recorded[:8]]).matching, 1_793_561_688, 1_793_561_689)
    assert found.author_too and found.sha == recorded


def test_recorded_ids_take_the_longest_prefix_and_ignore_short_hex():
    ids = RecordedIds(["86b201f8 t6k6p merge", "ref 86b201f88afe5ceb", "abc123 deadbee"])
    assert ids.matching("86b201f88afe5ceb9b7a1c53c74cb2e04fd87bb7") == "86b201f88afe5ceb"
    assert ids.matching("abc1230000000000000000000000000000000000") is None       # six digits are no id
    assert ids.matching("deadbee000000000000000000000000000000000") == "deadbee"
    assert ids.matching("0" * 40) is None and len(ids) == 3


def test_reparent_names_the_rewritten_parent():
    new_parent = "1" * 40
    assert reparent(commit("k7q2m", 1, "k7q2m", 1), {PARENT: new_parent}) == \
        commit("k7q2m", 1, "k7q2m", 1, parent=new_parent)


def test_renamed_text_rewrites_every_abbreviation_of_an_old_id():
    old, new = "d18ae13918d6ff6c5029a344904e6ba5e6f77688", "77eabf6170eb315182e543f672c98eec3d5a851a"
    text = f"[t6k6p d18ae13] fix\n   85bb23a..d18ae139  t6k6p -> t6k6p\n{old}"
    assert renamed_text(text, {old: new}) == f"[t6k6p 77eabf6] fix\n   85bb23a..77eabf61  t6k6p -> t6k6p\n{new}"
    assert renamed_text(text, {}) == text
