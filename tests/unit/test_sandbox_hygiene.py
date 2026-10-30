"""Sandbox hygiene (src/sandbox/sandbox_hygiene.py, src/sandbox/sandbox_probe.py): the in-sandbox scripts compile, a
root script keeps the working directory off its path, and the store counts each agent's reaps."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

from src.sandbox import sandbox_hygiene as hygiene
from src.sandbox import sandbox_probe as probe
from tests.support.fakes import FakeStore


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
    st = FakeStore()
    monkeypatch.setattr(hygiene, "store", lambda: st)
    hygiene.record_reap("a01", {"killed": [{"pid": 7, "cmd": "sleep 999"}], "survivors": []})
    hygiene.record_reap("a01", {"killed": [], "survivors": []})
    hygiene.record_reap("a02", {"killed": [{"pid": 8, "cmd": "nc -l 9"}, {"pid": 9, "cmd": "python3 serve.py"}],
                                "survivors": [{"pid": 9, "cmd": "python3 serve.py"}], "error": None})
    reaps = st.get(hygiene.REAP_STORE)
    assert reaps["a01"] == {"commands": 2, "killed": 1, "max_alive": 1, "survivors": [], "errors": []}
    assert reaps["a02"]["commands"] == 1 and reaps["a02"]["killed"] == 2 and reaps["a02"]["max_alive"] == 2
    assert reaps["a02"]["survivors"] == [[{"pid": 9, "cmd": "python3 serve.py"}]]
