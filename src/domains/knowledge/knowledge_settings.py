"""Fixed settings of the knowledge-work domain: paths, the images, the groups and the limits. Nothing here is a knob
of a run; those live in src/configuration/."""
from src.configuration import settings

TASK = "knowledge_work"                         # CheatBench's task name (Benchmark.name)
TASK_DIR = settings.CHEATBENCH / "tasks" / TASK
BUILD = settings.BUILD / TASK                   # build.py's output: data.jsonl and environment/files/<row>/
DEFAULT_ROW = "2ea2e5b5"                        # the work-time study (PPTX), the README's example assignment

OFFICE_IMAGE = "cbkw:office"                    # CheatBench's task Dockerfile (LibreOffice, PyMuPDF, Pillow, NumPy)
OFFICE_LABEL = "cbkw.dockerfile_sha256"         # the image label that names the Dockerfile it was built from
AGENT_IMAGE = "cbkw:agent"                      # the office image plus our agent user (sandbox/)
AGENT_LABEL = "cbkw.agent_recipe_sha256"        # sha256 of the agent recipe plus the office image id

EXTERNAL = "/external"                          # /external/contract_<id> is a symlink to the contract directory
CACHE = "/data/cache"                           # evaluation.json and the review logs
ARTIFACTS = "/data/cache/artifacts"             # the current pipeline's deliverable (build.py REVIEW_ROOT)
REVIEW_DIR = "/opt/review"                      # root 700: CheatBench's grader.py, the runner and the reference copy
STAGE_TAR = "/root/.knowledge_stage.tgz"        # where Sample.files puts the staging tarball; setup removes it

TEAM_GROUP, TEAM_GID = "team", 2000             # owns the shared contract directory of a shared_task team
PIPELINE_GROUP, PIPELINE_GID = "pipeline", 2001  # may read the pipeline artifact on a team (the access item `read`)
REVIEW_GROUP, REVIEW_GID = "review", 2002       # may read the review logs and evaluation.json on a team (`review`)
REVIEW_TIMEOUT_S = 600                          # one review: two renders, each LibreOffice conversion capped at 90 s
README_REVIEW_END = "internal quality bar."     # where the task README's sentence on the review tool ends
