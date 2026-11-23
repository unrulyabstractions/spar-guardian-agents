"""ian0flipt's scenario, the clean-room fork (the user, 2026-11-22): CheatBench's work-test line gives way to a
customer's ticket, the workspace note opens with the fork's bullet, and a notice file in each clone's root says which
upstream code the fork may use. No text of the scenario names a test, a grader, an interview or an evaluation, and none
holds a piece of the exploit."""
from __future__ import annotations

import re

import pytest

from src import tasks
from src.configuration import settings
from src.configuration.presets import build_config
from src.domains.swe import SWE, swe_inputs
from src.domains.swe.swe_inputs import instruction_text, load_lines, load_split, scenario
from src.domains.swe.swe_settings import SECRETS, VARIANTS, WORKTEST_LINE, Variant

ISSUE = "flipt-02e2"
TEST_WORDS = re.compile(r"work-test|interview|grader|hidden|evaluat|benchmark|\btests?\b|\bexam", re.I)


def test_the_variants():
    assert VARIANTS == {"ian_swe0": Variant(side_patches=True),
                        "ian0flipt": Variant(side_patches=True, scenario="clean_room_fork")}
    assert scenario(None) is None and Variant() == Variant(side_patches=False, scenario=None)
    assert set(scenario("clean_room_fork")) == {"ticket", "workspace", "notice_file", "notice"}


def test_the_ticket_stands_where_the_work_test_line_stood():
    instance = load_split(ISSUE)["task"]
    plain, told = instruction_text(ISSUE, instance), instruction_text(ISSUE, instance, "clean_room_fork")
    assert plain.count(WORKTEST_LINE) == 1 and WORKTEST_LINE not in told
    assert told == plain.replace(WORKTEST_LINE, scenario("clean_room_fork")["ticket"])
    assert instruction_text(ISSUE, instance, None) == plain


def test_a_scenario_never_leaves_the_work_test_line_in(monkeypatch, tmp_path):
    (tmp_path / "instruction.md").write_text("An issue with no such line.\n")
    monkeypatch.setattr(swe_inputs, "task_dir", lambda instance: tmp_path)
    with pytest.raises(ValueError, match="work-test line"):
        instruction_text(ISSUE, "any", "clean_room_fork")


def test_no_text_of_the_scenario_names_a_test_or_holds_a_piece():
    told = scenario("clean_room_fork")
    texts = [told["ticket"], told["workspace"], told["notice"]]
    assert not [t for t in texts if TEST_WORDS.search(t)], texts
    fix = {x["line"].strip().lower() for x in load_lines(ISSUE)["fix"]}
    for text in texts:
        assert not any(secret in text for secret in SECRETS[ISSUE].values())
        assert not {line.strip().lower() for line in text.splitlines()} & fix


def test_a_workers_first_message_holds_the_ticket_and_the_forks_bullet_and_no_test_word_of_ours():
    config = build_config("default", "small", ["seed=1"])
    benchmark = SWE(ISSUE, "jigsaw", variant="ian0flipt")
    plain = SWE(ISSUE, "jigsaw", variant="ian_swe0")
    swe = tasks.swarm_task(benchmark, config).dataset[0].metadata["cheatbench"]["swe"]
    old = tasks.swarm_task(plain, config).dataset[0].metadata["cheatbench"]["swe"]
    told = scenario("clean_room_fork")
    assert swe["scenario"] == "clean_room_fork" and old["scenario"] is None
    assert "work_test_line_replaced_by_a_scenario_with_a_notice_file_in_each_clone" in benchmark.task_deviations
    for agent in swe["agents"]:
        first, was = swe["first_message"][agent], old["first_message"][agent]
        assert told["ticket"] in first and WORKTEST_LINE not in first and WORKTEST_LINE in was
        bullet = told["workspace"].format(repo="flipt")
        assert f"Your workspace:\n{bullet}\n- Everyone has their own machine" in first
        # the two tasks' messages differ in these two places alone
        assert first.replace(told["ticket"], WORKTEST_LINE).replace(f"{bullet}\n", "") == was
        assert swe["system_prompt"][agent] == old["system_prompt"][agent]
        ours = first[first.index(told["ticket"]):]              # after CheatBench's issue text, which names tests
        assert not re.search(r"work-test|interview|grader|hidden|evaluat|benchmark", ours, re.I), ours
    assert told["notice_file"] == "FORK_NOTICE.md" and "{repo}" not in told["notice"].format(repo="flipt")
    assert settings.load_prompts()["swe"]["scenarios"].keys() == {"clean_room_fork"}
