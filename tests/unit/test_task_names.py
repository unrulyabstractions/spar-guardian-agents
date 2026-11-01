"""The task names (src/configuration/task_names.py): one table of each task's CheatBench name, domain key and display
name, the same as the domain classes carry, read by the registry, the analysis tools and the results browser."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from src.configuration import settings
from src.configuration.task_names import NAMES, display_name, domain_of, names_of, task_of
from src.domains import BY_TASK, DOMAINS
from src.ui._run_store import SETTING_KEYS, TASK_SETTINGS, setting_of
from tools import run_reader


def test_every_domain_class_carries_the_names_of_the_table():
    """A domain class's key and CheatBench name are the table's pair, and the table holds every domain in the
    registry's order and nothing else."""
    assert [names.domain for names in NAMES] == list(DOMAINS)
    for cls in DOMAINS.values():
        assert (domain_of(cls.name), task_of(cls.key)) == (cls.key, cls.name), cls.__name__


def test_the_registry_by_task_name_is_the_table():
    assert list(BY_TASK) == [names.task for names in NAMES]
    assert all(BY_TASK[names.task] is DOMAINS[names.domain] for names in NAMES)
    assert BY_TASK["creative_writings"] is DOMAINS["creative_writing"]
    assert BY_TASK["software_engineering"] is DOMAINS["swe"] and BY_TASK["openmath_agent"] is DOMAINS["openmath"]


def test_a_name_and_a_key_never_collide_and_each_lookup_takes_its_own_kind():
    """No string is one task's CheatBench name and another task's key, so names_of may take either. domain_of takes a
    CheatBench name only and task_of a key only."""
    assert not any(a.task == b.domain for a in NAMES for b in NAMES if a != b)
    assert names_of("creative_writing") == names_of("creative_writings")
    assert domain_of("creative_writings") == "creative_writing" and task_of("swe") == "software_engineering"
    for wrong in (lambda: domain_of("creative_writing"), lambda: domain_of("openmath"), lambda: task_of("chess_x"),
                  lambda: task_of("software_engineering"), lambda: names_of("protein")):
        with pytest.raises(KeyError):
            wrong()


def test_each_task_has_its_own_display_name():
    displays = [names.display for names in NAMES]
    assert len(set(displays)) == len(NAMES) and all(displays)
    assert display_name("creative_writings") == display_name("creative_writing") == "creative writing"
    assert display_name("software_engineering") == "SWE" and display_name("openmath") == "OpenMath"


def test_the_browser_labels_a_task_by_its_display_name():
    """The browser keys its own settings by domain key and finds a run by its CheatBench name."""
    assert set(TASK_SETTINGS) == {task_of(domain) for domain in SETTING_KEYS}
    assert all(label == display_name(task) for task, (_, label, _) in TASK_SETTINGS.items())
    run = {"task": "creative_writings", "topology": "board"}
    assert setting_of(run, 3) == {"key": "creative-board-3", "order": 8, "n": 3, "label": "creative writing board of 3"}


def test_the_analysis_tools_read_the_domain_key_from_the_table(tmp_path):
    run_dir = tmp_path / "poem_minimal_luna1"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"task": "creative_writings", "agents": ["a01"], "config": {}}))
    run = run_reader.load_run(run_dir)
    assert (run.task, run.domain) == ("creative_writings", "creative_writing")
    (run_dir / "run.json").write_text(json.dumps({"task": "creative_writing", "agents": ["a01"], "config": {}}))
    assert run_reader.load_runs([run_dir], "test") is None      # a domain key is no task name: unreadable


def test_the_table_imports_nothing_outside_the_standard_library():
    """tools/.venv lacks ORBIT and the run environment's packages: importing the table loads none of them."""
    code = ("import sys, src.configuration.task_names; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'orbit', 'inspect_ai', 'pydantic', 'yaml'}))")
    done = subprocess.run([sys.executable, "-c", code], cwd=settings.ROOT, capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["[]"]
