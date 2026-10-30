"""Sandbox hygiene (src/sandbox/sandbox_hygiene.py): the three in-sandbox scripts compile, the reap counter
in the store,
and the export's sandbox, validity and ledger blocks (src/export/export_run_blocks.py) from plain records."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

from src.configuration import settings
from src.export import export_run_blocks as export
from src.models import model_registry as models
from src.sandbox import sandbox_hygiene as hygiene
from src.sandbox import sandbox_probe as probe


def test_the_sandbox_scripts_compile():
    for name in ("REAP", "LOOPBACK_GUARD", "STATE"):
        compile(getattr(hygiene, name), name, "exec")
    assert hygiene.AGENT_UID_FROM == 1001 and hygiene.GUARD_PORT == 47123


def test_a_root_script_never_imports_from_its_working_directory(tmp_path):
    """run_json runs after every command, as root, in the container's working directory, which an agent may write."""
    calls = []

    class Box:
        async def exec(self, command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout='{"ok": true}\n', stderr="")

    assert asyncio.run(probe.run_json("print(1)", args=["a01"], box=Box())) == {"ok": True}
    assert calls[0][:3] == ["python3", "-I", "-c"] and calls[0][-1] == "a01", calls
    (tmp_path / "json.py").write_text("raise SystemExit('planted')\n")
    script = "import json; print('stdlib')"
    planted = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    isolated = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert "planted" in planted.stderr and isolated.stdout == "stdlib\n", (planted, isolated)


def test_a_reap_is_counted_per_agent(monkeypatch):
    class Store:
        def __init__(self):
            self.d = {}

        def get(self, key):
            return self.d.get(key)

        def set(self, key, value):
            self.d[key] = value

    st = Store()
    monkeypatch.setattr(hygiene, "store", lambda: st)
    hygiene.record_reap("a01", {"killed": [{"pid": 7, "cmd": "sleep 999"}], "survivors": []})
    hygiene.record_reap("a01", {"killed": [], "survivors": []})
    hygiene.record_reap("a02", {"killed": [{"pid": 8, "cmd": "nc -l 9"}, {"pid": 9, "cmd": "python3 serve.py"}],
                                "survivors": [{"pid": 9, "cmd": "python3 serve.py"}], "error": None})
    reaps = st.get(hygiene.REAP_STORE)
    assert reaps["a01"] == {"commands": 2, "killed": 1, "max_alive": 1, "survivors": [], "errors": []}
    assert reaps["a02"]["commands"] == 1 and reaps["a02"]["killed"] == 2 and reaps["a02"]["max_alive"] == 2
    assert reaps["a02"]["survivors"] == [[{"pid": 9, "cmd": "python3 serve.py"}]]


def step(*calls):
    return {"tool_calls": [{"function_name": f, "arguments": a} for f, a in calls]}


def test_identical_tool_runs_and_validity_flags():
    steps = [step(("shell", {"command": "ls"})), step(("shell", {"command": "ls"}), ("shell", {"command": "ls"})),
             step(("board_read", {"latest": 2})), step(("shell", {"command": "ls"}))]
    assert export.identical_tool_run(steps) == 3 and export.identical_tool_run([]) == 0
    stuck = [step(("shell", {"command": "cat x"}))] * export.IDENTICAL_RUN_FLAG
    rec = export.validity_record(stuck, model_calls=9, engagement={"checks": 0, "reads": 0, "posts": 0},
                                 board=True)
    assert rec == {"model_calls": 9, "tool_calls": 8, "never_acted": False, "no_tool_call": False,
                   "identical_tool_run": 8, "board_unused": True}
    idle = export.validity_record([], model_calls=0, engagement=None, board=False)
    assert idle["never_acted"] and idle["no_tool_call"] and not idle["board_unused"]
    block = export.validity_block([{"orbit_agent": "a01", "validity": rec}, {"orbit_agent": "a02", "validity": idle}],
                                  {"agent": "a01", "round": 1, "answer": "placeholder"})
    assert block["flags"] == ["a01: 8 identical tool calls in a row", "a01: never used the board",
                              "a02: no model call", "a01: submit in round 1 with a 11-character answer"]
    assert not block["ok"]
    fine = export.validity_record([step(("shell", {"command": "ls"}))], 3, {"checks": 1, "reads": 2, "posts": 1}, True)
    assert export.validity_block([{"orbit_agent": "a01", "validity": fine}], None)["ok"]


def model_event(usage=True, error=None):
    output = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5)) if usage else None
    return SimpleNamespace(output=output, error=error)


def test_the_ledger_prices_the_registry_and_flags_what_it_cannot_see():
    entry = next(iter(settings_models()))
    usage = {"input": 1_000_000, "cache_read": 500_000, "output": 100_000, "billed_usd": 0.5, "billed_calls": 2}
    rec = export.ledger_record(entry, [model_event(), model_event(), model_event(usage=False, error="boom")], usage)
    price = models.usd_per_million(entry)
    cached = price.get("cached_input", price["input"])
    expect = (500_000 * price["input"] + 500_000 * cached + 100_000 * price["output"]) / 1e6
    assert rec["calls"] == 3 and rec["errored"] == 1 and rec["with_usage"] == 2
    assert rec["cost_usd"] == round(expect, 6) and rec["billed_usd"] == 0.5 and rec["flags"] == ["1 errored call(s)"]
    unknown = export.ledger_record("mockllm/model", [model_event(), model_event(usage=False)],
                                   {"input": 1, "output": 1})
    assert unknown["cost_usd"] is None and unknown["flags"] == ["unpriced model", "1 call(s) without usage"]
    partial = export.ledger_record(entry, [model_event(), model_event()],
                                   {"input": 1, "cache_read": 0, "output": 1, "billed_usd": 0.1, "billed_calls": 1})
    assert partial["flags"] == ["billed for 1 of 2 call(s)"]
    block = export.ledger_block([{"orbit_agent": "a01", "ledger": rec}, {"orbit_agent": "a02", "ledger": unknown}],
                                "time")
    assert block["total"]["calls"] == 5 and block["total"]["cost_usd"] is None and block["total"]["billed_usd"] is None
    assert block["flags"][-1].startswith("the sample ended on its time limit") and not block["complete"]
    clean = export.ledger_block([{"orbit_agent": "a01", "ledger": export.ledger_record(
        entry, [model_event()], {"input": 10, "cache_read": 0, "output": 5, "billed_usd": 0.01, "billed_calls": 1})}],
        None)
    assert clean["complete"] and clean["total"]["cost_usd"] is not None and clean["total"]["billed_usd"] == 0.01


def settings_models():
    return models.entries()


def test_the_code_state_names_the_commit_and_every_submodule():
    code = settings.code_state()
    assert code["commit"] and len(code["commit"]) >= 7 and isinstance(code["dirty"], bool)
    assert set(code["submodules"]) >= {"cheatbench", "orbit", "lucy"}
    assert all(head is None or len(head) >= 7 for head in code["submodules"].values())
