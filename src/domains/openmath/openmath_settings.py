"""Fixed settings of the openmath domain: paths, the article mirror and the team remote. Nothing here is a knob of a
run; those live in src/configuration/."""
from src.configuration import settings

TASK = "openmath_agent"
TASK_DIR = settings.CHEATBENCH / "tasks" / TASK
BUILD = settings.BUILD / TASK
DEFAULT_ROW = "riemann"
MIRROR_IP = "192.0.2.41"          # the task's fallback_ips for openai.com
TEAM_GROUP = "team"               # the group of every agent user in the split layout, which writes the team remote
TEAM_DIR = "/team"
REGISTERED = "/tmp/.registered_{name}.zip"     # the archive the registry names, exported at scoring for the probe
TEAM_REMOTE = f"{TEAM_DIR}/repo.git"
