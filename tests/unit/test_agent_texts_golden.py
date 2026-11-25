"""tests/golden/agent_texts/ holds every text an agent reads before its first call, as the code renders it now
(scripts/study/agent_texts.py). A change to a prompt, a first message or a tool shows as a diff of that folder, and
this test fails until `make texts` writes it again."""
from __future__ import annotations

import pytest

from scripts.study import agent_texts
from src import tasks
from src.swarm.turn_rules._turn_state import TEAM_SUBMIT
from tests.support.ordered_ids import drawn_ids


@pytest.fixture(scope="module")
def rendered() -> dict[str, str]:
    """Every file, rendered on the real draw of agent IDs, as a run draws them."""
    with drawn_ids():
        return agent_texts.render()


def test_the_golden_folder_holds_what_the_code_renders(rendered):
    found = agent_texts.first_difference(rendered)
    assert found is None, f"the agent texts changed; `make texts` writes them, then review `git diff`:\n{found}"


def test_every_task_has_every_setting(rendered):
    spec = agent_texts.gated_study.load_spec(agent_texts.STUDY_FILE)
    names = {setting.name for setting in agent_texts.study_settings(spec)}
    folders = {tuple(path.split("/")[:2]) for path in rendered}
    assert folders == {(task, name) for task in spec.tasks for name in names}


@pytest.mark.parametrize("name", ["seeded", "positive_control", "regulator_weak", "disregulator_weak"])
@pytest.mark.parametrize("task", ["ian_flipt", "ian_poem", "ian_binder"])
def test_the_render_matches_the_task_a_run_builds(task, name):
    """The render's steps give each agent the system prompt, first message and tools of the task swarm_task builds,
    so the render cannot drift from a run. Docker's calls are stubbed as conftest.py and no_docker stub them."""
    spec = agent_texts.gated_study.load_spec(agent_texts.STUDY_FILE)
    setting = next(s for s in agent_texts.study_settings(spec) if s.name == name)
    module, argv = agent_texts.cell_argv(spec, task, setting)
    with drawn_ids():
        config, benchmark, extra = agent_texts.cell_parts(module, argv)
        texts = agent_texts.agent_texts(config, benchmark, extra)
        with agent_texts.no_docker():
            sample = tasks.swarm_task(benchmark, config, extra_instruction=extra).dataset[0]
    experiment, first = sample.metadata["experiment"], sample.metadata["cheatbench"].get("first_message") or {}
    assert not experiment["system_prompts"]
    for spec_ in experiment["setup"]["agents"]:
        agent = spec_["name"]
        role, system, message, tools = texts[agent]
        assert system == spec_["system_prompt"]
        assert message == first.get(agent, sample.input)
        shown = ["submit" if tool == TEAM_SUBMIT else tool for tool in spec_["tools"]]
        assert [tool.splitlines()[0].removeprefix("--- ") for tool in tools] == shown


def test_a_changed_line_is_named_with_its_file_and_number(tmp_path, rendered):
    path, text = next(iter(rendered.items()))
    agent_texts.write({path: text}, tmp_path)
    assert agent_texts.first_difference({path: text}, tmp_path) is None
    lines = text.splitlines()
    lines[3] += " changed"
    found = agent_texts.first_difference({path: "\n".join(lines) + "\n"}, tmp_path)
    assert f"{path} line 4:" in found and found.endswith(lines[3])
    assert "no such file" in agent_texts.first_difference({path: text, "x/y/z.txt": "z"}, tmp_path)
