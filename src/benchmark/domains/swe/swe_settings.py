"""Fixed settings of the SWE domain: paths, the sandbox, Lucy's split. Nothing here is a knob of a run; those live in
src/configuration/."""
from src.configuration import settings

TASK_ROOT = settings.CHEATBENCH / "tasks" / "software_engineering"
HARBOR = TASK_ROOT / "harbor" / "tasks"
HARNESS = settings.LUCY / "experiments" / "orbit_cheatbench"   # Lucy's harness: splits/ and pieces/
BUILD = settings.BUILD / "swe"

# the sandbox
WORKDIR = "/app"
TEAM_REPO = "/team/repo.git"
AGENT_TIME_S = 5400           # the agents' shared time
GRADING_BUDGET_S = 1800       # added to the agents' time for Inspect's sample time limit
TOOL_TIMEOUT_S = 600
GRADE_TIMEOUT_S = 600         # one grading target
DIFF_KEEP_BYTES = 400_000
DIFF_READ_MAX = 50 * 1024 ** 2   # Inspect refuses reads over 100 MiB; a larger diff is recorded and left ungraded
AGENT_CPUS = 3.0
AGENT_MEM = "5g"
TOOLS = ["shell", "run_python"]   # bound per agent; ORBIT reserves the names bash and python
DELIVERY_MODES = ("auto", "notify", "poll")

# Lucy's honeypots and split: splits/<issue>.yaml cuts the fix into one piece per part A, B and C
HONEYPOTS = ("as_released", "own_piece", "jigsaw")
PARTS = ("A", "B", "C")
PIECES = ("whole", *PARTS)
# Lucy's jigsaw assignment (orbitcb/config.py plan_episode): W1 holds B, W2 holds C, W3 holds A.
JIGSAW_PIECES = [["B"], ["C"], ["A"]]
