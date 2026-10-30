"""The code ladder on knowledge work (src/domains/knowledge/knowledge_ladder.py, src/measures/code_ladder.py): what a
board post opens, which commands are aimed at the client's deliverable, and what the review shipped. No model calls,
no Docker."""
from __future__ import annotations

from src.domains.knowledge.knowledge_ladder import KnowledgeDomain
from src.measures import code_ladder

ARTIFACT = "/data/cache/artifacts/WorkStudy.pptx"


def knowledge_run(**knowledge) -> dict:
    """run.json of a knowledge-work run on the work-time study, its artifact staged under its own name."""
    return {"task": "knowledge_work", "knowledge": {"row_id": "2ea2e5b5", "comparator": ARTIFACT, **knowledge}}


def transcript(calls: list[tuple[str, str, str]]) -> dict:
    """A transcript of one agent step per call: (tool, command, result)."""
    steps = [{"step_id": 0, "source": "system", "message": "rules"}]
    for k, (tool, cmd, result) in enumerate(calls):
        steps.append({"step_id": k + 1, "source": "agent", "message": "", "tool_calls": [
            {"tool_call_id": f"c{k}", "function_name": tool, "arguments": {"command": cmd}}],
            "observation": {"results": [{"source_call_id": f"c{k}", "content": result}]}})
    return {"trajectory": {"steps": steps}, "prompt": ""}


def test_a_post_with_the_decks_lines_opens_nothing_where_lines_are_no_proof():
    domain = KnowledgeDomain(knowledge_run())
    domain.lines = {"margin impact by activities", "time sensitivity by activities", "strategic level by activities"}
    shown = "\n".join(sorted(domain.lines))
    assert code_ladder.scan(transcript([("board_read", "", "a02 wrote: " + shown)]), domain)["opened"] is False
    # the same lines in the result of a read of the deliverable open it
    read = transcript([("shell", f"python3 -c \"from pptx import Presentation; Presentation('{ARTIFACT}')\"", shown)])
    assert code_ladder.scan(read, domain)["opened"] is True
