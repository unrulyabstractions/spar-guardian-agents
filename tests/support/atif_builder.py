"""An ATIF transcript as the export writes it, built from a list of tool calls.

    t = transcript([("shell", "cat notes.md", "the file's text")])
"""
from __future__ import annotations


def transcript(calls: list[tuple[str, str, str]]) -> dict:
    """A transcript of one agent step per call: (tool, command, result)."""
    steps = [{"step_id": 0, "source": "system", "message": "rules"}]
    for k, (tool, cmd, result) in enumerate(calls):
        steps.append({"step_id": k + 1, "source": "agent", "message": "", "tool_calls": [
            {"tool_call_id": f"c{k}", "function_name": tool, "arguments": {"command": cmd}}],
            "observation": {"results": [{"source_call_id": f"c{k}", "content": result}]}})
    return {"trajectory": {"steps": steps}, "prompt": ""}
