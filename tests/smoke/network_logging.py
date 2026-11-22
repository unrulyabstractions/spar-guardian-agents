"""Mock-model smoke scenarios for the network log on the real sandbox. No model spend.

    uv run python -m tests.smoke.network_logging [--only observer_connect,observer_protein,swe_http,openmath_workers,
                                                         procnet,monitor_killed,network_off]

Every scenario starts the real sandbox with the access log's sidecar, drives ORBIT with Inspect's mockllm scripted per
agent, exports the log with src.export.run_export.write_run and reads network.jsonl and run.json back. Each check is
an assertion; a failing scenario exits non-zero. Results go to runs/study_smoke/smoke_network/<stamp>/ and
logs/study_smoke/smoke_network/<stamp>/, a fresh directory per invocation. One sample runs at a time.

  observer_connect  a subset_sum team with a regulator under peer_shell: a worker's call serves once on loopback while
                    the regulator's peer_shell call connects to it from the worker's machine as the observer uid. The
                    rows name the worker's listen and accept, the regulator's connect by its observer, each one's call
                    and the other end, and run.json "network" lists the connection as cross-agent and connected, by
                    ebpf
  observer_protein  the same on a protein design team, a task the transcript viewer lists
  swe_http          a SWE team (flipt-02e2, one machine per agent): a worker leaves `python3 -m http.server` running in
                    the background, then writes to it through bash's /dev/tcp from a child shell that exits at once,
                    and in a later round the regulator's peer_shell runs curl to it. The worker's listen and accept, the
                    /dev/tcp connect with its call through its parent (via), the observer's connects named as the
                    regulator with the worker at the other end and established (curl does not wait: its socket's state
                    change settles it), the connected cross-agent entry, the counts by agent and both controls on every
                    machine
  openmath_workers  two workers in openmath's one shared container (split layout): one serves http.server in the
                    background while the other runs curl to it in the same round. The loopback guard refuses every
                    connect, none is accepted, each connect reads refused, and each one after the listen names both
                    workers and reads as an attempted cross-agent call, none connected
  procnet           the same run as observer_connect with no tracer mounted: the socket tables record it, the fallback
                    says why, the status reads partial for procnet, and the connection still reads as cross-agent
  monitor_killed    the daemon dies mid-run: the end control is missing, status broken, the error says the monitor
                    was not running and left no stopped row, earlier rows kept
  network_off       network_log=false: no block but {"status": "off"}, no file, no record in the store
"""
from __future__ import annotations

from inspect_ai.model import ModelOutput
from inspect_ai.util import sandbox, store

from src.configuration.swarm_config import SwarmConfig
from src.domains.interview import SubsetSum
from src.domains.openmath import OpenMath
from src.domains.protein import ProteinDesign
from src.domains.swe import SWE
from src.judging.cheatbench_judge import judge_spec
from src.measures.run_directory import RunDir
from src.sandbox.access_log.access_monitor import AUDITOR
from src.sandbox.network_log import network_monitor
from src.sandbox.network_log.network_monitor import NETWORK_DEVIATION, STORE
from src.tasks import agent_roles, swarm_task
from src.utils.json_files import read_jsonl
from tests.smoke.common import Suite, assert_ok, by_agent, load_transcript, main, results_of, small
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
HTTP_PORT = 8180
SWE_ISSUE = "flipt-02e2"
# A worker's server in the background, as an agent would start one: it listens while the call's shell still runs, so
# its listen holds the call; once the shell exits it is detached, and its later accepts name the worker by its machine.
HTTP_START = f"cd /tmp && nohup python3 -m http.server {HTTP_PORT} --bind 127.0.0.1 >/dev/null 2>&1 &"
HTTP_SERVE = f"{HTTP_START} sleep 1.5; echo started"
# The same server held for the length of the call, for a layout that reaps an agent's processes after each command.
HTTP_HOLD = f"{HTTP_START} sleep 10; echo held"
# A write to the server through bash's /dev/tcp in a child shell, which exits before the monitor reads /proc.
DEV_TCP = f"bash -c 'echo hi > /dev/tcp/127.0.0.1/{HTTP_PORT}'; echo wrote"
# curl to the server, retrying a refused connect, then curl's own exit code.
CURL = (f"curl -s -o /dev/null --retry 8 --retry-connrefused --retry-delay 1 -w '%{{http_code}}' "
        f"http://127.0.0.1:{HTTP_PORT}/; echo \" exit=$?\"")


class Networked:
    """One scenario's run as a reader finds it."""

    def __init__(self, sample, run: RunDir, records: list[dict] | None = None) -> None:
        self.sample, self.run_json, self.records = sample, run.run_json, by_agent(records or [])
        self.block = run.run_json.get("network")
        path = run.path / "processed/network.jsonl"
        self.rows = read_jsonl(path) if path.is_file() else None

    def own(self, **want) -> list[dict]:
        """The rows that are no control and hold `want`."""
        return [row for row in self.rows or [] if row["origin"] != "control"
                and all(row.get(key) == value for key, value in want.items())]


def network_run(name: str, config: SwarmConfig, script: dict, *, damaged: bool = False, benchmark=None) -> Networked:
    benchmark = benchmark or SubsetSum()
    suite = SUITE.with_spec(judge_spec(benchmark.task_dir, benchmark.name))
    log, _, records = suite.run(name, swarm_task(benchmark, config), scripted(script), damaged_log=damaged)
    return Networked(assert_ok(log), RunDir(suite.runs / name), records)


def team_config(*pairs: str) -> SwarmConfig:
    """Two workers and a regulator with peer_shell, rounds at once, so a worker's call and the regulator's run
    together."""
    return small("n_agents=3", "regulators=1", "peer_shell=true", "judge=none", "rounds=3", *pairs)


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
    """The worker's listen and accept, the regulator's connect, each one's call and peer, and the cross-agent entry:
    valid by ebpf, partial for procnet by the socket tables."""
    status, reasons = ("valid", []) if method == "ebpf" else ("partial", ["procnet"])
    assert (run.block["status"], run.block["partial_reasons"], run.block["method"]) == (status, reasons, method), \
        run.block
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
    assert connect["outcome"] == "established", connect
    cross = [c for c in run.block["cross_agent"]["connected"] if c["rport"] == PORT]
    assert [(c["from"], c["to"], c["call_id"]) for c in cross] == [(regulator, worker, "reach")], run.block
    assert run.block["control"]["start"]["missing"] == run.block["control"]["end"]["missing"] == [], run.block
    assert NETWORK_DEVIATION in run.run_json["deviations"], run.run_json["deviations"]
    return {"block": {k: run.block[k] for k in ("status", "partial_reasons", "method", "fallback", "ops", "agents",
                                                 "outcomes", "cross_agent")},
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


def tool_result(run: Networked, agent: str, index: int) -> str:
    """The agent's index-th tool result, as its exported transcript holds it."""
    return results_of(load_transcript(run.records[agent]))[index]


def assert_controls(run: Networked) -> None:
    """Both controls connected and accepted on every machine that holds a seat."""
    control = run.block["control"]
    assert control["start"]["missing"] == control["end"]["missing"] == [], control
    assert control["start"]["seen"] > 0 and control["end"]["seen"] > 0, control
    for name in ("start", "end"):
        rows = [r for r in run.rows if r["origin"] == "control" and r["control"] == name]
        assert {r["op"] for r in rows} >= {"listen", "connect", "accept"}, (name, rows)


def scenario_swe_http() -> dict:
    """A worker's background http.server in its own SWE machine, reached by the regulator's peer_shell curl."""
    config = team_config("concurrent_rounds=false")
    roles = agent_roles(config)
    regulator = next(a for a in config.names if roles[a] == "regulator")
    worker, other = [a for a in config.names if roles[a] != "regulator"]
    script = {worker: [call("shell", {"command": HTTP_SERVE}, "serve"),
                       call("shell", {"command": DEV_TCP}, "devtcp"),
                       call("shell", {"command": "echo w1"}, "w1"),
                       call("stop_working", {}, "w-stop")],
              other: [*[call("shell", {"command": f"echo o{i}"}, f"o{i}") for i in range(3)],
                      call("stop_working", {}, "o-stop")],
              regulator: [call("shell", {"command": "echo r0"}, "r0"),
                          call("peer_shell", {"peer": worker, "command": CURL}, "reach"),
                          *[call("shell", {"command": f"echo r{i}"}, f"r{i}") for i in range(1, 4)]]}
    run = network_run("swe_http", config, script, benchmark=SWE(SWE_ISSUE, "as_released", grade=False))
    assert run.block["status"] == "valid" and run.block["method"] == "ebpf", run.block
    served = tool_result(run, worker, 0)
    assert "started" in served, served
    reached = tool_result(run, regulator, 1)
    assert "200 exit=0" in reached, reached
    uid = run.sample.metadata["cheatbench"]["peer_shell"]["observers"][regulator]
    listens = run.own(op="listen", lport=HTTP_PORT)
    assert [(r["agent"], r["machine"], r["origin"], r["call_id"]) for r in listens] == \
        [(worker, worker, "agent", "serve")] and "http.server" in listens[0]["cmd"], listens
    connects = [c for c in run.own(op="connect", rport=HTTP_PORT) if c["comm"] != "bash"]
    assert connects and all((c["agent"], c["named_by"], c["uid"], c["machine"], c["call_id"], c["peer_agent"]) ==
                            (regulator, "observer", uid, worker, "reach", worker) for c in connects), connects
    assert all(c["result"] in ("ok", "EINPROGRESS") for c in connects), connects    # curl connects without waiting
    assert all((c["outcome"], c["outcome_by"]) in (("established", "state"), ("established", "syscall"))
               for c in connects), connects
    assert any("curl" in (c["exe"] or c["comm"]) for c in connects), connects
    assert "wrote" in tool_result(run, worker, 1)
    [dev_tcp] = run.own(op="connect", rport=HTTP_PORT, comm="bash")
    assert (dev_tcp["agent"], dev_tcp["origin"], dev_tcp["call_id"], dev_tcp["machine"], dev_tcp["outcome"]) == \
        (worker, "agent", "devtcp", worker, "established") and dev_tcp["via"], dev_tcp
    accepts = [a for a in run.own(op="accept", lport=HTTP_PORT) if a["peer_pid"] != dev_tcp["pid"]]
    assert accepts and all((a["agent"], a["named_by"], a["origin"], a["machine"], a["peer_agent"]) ==
                           (worker, "machine", "detached", worker, regulator) for a in accepts), accepts
    assert {a["pid"] for a in run.own(op="accept", lport=HTTP_PORT)} == {listens[0]["pid"]}, (accepts, listens)
    cross = [c for c in run.block["cross_agent"]["connected"] if c["rport"] == HTTP_PORT]
    assert cross and {(c["from"], c["to"], c["call_id"], c["named_by"], c["machine"]) for c in cross} == \
        {(regulator, worker, "reach", "observer", worker)}, run.block["cross_agent"]
    assert [c for c in run.block["cross_agent"]["attempted"] if c["rport"] == HTTP_PORT] == [], run.block
    agents = run.block["agents"]
    assert agents[worker].get("listen", 0) >= 1 and agents[worker].get("accept", 0) >= len(accepts), agents
    assert agents[regulator].get("connect", 0) >= len(connects), agents
    assert_controls(run)
    machines = {r["machine"] for r in run.rows if r["origin"] == "control"}
    assert set(config.names) <= machines, machines
    return {"roles": roles, "observer_uid": uid, "connects": len(connects), "accepts": len(accepts),
            "dev_tcp": {k: dev_tcp[k] for k in ("pid", "via", "call_id", "outcome")}, "cross_agent": cross,
            "agents": agents, "control": run.block["control"]}


def scenario_openmath_workers() -> dict:
    """Two workers in openmath's one container: one serves, the other's curl is refused by the loopback guard."""
    config = small("n_agents=2", "judge=none", "rounds=3")
    server, client = config.names
    script = {server: [call("shell", {"command": HTTP_HOLD}, "serve"), call("stop_working", {}, "s-stop")],
              client: [call("shell", {"command": CURL}, "reach"), call("stop_working", {}, "c-stop")]}
    run = network_run("openmath_workers", config, script, benchmark=OpenMath())
    assert run.block["status"] == "valid" and run.block["method"] == "ebpf", run.block
    assert "held" in tool_result(run, server, 0)
    reached = tool_result(run, client, 0)
    assert "000 exit=7" in reached, reached
    [listen] = run.own(op="listen", lport=HTTP_PORT)
    machine = listen["machine"]
    assert (listen["agent"], listen["named_by"], listen["origin"], listen["call_id"]) == \
        (server, "uid", "agent", "serve"), listen
    connects = run.own(op="connect", rport=HTTP_PORT)
    assert connects and all((c["agent"], c["named_by"], c["machine"], c["call_id"]) == (client, "uid", machine, "reach")
                            for c in connects), connects
    # curl connects without waiting (EINPROGRESS); the guard's refusal shows as curl's exit 7, no accept below and
    # each connect's socket leaving SYN_SENT refused
    assert all(c["result"] in ("EINPROGRESS", "ECONNREFUSED") for c in connects), connects
    assert all((c["outcome"], c["outcome_error"], c["outcome_by"]) == ("refused", "ECONNREFUSED", "icmp")
               for c in connects), connects
    after = [c for c in connects if c["t"] >= listen["t"]]
    assert after and all(c["peer_agent"] == server and c["peer_pid"] == listen["pid"] for c in after), connects
    assert run.own(op="accept", lport=HTTP_PORT) == [], run.own(op="accept", lport=HTTP_PORT)
    assert [c for c in run.block["cross_agent"]["connected"] if c["rport"] == HTTP_PORT] == [], run.block
    cross = [c for c in run.block["cross_agent"]["attempted"] if c["rport"] == HTTP_PORT]
    assert len(cross) == len(after) and {(c["from"], c["to"], c["call_id"], c["outcome"]) for c in cross} == \
        {(client, server, "reach", "refused")}, run.block["cross_agent"]
    agents = run.block["agents"]
    assert agents[server].get("listen", 0) >= 1 and agents[client].get("connect", 0) >= len(connects), agents
    assert_controls(run)
    return {"machine": machine, "connects": len(connects), "results": sorted({c["result"] for c in connects}),
            "outcomes": sorted({c["outcome"] for c in connects}), "attempted": len(cross), "agents": agents,
            "control": run.block["control"]}


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
    config = small("n_agents=2", "judge=none", "rounds=4", "concurrent_rounds=false")
    first, second = config.names
    after = call("shell", {"command": "echo after"}, "k2")
    script = {first: [call("shell", {"command": LISTEN}, "k1"), lambda messages: kill_monitor(after),
                      call("stop_working", {}, "k3")],
              second: [call("stop_working", {}, "s1")]}
    run = network_run("monitor_killed", config, script, damaged=True)
    assert run.block["status"] == "broken", run.block
    assert run.block["control"]["end"]["missing"] and not run.block["control"]["start"]["missing"], run.block
    assert run.block["dropped"] is None, run.block
    assert "is not running" in run.block["error"] and "no stopped row" in run.block["error"], run.block
    [listen] = run.own(op="listen", lport=PORT)
    assert (listen["agent"], listen["call_id"]) == (first, "k1"), listen
    return {"control": run.block["control"], "status": run.block["status"]}


def scenario_network_off() -> dict:
    config = small("n_agents=2", "judge=none", "rounds=2", "network_log=false")
    script = {name: [call("stop_working", {}, f"{name}-stop")] for name in config.names}
    run = network_run("network_off", config, script)
    assert run.block == {"status": "off"} and run.rows is None, run.block
    assert STORE not in run.sample.store and NETWORK_DEVIATION not in run.run_json["deviations"]
    assert run.run_json["access_log"]["status"] == "valid", run.run_json["access_log"]
    return {"block": run.block}


SCENARIOS = {"observer_connect": scenario_observer_connect, "observer_protein": scenario_observer_protein,
             "swe_http": scenario_swe_http, "openmath_workers": scenario_openmath_workers,
             "procnet": scenario_procnet,
             "monitor_killed": scenario_monitor_killed, "network_off": scenario_network_off}

if __name__ == "__main__":
    raise SystemExit(main(SUITE, SCENARIOS, __doc__))
