"""/proc's hiding (src/sandbox/proc_hiding/): the profile's text, the compose file, the proof's reading and the record,
and where the task puts it. No Docker: the reports below are the proof's own output from three throwaway containers
on Colima (2026-11-22): a plain one, one under the profile only, and one under the profile with /proc remounted."""
from __future__ import annotations

import importlib.util
import re

import pytest
import yaml
from inspect_ai._util.registry import registry_log_name

from src import tasks
from src.configuration.presets import build_config
from src.domains.chess import Chess
from src.domains.interview import SubsetSum
from src.domains.openmath import OpenMath
from src.export import _export_run_blocks as run_blocks
from src.sandbox import peer_shell
from src.sandbox.access_log.access_monitor import AUDITOR
from src.sandbox.agent_tools import Seat
from src.sandbox.agent_users.agent_users import FIRST_UID
from src.sandbox.network_log.network_monitor import NETWORK_SIDECAR
from src.sandbox.proc_hiding import proc_hiding as ph
from src.tasks import swarm_task

SPEC = importlib.util.spec_from_file_location("host_helper", ph.HERE / "host_helper.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)

PLAIN_VIEWER = {"pids": 5, "other_owners": [0, 2997], "sees_holder": True, "sees_init": True, "sees_parent": True,
                "holder_cmdline": "ok", "holder_environ": "EACCES", "self": "ok", "self_fd": "ok", "dev_fd": "ok",
                "pagemap": "ok", "task_pagemap": "ok"}
HIDDEN_VIEWER = {"pids": 1, "other_owners": [], "sees_holder": False, "sees_init": False, "sees_parent": False,
                 "holder_cmdline": "ENOENT", "holder_environ": "ENOENT", "self": "ok", "self_fd": "ok", "dev_fd": "ok",
                 "pagemap": "EACCES", "task_pagemap": "EACCES"}
ESCALATED = {"egid": 2999, "other_owners": [0, 2997], "no_new_privs": "0"}     # a setgid-procview copy took effect
KEPT = {"egid": 2998, "other_owners": [], "no_new_privs": "1"}                  # no-new-privileges kept the viewer's
PLAIN = {"profile": "docker-default", "proc": "rw", "viewer": PLAIN_VIEWER, "setgid": ESCALATED,
         "root": {"sees_holder": True, "holder_cmdline": "ok", "pagemap": "ok"}}
PROFILE_ONLY = {**PLAIN, "profile": ph.PROFILE, "root": {**PLAIN["root"], "pagemap": "EACCES"},
                "viewer": {**PLAIN_VIEWER, "pagemap": "EACCES", "task_pagemap": "EACCES"}}
HIDDEN = {"profile": ph.PROFILE, "proc": "rw,gid=2999,hidepid=invisible", "kernel": "6.8.0-117-generic",
          "viewer": HIDDEN_VIEWER, "setgid": KEPT,
          "root": {"sees_holder": True, "holder_cmdline": "ok", "pagemap": "EACCES"}}


def test_the_profile_is_docker_default_with_one_rule_more():
    """The template keeps docker-default's denials and peers and adds only the page map's deny; its name follows its
    text."""
    rules = [line.strip() for line in ph.TEMPLATE.splitlines() if line.strip().startswith("deny ")]
    assert "deny mount," in rules and "deny @{PROC}/kcore rwklx," in rules and "deny network alg," in rules
    assert rules[-1] == "deny @{PROC}/**/pagemap rwklx,"
    assert len(rules) == 17           # docker-default's sixteen, then the page map
    assert 'ptrace (trace,tracedby,read,readby) peer="${name}",' in ph.TEMPLATE
    assert re.fullmatch(r"swarm-agent-[0-9a-f]{12}", ph.PROFILE)


@pytest.mark.parametrize("has", [True, False])
def test_the_helper_renders_the_profile_as_docker_does(has, monkeypatch):
    monkeypatch.setattr(helper, "daemon_profile", lambda: "unconfined")
    text, daemon = helper.render(ph.TEMPLATE, ph.PROFILE, lambda macro: has)
    assert "${" not in text and daemon == "unconfined" and f'profile "{ph.PROFILE}"' in text
    assert ("abi <abi/3.0>," in text) is has and ("#include <tunables/global>" in text) is has
    assert ("@{PROC}=/proc/" in text) is not has and ("#include <abstractions/base>" in text) is has
    assert f'signal (send,receive) peer="{ph.PROFILE}",' in text


def test_every_service_runs_under_the_profile(tmp_path):
    compose = tmp_path / "compose-abc.yaml"
    services = {"default": {"image": "x", "security_opt": ["no-new-privileges", "apparmor=docker-default"]},
                AUDITOR: {"image": "x", "pid": "host"}, "mirror": {"build": "."}}
    compose.write_text(yaml.safe_dump({"services": services, "volumes": {"v": {}}}))
    out = ph.with_proc_hiding(compose)
    document = yaml.safe_load(out.read_text())
    assert out.name == "compose-abc-hidden.yaml" and document["volumes"] == {"v": {}}
    assert document["services"]["default"]["security_opt"] == ["no-new-privileges:true", f"apparmor={ph.PROFILE}"]
    assert all(s["security_opt"][-1] == f"apparmor={ph.PROFILE}" for s in document["services"].values())
    assert all("no-new-privileges:true" in s["security_opt"] for s in document["services"].values())
    assert document["services"]["mirror"]["build"] == "."


def test_no_setgid_file_takes_effect_in_any_service():
    """A peer_shell observer holds PROC_GID, so it can leave a setgid-PROC_GID file where anyone can write; every
    service runs with no-new-privileges, so no exec of it gives an agent PROC_GID."""
    services = {"default": {"image": "x", "security_opt": ["no-new-privileges=false"]}, "mirror": {"build": "."}}
    out = ph.under_profile(services, ph.PROFILE)
    assert all(s["security_opt"] == ph.security_options(ph.PROFILE) for s in out.values())
    assert ph.security_options(ph.PROFILE) == ["no-new-privileges:true", f"apparmor={ph.PROFILE}"]


def test_a_host_without_apparmor_is_named():
    assert ph.apparmor_enabled({"SecurityOptions": ["name=apparmor", "name=seccomp,profile=builtin"]})
    assert not ph.apparmor_enabled({"SecurityOptions": ["name=seccomp,profile=builtin"]})
    assert not ph.apparmor_enabled({})


def test_the_helper_runs_privileged_in_the_hosts_pid_namespace_with_no_network():
    argv = ph.helper_argv()
    assert argv[:3] == ["docker", "run", "--rm"] and "--privileged" in argv
    assert argv[argv.index("--pid") + 1] == "host" and argv[argv.index("--network") + 1] == "none"
    assert argv[-1] == ph.HOST_HELPER and ph.HELPER_IMAGE in argv


@pytest.mark.parametrize(("options", "hides"), [
    ("rw,gid=2999,hidepid=invisible", True), ("rw,hidepid=2,gid=2999", True), ("rw,hidepid=invisible", False),
    ("rw,gid=2999", False), ("rw,gid=29990,hidepid=invisible", False), ("rw", False)])
def test_hides_pids_needs_hidepid_and_the_group(options, hides):
    assert ph.hides_pids(options) is hides


def test_the_proof_reads_each_container_as_it_was():
    """Plain fails both protections; the profile alone fails only hidepid; the profile and the remount pass."""
    plain, profile_only, hidden = map(ph.proof_failures, (PLAIN, PROFILE_ONLY, HIDDEN))
    assert plain["hidepid"] and plain["pagemap"]
    assert any("not swarm-agent" in m for m in plain["pagemap"]) and len(plain["pagemap"]) == 4
    assert profile_only["hidepid"] and not profile_only["pagemap"]
    assert hidden == {"hidepid": [], "pagemap": []}


@pytest.mark.parametrize(("change", "where"), [
    ({"root": {"sees_holder": False, "holder_cmdline": "ENOENT", "pagemap": "EACCES"}}, "hidepid"),
    ({"viewer": {**HIDDEN_VIEWER, "dev_fd": "EACCES"}}, "hidepid"),
    ({"viewer": {**HIDDEN_VIEWER, "self_fd": "ENOENT"}}, "hidepid"),
    ({"viewer": {**HIDDEN_VIEWER, "task_pagemap": "ok"}}, "pagemap"),
    ({"viewer": {"error": "no python3"}}, "hidepid"),
    ({"setgid": ESCALATED}, "hidepid"),
    ({"setgid": {**KEPT, "no_new_privs": "0"}}, "hidepid"),
    ({"setgid": {**KEPT, "egid": 2999}}, "hidepid"),
    ({"setgid": {"error": "exec failed"}}, "hidepid"),
    ({"setgid": None}, "hidepid"),
])
def test_one_wrong_reading_fails_its_protection(change, where):
    """Root without the group (an exec with no --user), an agent's own <(...) or fds lost, a thread's page map, a
    viewer that never reported, a setgid-PROC_GID copy that took effect or never ran."""
    found = ph.proof_failures({**HIDDEN, **change})
    assert found[where]


def team_seats(user_of) -> dict[str, Seat]:
    return {a: Seat(user=user_of(a), service=None) for a in ("a01", "a02", "a03")}


def test_the_record_names_the_machines_where_agents_share_one_uid():
    shared = team_seats(lambda a: "user")
    machines = {"a01": Seat(user="user", service="a01"), "a02": Seat(user="user", service="a02")}
    own = team_seats(lambda a: a)
    assert ph.shared_uids(shared) == {"default": ["a01", "a02", "a03"]}
    assert ph.shared_uids(machines) == {} and ph.shared_uids(own) == {}
    assert ph.machines_of(machines) == {"a01": ["a01"], "a02": ["a02"]}
    record = ph.record(own, {"default": {"group": "procview:x:2999:root"}},
                       {"default": {"mount": "rw,nosuid", "super": HIDDEN["proc"]}}, {"default": HIDDEN})
    assert record["status"] == "on" and record["proc_hidepid"] and record["pagemap_hidden"]
    assert record["kernel"] == "6.8.0-117-generic" and record["shared_uid"] == {}
    assert record["machines"]["default"]["proc"] == HIDDEN["proc"]


def test_a_failed_proof_refuses_the_run():
    with pytest.raises(RuntimeError, match="proc_hiding=false"):
        ph.record(team_seats(lambda a: a), {}, {}, {"default": PROFILE_ONLY})


def test_the_ids_never_meet():
    """The group and the proof's uids lie above every agent user (from FIRST_UID, ten agents and more) and below
    the observers."""
    assert FIRST_UID + 100 < min(ph.PROOF_UIDS) < ph.PROC_GID < peer_shell.OBSERVER_UID_FROM
    assert len(set(ph.PROOF_UIDS)) == 2 and ph.REMOUNT_DATA == "hidepid=2,gid=2999"


@pytest.mark.parametrize(("benchmark", "pairs", "keeps"), [
    (OpenMath(), [], True), (SubsetSum(), [], True), (Chess(), [], False), (OpenMath(), ["proc_hiding=false"], False)])
def test_the_switch_on_a_benchmark_with_a_shell(benchmark, pairs, keeps):
    config = build_config("default", "small", ["n_agents=2", *pairs])
    assert ph.keeps_proc_hiding(benchmark, config) is keeps
    task = swarm_task(benchmark, config)
    names = [registry_name(s) for s in tasks.setup_solvers(benchmark, benchmark.plan(config, None))]
    assert ("hide_proc" in names) is keeps
    if keeps:
        assert names.index("hide_proc") == 1                # after the turn rules, before the benchmark's setup
    deviations = task.dataset[0].metadata["cheatbench"]["deviations"]
    assert (ph.PROC_DEVIATION in deviations) is keeps
    compose = yaml.safe_load(open(task.dataset[0].sandbox.config).read())
    under = [f"apparmor={ph.PROFILE}" in (s.get("security_opt") or []) for s in compose["services"].values()]
    assert all(under) if keeps else not any(under)
    if keeps:
        assert {AUDITOR, NETWORK_SIDECAR} <= set(compose["services"]) or benchmark.watched is None


def registry_name(solver) -> str:
    return registry_log_name(solver).split("/")[-1]


class StoreView:
    """A sample view with a store and nothing else."""
    def __init__(self, store: dict) -> None:
        self.store = store

    def store_get(self, key):
        return self.store.get(key)


def test_run_json_records_the_protections_or_says_they_were_off(monkeypatch):
    monkeypatch.setattr(run_blocks, "domain_score", lambda view: {"sandbox": {"processes": {}}})
    on = ph.record(team_seats(lambda a: a), {}, {"default": {"super": HIDDEN["proc"]}}, {"default": HIDDEN})
    assert run_blocks.sandbox_block(StoreView({ph.STORE: on}))["proc"] == on
    older = run_blocks.sandbox_block(StoreView({}))
    assert older["proc"] == ph.OFF and older["end"] == {"processes": {}}
    monkeypatch.setattr(run_blocks, "domain_score", lambda view: None)
    assert run_blocks.sandbox_block(StoreView({})) is None
