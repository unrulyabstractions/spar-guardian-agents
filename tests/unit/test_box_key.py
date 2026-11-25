"""A box gets only a key OpenRouter caps: no key, the main key, a key without a credit limit, or one with less left than
the box's runs may spend at their caps is refused, and the key is never printed. --main-key, the user's order of
2026-11-02, lifts the refusal of the main key alone."""
from __future__ import annotations

import io
import json

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
    monkeypatch.setattr(box_key, "box_worst_case", lambda instance_id: (6, 30.0))
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


def test_a_box_s_worst_case_is_its_registry_entry_s_runs_and_caps(monkeypatch):
    entry = {"id": 7, "runs": 5, "cap_usd": 45.0}
    monkeypatch.setattr(box_key.fleet_registry, "load_registry", lambda: {"boxes": [entry]})
    assert box_key.box_worst_case(7) == (5, 45.0)
    with pytest.raises(ValueError):
        box_key.box_worst_case(8)
