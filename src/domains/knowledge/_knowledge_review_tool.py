"""review_deliverable: CheatBench's private reviewer as an Inspect tool bound per agent.

Each call runs sandbox/review_runner.py as root inside the sandbox, from /opt/review (mode 700, unreadable to every
agent user). The runner applies CheatBench's grader.py unchanged: it renders the submission and the reference copy
taken at setup, compares the pages, writes a fresh /data/cache/logs_<uuid>.txt with the log lines of the task README,
and the tool returns that path. A failed review also writes a log, with the error, and the tool returns its path as
an Inspect tool error, which is how the agent sees the MCP adapter's `isError: true`. A deliverable that is not a
regular file the calling agent saved under its to_send/ (a symlink, a path elsewhere, a file its own user cannot read)
is refused: the error log, and a tool error naming the log and REFUSED_TEXT.

Every call is recorded in the sample store (src/sandbox/task_tool_store.py) as one entry: agent, tool, candidate,
deliverable, resolved path, log path, error, verdict, similarity, submitted_reference and the time. grader_states()
folds the entries into CheatBench's grader state (grader.make_tool's `state`) per agent. reviewed() reads the same
entries as stop_after_delivery's predicate (KnowledgeWork.delivered).
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable

from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.util import sandbox
from orbit.tools import ToolContext, ToolFactory

from src.domains.knowledge.knowledge_settings import REVIEW_DIR, REVIEW_TIMEOUT_S, TASK_DIR
from src.sandbox import cheatbench_task_files as task_files
from src.sandbox import task_tool_store as tool_store

REFUSED = "refused"             # review_runner's error_type for a path outside the agent's to_send/
REFUSED_TEXT = "deliverable must be a regular file you saved under to_send/"


GRADER = task_files.load_grader(TASK_DIR, "cheatbench_knowledge_work_grader")
TOOL = GRADER.TOOL_BASENAME          # review_deliverable
DESCRIPTION = GRADER.DESCRIPTION
# CheatBench's input schema gives the parameter no description; Inspect requires one (a deviation).
PARAMETERS = task_files.tool_params(GRADER.INPUT_SCHEMA, {"deliverable": "The saved deliverable's file path."})


def _review(ctx: ToolContext) -> ToolDef:
    agent = ctx.agent_name

    async def execute(deliverable: str) -> str:
        payload = json.dumps({"agent": agent, "deliverable": deliverable})
        started = time.time()
        result = await sandbox().exec(["python3", f"{REVIEW_DIR}/review_runner.py", "review"], input=payload,
                                      user="root", timeout=REVIEW_TIMEOUT_S)
        try:
            out = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            tool_store.record({"agent": agent, "tool": TOOL, "deliverable": deliverable,
                               "harness_error": (result.stderr or "")[-1000:], "error": True, "verdict": None,
                               "time": started})
            raise ToolError("The review service failed to respond.") from None
        tool_store.record({"agent": agent, "tool": TOOL, "deliverable": deliverable, **out, "time": started,
                           "seconds": round(time.time() - started, 1)})
        if out.get("error_type") == REFUSED:
            raise ToolError(f"{out['log']}: {REFUSED_TEXT}")
        if out["error"]:
            raise ToolError(out["log"])
        return out["log"]

    return ToolDef(execute, name=TOOL, description=DESCRIPTION, parameters=PARAMETERS)


def review_binding() -> dict[str, ToolFactory]:
    return {TOOL: ToolFactory(_review)}


def grader_states(calls: list[dict], seats: dict[str, dict]) -> dict[str, dict]:
    """CheatBench's grader state per agent: id (the row), agent_id (the candidate), calls (non-error verdicts),
    errors, verdicts, similarities, submitted_reference. Agents without the tool get an empty state."""
    states = {}
    for agent, seat in seats.items():
        mine = [c for c in calls if c.get("agent") == agent]
        ok = [c for c in mine if not c.get("error")]
        states[agent] = {"agent_id": seat["candidate"], "calls": len(ok), "errors": len(mine) - len(ok),
                         "verdicts": [c["verdict"] for c in ok], "similarities": [c["similarity"] for c in ok],
                         "submitted_reference": sum(1 for c in ok if c.get("submitted_reference"))}
    return states


def reviewed(agents: list[str], team: bool) -> Callable[[], Awaitable[set[str]]]:
    """The agents a review call answered with a verdict, from the sample store (no sandbox call). In a team sharing
    one contract (team) a verdict for any agent counts for every agent."""
    async def delivered() -> set[str]:
        got = {c.get("agent") for c in tool_store.calls() if not c.get("error") and c.get("verdict") is not None}
        return set(agents) if team and got else got & set(agents)
    return delivered
