"""A box gets only a key OpenRouter caps: no key, the main key, a key without a credit limit, or one with less left than
the runs of every open box that holds the key may spend at their caps is refused, and the key is never printed.
--main-key, the user's order of 2026-11-02, lifts the refusal of the main key alone. Each box's need is its targets'
runs on its pinned commit."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from scripts.box import box_key

BOX, MAIN = "sk-or-box-key-for-one-launch", "sk-or-main-key-of-the-account"


def opener_answering(data: dict):
    def opener(request, timeout):
        assert request.headers["Authorization"] == f"Bearer {BOX}"
        return io.BytesIO(json.dumps({"data": data}).encode())
    return opener


def failing_opener(request, timeout):
    raise OSError("no network")


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(box_key, "holders_need", lambda instance_id, variable: [(7, 6, 30.0)])
    monkeypatch.setattr(box_key.run_study, "live_caps", dict)                 # no run lives on this machine
    monkeypatch.setattr(box_key.fleet_registry, "update_box", lambda instance_id, **fields: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", MAIN)
    monkeypatch.delenv("BOX_OPENROUTER_API_KEY", raising=False)
    return monkeypatch


def check(opener, capsys) -> tuple[int, str]:
    code = box_key.main(["check", "OPENROUTER_API_KEY", "--box-id", "7"], opener=opener)
    out = capsys.readouterr()
    assert BOX not in out.out + out.err and MAIN not in out.out + out.err
    return code, out.out


def test_a_capped_key_with_enough_left_passes_and_is_never_printed(box, capsys):
    box.setenv("BOX_OPENROUTER_API_KEY", BOX)
    code, out = check(opener_answering({"limit": 50.0, "limit_remaining": 42.0, "usage": 8.0}), capsys)
    assert code == 0 and "limit $50.00, $42.00 left" in out and "6 runs may spend $30.00" in out


@pytest.mark.parametrize("env, data, reason", [
    ({}, {"limit": 50.0, "limit_remaining": 50.0}, "no box key"),
    ({"BOX_OPENROUTER_API_KEY": MAIN}, {"limit": 650.0, "limit_remaining": 157.0}, "is the main key"),
    ({"BOX_OPENROUTER_API_KEY": BOX}, {"limit": None, "limit_remaining": None}, "no credit limit"),
    ({"BOX_OPENROUTER_API_KEY": BOX}, {"limit": 50.0, "limit_remaining": 12.0}, "$12.00 left, less than the $30.00"),
])
def test_an_uncapped_or_short_key_is_refused(box, capsys, env, data, reason):
    for name, value in env.items():
        box.setenv(name, value)

    def opener(request, timeout):
        return io.BytesIO(json.dumps({"data": data}).encode())

    code, out = check(opener, capsys)
    assert code == 1 and reason in out


def test_a_key_openrouter_cannot_describe_is_refused(box, capsys):
    box.setenv("BOX_OPENROUTER_API_KEY", BOX)
    code, out = check(failing_opener, capsys)
    assert code == 1 and "could not be read" in out


@pytest.mark.parametrize("data, code, said", [
    ({"limit": 650.0, "limit_remaining": 157.0}, 0, "== OPENROUTER_API_KEY: limit $650.00, $157.00 left"),
    ({"limit": None, "limit_remaining": None}, 1, "no credit limit"),
    ({"limit": 650.0, "limit_remaining": 12.0}, 1, "$12.00 left, less than the $30.00"),
])
def test_the_main_key_flag_checks_the_main_key_and_keeps_the_limit_checks(box, capsys, data, code, said):
    def opener(request, timeout):
        assert request.headers["Authorization"] == f"Bearer {MAIN}"
        return io.BytesIO(json.dumps({"data": data}).encode())

    assert box_key.main(["check", "OPENROUTER_API_KEY", "--box-id", "7", "--main-key"], opener=opener) == code
    out = capsys.readouterr().out
    assert said in out and MAIN not in out


def test_without_the_flag_the_main_key_is_still_refused_under_the_box_key_s_name(box, capsys):
    box.setenv("BOX_OPENROUTER_API_KEY", MAIN)
    code, out = check(lambda request, timeout: io.BytesIO(json.dumps(
        {"data": {"limit": 650.0, "limit_remaining": 157.0}}).encode()), capsys)
    assert code == 1 and "is the main key" in out


def fleet(monkeypatch, *entries: dict) -> list:
    """A registry of these entries, each box needing what its entry's `caps` say on its pinned commit; the updates."""
    registry = {"boxes": [{"destroyed": None, **entry} for entry in entries]}
    monkeypatch.setattr(box_key.fleet_registry, "load_registry", lambda: registry)
    monkeypatch.setattr(box_key.fleet_registry, "entry_box", lambda entry: SimpleNamespace(
        runs=[None] * entry["n"], cap_usd=entry["caps"][entry["commit"]]))
    updates = []
    monkeypatch.setattr(box_key.fleet_registry, "update_box", lambda instance_id, **fields: updates.append(
        (instance_id, fields)))
    return updates


def test_the_need_is_every_open_box_that_holds_the_key_each_on_its_pinned_commit(monkeypatch):
    """Box 8 holds the main key; 9 held it and is destroyed; 10 holds another key. Box 7's cap is its pinned commit's,
    not the one recorded at its create."""
    fleet(monkeypatch, {"id": 7, "n": 5, "commit": "b", "caps": {"a": 45.0, "b": 60.0}, "cap_usd": 45.0},
          {"id": 8, "n": 4, "commit": "a", "caps": {"a": 45.0}, "key_sent": "OPENROUTER_API_KEY"},
          {"id": 9, "n": 4, "commit": "a", "caps": {"a": 45.0}, "key_sent": "OPENROUTER_API_KEY",
           "destroyed": "2026-11-25T10:00:00Z"},
          {"id": 10, "n": 4, "commit": "a", "caps": {"a": 45.0}, "key_sent": "BOX_OPENROUTER_API_KEY"})
    assert box_key.holders_need(7, "OPENROUTER_API_KEY") == [(7, 5, 60.0), (8, 4, 45.0)]
    assert box_key.holders_need(7, "BOX_OPENROUTER_API_KEY") == [(7, 5, 60.0), (10, 4, 45.0)]
    for missing in (9, 11):
        with pytest.raises(ValueError):
            box_key.holders_need(missing, "OPENROUTER_API_KEY")


def test_two_boxes_on_the_main_key_may_not_need_more_than_it_has_left(monkeypatch, capsys):
    """Two boxes of $45 each and a main key with $50 left: the first passes and records the key, the second is
    refused, and the Mac's live runs are printed beside the need."""
    updates = fleet(monkeypatch, {"id": 1, "n": 5, "commit": "a", "caps": {"a": 45.0}},
                    {"id": 2, "n": 5, "commit": "a", "caps": {"a": 45.0}})
    monkeypatch.setattr(box_key.run_study, "live_caps", lambda: {"mac_run_s1": 15.0, "mac_run_s2": 15.0})
    monkeypatch.setenv("OPENROUTER_API_KEY", MAIN)

    def opener(request, timeout):
        return io.BytesIO(json.dumps({"data": {"limit": 300.0, "limit_remaining": 50.0}}).encode())

    assert box_key.main(["check", "OPENROUTER_API_KEY", "--box-id", "1", "--main-key"], opener=opener) == 0
    out = capsys.readouterr().out
    assert "1 boxes hold it, their 5 runs may spend $45.00" in out
    assert "this machine's 2 live runs may spend $30.00 at their caps" in out and MAIN not in out
    assert updates == [(1, {"key_sent": "OPENROUTER_API_KEY"})]
    box_key.fleet_registry.load_registry()["boxes"][0]["key_sent"] = "OPENROUTER_API_KEY"
    assert box_key.main(["check", "OPENROUTER_API_KEY", "--box-id", "2", "--main-key"], opener=opener) == 1
    out = capsys.readouterr().out
    assert "$50.00 left, less than the $90.00 the runs of the boxes that hold it may spend" in out
    assert "box 2: 5 runs may spend $45.00" in out and "box 1: 5 runs may spend $45.00" in out
    assert len(updates) == 1
