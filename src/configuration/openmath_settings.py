"""Fixed settings of the openmath domain (src/benchmark/domains/openmath/): paths, the sandbox user, limits."""
from src.configuration import settings

TASK = "openmath_agent"
TASK_DIR = settings.CHEATBENCH / "tasks" / TASK
BUILD = settings.BUILD / TASK
AGENT_UID = 1000
AGENT_NAME = "user"
WORKDIR = "/workspace"
MIRROR_IP = "192.0.2.41"          # the task's fallback_ips for openai.com
TEAM_IDENTITY = "agent-018"       # the git identity the staged row gives the agent (README)
TOKEN_LIMIT = 8_000_000           # spend guard per agent; the episode stays judgeable if it trips
TOOL_TIMEOUT_S = 300
# Inspect's bash/python tools accept `user`; the sandbox container runs as root so the
# setup script can chown and the mirror sidecar can bind :443, and every agent command
# runs as the unprivileged user, as in CheatBench.
TOOL_KWARGS = {"bash": {"user": AGENT_NAME, "timeout": TOOL_TIMEOUT_S},
               "python": {"user": AGENT_NAME, "timeout": TOOL_TIMEOUT_S}}
