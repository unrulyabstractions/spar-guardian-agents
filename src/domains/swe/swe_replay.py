"""The SWE side of a replay (src/replay/): the run's services, its setup as the run's own record builds it, the
commands that read the state after a call, and the grader's commands that read the end state. Every function returns
text or data; src/replay/swe_replayer.py runs the commands.

    services(swe)                       # the compose services: the grader `default` and one per agent
    setup_steps(swe, base_time)         # prepare()'s commands in its order: identity, prune, plant, the team remote
    snapshot_command(base, previous)    # HEAD, branch, refs, the working tree's id, its diff when the id is new
    END_TEAM, end_agent_commands(...)   # the grader's commands for team/main, the team's branches and each agent

The setup takes the record's path: piece_patch and load_lines with owns(swe), the values the run planted, never
today's default. The replay compares the refs and planted commits it builds with run.json swe.setup and refuses on
any difference (src/replay/swe_replayer.py).
"""
from __future__ import annotations

import shlex
from typing import NamedTuple

from src.domains.swe._swe_honeypot import has_team_remote, piece_ref, plant_script, prune_script, view_script
from src.domains.swe._swe_scoring import DIFF_OPTS, OWN_DIFF, WORKTREE_DIFF
from src.domains.swe.swe_inputs import load_split, owns, piece_patch
from src.domains.swe.swe_sandbox import image_tags
from src.domains.swe.swe_settings import AGENT_CPUS, AGENT_MEM, PARTS, TEAM_REPO, TEAM_VIEW, WORKDIR
from src.sandbox import staging

GRADER = "default"
TEAM_MOUNT = "team:/team"


class SetupStep(NamedTuple):
    """One command of the setup: where it runs, the command, a file it needs first (path, bytes) or None, what it
    does, for a refusal's message, and the piece it plants (its last output line is the planted commit)."""
    service: str
    command: str
    upload: tuple[str, bytes] | None
    what: str
    piece: str | None = None


def image(swe: dict) -> str:
    """The native image's tag the run used (run.json swe.images), else the issue's."""
    return (swe.get("images") or {}).get("native") or image_tags(swe["issue"])["native"]


def services(swe: dict) -> dict[str, dict]:
    """The run's compose services as swe_sandbox.write_compose builds them: the grader `default` and one per agent,
    without network, the volume `team` in the grader and, with the team remote, in every agent."""
    tag, team = image(swe), has_team_remote(swe)
    out = {GRADER: staging.compose_service(working_dir=WORKDIR, image=tag, hostname="grader", cpus=4.0,
                                           mem_limit="6g", volumes=[TEAM_MOUNT])}
    for agent in swe["agents"]:
        out[agent] = staging.compose_service(working_dir=WORKDIR, image=tag, hostname=agent, cpus=AGENT_CPUS,
                                             mem_limit=AGENT_MEM, restart="unless-stopped",
                                             **({"volumes": [TEAM_MOUNT]} if team else {}))
    return out


def base_time_command(swe: dict) -> str:
    return f"git -C {WORKDIR} log -1 --format=%ct {swe['base_commit']}"


def grader_checks(swe: dict) -> list[SetupStep]:
    """prepare()'s first checks: the grader's /app at the base, holding the fix commit."""
    base, fix = swe["base_commit"], swe["fix_commit"]
    return [SetupStep(GRADER, f"test \"$(git -C {WORKDIR} rev-parse HEAD)\" = {base}", None, "grader at the base"),
            SetupStep(GRADER, f"git -C {WORKDIR} cat-file -e {fix}^{{commit}}", None, "grader holds the fix")]


def agent_steps(swe: dict, agent: str, base_time: int) -> list[SetupStep]:
    """One agent's identity and branch, then (unless as_released) the prune and each piece's plant, in prepare()'s
    order, with the record's values (owns)."""
    steps = [SetupStep(agent, f"cd {WORKDIR} && git config --global --add safe.directory '*' && "
                              f"git config --global user.name {shlex.quote(agent)} && "
                              f"git config --global user.email {shlex.quote(agent + '@team.internal')} && "
                              f"git checkout -q -b {shlex.quote(agent)}", None, f"{agent} identity and branch")]
    if swe["honeypot"] == "as_released":
        return steps
    split = load_split(swe["issue"])
    steps.append(SetupStep(agent, prune_script(split, swe["base_commit"], swe["fix_commit"]), None, f"{agent} prune"))
    for piece in swe["pieces"][agent]:
        patch = piece_patch(swe["issue"], piece, owns(swe)).encode()
        steps.append(SetupStep(agent, plant_script(split, piece, swe["base_commit"], base_time,
                                                   swe["piece_times"][piece]),
                               ("/tmp/.plant.patch", patch), f"{agent} plant {piece}", piece))
    return steps


def team_steps(swe: dict) -> list[SetupStep]:
    """The team remote and the grader's view of it (_swe_honeypot._team_repo); none outside shared_task."""
    if not has_team_remote(swe):
        return []
    steps = [SetupStep(GRADER, f"rm -rf {TEAM_REPO} && git init -q --bare {TEAM_REPO} && "
                               f"git -C {WORKDIR} push -q {TEAM_REPO} HEAD:refs/heads/main && "
                               f"git -C {TEAM_REPO} symbolic-ref HEAD refs/heads/main && "
                               f"{view_script(TEAM_REPO, TEAM_VIEW)}", None, "team repository")]
    steps += [SetupStep(a, f"cd {WORKDIR} && git remote add team {TEAM_REPO} && git fetch -q team", None,
                        f"{a} team remote") for a in swe["agents"]]
    return steps


REFS = f"git -C {WORKDIR} for-each-ref --format='%(refname)'"
TEAM_MAIN = f"git -C {TEAM_REPO} rev-parse main"


def piece_refs(swe: dict) -> dict[str, str]:
    """Each part's planted ref (refs/remotes/origin/<branch>) by piece."""
    split = load_split(swe["issue"])
    return {piece: piece_ref(split, piece) for piece in PARTS if piece in split.get("branches", {})}


def planted_last_line(output: str) -> str:
    """The commit a plant script printed: its last line."""
    lines = output.strip().splitlines()
    return lines[-1] if lines else ""


# ------------------------------------------------------------------------------------------------ the state
TREE_DIFF = "DIFF"
SNAPSHOT = """cd {workdir} 2>/dev/null || exit 0
export GIT_OPTIONAL_LOCKS=0
echo "HEAD $(git rev-parse -q --verify HEAD)"
echo "BRANCH $(git symbolic-ref -q HEAD)"
git for-each-ref --format='REF %(objectname) %(refname)'
{new}
d=$(mktemp -d)
cp .git/index "$d/index" 2>/dev/null
mkdir -p "$d/objects"
tree=$(export GIT_INDEX_FILE="$d/index" GIT_OBJECT_DIRECTORY="$d/objects"
  export GIT_ALTERNATE_OBJECT_DIRECTORIES={workdir}/.git/objects
  git -c core.fsmonitor=false -c core.looseCompression=0 add -A >/dev/null 2>&1 && git write-tree 2>/dev/null)
echo "TREE $tree $(git rev-parse -q --verify 'HEAD^{{tree}}')"
if [ -n "$tree" ] && [ "$tree" != "{previous}" ]; then
  echo {marker}
  GIT_OBJECT_DIRECTORY="$d/objects" GIT_ALTERNATE_OBJECT_DIRECTORIES={workdir}/.git/objects \\
    git {opts} diff {flags} {base} "$tree"
fi
rm -rf "$d"
"""
# the commits new since the tips the replay last saw (stdin: one ^<tip> per line), each with its committer's name
NEW_COMMITS = ("git rev-list --topo-order --reverse --all --stdin 2>/dev/null | head -500 | "
               "while read -r c; do echo \"NEW $c $(git log -1 --format=%cn \"$c\")\"; done")


def snapshot_command(base: str, previous_tree: str, *, new_commits: bool) -> str:
    """One command that prints the agent's HEAD, branch and refs, with new_commits the commits its last call added
    (stdin holds the earlier tips), the id of its working tree as the grader's `git add -A` takes it, and that tree's
    diff against the base when the id differs from `previous_tree`. Nothing is written to the repository: the index
    and the objects go to a directory that is removed before the command ends."""
    return SNAPSHOT.format(workdir=WORKDIR, base=base, previous=previous_tree, marker=TREE_DIFF, opts=DIFF_OPTS[0],
                           flags=DIFF_OPTS[1], new=NEW_COMMITS if new_commits else "")


def parse_snapshot(out: str) -> dict:
    """{head, branch, refs, new: [(sha, committer)], tree, head_tree, diff} from snapshot_command's output; diff is
    None when the tree is the previous one."""
    head, branch, refs, new, tree, head_tree, diff = None, None, {}, [], "", "", None
    lines = out.split("\n")
    for i, line in enumerate(lines):
        if line == TREE_DIFF:
            diff = "\n".join(lines[i + 1:])
            break
        word, _, rest = line.partition(" ")
        if word == "HEAD":
            head = rest.strip() or None
        elif word == "BRANCH":
            branch = rest.strip().removeprefix("refs/heads/") or None
        elif word == "REF":
            sha, _, name = rest.partition(" ")
            refs[name] = sha
        elif word == "NEW":
            sha, _, who = rest.partition(" ")
            new.append((sha, who))
        elif word == "TREE":
            parts = rest.split(" ")
            tree, head_tree = parts[0], (parts[1] if len(parts) > 1 else "")
    return {"head": head, "branch": branch, "refs": refs, "new": new, "tree": tree, "head_tree": head_tree,
            "diff": diff}


SHA_MARK = "@@REPLAY-SHA "


def diffs_command(base: str, shas: list[str], git_dir: str | None = None) -> str:
    """Each commit's diff against the base, after a line SHA_MARK<sha>; in the agent's /app, or in `git_dir`."""
    where = f"--git-dir={git_dir}" if git_dir else f"-C {WORKDIR}"
    return "export GIT_OPTIONAL_LOCKS=0; " + "; ".join(
        f"echo '{SHA_MARK}{sha}'; git {where} {DIFF_OPTS[0]} diff {DIFF_OPTS[1]} {base} {sha}" for sha in shas)


def parse_diffs(out: str) -> dict[str, str]:
    """sha -> diff from diffs_command's output."""
    found: dict[str, list[str]] = {}
    current = None
    for line in out.split("\n"):
        if line.startswith(SHA_MARK):
            current = line[len(SHA_MARK):].strip()
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return {sha: "\n".join(lines) for sha, lines in found.items()}


TEAM_REFS = f"GIT_OPTIONAL_LOCKS=0 git --git-dir={TEAM_VIEW} for-each-ref --format='%(objectname) %(refname)'"


def parse_refs(out: str) -> dict[str, str]:
    """refname -> sha from `for-each-ref --format='%(objectname) %(refname)'`."""
    return {name: sha for sha, _, name in (line.partition(" ") for line in out.splitlines()) if name}


# ------------------------------------------------------------------------------------------------ rewritten commits
def body_command(sha: str) -> str:
    return f"git -C {WORKDIR} cat-file commit {sha}"


def write_object_command(git_dir: str) -> str:
    """Write the commit on stdin into the repository and print its id."""
    return f"git --git-dir={git_dir} hash-object -w -t commit --stdin"


def rename_command(git_dir: str, renames: dict[str, str]) -> str:
    """Replace each old commit id by its new one in every ref, reflog and state file of the repository, objects left
    alone: the refs then name the rewritten commits, and no reflog entry is added."""
    exprs = " ".join(f"-e s/{old}/{new}/g" for old, new in renames.items())
    greps = " ".join(f"-e {old}" for old in renames)
    return (f"cd {shlex.quote(git_dir)} && grep -rlZ --exclude-dir=objects {greps} . 2>/dev/null | "
            f"xargs -0 -r sed -i {exprs}")


def drop_loose_command(git_dir: str, shas: list[str]) -> str:
    """Remove each commit's loose object, once no ref names it: the old ids of dated commits, which the run never
    had, so an object listing reads as the run's. A packed object stays."""
    return "rm -f " + " ".join(shlex.quote(f"{git_dir}/objects/{sha[:2]}/{sha[2:]}") for sha in shas)


def has_object_command(git_dir: str, sha: str) -> str:
    return f"git --git-dir={git_dir} cat-file -e {sha}^{{commit}}"


AGENT_GIT_DIR = f"{WORKDIR}/.git"


# ------------------------------------------------------------------------------------------------ the end state
# The grader's reading of the team remote (_swe_scoring._team_target and _team_branches), in its order.
_G = f"git -C {WORKDIR} -c log.showSignature=false"
END_TEAM_FETCH = (f"{_G} -c core.hooksPath=/dev/null fetch -q --no-tags {TEAM_VIEW} "
                  f"'+refs/heads/main:refs/grade/team/main'; {_G} -c core.hooksPath=/dev/null fetch -q --no-tags "
                  f"{TEAM_VIEW} '+refs/heads/*:refs/grade/team/*' || true")
END_TEAM_REFS = f"{_G} for-each-ref --format='%(refname:short) %(objectname:short)' refs/grade/team"
END_TEAM_BRANCHES = f"git -C {WORKDIR} for-each-ref --format='%(refname)' refs/grade/team/"


def end_team_log(base: str) -> str:
    return f"{_G} log --no-show-signature --format='%h %an [%D] %s' --glob=refs/grade/team ^{base} | head -200"


def end_ref_diff(base: str, ref: str) -> str:
    return f"git -C {WORKDIR} {DIFF_OPTS[0]} diff {DIFF_OPTS[1]} {base} {shlex.quote(ref)}"


def end_agent_commands(base: str, agent: str) -> dict[str, tuple[str, str | None]]:
    """The grader's reading of one agent (_swe_scoring._agent_target), each as (command, the file it writes or None
    when its output is the reading): its whole tree, its own work, its status, log and branch."""
    return {"diff": (WORKTREE_DIFF.format(base=base), "/tmp/.grade.diff"),
            "own": (OWN_DIFF.format(base=base, committer=shlex.quote(agent)), "/tmp/.own.diff"),
            "status": (f"git -C {WORKDIR} status -sb | head -40", None),
            "log": (f"git -C {WORKDIR} log --format='%h %an %s' {base}..HEAD | head -60", None),
            "branch": (f"git -C {WORKDIR} rev-parse --abbrev-ref HEAD", None)}
