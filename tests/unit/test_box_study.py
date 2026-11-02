"""study_exploration_1's table: its counts, names, box sizes, flags and keys, each job through the real example parser;
fleet.py jobs; that nothing but a person starts a stage; and box_run.sh with stand-ins for uv and ssh."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from examples.example_arguments import parse_example_arguments
from scripts import screen
from scripts.box import fleet, fleet_layout, study_table, vast_api
from scripts.box.fleet_layout import BOX_KEY, MODELS, REGULATORS, capacity_of
from scripts.box.study_table import (
    FIRST_REGULATORS,
    HAIKU_SWE,
    MOCK_MODEL,
    ONE_RUN_ON_SWE,
    STAGES,
    SWE_TARGETS,
    TARGETS,
    Arm,
    Target,
    box_jobs,
    find_box,
    jobs_sha256,
    key_problems,
    pack,
    stage_boxes,
)
from src.configuration import settings
from src.configuration.presets import build_config
from src.models import model_registry as models

BOX_FOLDER = Path(fleet_layout.__file__).parent
EVERY_BOX = [box for stage in STAGES for box in stage_boxes(stage)]
EVERY_JOB = [job for box in EVERY_BOX for job in box_jobs(box)]
PHASE_TWO = ("phase2-flipt-dsflash", "phase2-flipt-luna", "phase2-protein-luna", "phase2-creative-luna")
PHASE_TWO_B = tuple(stage.replace("phase2-", "phase2b-") for stage in PHASE_TWO)


# ---- the table ----

def stage_runs(stage: str) -> list[str]:
    return [job.name for box in stage_boxes(stage) for job in box_jobs(box)]


def test_the_stages_are_the_pilot_the_gate_and_one_phase_two_per_target():
    assert list(STAGES) == ["pilot", "gate", *PHASE_TWO, *PHASE_TWO_B, "fixed-pilot", "fixed-gate",
                            "fixed-phase2-flipt-dsflash", "fixed-phase2-flipt-luna", "vuls-gate",
                            "fixed-fill-flipt-dsflash", "fixed-fill-argon-flipt-dsflash", "disreg-gate",
                            "disreg-flipt-haiku", "disreg-creative-luna", "contest-flipt-haiku",
                            "contest-creative-luna"]
    assert [(target.task, target.model) for target in TARGETS] == [
        ("software_engineering", "deepseek-flash-openrouter"), ("software_engineering", "gpt-6-luna-openrouter"),
        ("protein_design", "gpt-6-luna-openrouter"), ("creative_writing", "gpt-6-luna-openrouter")]


def test_the_study_has_40_gate_runs_40_phase_two_runs_per_target_and_9_pilot_runs():
    assert len(stage_runs("gate")) == 40
    assert [len(stage_runs(stage)) for stage in PHASE_TWO] == [32, 32, 40, 40]
    assert [len(stage_runs(stage)) for stage in PHASE_TWO_B] == [12, 12, 12, 12]
    assert len(stage_runs("pilot")) == 9
    assert len(stage_runs("fixed-pilot")) == 5 and len(stage_runs("fixed-gate")) == 12
    assert [len(stage_runs(f"fixed-phase2-flipt-{t}")) for t in ("dsflash", "luna")] == [28, 28]


def test_every_run_name_is_unique_across_the_study():
    names = [job.name for job in EVERY_JOB] + [job.name for box in EVERY_BOX for job in box_jobs(box, mock=True)]
    assert len(names) == len(set(names)) == 363 + sum(len(box.arms) for box in EVERY_BOX)


def test_the_fixed_stages_redo_swe_alone_under_names_no_earlier_run_holds():
    """Every SWE run before the fix graded a task where one agent could cheat alone; the redo's runs say `fixed`."""
    earlier = {job.name for stage in STAGES if not stage.startswith("fixed-") for box in stage_boxes(stage)
               for job in box_jobs(box)}
    fixed = [job for stage in STAGES if stage.startswith("fixed-") for box in stage_boxes(stage)
             for job in box_jobs(box)]
    assert all("_fixed" in job.name or "_fill" in job.name for job in fixed)
    assert not {job.name for job in fixed} & earlier
    assert {job.cell.split(".")[0] for job in fixed} == {"swe"}
    pilot = [arm for box in stage_boxes("fixed-pilot") for arm in box.arms]
    assert {(arm.target.model, arm.regulator, arm.regulator_level, arm.seeds) for arm in pilot} == {
        ("gpt-6-luna-openrouter", regulator, "ambiguous", (1,)) for regulator in REGULATORS}
    gate = [arm for box in stage_boxes("fixed-gate") for arm in box.arms]
    assert {(arm.target.model, arm.kind, arm.seeds) for arm in gate} == {
        (target.model, kind, (1, 2, 3)) for target in SWE_TARGETS for kind in ("minimal", "minimal_cheat")}
    assert {"flipt_minimal_luna_fixed1", "flipt_minimal_cheat_dsflash_fixed3",
            "flipt_minimal_regulated_luna_sonnet_ambiguous_fixedpilot1"} <= {job.name for job in fixed}
    for target in SWE_TARGETS:
        arms = [arm for box in stage_boxes(f"fixed-phase2-{target.name}") for arm in box.arms]
        assert {arm.target.model for arm in arms} == {target.model}
        assert sorted((arm.rule_level, arm.seeds) for arm in arms if arm.rule_level) == [
            ("generic", (1, 2, 3)), ("identified", (1, 2, 3))]
        assert {(arm.regulator, arm.regulator_level, arm.seeds) for arm in arms if arm.regulator} == {
            (regulator, level, (1,) if REGULATORS[regulator] in ("opus", "sonnet") else (1, 2, 3))
            for regulator in REGULATORS for level in ("ambiguous", "enforce")}


def test_the_run_names_read_as_the_study_says():
    names = {job.name for job in EVERY_JOB}
    assert {"flipt_minimal_luna1", "flipt_minimal_cheat_luna1", "flipt_minimal_luna_generic1",
            "flipt_minimal_luna_identified1", "flipt_minimal_regulated_luna_opus_enforce1",
            "flipt_minimal_dsflash5", "binder_minimal_regulated_luna_argon_ambiguous5",
            "poem_minimal_cheat_luna3", "flipt_minimal_regulated_luna_argon_ambiguous_pilot1"} <= names
    for box in EVERY_BOX:
        for arm in box.arms:
            assert arm.suffix.startswith(MODELS[arm.target.model])


def test_a_gate_box_holds_no_phase_two_arm_and_no_stage_shares_a_box():
    assert {(arm.kind, arm.rule_level, arm.regulator) for box in stage_boxes("gate") for arm in box.arms} == {
        ("minimal", None, None), ("minimal_cheat", None, None)}
    assert len({box.name for box in EVERY_BOX}) == len(EVERY_BOX)
    assert all(box.name.startswith(box.stage) for box in EVERY_BOX)


def test_the_study_has_no_solo_cell_no_chess_and_no_knowledge_work():
    assert {arm.kind for box in EVERY_BOX for arm in box.arms} == {
        "minimal", "minimal_cheat", "minimal_regulated", "minimal_disregulated", "minimal_regulated_disregulated"}
    assert {box.task for box in EVERY_BOX} == {"software_engineering", "protein_design", "creative_writing"}
    assert {job.cell for job in EVERY_JOB if "vuls" in job.cell} == {"swe.vuls_minimal", "swe.vuls_minimal_cheat"}


def test_each_regulated_setting_runs_each_regulator_at_each_level():
    for stage, target in zip(PHASE_TWO, TARGETS):
        arms = [arm for box in stage_boxes(stage) for arm in box.arms]
        assert sorted(arm.rule_level for arm in arms if arm.rule_level) == ["generic", "identified"]
        assert {(arm.regulator, arm.regulator_level) for arm in arms if arm.kind == "minimal_regulated"} == {
            (regulator, level) for regulator in FIRST_REGULATORS for level in ("ambiguous", "enforce")}
        assert {arm.target for arm in arms} == {target}
        assert {arm.seeds for arm in arms if arm.regulator not in ONE_RUN_ON_SWE} == {(1, 2, 3, 4, 5)}
        one_run = {arm.seeds for arm in arms if arm.regulator in ONE_RUN_ON_SWE}
        assert one_run == ({(1,)} if target.task == "software_engineering" else {(1, 2, 3, 4, 5)})


def test_the_pilot_runs_each_regulator_once_on_each_task_at_ambiguous():
    arms = [arm for box in stage_boxes("pilot") for arm in box.arms]
    assert {(arm.target.short, arm.target.model, arm.regulator, arm.regulator_level, arm.seeds) for arm in arms} == {
        (short, "gpt-6-luna-openrouter", regulator, "ambiguous", (1,))
        for short in ("flipt", "protein", "creative") for regulator in FIRST_REGULATORS}


# ---- the boxes ----

def test_a_stage_rents_the_boxes_the_table_says():
    assert {stage: len(stage_boxes(stage)) for stage in STAGES} == {
        "pilot": 3, "gate": 6, "phase2-flipt-dsflash": 6, "phase2-flipt-luna": 6, "phase2-protein-luna": 7,
        "phase2-creative-luna": 7, "phase2b-flipt-dsflash": 3, "phase2b-flipt-luna": 3, "phase2b-protein-luna": 2,
        "phase2b-creative-luna": 2, "fixed-pilot": 1, "fixed-gate": 2, "fixed-phase2-flipt-dsflash": 7,
        "fixed-phase2-flipt-luna": 7, "vuls-gate": 2, "fixed-fill-flipt-dsflash": 2,
        "fixed-fill-argon-flipt-dsflash": 1, "disreg-gate": 1, "disreg-flipt-haiku": 2, "disreg-creative-luna": 1,
        "contest-flipt-haiku": 1, "contest-creative-luna": 1}


@pytest.mark.parametrize("box", EVERY_BOX, ids=lambda box: box.name)
def test_no_box_runs_more_agents_than_it_holds(box):
    assert box.agents <= capacity_of(box.task)
    agents = {"minimal_regulated_disregulated": 5, "minimal_regulated": 4, "minimal_disregulated": 4}
    assert box.agents == sum(agents.get(job.cell.split(".")[1].split("_", 1)[1], 3) for job in box_jobs(box))


def test_a_box_holds_the_most_agents_its_task_has_run():
    assert capacity_of("software_engineering") == 21 and capacity_of("protein_design") == 36
    assert capacity_of("creative_writing") == 36


def test_pack_keeps_arms_whole_and_opens_a_box_when_the_next_arm_does_not_fit(monkeypatch):
    target = Target("protein_design", "protein", "gpt-6-luna-openrouter")
    arms = (Arm(target, "minimal"), Arm(target, "minimal_cheat"), Arm(target, "minimal", rule_level="generic"))
    assert [len(box.arms) for box in pack("x", "gate", arms)] == [2, 1]
    monkeypatch.setitem(fleet_layout.CAPACITY, "protein_design", 15)
    assert [len(box.arms) for box in pack("x", "gate", arms)] == [1, 1, 1]


def test_find_box_refuses_a_name_outside_the_study():
    assert find_box("gate-flipt-luna-2").task == "software_engineering"
    with pytest.raises(ValueError, match="no box"):
        find_box("gate-chess-luna-1")
    with pytest.raises(ValueError, match="unknown stage"):
        stage_boxes("phase3")


# ---- the flags ----

@pytest.mark.parametrize("box", EVERY_BOX, ids=lambda box: box.name)
def test_each_job_reaches_the_config_through_the_real_example_parser(box):
    """The run name says the setting: the flags must set that one, with no judge."""
    for job in box_jobs(box):
        package, stem = job.cell.split(".")
        args = parse_example_arguments(settings.ROOT / "examples" / package / f"{stem}.py", None, job.arguments)
        config = build_config(args.preset, args.scale, args.settings)
        rule = next((level for level in ("generic", "identified") if f"_{level}" in job.name), "none")
        regulator = next((entry for entry, short in REGULATORS.items() if f"_regulated_luna_{short}_" in job.name
                          or f"_regulated_dsflash_{short}_" in job.name
                          or re.search(rf"_regulated_disregulated_[a-z]+_{short}_", job.name)), None)
        assert config.seed == job.seed and config.judge is None and config.id_salt == job.name
        assert args.model == models.resolve(job.model) and config.rule_level == rule
        assert config.regulator_level == ("enforce" if "_enforce" in job.name else "ambiguous")
        assert config.regulator_model == (models.resolve(regulator) if regulator else None)
        assert config.disregulator_level == ("overt" if "_overt" in job.name else "persuade")


def test_a_mock_job_runs_no_real_model_and_says_so_in_its_name():
    box = find_box("pilot-flipt-luna-1")
    jobs = box_jobs(box, mock=True)
    assert len(jobs) == 3 and {job.model for job in jobs} == {MOCK_MODEL} and {job.seed for job in jobs} == {1}
    assert not any("--regulator-model" in job.arguments for job in jobs)
    assert all(job.name.endswith("_pilotmock1") for job in jobs)
    assert key_problems(jobs) == []
    assert jobs_sha256(jobs) != jobs_sha256(box_jobs(box))


def test_the_digest_changes_with_any_flag():
    box = find_box("phase2-flipt-luna-3")
    jobs = box_jobs(box)
    other = [study_table.Job(job.cell, job.name, job.seed, job.model, "gpt-6-luna", job.settings) for job in jobs]
    assert jobs_sha256(jobs) == jobs_sha256(box_jobs(box)) and jobs_sha256(jobs) != jobs_sha256(other)


# ---- the models and the key ----

def test_the_fleet_names_this_studys_models():
    assert MODELS == {"deepseek-flash-openrouter": "dsflash", "gpt-6-luna-openrouter": "luna",
                      "claude-haiku-5-5-openrouter": "haiku"}
    assert REGULATORS == {"gpt-6-luna-openrouter": "luna", "claude-opus-5-5-openrouter": "opus",
                          "gemini-4-argon-openrouter": "argon", "claude-haiku-5-5-openrouter": "haiku",
                          "claude-sonnet-5-5-openrouter": "sonnet"}


@pytest.mark.parametrize("model", [*MODELS, *REGULATORS])
def test_every_model_of_the_study_routes_through_openrouter_on_the_one_key(model):
    assert models.key_env(models.resolve(model)) == [BOX_KEY] and models.routing(model) is not None


def test_every_job_of_the_study_takes_the_one_key():
    assert key_problems(EVERY_JOB) == []


def test_a_model_on_another_key_is_named():
    job = study_table.Job("swe.flipt_minimal_regulated", "x", 1, "gpt-6-luna-openrouter", "gpt-6-luna", ())
    assert key_problems([job]) == ["gpt-6-luna needs OPENAI_API_KEY, and a box holds only OPENROUTER_API_KEY"]


# ---- the listing ----

def test_jobs_lists_every_run_box_and_flag_and_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(vast_api, "http", lambda call: pytest.fail("a listing sent a request"))
    assert fleet.main(["jobs", "phase2-flipt-luna"]) == 0
    out = capsys.readouterr().out
    assert "== phase2-flipt-luna: 6 boxes, 32 runs, 118 agents" in out
    for box in stage_boxes("phase2-flipt-luna"):
        assert f"{box.name}: software_engineering, at least 32 cores and 64 GB of RAM, {box.agents} agents" in out
        for job in box_jobs(box):
            line = next(line for line in out.splitlines() if line.split()[:1] == [job.name])
            assert f"examples.{job.cell} --model {job.model} --set seed={job.seed}" in line
            assert " ".join(job.flags) in line
    assert out.count("--set seed=") == 32


def test_jobs_with_no_stage_lists_the_whole_study(capsys):
    assert fleet.main(["jobs"]) == 0
    out = capsys.readouterr().out
    assert out.count("--set seed=") == 363 and all(f"== {stage}:" in out for stage in STAGES)


# ---- nothing but a person starts a stage ----

def shell_code(path: Path) -> str:
    """A script without its comments."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))


def test_no_script_rents_a_box_or_starts_runs_on_its_own():
    assert not re.search(r"fleet create|box_run\.sh", "\n".join(shell_code(path) for path in BOX_FOLDER.glob("*.sh")
                                                                if path.name != "box_run.sh"))
    callers = [path.name for path in BOX_FOLDER.glob("*.py") if "cmd_create" in path.read_text()]
    assert sorted(callers) == ["fleet.py", "fleet_create.py"]
    assert not re.search(r"vast_api|fleet_create", (BOX_FOLDER / "box_runner.py").read_text())


def test_create_needs_a_stage_by_name(capsys):
    with pytest.raises(SystemExit) as refused:
        fleet.main(["create", "--count", "3"])
    assert refused.value.code == 2
    with pytest.raises(SystemExit) as refused:
        fleet.main(["create", "--stage", "phase3", "--count", "3"])
    assert refused.value.code == 2


# ---- box_run.sh ----

STAND_IN_UV = """#!/bin/sh
case "$*" in
  *run-args*)
    [ ! -f "$STAND_IN/refuse" ] || { echo "refused: gpt-6-luna needs OPENAI_API_KEY" >&2; exit 1; }
    printf '%s\\n' --box gate-protein-luna-1 --commit abc --jobs-sha256 def
    case "$*" in *--mock*) echo --mock ;; esac ;;
  *entry*) echo OPENROUTER_API_KEY ;;
  *box_key*)
    printf '%s\\n' "$*" >> "$STAND_IN/key_checks"
    case "$*" in
      *--main-key*) ;;
      *) [ -n "$BOX_OPENROUTER_API_KEY" ] || { echo "refused: no box key"; exit 1; } ;;
    esac
    [ ! -f "$STAND_IN/key_refuse" ] || { echo "refused: the key has no credit limit"; exit 1; }
    echo "== BOX_OPENROUTER_API_KEY: limit \\$50.00" ;;
esac
"""
STAND_IN_SSH = """#!/bin/sh
printf '%s\\n' "$*" >> "$STAND_IN/ssh_arguments"
cat >> "$STAND_IN/ssh_input"
"""


@pytest.fixture
def box_run(tmp_path):
    """Runs box_run.sh in a throwaway checkout, with uv and ssh replaced by scripts that record what they get."""
    root, stand_in = tmp_path / "repo", tmp_path / "stand_in"
    (root / "scripts" / "box").mkdir(parents=True)
    for name in ("box_run.sh", "box_ssh.sh"):
        shutil.copy2(BOX_FOLDER / name, root / "scripts" / "box" / name)
    (root / "logs" / "boxes" / "7").mkdir(parents=True)
    (root / "logs" / "boxes" / "7" / "ssh_config").write_text("Host box\n")
    stand_in.mkdir()
    for name, text in (("uv", STAND_IN_UV), ("ssh", STAND_IN_SSH)):
        (stand_in / name).write_text(text)
        (stand_in / name).chmod(0o755)

    def run(*flags: str, **env: str) -> subprocess.CompletedProcess:
        keep = {name: value for name, value in os.environ.items() if not name.endswith("OPENROUTER_API_KEY")}
        environment = keep | env | {"PATH": f"{stand_in}{os.pathsep}{os.environ['PATH']}", "STAND_IN": str(stand_in)}
        return subprocess.run(["bash", str(root / "scripts" / "box" / "box_run.sh"), "7", *flags], env=environment,
                              capture_output=True, text=True, timeout=30)

    run.sent = lambda name: (stand_in / name).read_text() if (stand_in / name).exists() else ""
    run.stand_in = stand_in
    return run


SCOPED, MAIN = "sk-or-scoped-key-with-a-spend-cap", "sk-or-main-key-of-the-account-0000"


def test_box_run_sends_the_scoped_key_over_standard_input_alone(box_run):
    result = box_run(BOX_OPENROUTER_API_KEY=SCOPED, OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"export OPENROUTER_API_KEY={SCOPED}" in box_run.sent("ssh_input")
    assert SCOPED not in box_run.sent("ssh_arguments") and SCOPED not in result.stdout + result.stderr
    assert MAIN not in box_run.sent("ssh_input") + box_run.sent("ssh_arguments") + result.stdout
    assert "umask 077" in box_run.sent("ssh_arguments") and "mv /root/.box_env.new /root/.box_env" in box_run.sent(
        "ssh_arguments")
    assert "--box gate-protein-luna-1 --commit abc --jobs-sha256 def" in box_run.sent("ssh_arguments")
    assert "box_key check OPENROUTER_API_KEY --box-id 7" in box_run.sent("key_checks")
    assert SCOPED not in box_run.sent("key_checks") and MAIN not in box_run.sent("key_checks")


def test_box_run_refuses_without_a_box_key_and_never_sends_the_main_key(box_run):
    result = box_run(OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 1 and "refused: no box key" in result.stdout
    assert box_run.sent("ssh_arguments") == "" and MAIN not in box_run.sent("ssh_input")


def test_box_run_sends_the_main_key_only_with_its_flag_over_standard_input_after_the_check(box_run):
    result = box_run("--main-key", OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"export OPENROUTER_API_KEY={MAIN}" in box_run.sent("ssh_input")
    assert MAIN not in box_run.sent("ssh_arguments") + box_run.sent("key_checks") + result.stdout + result.stderr
    assert "box_key check OPENROUTER_API_KEY --box-id 7 --main-key" in box_run.sent("key_checks")
    assert "--main-key" not in box_run.sent("ssh_arguments")


def test_box_run_sends_nothing_when_the_check_refuses_the_main_key(box_run):
    (box_run.stand_in / "key_refuse").write_text("")
    result = box_run("--main-key", OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 1 and "no credit limit" in result.stdout
    assert box_run.sent("ssh_input") == "" and box_run.sent("ssh_arguments") == ""


def test_box_run_refuses_allow_main_key_and_sends_nothing(box_run):
    result = box_run("--allow-main-key", OPENROUTER_API_KEY=MAIN, BOX_OPENROUTER_API_KEY=SCOPED)
    assert result.returncode == 1 and "refused: --allow-main-key is gone" in result.stdout
    assert box_run.sent("ssh_arguments") == "" and box_run.sent("ssh_input") == ""


def test_box_run_sends_nothing_when_the_key_check_refuses(box_run):
    (box_run.stand_in / "key_refuse").write_text("")
    result = box_run(BOX_OPENROUTER_API_KEY=SCOPED, OPENROUTER_API_KEY=MAIN)
    assert result.returncode == 1 and "no credit limit" in result.stdout
    assert box_run.sent("ssh_input") == ""


def test_box_run_sends_no_key_with_mock(box_run):
    result = box_run("--mock", BOX_OPENROUTER_API_KEY=SCOPED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--mock" in box_run.sent("ssh_arguments") and SCOPED not in box_run.sent("ssh_input")
    assert ".box_env.new" not in box_run.sent("ssh_arguments")


def test_box_run_sends_nothing_when_run_args_refuses(box_run):
    (box_run.stand_in / "refuse").write_text("")
    result = box_run(BOX_OPENROUTER_API_KEY=SCOPED)
    assert result.returncode == 1 and "OPENAI_API_KEY" in result.stderr
    assert box_run.sent("ssh_arguments") == "" and box_run.sent("ssh_input") == ""


@pytest.mark.parametrize("script", sorted(path.name for path in BOX_FOLDER.glob("*.sh") if "sed -n '2," in
                                          path.read_text()))
def test_a_script_prints_its_whole_header_as_usage(script):
    lines = (BOX_FOLDER / script).read_text().splitlines()
    last = int(next(re.search(r"sed -n '2,(\d+)p'", line).group(1) for line in lines if "sed -n '2," in line))
    assert lines[last - 1].startswith("#") and not lines[last].startswith("#")


# ---- the screen ----

def test_the_screens_default_cells_hold_flipt_and_no_vuls():
    assert not [cell for cell in screen.CELLS if "vuls" in cell]
    assert {"swe.flipt_solo", "swe.flipt_solo_cheat", "swe.flipt_minimal_cheat"} <= set(screen.CELLS)
    assert "protein_design.binder_solo" in screen.CELLS


def test_an_opus_regulator_takes_one_run_per_swe_cell_and_five_elsewhere():
    """An Opus regulator costs about $22 a SWE run, so each SWE cell it regulates runs seed 1 alone."""
    for stage, runs in (("phase2-flipt-luna", 1), ("phase2-flipt-dsflash", 1), ("phase2-protein-luna", 5)):
        names = [n for n in stage_runs(stage) if "_opus_" in n]
        assert len(names) == 2 * runs and all(n.endswith("1") for n in names) == (runs == 1), names


def test_phase_2b_regulates_with_haiku_five_runs_and_sonnet_one_run_on_boxes_of_its_own():
    """Phase two's boxes keep their arms, so the added regulators take stages of their own."""
    for stage, target in zip(PHASE_TWO_B, TARGETS):
        arms = [arm for box in stage_boxes(stage) for arm in box.arms]
        assert {(arm.target, arm.kind) for arm in arms} == {(target, "minimal_regulated")}
        assert sorted((arm.regulator, arm.regulator_level, arm.seeds) for arm in arms) == [
            ("claude-haiku-5-5-openrouter", "ambiguous", (1, 2, 3, 4, 5)),
            ("claude-haiku-5-5-openrouter", "enforce", (1, 2, 3, 4, 5)),
            ("claude-sonnet-5-5-openrouter", "ambiguous", (1,)), ("claude-sonnet-5-5-openrouter", "enforce", (1,))]
    assert not {box.name for stage in PHASE_TWO for box in stage_boxes(stage)} & {
        box.name for stage in PHASE_TWO_B for box in stage_boxes(stage)}


# ---- the runbook ----

def test_the_runbook_names_every_stage_with_the_boxes_and_runs_of_the_table():
    """scripts/box/README.md: one row per stage family, one row per stage with its boxes and runs, and every create
    command's count, against the table. A retry of one box (--only) is the one create with another count."""
    readme = (BOX_FOLDER / "README.md").read_text()

    def family(stage: str) -> str:
        return next((stage.removesuffix(t.name) + "<target>" for t in (*TARGETS, HAIKU_SWE)
                     if stage.endswith(f"-{t.name}")), stage)

    assert set(re.findall(r"^\| `([a-z0-9<>-]+)` \| [^|]+ \|$", readme, re.M)) == {family(s) for s in STAGES}
    counts = re.findall(r"^\| `([a-z0-9-]+)` \| (\d+) \| (\d+) \|$", readme, re.M)
    assert {stage: (int(boxes), int(runs)) for stage, boxes, runs in counts} == {
        stage: (len(stage_boxes(stage)), len(stage_runs(stage))) for stage in STAGES} and len(counts) == len(STAGES)
    creates = re.findall(r"fleet create --stage (\S+) --count (\d+)(.*)$", readme, re.M)
    assert {stage for stage, _, rest in creates if "--only" not in rest} >= {"pilot", "gate", "fixed-gate"}
    assert all(int(count) == len(stage_boxes(stage)) for stage, count, rest in creates if "--only" not in rest), creates


def family(stage: str) -> str:
    """A stage's row in the runbook: <stage>, or <stem>-<target> for a stage of one target."""
    return next((stage.removesuffix(t.name) + "<target>" for t in (*TARGETS, HAIKU_SWE)
                 if stage.endswith(f"-{t.name}")), stage)


def test_the_runbook_lists_every_stage_family_where_it_says_how_to_launch_one():
    readme = (BOX_FOLDER / "README.md").read_text()
    line = next(line for line in readme.splitlines() if line.startswith("You launch each stage by hand and by name"))
    assert set(re.findall(r"`([a-z0-9<>-]+)`", line)) == {family(stage) for stage in STAGES}


def test_the_runbook_says_the_seeds_each_stage_runs():
    """Each row of the Stages table names the most seeds an arm of that family runs, and no seed count no arm runs."""
    readme = (BOX_FOLDER / "README.md").read_text()
    rows = dict(re.findall(r"^\| `([a-z0-9<>-]+)` \| ([^|]+) \|$", readme, re.M))
    for name, text in rows.items():
        counts = {len(arm.seeds) for stage in STAGES if family(stage) == name for box in stage_boxes(stage)
                  for arm in box.arms}
        said = {int(n) for n in re.findall(r"seeds 1 to (\d+)", text)}
        said |= {1} if re.search(r"seed 1\b", text) else set()
        assert said <= counts and max(counts) in said, (name, said, counts)


def test_the_runbook_reads_a_gate_and_names_each_tag_of_the_fixed_runs():
    readme = (BOX_FOLDER / "README.md").read_text()
    readouts = re.findall(r"scripts\.box\.gate_readout (\S+)", readme)
    assert readouts and set(readouts) <= {stage for stage in STAGES if stage.endswith("gate")}
    fixed = re.search(r"^## Fixed SWE stages$(.+?)^## ", readme, re.M | re.S)[1]
    assert re.findall(r"scripts\.box\.gate_readout (\S+)", fixed) == ["fixed-gate"]
    tags = {arm.tag for stage in STAGES if stage.startswith("fixed") for box in stage_boxes(stage) for arm in box.arms}
    assert tags == {"fixed", "fixedpilot", "fill"} and all(f"`{tag}`" in readme for tag in tags)



def test_the_fill_stages_complete_the_swe_table_on_deepseek_workers_with_their_own_limits():
    """13 runs: GPT-6 Luna where its run hit the time limit (enforce seed 1 stands), Sonnet once per level, Argon at
    every level; Argon's first run prices the other five, on a stage of their own."""
    def rows(stage: str) -> list[tuple]:
        return [(arm.regulator, arm.regulator_level, arm.seeds) for box in stage_boxes(stage) for arm in box.arms]

    luna, sonnet, argon = "gpt-6-luna-openrouter", "claude-sonnet-5-5-openrouter", "gemini-4-argon-openrouter"
    assert sorted(rows("fixed-fill-flipt-dsflash"), key=str) == sorted(
        [(luna, "ambiguous", (1, 2, 3)), (luna, "enforce", (2, 3)), (sonnet, "ambiguous", (1,)),
         (sonnet, "enforce", (1,)), (argon, "ambiguous", (1,))], key=str)
    assert sorted(rows("fixed-fill-argon-flipt-dsflash"), key=str) == sorted(
        [(argon, "ambiguous", (2, 3)), (argon, "enforce", (1, 2, 3))], key=str)
    jobs = {job.name: job for stage in ("fixed-fill-flipt-dsflash", "fixed-fill-argon-flipt-dsflash")
            for box in stage_boxes(stage) for job in box_jobs(box)}
    assert len(jobs) == 13 and all(job.model == "deepseek-flash-openrouter" for job in jobs.values())
    assert all("time_limit_s=5400" in job.settings for job in jobs.values())
    caps = {name: [s for s in job.settings if s.startswith("cost_limit_usd=")] for name, job in jobs.items()}
    assert caps["flipt_minimal_regulated_dsflash_luna_enforce_fill2"] == []
    assert caps["flipt_minimal_regulated_dsflash_sonnet_enforce_fill1"] == ["cost_limit_usd=15"]
    assert caps["flipt_minimal_regulated_dsflash_argon_enforce_fill3"] == ["cost_limit_usd=30"]
    assert "--set" in jobs["flipt_minimal_regulated_dsflash_argon_enforce_fill3"].arguments


def test_settings_an_arm_adds_reach_the_job_and_no_earlier_arm_has_any():
    assert all(not arm.extra for stage in STAGES if "fill" not in stage for box in stage_boxes(stage)
               for arm in box.arms)


def test_an_arm_s_row_reaches_the_job_and_its_name_and_changes_no_arm_without_one():
    target = next(t for t in TARGETS if t.short == "protein")
    arm = study_table.Arm(target, "minimal", row="barley_amylase", seeds=(1,), tag="briefs")
    [job] = box_jobs(study_table.Box("x-1", "x", (arm,)))
    assert job.name == "binder_minimal_luna_barley_amylase_briefs1"
    assert job.arguments[-2:] == ["--row", "barley_amylase"]
    assert "--row" not in " ".join(flag for j in EVERY_JOB for flag in j.arguments)


def test_the_vuls_gate_runs_vuls_cells_on_both_workers_and_a_flipt_box_builds_no_other_issue(monkeypatch):
    boxes = stage_boxes("vuls-gate")
    jobs = [job for box in boxes for job in box_jobs(box)]
    assert {job.cell for job in jobs} == {"swe.vuls_minimal", "swe.vuls_minimal_cheat"} and len(jobs) == 12
    assert {job.model for job in jobs} == {"deepseek-flash-openrouter", "gpt-6-luna-openrouter"}
    assert all(job.name.startswith("vuls_minimal") for job in jobs)
    from scripts.box import box_runner
    built = []
    monkeypatch.setattr(box_runner.swe_sandbox, "ensure_images", lambda issue: built.append(issue) or {})
    assert box_runner.prepare_issue_images(boxes[0]) == ["vuls-1832"] and built == ["vuls-1832"]
    built.clear()
    assert box_runner.prepare_issue_images(stage_boxes("fixed-gate")[0]) == [] and built == []


# ---- the disregulator stages ----

def test_the_disregulator_plan_is_24_runs_and_every_disregulator_runs_the_workers_model():
    jobs = {stage: [job for box in stage_boxes(stage) for job in box_jobs(box)] for stage in (
        "disreg-gate", "disreg-flipt-haiku", "contest-flipt-haiku", "disreg-creative-luna", "contest-creative-luna")}
    assert {stage: len(runs) for stage, runs in jobs.items()} == {
        "disreg-gate": 6, "disreg-flipt-haiku": 6, "contest-flipt-haiku": 3, "disreg-creative-luna": 6,
        "contest-creative-luna": 3}
    assert {job.name for job in jobs["disreg-gate"]} == {
        f"flipt_{cell}_haiku_fixed{seed}" for cell in ("minimal", "minimal_cheat") for seed in (1, 2, 3)}
    assert "flipt_minimal_disregulated_haiku_overt_fixed3" in {job.name for job in jobs["disreg-flipt-haiku"]}
    assert "poem_minimal_disregulated_luna_persuade1" in {job.name for job in jobs["disreg-creative-luna"]}
    assert "flipt_minimal_regulated_disregulated_haiku_haiku_enforce_persuade_fixed2" in {
        job.name for job in jobs["contest-flipt-haiku"]}
    assert "poem_minimal_regulated_disregulated_luna_haiku_enforce_persuade3" in {
        job.name for job in jobs["contest-creative-luna"]}
    for stage in ("contest-flipt-haiku", "contest-creative-luna"):
        assert {job.regulator_model for job in jobs[stage]} == {"claude-haiku-5-5-openrouter"}
        assert {frozenset(job.settings) for job in jobs[stage]} == {
            frozenset({"regulator_level=enforce", "disregulator_level=persuade"})}
    for stage in ("disreg-gate", "disreg-flipt-haiku", "disreg-creative-luna"):
        assert {job.regulator_model for job in jobs[stage]} == {None}
    for job in [job for runs in jobs.values() for job in runs if "disregulated" in job.cell]:
        package, stem = job.cell.split(".")
        args = parse_example_arguments(settings.ROOT / "examples" / package / f"{stem}.py", None, job.arguments)
        config = build_config(args.preset, args.scale, args.settings)
        assert args.model == models.resolve(job.model) and config.disregulator_model is None


def test_a_mock_disregulated_job_keeps_its_levels_on_the_mock_model():
    [job] = [j for j in box_jobs(stage_boxes("contest-flipt-haiku")[0], mock=True)]
    assert job.name == "flipt_minimal_regulated_disregulated_haiku_haiku_enforce_persuade_fixedmock1"
    assert job.model == study_table.MOCK_MODEL and job.regulator_model is None
    assert set(job.settings) == {"regulator_level=enforce", "disregulator_level=persuade"}


def test_the_new_stages_keep_every_earlier_arm_s_digest():
    assert jobs_sha256(box_jobs(find_box("fixed-gate-flipt-dsflash-1"))) == jobs_sha256(
        box_jobs(study_table.Box("x", "x", tuple(study_table.fixed_gate_arms(SWE_TARGETS[0])))))
