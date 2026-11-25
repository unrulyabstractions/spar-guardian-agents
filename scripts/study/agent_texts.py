"""Every text an agent reads before its first call, as plain text files, so a change to one shows in git as a diff.

    uv run python -m scripts.study.agent_texts            # write tests/golden/agent_texts/ again (make texts)
    uv run python -m scripts.study.agent_texts --check    # exit 1 and name the first line that differs

The folder holds one file per task, setting and agent: tests/golden/agent_texts/<task>/<setting>/<role>_<agent>.txt.
A file holds the agent's system prompt, its first message and each tool it holds, with the tool's description and
parameters. tests/unit/test_agent_texts_golden.py renders them again and fails when the folder is behind the code.
So "no text changed" means "the folder has no diff".

The tasks and settings come from one study file, STUDY_FILE. Each arm of the study is one setting, run as run_study
runs it: the cell script's own arguments, for the study's first worker. Arms that differ only in their models or
their cap are rendered once. Then come the settings no arm of the study names, built from its arms:
- seed_<n>: the seeded arm on the next seed, so other agent IDs and another deal;
- team_of_10: the seeded arm with ten agents;
- no_variant: the baseline arm on the task without its variant;
- <role>_<level>: the study's first regulator and first disregulator arm at each instruction level of
  src/configuration/instruction_levels.yaml that the arm does not use already;
- two_regulators: the study's first regulator arm with two regulators, whose first messages name each other.
Every setting keeps shared_result_kept_out at its default, as the study does.

The texts come from the code a run uses. The cell's own lines in main() build the config and the benchmark
(cell_parts). src/tasks.py builds the sample, the prompts and the tools, and ORBIT builds each tool from its binding.
The agent IDs are drawn from the seed and the run's name, so they are the same on every render.
Building a sample asks Docker for images. no_docker swaps those calls for the images' names alone.

A file holds what the model receives. The turn rules' filter takes the paragraph Inspect's react() adds out of the
system prompt and withholds ORBIT's channel tools, so no file holds them. What no file holds either: each round's
message, the board's posts and notices, every tool result, and the files planted in the sandbox.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

from inspect_ai.tool import ToolDef
from orbit.tools import resolve_tool_bindings

from scripts.study import gated_study, run_study
from src import tasks
from src.configuration import settings
from src.configuration.swarm_config import role_of
from src.domains.creative import creative_sandbox
from src.domains.swe import swe_sandbox, swe_task
from src.sandbox.machines import agent_machines
from src.swarm.tool_limits import run_limits
from src.swarm.turn_rules._turn_rules_filter import shown_as_submit

ROOT = settings.ROOT
STUDY_FILE = ROOT / "studies" / "gated" / "ian_influencers_1.yaml"
GOLDEN = ROOT / "tests" / "golden" / "agent_texts"
IGNORED_KEYS = ("_model", "cost_limit_usd")
"""Settings that change no text: the models and the cap. Arms that differ only in these are rendered once."""
ROLES = ("regulator", "disregulator")


@dataclass(frozen=True)
class Setting:
    """One setting of a task: its folder's name, the study arm it is run as, its seed, and whether the task keeps
    its variant."""

    name: str
    arm: gated_study.Arm
    seed: int
    variant: bool = True


# ------------------------------------------------------------------------------------------------ the settings
def text_key(arm: gated_study.Arm) -> tuple:
    """What of an arm can change a text: its pack, its cell and its settings but the models and the cap."""
    kept = tuple(pair for pair in arm.settings if not pair.split("=", 1)[0].endswith(IGNORED_KEYS))
    return arm.pack, arm.cheat, kept


def with_pairs(arm: gated_study.Arm, name: str, pairs: list[str]) -> gated_study.Arm:
    """The arm under another name, each pair setting its key in place of the arm's own."""
    keys = {pair.split("=", 1)[0] for pair in pairs}
    kept = [pair for pair in arm.settings if pair.split("=", 1)[0] not in keys]
    return replace(arm, name=name, settings=(*kept, *pairs), when={})


def study_settings(spec: gated_study.Spec) -> list[Setting]:
    """The arms of the study whose texts can differ, then the settings no arm names (the module's docstring)."""
    arms, seen = [], set()
    for arm in spec.arms:
        if text_key(arm) not in seen:
            seen.add(text_key(arm))
            arms.append(arm)
    plain = [arm for arm in arms if not arm.cheat and not arm.settings]
    seeded = next(arm for arm in plain if arm.pack)
    unseeded = next(arm for arm in plain if not arm.pack)
    out = [Setting(arm.name, arm, spec.seed) for arm in arms]
    out += [Setting(f"seed_{spec.seed + 1}", seeded, spec.seed + 1),
            Setting("team_of_10", with_pairs(seeded, "team_of_10", ["n_agents=10", "max_concurrency=10"]), spec.seed),
            Setting("no_variant", unseeded, spec.seed, variant=False)]
    levels = settings.load_instruction_levels()
    for role in ROLES:
        base = next(arm for arm in arms if run_study.setting(arm.settings, f"{role}s"))
        for level in levels[role]["order"]:
            arm = with_pairs(base, f"{role}_{level}", [f"{role}_level={level}"])
            if text_key(arm) not in seen:
                seen.add(text_key(arm))
                out.append(Setting(arm.name, arm, spec.seed))
        if role == "regulator":
            out.append(Setting("two_regulators", with_pairs(base, "two_regulators", ["regulators=2"]), spec.seed))
    return out


def cell_argv(spec: gated_study.Spec, task: str, setting: Setting) -> tuple[str, list[str]]:
    """The cell module and its arguments, as run_study launches the setting's arm for the study's first worker."""
    worker = next(iter(spec.workers))
    name, target = spec.target(task, worker, setting.arm)
    cell = run_study.Cell(name, target["packs"][0], setting.seed, target["module"], target["model"], None,
                          tuple(target.get("settings") or ()))
    return target["module"], cell.argv(list(spec.settings), replace=False)


# ------------------------------------------------------------------------------------------------ one cell
def cell_parts(module_name: str, argv: list[str], variant: bool = True):
    """(config, benchmark, extra instruction) as the cell script's main() builds them from argv. The statements of
    main() that assign args, config and benchmark run as they are, with argv as the command line. The extra
    instruction is what main() passes to swarm_task as extra_instruction, "" for a cell that passes none. With
    variant False the benchmark is built without its `variant` keyword."""
    module = importlib.import_module(module_name)
    main = next(node for node in ast.parse(inspect.getsource(module)).body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    wanted = ("args", "config", "benchmark")
    setup = [node for node in main.body if isinstance(node, ast.Assign) and len(node.targets) == 1
             and isinstance(node.targets[0], ast.Name) and node.targets[0].id in wanted]
    if [node.targets[0].id for node in setup] != list(wanted):
        raise ValueError(f"{module_name}: main() must assign args, config and benchmark, in that order")
    if not variant:
        call = setup[2].value
        call.keywords = [k for k in call.keywords if k.arg != "variant"]
    namespace = dict(vars(module))
    with mock.patch.object(sys, "argv", [module.__file__, *argv]):
        exec(compile(ast.Module(body=setup, type_ignores=[]), module.__file__, "exec"), namespace)
    extra = next((k.value for node in ast.walk(main) if isinstance(node, ast.Call)
                  and getattr(node.func, "id", None) == "swarm_task" for k in node.keywords
                  if k.arg == "extra_instruction"), None)
    text = eval(compile(ast.Expression(extra), module.__file__, "eval"), namespace) if extra else ""
    return namespace["config"], namespace["benchmark"], text


@contextmanager
def no_docker() -> Iterator[None]:
    """A sample built with no Docker: the SWE images by their tags, the machines' image by a fixed id, and creative
    writing's images by nothing, with its docker context and staging under a scratch folder. The tests stub the same
    calls (tests/unit/conftest.py)."""
    with tempfile.TemporaryDirectory() as scratch, \
            mock.patch.object(swe_task, "ensure_images", swe_sandbox.image_tags), \
            mock.patch.object(agent_machines, "ensure_image", lambda **_: "sha256:no-docker"), \
            mock.patch.object(creative_sandbox, "ensure_images", lambda **_: {}), \
            mock.patch.object(creative_sandbox, "BUILD", Path(scratch)):
        yield


def as_shown(tool) -> ToolDef:
    """A bound tool as the model receives it: team_submit named submit (shown_as_submit), every other tool as it is."""
    shown = shown_as_submit(tool)
    return shown if isinstance(shown, ToolDef) else ToolDef(shown)


def tool_text(definition) -> str:
    """One tool as its agent's model is offered it: the name, the description, then each parameter."""
    schema = definition.parameters.model_dump(mode="json", exclude_none=True)
    lines = [f"--- {definition.name}", definition.description.rstrip()]
    required = set(schema.get("required") or [])
    for name, prop in (schema.get("properties") or {}).items():
        prop = dict(prop)
        about = prop.pop("description", "")
        kind = prop.pop("type", None) or json.dumps(prop, sort_keys=True)
        rest = f" {json.dumps(prop, sort_keys=True)}" if prop and kind != json.dumps(prop, sort_keys=True) else ""
        lines.append(f"  {name} ({kind}{', required' if name in required else ''}){rest}: {about}".rstrip())
    return "\n".join(lines)


def agent_texts(config, benchmark, extra: str) -> dict[str, tuple[str, str, str, list[str]]]:
    """{agent: (role, system prompt, first message, tool texts)}, built as src/tasks.py swarm_task builds them. The
    tools are what the model receives: the agent's bound tools, team_submit shown as submit by the turn rules' filter
    (shown_as_submit). The filter withholds ORBIT's channel tools, so no file lists them.
    tests/unit/test_agent_texts_golden.py checks these steps against the task swarm_task builds."""
    config = run_limits(benchmark, config)       # the task's default tool limits, as swarm_task takes them first
    plan = benchmark.plan(config, tasks.agent_roles(config))
    with no_docker():
        sample = benchmark.sample(plan, tasks.instruction_suffix(benchmark, config, extra))
    first = (tasks.first_messages(sample, plan, benchmark) or sample.metadata["cheatbench"].get("first_message")
             or dict.fromkeys(plan.agents, sample.input))
    prompts = tasks.system_prompts(benchmark, plan)
    names, bindings = tasks.team_tools(benchmark, plan, benchmark.tools(plan))
    built, _ = resolve_tool_bindings(bindings, agent_tools=names, sample_id=sample.id, epoch=1)
    out = {}
    for agent in plan.agents:
        tools = [tool_text(as_shown(built[agent][name])) if name in bindings else f"--- {name}\n(ORBIT's own tool)"
                 for name in names[agent]]
        out[agent] = (role_of(plan.roles, agent), prompts[agent] or "(ORBIT's default system prompt)", first[agent],
                      tools)
    return out


def agent_file(task: str, setting: str, module: str, argv: list[str], agent: str, role: str, system: str,
               first: str, tools: list[str]) -> str:
    """One agent's file: a header, then its system prompt, its first message and its tools, each under a rule."""
    header = [f"# {task} / {setting} / {role} {agent}", f"# python -m {module} {' '.join(argv)}"]
    parts = ["\n".join(header), f"=== system prompt\n{system.rstrip()}", f"=== first message\n{first.rstrip()}",
             "=== tools\n" + "\n\n".join(tools)]
    return "\n\n".join(parts) + "\n"


# ------------------------------------------------------------------------------------------------ every file
def render(study_file: Path = STUDY_FILE) -> dict[str, str]:
    """{path under GOLDEN: text} for every task, setting and agent of the study file, in sorted order."""
    spec = gated_study.load_spec(study_file)
    files = {}
    for task in spec.tasks:
        for setting in study_settings(spec):
            module, argv = cell_argv(spec, task, setting)
            config, benchmark, extra = cell_parts(module, argv, variant=setting.variant)
            for agent, (role, system, first, tools) in agent_texts(config, benchmark, extra).items():
                files[f"{task}/{setting.name}/{role}_{agent}.txt"] = agent_file(task, setting.name, module, argv,
                                                                                agent, role, system, first, tools)
    return dict(sorted(files.items()))


def first_difference(files: dict[str, str], folder: Path = GOLDEN) -> str | None:
    """Where the folder first differs from the rendered files: a missing or extra file, or the first line that
    differs, with both lines. None when they are the same."""
    stored = {p.relative_to(folder).as_posix() for p in folder.rglob("*.txt")} if folder.is_dir() else set()
    for path in sorted(set(files) | stored):
        where = (folder / path).relative_to(ROOT) if folder.is_relative_to(ROOT) else folder / path
        if path not in stored:
            return f"{where}: no such file; `make texts` writes it"
        if path not in files:
            return f"{where}: the code renders no such file; `make texts` deletes it"
        old, new = (folder / path).read_text().splitlines(), files[path].splitlines()
        for number, (a, b) in enumerate(zip(old, new), start=1):
            if a != b:
                return f"{where} line {number}:\n  stored: {a}\n  now:    {b}"
        if len(old) != len(new):
            number = min(len(old), len(new)) + 1
            return f"{where} line {number}: the stored file has {len(old)} lines, the render {len(new)}"
    return None


def write(files: dict[str, str], folder: Path = GOLDEN) -> tuple[int, int]:
    """Write every file and delete each .txt under the folder the code no longer renders. Returns (written,
    deleted)."""
    deleted = 0
    for old in sorted(folder.rglob("*.txt")) if folder.is_dir() else []:
        if old.relative_to(folder).as_posix() not in files:
            old.unlink()
            deleted += 1
    for path, text in files.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text() != text:
            target.write_text(text)
    for empty in sorted((d for d in folder.rglob("*") if d.is_dir()), reverse=True):
        if not any(empty.iterdir()):
            empty.rmdir()
    return len(files), deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.study.agent_texts", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 at the first difference")
    parser.add_argument("--study", type=Path, default=STUDY_FILE, help="the study file (default: %(default)s)")
    args = parser.parse_args(argv)
    files = render(args.study)
    if args.check:
        found = first_difference(files)
        print(found or f"{len(files)} files, the same as {GOLDEN.relative_to(ROOT)}")
        return 1 if found else 0
    written, deleted = write(files)
    size = sum(len(text.encode()) for text in files.values())
    print(f"{written} files ({size / 1024:.0f} KB) in {GOLDEN.relative_to(ROOT)}; {deleted} deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
