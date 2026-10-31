"""Fixed settings of the creative-writing domain: paths, the images, the review endpoint and the group of a team.
Nothing here is a knob of a run; those live in src/configuration/."""
from src.configuration import settings

TASK = "creative_writings"                      # CheatBench's task name (Benchmark.name)
KEY = "creative_writing"                        # the registry's key (Benchmark.key)
TASK_DIR = settings.CHEATBENCH / "tasks" / TASK
BUILD = settings.BUILD / TASK                   # the docker context, the compose file and the staging files
DEFAULT_ROW = "museum_poem"                     # the README's example brief

BRIEF = "instruction.txt"                       # _builder.py BRIEF_NAME: the brief, at the top of the workspace
DELIVERABLE = "writing.md"                      # build.py's deliverable, named by the prompt and the brief
GRADER = "grading/grade.pyc"                    # task.yaml grader.staged_grader
PLANT_ROOT = "/tmp/grading/ref"                 # _builder.py build_variant: /tmp/grading/ref/<hex>/candidate_<id>.md

AGENT_IMAGE = "cbcw:agent"                      # CheatBench's python311 image, iptables and the compiled scorer
REVIEW_IMAGE = "cbcw:review"                    # the review sidecar: the same interpreter, iproute2 and the scorer
IMAGE_LABEL = "cbcw.recipe_sha256"              # sha256 of the docker context plus the python311 image's id
SCORE_SO = "/opt/creative/_score.so"            # score_ext.c compiled in both images
REVIEW_SERVER = "/opt/creative/creative_review_server.py"
REVIEW_IP = "192.0.2.42"                        # the sidecar's loopback alias, which the agents reach
REVIEW_CONFIG = "/srv/review/config.json"       # in the sidecar: the tokens, the reference and the threshold
REVIEW_CALLS = "/srv/review/calls.jsonl"        # in the sidecar: one line per review call
STAGE_TAR = "/root/.creative_stage.tgz"         # where Sample.files puts the staging tarball; setup removes it
HAND_INS = "/root/.creative_hand_ins"           # where a submit keeps writing.md, by its sha256

TEAM_GROUP, TEAM_GID = "team", 2000             # owns the shared workspace of a shared_task team
WRITING_CHARS = 1 << 18                         # the most of a writing.md or a review's submission the scorer keeps
