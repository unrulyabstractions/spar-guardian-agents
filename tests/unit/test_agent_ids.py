"""The agents' IDs (src/configuration/swarm_config.py agent_ids): drawn from the run's seed, valid wherever a name goes,
silent about seats and roles. The roster an agent reads is sorted, the seats stay in the roles and the deal, an access
map names seats, and what is keyed by the name works on a drawn ID. No model calls, no Docker."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from scripts import screen
from src.configuration import settings
from src.configuration.presets import build_config
from src.configuration.scales import SCALES
from src.configuration.swarm_config import ID_POOL, SwarmConfig
from src.domains import DOMAINS
from src.domains.openmath import OpenMath
from src.domains.swe import swe_sandbox
from src.measures import code_ladder
from src.measures import team_ladder as team
from src.measures.run_directory import RunDir
from src.run import access_line
from src.swarm.hidden_roles import assign_roles
from src.swarm.turn_rules._turn_steps import follow_up_id, free_index, is_round_step, step_id, step_round
from src.tasks import hidden_roles, swarm_task
from tests.support.ordered_ids import DRAWN, drawn_ids, ordered_ids
from tests.support.stub_ladder import OPENS, SHARDS, make_run

LARGEST = max(scale.n_agents for scale in SCALES.values())
LINUX_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")      # useradd's default NAME_REGEX, within its 32 characters
ENTRY_POINTS = """
import json, sys
from src.configuration.presets import PRESETS, build_config
from src.configuration.scales import SCALES
from src.configuration.swarm_config import SwarmConfig
print(json.dumps({"presets": [PRESETS[p].at_scale(s).names for p in PRESETS for s in SCALES],
                  "lone": PRESETS["default"].replace(n_agents=1).names, "fields": SwarmConfig(n_agents=3).names,
                  "built": build_config("default", None, ["seed=1"]).names, "tests": "tests" in sys.modules}))
"""


@pytest.fixture
def drawn():
    """The real draw in place of the unit tests' ordered names."""
    with drawn_ids():
        yield


def first_seen(text: str, names: list[str]) -> list[str]:
    """The names the text holds, in the order it first mentions them."""
    return sorted((name for name in names if name in text), key=text.index)


def config_with_seats_reversed(*pairs: str) -> SwarmConfig:
    """A config whose seat order is the roster's exactly backwards, so that any list in seat order shows."""
    for seed in range(5000):
        config = build_config("default", "small", [*pairs, f"seed={seed}"])
        if config.seats == config.names[::-1]:
            return config
    raise AssertionError("no seed reverses the seats")


# ---------------------------------------------------------------- the draw
def test_a_seed_draws_the_same_ids_again(drawn):
    assert DRAWN(3, 7) == DRAWN(3, 7) and DRAWN(LARGEST, 7) == DRAWN(LARGEST, 7)
    config = build_config("default", "small", ["seed=7"])
    assert config.seats == DRAWN(3, 7) and config.names == sorted(DRAWN(3, 7))
    assert config.replace(judge=None).names == config.names         # another field leaves the IDs alone


def test_different_seeds_draw_different_ids():
    for n in (1, 3, 4):
        assert len({tuple(DRAWN(n, seed)) for seed in range(100)}) == 100
    assert len({DRAWN(3, seed)[0] for seed in range(100)}) > 90     # a seat keeps no label across seeds


@pytest.mark.parametrize("n", [1, 3, 10, 100, 500, 1000])
def test_ids_are_unique_and_one_length_at_every_size(n):
    for seed in range(3):
        names = DRAWN(n, seed)
        assert len(names) == len(set(names)) == n and {len(name) for name in names} == {5}


def test_the_supply_stays_well_above_the_largest_scale():
    assert len(set(ID_POOL)) == len(ID_POOL) >= 50 * LARGEST
    assert len(set(DRAWN(LARGEST, 0))) == LARGEST


def test_every_id_is_a_valid_user_git_author_directory_and_episode_part():
    for name in ID_POOL:
        assert LINUX_USER.fullmatch(name) and name == Path(name).name and f"r#1@{name}".rsplit("@", 1)[1] == name, name
        assert len(name) == 5 and name.isalnum() and name.islower(), name
        assert re.search(r"\d", name), f"{name} could be a word"
        assert not re.fullmatch(r"[0-9a-f]+", name), f"{name} reads as a commit hash"
        assert not re.fullmatch(r"a\d+", name), f"{name} has the old ordered shape"
        assert not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", name), f"{name} reads as a chess move"
    assert not {name[0] for name in ID_POOL} & set("rdw"), "an ID opens with a role's letter"
    assert yaml.safe_load(yaml.safe_dump(dict.fromkeys(ID_POOL[:3000], 1))) == dict.fromkeys(ID_POOL[:3000], 1)
    assert subprocess.run(["git", "check-ref-format", "--branch", DRAWN(1, 0)[0]], capture_output=True).returncode == 0


def test_a_lone_agent_draws_an_id_too(drawn):
    config = build_config("default", "small", ["seed=4"]).replace(n_agents=1)
    [spec] = swarm_task(OpenMath(), config).dataset[0].metadata["experiment"]["setup"]["agents"]
    assert config.names == DRAWN(1, 4) and spec["name"] == config.names[0]
    assert spec["system_prompt"].startswith(f"You are {config.names[0]}.")


# ---------------------------------------------------------------- what an ID and a roster tell
def test_an_ids_place_in_the_roster_tells_no_seat(drawn):
    configs = [SwarmConfig(n_agents=4, seed=seed) for seed in range(400)]
    assert all(c.names == sorted(c.seats) for c in configs)
    for seat in range(4):
        places = Counter(c.names.index(c.seats[seat]) for c in configs)
        assert set(places) == {0, 1, 2, 3} and min(places.values()) > 70, (seat, places)


def test_an_ids_place_in_the_roster_tells_no_role(drawn):
    configs = [build_config("default", "small", ["n_agents=4", "regulators=1", f"seed={seed}"]) for seed in range(400)]
    places = Counter()
    for config in configs:
        roles = hidden_roles(config)
        assert list(roles) == config.names
        places[config.names.index(next(a for a, role in roles.items() if role == "regulator"))] += 1
    assert set(places) == {0, 1, 2, 3} and min(places.values()) > 70, places


@pytest.mark.parametrize("key", sorted(DOMAINS))
def test_no_list_an_agent_can_read_follows_the_seats(key, drawn):
    """Every text of the task under seats exactly backwards: the system prompts, the setup script, the compose file and
    the blocks staged into the sandbox mention the agents in the roster's order."""
    config = config_with_seats_reversed("n_agents=4", "regulators=1", "max_concurrency=4")
    sample = swarm_task(DOMAINS[key](), config).dataset[0]
    specs = sample.metadata["experiment"]["setup"]["agents"]
    assert [spec["name"] for spec in specs] == config.names
    mentioned = 0
    for spec in specs:
        others = [name for name in config.names if name != spec["name"]]
        prompt = spec.get("system_prompt") or ""
        for text in (prompt, *prompt.split("\n")):             # the whole prompt, then each sentence's own list
            shown = first_seen(text, others)
            mentioned += len(shown)
            assert shown == [name for name in others if name in shown], text
    assert mentioned > 0
    cheatbench = sample.metadata["cheatbench"]
    assert list(cheatbench["roles"]) == config.names and list(cheatbench["access"]["agents"]) == config.names
    compose = Path(sample.sandbox.config).read_text() if sample.sandbox else ""
    for text in (json.dumps(cheatbench), sample.setup or "", compose):
        shown = first_seen(text, config.names)
        assert shown == [name for name in config.names if name in shown], text[:400]


def test_openmath_makes_its_users_in_roster_order(drawn):
    config = config_with_seats_reversed()
    script = swarm_task(OpenMath(), config).dataset[0].setup
    assert first_seen(script, config.names) == config.names
    agents = OpenMath().stage(OpenMath().plan(config), OpenMath().row_of("riemann")).block["agents"]
    assert [agents[name]["uid"] for name in config.names] == sorted(seat["uid"] for seat in agents.values())


# ---------------------------------------------------------------- the seats
def test_the_deal_follows_the_seats_and_not_the_roster(drawn):
    config = config_with_seats_reversed()
    holds = OpenMath().plan(config).holds
    assert [holds[name] for name in config.seats] == [["slice1"], ["slice2"], ["slice3"]]
    assert list(holds) == config.names
    assert access_line(swarm_task(OpenMath(), config)).endswith(
        ", ".join(f"{name} {holds[name][0]}" for name in config.names))


def test_the_roles_follow_the_seats_and_not_the_roster(drawn):
    for seed in range(20):
        config = build_config("default", "small", ["n_agents=5", "regulators=1", "disregulators=1", f"seed={seed}"])
        ordered = assign_roles(ordered_ids(5), 1, 1, seed)
        assert [hidden_roles(config)[name] for name in config.seats] == list(ordered.values())


def test_a_map_names_seats_and_lands_on_the_drawn_ids(drawn):
    config = config_with_seats_reversed("access=seat1:slice1+slice2,seat2:slice3,seat3:")
    plan = OpenMath().plan(config)
    first, second, third = config.seats
    assert plan.holds == {first: ["slice1", "slice2"], second: ["slice3"], third: []}
    assert list(plan.holds) == config.names and plan.pieces == {first: [1, 2], second: [3], third: []}
    cheatbench = swarm_task(OpenMath(), config).dataset[0].metadata["cheatbench"]
    assert cheatbench["access"]["policy"] == "explicit" and cheatbench["access"]["agents"] == plan.holds


def test_a_map_written_with_ids_is_refused(drawn):
    names = build_config("default", "small", []).names
    for keys in (ordered_ids(3), names):
        text = ",".join(f"{key}:slice{k}" for k, key in enumerate(keys, 1))
        with pytest.raises(ValidationError, match="every seat"):
            build_config("default", "small", [f"access={text}"])
        with pytest.raises(ValidationError, match="every seat"):
            SwarmConfig(n_agents=3, access={key: [] for key in keys})
    with pytest.raises(ValidationError, match="every seat"):
        build_config("default", "small", ["access=seat1:slice1,seat2:slice2,seat4:slice3"])


# ---------------------------------------------------------------- what is keyed by the name
def test_step_ids_carry_a_drawn_id():
    for name in DRAWN(100, 1):
        assert step_id(3, name) == f"r3-{name}" and step_round(step_id(12, name)) == 12
        assert is_round_step(step_id(3, name)) and is_round_step(step_id(9, name, revision=4))
        follow_up = follow_up_id(step_id(9, name, revision=4), 2)
        assert (step_round(follow_up), free_index(follow_up), is_round_step(follow_up)) == (9, 2, False)


def test_the_ladders_roster_matching_reads_a_drawn_id():
    sender, *teammates = DRAWN(3, 2)
    assert team.asks(f"{teammates[0]}: post it", teammates) and team.asks(f"could {teammates[1]} read it", teammates)
    assert team.asks(f"{teammates[0]}/{teammates[1]}, did you read it?", teammates)
    assert not team.asks("team: post it", teammates)                # a word is not an agent
    assert not team.asks(f"{sender}: post it", teammates)           # nor is the sender


def test_the_ladder_reads_a_run_of_drawn_ids(tmp_path, key_task):
    first, second, third = sorted(DRAWN(3, 2))
    both = f"{SHARDS['shard1']} / {SHARDS['shard2']}"
    calls = {first: OPENS, second: [("board_read", "", f"{first} wrote: {both}")], third: []}
    posts = [{"sender": first, "content": f"{second}: read the shard here: {both}", "readers": [second]}]
    access = {"policy": "split", "agents": {first: {"holds": ["shard1"]}, second: {"holds": ["shard2"]},
                                            third: {"holds": []}}}
    folder = make_run(tmp_path, calls, {"access": access}, posts)
    code_ladder.run_signals(folder)
    rows = {row["agent"]: row for row in RunDir(folder).signals()}
    [record] = RunDir(folder).team_signals()
    assert list(rows) == record["agents"] == [first, second, third]
    assert rows[second]["pieces_seen"] == ["shard1", "shard2"]
    assert record["collaboration_evidence"]["crossed"] == [f"{second}: saw shard1 holding shard2"]
    assert [line.split(":")[0] for line in record["collaboration_evidence"]["solicited"]] == [f"post 1 by {first}"]


def test_each_roster_gets_a_compose_file_of_its_own(monkeypatch, tmp_path):
    monkeypatch.setattr(swe_sandbox, "BUILD", tmp_path)
    rosters = [DRAWN(3, 1), DRAWN(3, 2)]
    paths = [swe_sandbox.write_compose("flipt-02e2", names, team_remote=True) for names in rosters]
    assert paths[0] != paths[1]
    for names, path in zip(rosters, paths):
        assert set(yaml.safe_load(path.read_text())["services"]) == {"default", *names}


# ---------------------------------------------------------------- real runs
def entry_points(hash_seed: str) -> dict:
    """What a fresh process without the tests' switch builds."""
    done = subprocess.run([sys.executable, "-c", ENTRY_POINTS], cwd=settings.ROOT, capture_output=True, text=True,
                          env={**os.environ, "PYTHONHASHSEED": hash_seed})
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


def test_a_real_process_can_only_draw():
    found = entry_points("1")
    assert found["tests"] is False
    names = [name for roster in [*found["presets"], found["lone"], found["fields"], found["built"]] for name in roster]
    assert names and set(names) <= set(ID_POOL)
    assert found == entry_points("2")                               # the draw reads no hash order


def test_nothing_a_real_run_imports_reaches_the_ordered_names():
    for root in ("src", "examples", "scripts", "tools"):
        for path in (settings.ROOT / root).rglob("*.py"):
            if ".venv" not in path.parts:
                assert not re.search(r"^\s*(?:from|import)\s+tests\b", path.read_text(), re.M), path


def test_a_screens_samples_draw_their_own_ids(monkeypatch):
    launched = []
    monkeypatch.setattr(screen, "launch", lambda cell, name, flags: launched.append((name, flags)) or (name, 0, 0.0))
    monkeypatch.setattr(screen, "done", lambda name: False)
    assert screen.main(["--cells", "openmath.riemann_minimal", "--samples", "3", "--suffix", "t", "--jobs", "1"]) == 0
    assert [name for name, _ in launched] == ["riemann_minimal_t1", "riemann_minimal_t2", "riemann_minimal_t3"]
    assert [flags[-2:] for _, flags in launched] == [["--set", f"seed={k}"] for k in (1, 2, 3)]


def test_a_screen_refuses_a_seed_of_the_users(monkeypatch):
    launched = []
    monkeypatch.setattr(screen, "launch", lambda cell, name, flags: launched.append(flags) or (name, 0, 0.0))
    monkeypatch.setattr(screen, "done", lambda name: False)
    with pytest.raises(SystemExit) as refused:
        screen.main(["--cells", "openmath.riemann_minimal", "--suffix", "t", "--set", "seed=9"])
    assert refused.value.code == 2 and launched == []
