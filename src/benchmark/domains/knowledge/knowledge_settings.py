"""Fixed settings of the knowledge-work domain: paths, the image, the sandbox users and the limits.

They live in the domain package because src/configuration/ holds the run's knobs; nothing here is a knob of a run.
"""
from src.configuration import settings

TASK = "knowledge_work"                         # CheatBench's task name (Benchmark.name)
TASK_DIR = settings.CHEATBENCH / "tasks" / TASK
BUILD = settings.BUILD / TASK                   # build.py's output: data.jsonl and environment/files/<row>/
DEFAULT_ROW = "2ea2e5b5"                        # the work-time study (PPTX), the README's example assignment

OFFICE_IMAGE = "cbkw:office"                    # CheatBench's task Dockerfile (LibreOffice, PyMuPDF, Pillow, NumPy)
OFFICE_LABEL = "cbkw.dockerfile_sha256"         # the image label that names the Dockerfile it was built from

WORKDIR = "/workspace"                          # the contract directory of a lone agent and of a shared_task team
EXTERNAL = "/external"                          # /external/contract_<id> is a symlink to the contract directory
CACHE = "/data/cache"                           # evaluation.json and the review logs
ARTIFACTS = "/data/cache/artifacts"             # the current pipeline's deliverable (build.py REVIEW_ROOT)
REVIEW_DIR = "/opt/review"                      # root 700: CheatBench's grader.py, the runner and the reference copy
STAGE_TAR = "/root/.kw_stage.tgz"               # where Sample.files puts the staging tarball; setup removes it

AGENT_NAME = "user"                             # the lone agent's Linux user, as in openmath
AGENT_UID = 1000
TEAM_GROUP = "team"                             # owns the shared contract directory of a shared_task team
PIPELINE_GROUP = "pipeline"                     # may read the pipeline artifact on a team (the access item `read`)
REVIEW_GROUP = "review"                         # may read the review logs and evaluation.json on a team (`review`)

TOOL_TIMEOUT_S = 300
REVIEW_TIMEOUT_S = 600                          # one review: two renders, each LibreOffice conversion capped at 90 s
