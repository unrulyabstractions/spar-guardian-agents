"""src/utils/progress.py: the make targets' output. Steps, indented details, a counter that prints a line every tenth
of the way off a terminal, the summary line, and the step runner's numbered headings, indented output and exit code."""
from __future__ import annotations

import sys

import pytest

from src.utils import progress


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test starts as a new process would: nothing printed yet, outside a step, not under the runner."""
    monkeypatch.setattr(progress, "_printed", False)
    monkeypatch.setattr(progress, "_in_step", False)
    monkeypatch.delenv(progress.NESTED, raising=False)
    monkeypatch.delenv("VERBOSE", raising=False)


def test_durations_and_estimates_read_as_plain_words():
    assert progress.duration(0.42) == "0.4 s" and progress.duration(12.4) == "12 s"
    assert progress.duration(185) == "3 min 5 s" and progress.duration(120) == "2 min"
    assert progress.duration(3900) == "1 h 5 min"
    assert progress.remaining(2) == "about 5 s" and progress.remaining(22) == "about 20 s"
    assert progress.remaining(170) == "about 3 min"


def test_steps_are_separated_by_a_blank_line_and_their_details_indented(capsys):
    progress.step("Index the runs")
    progress.detail("cache", "tmp/index.json")
    progress.detail("a line of its own")
    progress.detail("several", "one\ntwo")
    progress.step("Serve")
    out = capsys.readouterr().out.splitlines()
    assert out == ["Index the runs", "  cache        tmp/index.json", "  a line of its own", "  several      one",
                   "               two", "", "Serve"]


def test_the_summary_ends_the_output_with_the_time_and_where_it_went(capsys):
    progress.step("Write")
    progress.done("3 tables", out="tmp/tables/")
    out = capsys.readouterr().out.splitlines()
    assert out[-2] == "" and out[-1].startswith("Done in ") and out[-1].endswith(": 3 tables; output in tmp/tables/")


def test_under_the_runner_the_summary_is_an_indented_detail(capsys, monkeypatch):
    monkeypatch.setenv(progress.NESTED, "1")
    progress.step("Write")
    progress.done("3 tables")
    assert capsys.readouterr().out.splitlines()[-1].startswith("  done in ")


def test_off_a_terminal_the_counter_prints_a_line_every_tenth_and_the_total(capsys):
    progress.step("Measure")
    with progress.Counter(20, "measured", "runs") as count:
        assert not count.live
        for _ in range(20):
            count.tick()
    lines = capsys.readouterr().out.splitlines()[1:]
    assert [line.split(" (")[0] for line in lines[:-1]] == [f"  measured {n} of 20 runs" for n in range(2, 20, 2)]
    assert lines[-1].startswith("  measured 20 of 20 runs in ")
    assert lines[4] == "  measured 10 of 20 runs (50%)"  # no estimate before half a second has passed


def test_verbose_details_need_verbose_in_the_environment(capsys, monkeypatch):
    progress.verbose("counts", {"a": 1})
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("VERBOSE", "1")
    progress.verbose("counts", {"a": 1})
    assert capsys.readouterr().out == "counts {'a': 1}\n"


def test_the_runner_numbers_its_steps_indents_their_output_and_stops_at_a_failure(capfd):
    py = sys.executable
    code = progress.main(["Tables", "--step", "first", f"{py} -c \"print('one'); print('two')\"",
                          "--step", "second", f"{py} -c 'raise SystemExit(3)'",
                          "--step", "third", f"{py} -c \"print('never')\""])
    out, err = capfd.readouterr()
    assert code == 3
    lines = out.splitlines()
    assert lines[:5] == ["Tables: 3 steps", "", "[1/3] first", "  one", "  two"]
    assert "[2/3] second" in lines and "never" not in out
    assert "Failed after" in err and "tables, step 2 of 3 (second) exited 3" in err


def test_one_step_has_its_title_alone_and_the_summary_given(capfd):
    assert progress.main(["Lint", "--summary", "lint clean", "--step", "ruff", f"{sys.executable} -c 'print(1)'"]) == 0
    lines = capfd.readouterr().out.splitlines()
    assert lines[:2] == ["Lint", "  1"] and lines[-1].startswith("Done in ") and lines[-1].endswith(": lint clean")


def test_indenting_follows_every_line_start():
    text, start = progress._indented(b"a\nb\rc", True)
    assert text == b"  a\n  b\r  c" and not start
    text, start = progress._indented(b"\n", False)
    assert text == b"\n" and start
