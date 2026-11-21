"""Mock-model smoke scenarios for the network log on the real sandbox. No model spend.

    uv run python -m tests.smoke.network_logging [--only observer_connect,observer_protein,procnet,monitor_killed,
                                                         network_off]

Every scenario starts the real sandbox with the access log's sidecar, drives ORBIT with Inspect's mockllm scripted per
agent, exports the log with src.export.run_export.write_run and reads network.jsonl and run.json back. Each check is
an assertion; a failing scenario exits non-zero. Results go to runs/study_smoke/smoke_network/<stamp>/ and
logs/study_smoke/smoke_network/<stamp>/, a fresh directory per invocation. One sample runs at a time.

  observer_connect  a subset_sum team with a regulator under peer_shell: a worker's call serves once on loopback while
                    the regulator's peer_shell call connects to it from the worker's machine as the observer uid. The
                    rows name the worker's listen and accept, the regulator's connect by its observer, each one's call
                    and the other end, and run.json "network" lists the connection as cross-agent, by ebpf
  observer_protein  the same on a protein design team, a task the transcript viewer lists
  procnet           the same run with no tracer mounted: the socket tables record it, the fallback says why, and the
                    connection still reads as cross-agent
  monitor_killed    the daemon dies mid-run: the end control is missing, status broken, earlier rows kept
  network_off       network_log=false: no block but {"status": "off"}, no file, no record in the store
"""
from __future__ import annotations

from inspect_ai.model import ModelOutput
from inspect_ai.util import sandbox, store

from src.configuration.swarm_config import SwarmConfig
from src.domains.interview import SubsetSum
from src.domains.protein import ProteinDesign
from src.export._export_network_log import NETWORK_LOG
from src.judging.cheatbench_judge import judge_spec
from src.measures.run_directory import RunDir
from src.sandbox.access_log.access_monitor import AUDITOR
from src.sandbox.network_log import network_monitor
from src.sandbox.network_log.network_monitor import NETWORK_DEVIATION, STORE
from src.tasks import agent_roles, swarm_task
from src.utils.json_files import read_jsonl
from tests.smoke.common import Suite, assert_ok, main, small
from tests.support.mock_model import call, scripted

SUITE = Suite("smoke_network", judge_spec(SubsetSum.task_dir, SubsetSum.name))
PORT = 8123
TARGET = "alfalfa_chi"
# The worker's server: one accept within the call, a reply, a moment held open so a read of the tables sees it.
SERVE = ("python3 -c \"import socket, time; s = socket.socket(); "
         "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', %d)); s.listen(); "
         "s.settimeout(60); c, _ = s.accept(); c.sendall(b'served'); time.sleep(1.5); print('served')\"" % PORT)
# A listener held a moment, which accepts nothing.
LISTEN = ("python3 -c \"import socket, time; s = socket.socket(); s.bind(('127.0.0.1', %d)); s.listen(); "
          "time.sleep(1.5); print('bound')\"" % PORT)
# The observer's client: it retries until the server listens, reads the reply and holds the connection a moment.
CONNECT = ("python3 -c \"import socket, time\nfor _ in range(240):\n    try:\n"
           "        c = socket.create_connection(('127.0.0.1', %d)); break\n"
           "    except OSError:\n        time.sleep(0.25)\nprint(c.recv(16).decode()); time.sleep(1.5)\"" % PORT)


class Networked:
    """One scenario's run as a reader finds it."""

    def __init__(self, sample, run: RunDir) -> None:
        self.sample, self.run_json = sample, run.run_json
        self.block = run.run_json.get("network")
        path = run.path / NETWORK_LOG
        self.rows = read_jsonl(path) if path.is_file() else None

    def own(self, **want) -> list[dict]:
        """The rows that are no control and hold `want`."""
        return [row for row in self.rows or [] if row["origin"] != "control"
                and all(row.get(key) == value for key, value in want.items())]


def network_run(name: str, config: SwarmConfig, script: dict, *, damaged: bool = False, benchmark=None) -> Networked:
    benchmark = benchmark or SubsetSum()
    suite = SUITE.with_spec(judge_spec(benchmark.task_dir, benchmark.name))
    log, _, _ = suite.run(name, swarm_task(benchmark, config), scripted(script), damaged_log=damaged)
    return Networked(assert_ok(log), RunDir(suite.runs / name))


def team_config(*pairs: str) -> SwarmConfig:
    """Two workers and a regulator with peer_shell, rounds at once, so a worker's call and the regulator's run
    together."""
    return small("n_agents=3", "regulators=1", "peer_shell=true", "judge=none", "steps_per_agent=3", *pairs)


def observer_script(config: SwarmConfig) -> tuple[dict, str, str]:
    """(the script, the serving worker, the regulator)."""
    roles = agent_roles(config)
    regulator = next(a for a in config.names if roles[a] == "regulator")
    worker, other = [a for a in config.names if roles[a] != "regulator"]
    script = {worker: [call("shell", {"command": SERVE}, "serve"), call("stop_working", {}, "w-stop")],
              other: [call("stop_working", {}, "o-stop")],
              regulator: [call("peer_shell", {"peer": worker, "command": CONNECT}, "reach"),
                          *[call("shell", {"command": f"echo r{i}"}, f"r{i}") for i in range(3)]]}
    return script, worker, regulator


def assert_connection(run: Networked, worker: str, regulator: str, method: str) -> dict:
    """The worker's listen and accept, the regulator's connect, each one's call and peer, and the cross-agent entry."""
    assert run.block["status"] == "valid" and run.block["method"] == method, run.block
    uid = run.sample.metadata["cheatbench"]["peer_shell"]["observers"][regulator]
    [listen] = [r for r in run.own(op="listen", lport=PORT)]
    assert (listen["agent"], listen["machine"], listen["call_id"]) == (worker, worker, "serve"), listen
    [accept] = [r for r in run.own(op="accept", lport=PORT)]
    connects = [r for r in run.own(op="connect", rport=PORT, result="ok")]
    assert len(connects) == 1, run.own(op="connect")
    [connect] = connects
    assert (connect["agent"], connect["named_by"], connect["uid"], connect["machine"], connect["call_id"]) == \
        (regulator, "observer", uid, worker, "reach"), connect
    assert (connect["peer_agent"], accept["peer_agent"], accept["agent"]) == (worker, regulator, worker), \
        (connect, accept)
    assert connect["peer_pid"] == accept["pid"] and accept["peer_pid"] == connect["pid"], (connect, accept)
    cross = [c for c in run.block["cross_agent"] if c["rport"] == PORT]
    assert [(c["from"], c["to"], c["call_id"]) for c in cross] == [(regulator, worker, "reach")], run.block
    assert run.block["control"]["start"]["missing"] == run.block["control"]["end"]["missing"] == [], run.block
    assert NETWORK_DEVIATION in run.run_json["deviations"], run.run_json["deviations"]
    return {"block": {k: run.block[k] for k in ("status", "method", "fallback", "ops", "agents", "cross_agent")},
            "connect": connect}


# ------------------------------------------------------------------------------------------------ scenarios

def scenario_observer_connect() -> dict:
    config = team_config()
    script, worker, regulator = observer_script(config)
    run = network_run("observer_connect", config, script)
    assert run.sample.store[STORE]["tracer"], "no tracer mounted: run on a host that has one"
    out = assert_connection(run, worker, regulator, "ebpf")
    assert all(r["source"] == "ebpf" for r in run.own() if not r["at_start"]), run.rows
    return out


def scenario_observer_protein() -> dict:
    config = team_config()
    script, worker, regulator = observer_script(config)
    run = network_run("observer_protein", config, script, benchmark=ProteinDesign(TARGET))
    return assert_connection(run, worker, regulator, "ebpf")


def scenario_procnet() -> dict:
    ensure = network_monitor.ensure_tracer
    network_monitor.ensure_tracer = lambda image: None
    try:
        config = team_config()
        script, worker, regulator = observer_script(config)
        run = network_run("procnet", config, script)
    finally:
        network_monitor.ensure_tracer = ensure
    assert run.block["fallback"] == "no tracer mounted", run.block
    out = assert_connection(run, worker, regulator, "procnet")
    assert {r["source"] for r in run.own()} == {"procnet"} and run.sample.store[STORE]["tracer"] is None, run.rows
    return out


async def kill_monitor(then: ModelOutput) -> ModelOutput:
    """SIGKILL this run's network daemon from its own sidecar, then the agent's next turn."""
    sent = await sandbox(AUDITOR).exec(["bash", "-c", f"kill -KILL {store().get(STORE)['pid']}"], user="root")
    assert sent.success, sent.stderr
    return then


def scenario_monitor_killed() -> dict:
    config = small("n_agents=2", "judge=none", "steps_per_agent=4", "concurrent_rounds=false")
    first, second = config.names
    after = call("shell", {"command": "echo after"}, "k2")
    script = {first: [call("shell", {"command": LISTEN}, "k1"), lambda messages: kill_monitor(after),
                      call("stop_working", {}, "k3")],
              second: [call("stop_working", {}, "s1")]}
    run = network_run("monitor_killed", config, script, damaged=True)
    assert run.block["status"] == "broken", run.block
    assert run.block["control"]["end"]["missing"] and not run.block["control"]["start"]["missing"], run.block
    assert run.block["dropped"] is None, run.block
    [listen] = run.own(op="listen", lport=PORT)
    assert (listen["agent"], listen["call_id"]) == (first, "k1"), listen
    return {"control": run.block["control"], "status": run.block["status"]}


def scenario_network_off() -> dict:
    config = small("n_agents=2", "judge=none", "steps_per_agent=2", "network_log=false")
    script = {name: [call("stop_working", {}, f"{name}-stop")] for name in config.names}
    run = network_run("network_off", config, script)
    assert run.block == {"status": "off"} and run.rows is None, run.block
    assert STORE not in run.sample.store and NETWORK_DEVIATION not in run.run_json["deviations"]
    assert run.run_json["access_log"]["status"] == "valid", run.run_json["access_log"]
    return {"block": run.block}


SCENARIOS = {"observer_connect": scenario_observer_connect, "observer_protein": scenario_observer_protein,
             "procnet": scenario_procnet,
             "monitor_killed": scenario_monitor_killed, "network_off": scenario_network_off}

if __name__ == "__main__":
    raise SystemExit(main(SUITE, SCENARIOS, __doc__))
