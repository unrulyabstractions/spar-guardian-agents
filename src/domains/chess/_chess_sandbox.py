"""The chess sandbox: one container from CheatBench's python311 image (src/sandbox/machines/), the service `default`,
with no network, whatever the team's size. No agent has a shell, and the checkout lives on the host
(_chess_host_game.py), so the container's one use is CheatBench's: it runs an edited opponent/play.py under grader.py's
bootstrap and limits, as the agent's user, where CheatBench's tool runs it inside the agent's container.

    compose = write_compose()
    report = await run_policy("k7q2m", source, state)     # {"status": "completed", "move": ..., "resign": ...}
"""
from __future__ import annotations

import json
from pathlib import Path

from inspect_ai.util import sandbox

from src.domains.chess._chess_cheatbench import GRADER
from src.domains.chess._chess_settings import BUILD
from src.sandbox import sandbox_hygiene as hygiene
from src.sandbox.agent_tools import AGENT_NAME
from src.sandbox.machines import agent_machines as machines


def write_compose() -> Path:
    """The compose file: one service `default` from the python311 image (agent_machines.write_compose)."""
    return machines.write_compose(BUILD, [machines.DEFAULT_SERVICE])


async def run_policy(agent: str, source: str, state: dict) -> dict:
    """CheatBench's run_policy (grader.py) in the container: the edited policy and the bootstrap in a fresh directory
    of root's, run as `user` with the state on stdin under POLICY_SECONDS, then every process of `user` killed and
    counted against the agent whose move asked for it (sandbox_hygiene.reap_after). Reports the decision, or why the
    policy made none."""
    box = sandbox()
    made = await box.exec(["mktemp", "-d", "/tmp/policy_XXXXXX"], user="root")
    folder = made.stdout.strip()
    if not made.success or not folder:
        return {"status": "execution_error", "error": "the policy's directory could not be made"}
    try:
        await box.write_file(f"{folder}/play.py", source)
        await box.write_file(f"{folder}/bootstrap.py", GRADER.POLICY_BOOTSTRAP)
        await box.exec(["chmod", "-R", "a+rX", folder], user="root")
        try:
            proc = await box.exec(["python3", "-I", "-S", f"{folder}/bootstrap.py", f"{folder}/play.py"],
                                  input=json.dumps(state), cwd=folder, user=AGENT_NAME,
                                  timeout=GRADER.POLICY_SECONDS)
        except TimeoutError:
            return {"status": "timeout", "error": "Policy exceeded its wall-time limit."}
    finally:
        await hygiene.reap_after(agent, AGENT_NAME, box=box)
        await box.exec(["rm", "-rf", folder], user="root")
    if not proc.success:
        return {"status": "execution_error", "error": (proc.stderr or "policy did not return a decision")[-2000:]}
    try:
        value = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "execution_error", "error": "policy printed no decision"}
    return {"status": "completed", "move": value.get("move"), "resign": bool(value.get("resign"))}
