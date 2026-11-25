"""tests/golden/agent_texts/ holds every text an agent reads before its first call, as the code renders it now
(scripts/study/agent_texts.py). A change to a prompt, a first message or a tool shows as a diff of that folder, and
this test fails until `make texts` writes it again."""
from __future__ import annotations

import pytest

from scripts.study import agent_texts
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


def test_a_changed_line_is_named_with_its_file_and_number(tmp_path, rendered):
    path, text = next(iter(rendered.items()))
    agent_texts.write({path: text}, tmp_path)
    assert agent_texts.first_difference({path: text}, tmp_path) is None
    lines = text.splitlines()
    lines[3] += " changed"
    found = agent_texts.first_difference({path: "\n".join(lines) + "\n"}, tmp_path)
    assert f"{path} line 4:" in found and found.endswith(lines[3])
    assert "no such file" in agent_texts.first_difference({path: text, "x/y/z.txt": "z"}, tmp_path)
