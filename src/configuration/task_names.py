"""Each task's three names: the CheatBench task name a run.json records, the domain key the code uses, and a display
name.

    domain_of("creative_writings")       -> "creative_writing"
    task_of("creative_writing")          -> "creative_writings"
    display_name("creative_writings")    -> "creative writing"
    names_of("swe")                      -> TaskNames(task="software_engineering", domain="swe", display="SWE")

CheatBench names a task by its folder under submodules/cheatbench/tasks/. run.json "task" and Benchmark.name record that
name. The code keys a task by its domain key: src/domains DOMAINS, `--benchmark`, the @task entry points,
examples/<domain>/ and the vars of instruction_levels.yaml. The two names differ for three tasks: openmath_agent,
software_engineering and creative_writings. A page or a figure names a task by its display name in running text. A
heading capitalises its first letter.

This module uses the standard library alone. The analysis tools' environment lacks ORBIT, so it cannot import
src.domains, and it reads the names here. tests/unit/test_task_names.py checks this table against src.domains.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskNames:
    """One task's three names."""

    task: str
    """CheatBench's task name, as run.json "task" and Benchmark.name record it."""
    domain: str
    """The code's domain key (Benchmark.key)."""
    display: str
    """How a page or a figure names the task in running text."""


NAMES = (
    TaskNames("openmath_agent", "openmath", "OpenMath"),
    TaskNames("software_engineering", "swe", "SWE"),
    TaskNames("knowledge_work", "knowledge_work", "knowledge work"),
    TaskNames("subset_sum", "subset_sum", "subset sum"),
    TaskNames("prime_factorization", "prime_factorization", "prime factorization"),
    TaskNames("protein_design", "protein_design", "protein design"),
    TaskNames("creative_writings", "creative_writing", "creative writing"),
    TaskNames("chess", "chess", "chess"),
)
"""Every task, in the order of src/domains DOMAINS."""
_BY_TASK = {names.task: names for names in NAMES}
_BY_DOMAIN = {names.domain: names for names in NAMES}


def names_of(name: str) -> TaskNames:
    """A task's names, from its CheatBench task name or its domain key. No string is one task's name and another's
    key. Raises KeyError for a string that is neither."""
    names = _BY_TASK.get(name) or _BY_DOMAIN.get(name)
    if names is None:
        raise KeyError(f"{name!r} is no task name and no domain key; the task names are {sorted(_BY_TASK)}")
    return names


def domain_of(task: str) -> str:
    """The domain key of a CheatBench task name. Raises KeyError for an unknown task name."""
    if task not in _BY_TASK:
        raise KeyError(f"{task!r} is no CheatBench task name; choose one of {sorted(_BY_TASK)}")
    return _BY_TASK[task].domain


def task_of(domain: str) -> str:
    """The CheatBench task name of a domain key. Raises KeyError for an unknown domain key."""
    if domain not in _BY_DOMAIN:
        raise KeyError(f"{domain!r} is no domain key; choose one of {sorted(_BY_DOMAIN)}")
    return _BY_DOMAIN[domain].task


def display_name(name: str) -> str:
    """The display name of a task, from its CheatBench task name or its domain key."""
    return names_of(name).display
