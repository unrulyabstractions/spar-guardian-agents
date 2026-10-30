"""CheatBench's nine chess tools as the task's tools: Inspect tools on the host, bound per agent.

grader.py hosts them as one MCP server per episode, in the agent's container: play_move, the game's actions
(game_status, reset_game, write_notes, read_notes), api_request, and the checkout's file tools (list_files,
read_file, write_file). Each keeps CheatBench's name, description and input schema (grader.py tool_spec,
make_chess_tools and the API constants, read from the module), with a description added to each parameter because
Inspect requires one. A call runs on the sample run's ChessRun (_chess_host_game.py), and an error answer becomes an
Inspect tool error with the same text, as the MCP adapter shows isError. No agent has a shell, as in CheatBench.

    bindings = harness_bindings()           # {tool name: ToolFactory}
    names = tool_names(holds_request=True)  # the nine, in grader.py's order; api_request only with `request`
"""
from __future__ import annotations

from typing import Any

from inspect_ai.tool import ToolDef, ToolError
from orbit.tools import ToolContext, ToolFactory

from src.domains.chess import _chess_host_game as host_game
from src.domains.chess._chess_cheatbench import GRADER
from src.domains.chess._chess_settings import CHECKOUT
from src.sandbox import cheatbench_task_files as task_files

API = GRADER.API_TOOL_BASENAME          # api_request
# CheatBench's specs: play_move for the nine-tool arm, then the actions, the request and the files
ACTIONS, FILES = GRADER.make_chess_tools({"tool_surface": "chess"}, CHECKOUT, {})
SPECS = {spec["name"]: spec for spec in [
    GRADER.tool_spec({"tool_surface": "chess"}), *ACTIONS,
    {"name": API, "description": GRADER.API_DESCRIPTION, "inputSchema": GRADER.API_INPUT_SCHEMA}, *FILES]}
TOOLS = list(SPECS)
# CheatBench's schemas give no parameter a description; Inspect requires one (a deviation)
PARAMETER_DESCRIPTIONS = {
    "play_move": {"san": "One White move in standard algebraic notation."},
    "write_notes": {"text": "The notepad's new text."},
    "list_files": {"path": f"An absolute directory path under {CHECKOUT}."},
    "read_file": {"path": f"An absolute file path under {CHECKOUT}.", "offset": "The byte offset to read from.",
                  "limit": "How many bytes to read."},
    "write_file": {"path": f"An absolute file path under {CHECKOUT}.", "text": "The file's new text."},
    API: {"url": "The service's URL.", "method": "The HTTP method.",
          "headers_json": "The request's headers as a JSON object of strings.",
          "body_json": "The request's body as JSON text, or empty."},
}
PARAMETERS = {name: task_files.tool_params(spec["inputSchema"], PARAMETER_DESCRIPTIONS.get(name, {}))
              for name, spec in SPECS.items()}


def tool_names(holds_request: bool) -> list[str]:
    """An agent's chess tools, in grader.py's order: all nine, or eight without api_request."""
    return [name for name in TOOLS if holds_request or name != API]


def harness_binding(name: str) -> ToolFactory:
    """One chess tool, bound per agent to the sample run's ChessRun."""
    def factory(ctx: ToolContext) -> ToolDef:
        agent = ctx.agent_name

        async def execute(**kwargs: Any) -> str:      # Inspect passes the arguments through only to **kwargs: Any
            text, error = await host_game.current().call(agent, name, kwargs)
            if error:
                raise ToolError(text)
            return text

        return ToolDef(execute, name=name, description=SPECS[name]["description"], parameters=PARAMETERS[name])

    return ToolFactory(factory)


def harness_bindings() -> dict[str, ToolFactory]:
    """ORBIT tool_bindings for the nine tools; ORBIT builds each only for the agents whose tools name it."""
    return {name: harness_binding(name) for name in TOOLS}
