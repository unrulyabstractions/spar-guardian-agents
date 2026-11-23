"""A stored protein design run played back in its machines: the setup the run's own code builds, checked against the
record, each recorded call in its agent's machine as `user`, check_design re-run on the host on the recorded
arguments, the commits given their recorded ids, the state after every call and the end state against the stored one.

    replayer = ProteinReplayer(run, calls)
    replayer.setup(box)                       # ReplayRefused unless each agent's staged files are the recorded ones
    output, timed_out = replayer.execute(box, call)
    output, state, dated, undated = replayer.after(box, call, output)
    line = replayer.line_state(call, own, standing, team)    # the state a line of steps.jsonl carries
    replayer.end_state(box, keep)             # {"equal": bool, "parts": {...}, "differences": {...}}

It keeps SweReplayer's interface (src/replay/swe_replayer.py), the one replay_run.replay drives.

The setup is _protein_scoring.prepare's, in its order: each agent's copy of the row (_protein_honeypot.agent_files)
staged by its tarball (agent_machines.stage_tar_for), the bare team remote in the first machine and each repository
on main with the remote as its origin (shared_task), or `git init` alone. A copy whose files hash otherwise than
run.json protein.agents.<agent>.staged is refused: the build's rows must hold the planted designs the run had.

The state after a call, the acting agent's (the SWE keys, then this task's):
- head, branch, refs: the agent's repository; dirty: its work dir's saved files differ from HEAD's.
- tree_pieces: the pieces (segment1..N, or whole) the files of its work dir hold, new or changed since staging
  (sandbox/saved_work.py, the scorer's saved work); ref_pieces: those each ref's commit holds (sandbox/team_binder.py).
- files: every such file of its machine (/workspace, /tmp, /home) that holds a piece or a whole design, by path:
  {"pieces", "design"}; ref_designs: the whole design each ref's commit holds.
- check: on a check_design line, the pieces and the whole design its sequence held, and its verdict.
The team remote's state is {"refs", "ref_pieces", "ref_designs"}. Every piece and design is read by ProteinDomain
(file_pieces, pieces_seen, found).

A line of steps.jsonl (line_state) carries the git keys SWE's lines carry (git_state.git_line), then files and
ref_designs, team_designs (the team remote's ref_designs) and check. Only a check_design call's own line holds a
check; every other line holds null, so a check is never carried to a later line.
"""
from __future__ import annotations

import copy
import json
import shlex
import threading
from pathlib import Path

from src.domains.protein import ProteinDesign
from src.domains.protein import _protein_scoring as scoring
from src.domains.protein._protein_checker_tool import TOOL as CHECKER
from src.domains.protein._protein_checker_tool import make_call
from src.domains.protein._protein_honeypot import agent_files
from src.domains.protein._protein_ladder import ProteinDomain, verdict
from src.domains.protein._protein_settings import BUILD, TEAM_BRANCH, TEAM_DIR, TEAM_REMOTE
from src.domains.protein.protein_task import sha256s
from src.measures.run_directory import RunDir
from src.measures.shell_commands import PYTHON_TOOLS, TOOLS
from src.replay.commit_dates import RecordedIds, find_date, renamed_text, reparent, retime
from src.replay.git_state import SHARED, git_line
from src.replay.output_match import mask
from src.replay.recorded_calls import NEVER_RAN, RecordedCall, ReplayRefused
from src.replay.replay_jobs import JobPlayer, Place
from src.replay.replay_sandbox import ReplaySandbox
from src.sandbox import cheatbench_task_files as task_files
from src.sandbox import sandbox_hygiene as hygiene
from src.sandbox import staging
from src.sandbox.agent_tools import AGENT_NAME, TOOL_TIMEOUT_S, WORKDIR, Seat, python_command
from src.sandbox.docker_images import image_id, image_labels, recipe_sha256
from src.sandbox.machines import agent_machines as machines
from src.sandbox.sandbox_probe import KEEP_ERR, KEEP_OUT

ROOT = "root"
PROBE_TIMEOUT_S = 60
TEAM_LOG = f"git --git-dir={TEAM_REMOTE} log --all -n{scoring.LOG_LIMIT} '--format=%h %an <%ae> [%D] %s'"
"""The team remote's log as team_record reads it."""
NEW_COMMITS = ("git rev-list --topo-order --reverse --all --not {tips} 2>/dev/null | head -500 | "
               "while read -r c; do echo \"NEW $c $(git log -1 --format=%cn \"$c\")\"; done")
"""The commits new since the tips the replay last saw, each with its committer."""
READ_TIMEOUT_S = 120
"""The longest a read of the replay's own (a snapshot, a script) may take."""
AS_AGENT = {"user": AGENT_NAME, "workdir": WORKDIR, "timeout": READ_TIMEOUT_S}
"""How the replay's own commands in a machine run: as the seat's user in /workspace (docker exec --user, as Inspect
ran the seat's commands), within READ_TIMEOUT_S."""


def snapshot_command(work: str, tips: list[str] | None) -> str:
    """The repository's HEAD, branch and refs, and with tips (the refs' commits the replay last saw) the commits new
    since them."""
    new = f"; {NEW_COMMITS.format(tips=' '.join(tips))}" if tips is not None else ""
    return (f"cd {shlex.quote(work)} 2>/dev/null || exit 0; export GIT_OPTIONAL_LOCKS=0; "
            "echo \"HEAD $(git rev-parse -q --verify HEAD)\"; echo \"BRANCH $(git symbolic-ref -q HEAD)\"; "
            "git for-each-ref --format='REF %(objectname) %(refname)'" + new)


def parse_snapshot(out: str) -> dict:
    """{head, branch, refs, new: [(sha, committer)]} from snapshot_command's output."""
    read: dict = {"head": None, "branch": None, "refs": {}, "new": []}
    for line in out.splitlines():
        word, _, rest = line.partition(" ")
        if word == "HEAD":
            read["head"] = rest.strip() or None
        elif word == "BRANCH":
            read["branch"] = rest.strip().removeprefix("refs/heads/") or None
        elif word == "REF":
            sha, _, name = rest.partition(" ")
            read["refs"][name] = sha
        elif word == "NEW":
            sha, _, who = rest.partition(" ")
            read["new"].append((sha, who))
    return read


def rename_command(git_dir: str, renames: dict[str, str]) -> str:
    """Every ref of the repository at an old commit, and a detached HEAD, moved to its new commit."""
    d = shlex.quote(git_dir)
    return "; ".join(
        f"for r in $(git --git-dir={d} for-each-ref --points-at {old} --format='%(refname)'); do "
        f"git --git-dir={d} update-ref \"$r\" {new} {old}; done; "
        f"[ \"$(git --git-dir={d} rev-parse -q --verify HEAD)\" != {old} ] || "
        f"git --git-dir={d} update-ref --no-deref HEAD {new} {old}"
        for old, new in renames.items()) + "; true"


def write_object_command(git_dir: str) -> str:
    return f"git --git-dir={shlex.quote(git_dir)} hash-object -w -t commit --stdin"


def script_command(script: str, *args: str) -> list[str]:
    """A sandbox script as run_json runs it: `python3 -I -c script args`."""
    return ["python3", "-I", "-c", script, *args]


def last_json(out: bytes):
    return json.loads(out.decode(errors="replace").strip().splitlines()[-1])


def triples(files: list[dict]) -> list[tuple]:
    """A file list as the end state compares it: (path, sha256, size), errors and texts left out."""
    return sorted((f.get("path"), f.get("sha256"), f.get("size")) for f in files if "error" not in f)


def recorded_check(call: dict) -> dict:
    """A protein.calls entry without its time, as the replayed one is compared with it."""
    return {k: call.get(k) for k in ("agent", "owner", "tool", "arguments", "text", "error")}


class ProteinReplayer:
    task = ProteinDesign.key

    def __init__(self, run: RunDir, calls: list[RecordedCall]) -> None:
        self.run, self.calls = run, calls
        self.block = run.run_json["protein"]
        self.seats = self.block["agents"]
        self.agents = list(self.seats)
        self.first = self.agents[0]
        self.work = self.block["work_dir"]
        self.git_dir = f"{self.work}/.git"
        self.team = bool(self.block.get("team_remote"))
        self.row = task_files.load_row(BUILD, self.block["row_id"])
        self.domain = ProteinDomain(run.run_json)
        n = self.block.get("n_pieces") or 0
        self.pieces = [f"{ProteinDesign.piece_stem}{k}" for k in range(1, n + 1)] if self.domain.split else ["whole"]
        self.staged = scoring.staged_paths({a: seat["staged"] for a, seat in self.seats.items()})
        record = self.block.get("team") or {}
        commits = [b.get("commit") or "" for b in (record.get("branches") or {}).values()]
        self.ids = RecordedIds([c.result for c in calls] + list(record.get("log") or []) + commits +
                               [(record.get("hand_in") or {}).get("commit") or "",
                                (run.run_json.get("team_submission") or {}).get("snapshot") or ""])
        self.checkers: dict[str, tuple[dict, object]] = {}
        self.checks: list[dict] = []
        self.held: dict[str, dict[str, dict]] = {}      # commit id -> {path: holding} of its new or changed files
        self.holding_of: dict[str, dict] = {}           # sha256 of a file -> its holding
        self.lock = threading.Lock()
        self.last: dict[str, dict] = {}
        self.probe: dict[str, dict[str, bool]] = {}
        self.jobs = JobPlayer.of(run, calls, service_of=self.service_of)    # None before the round clock

    # ---------------------------------------------------------------- the sandbox
    def services(self) -> dict[str, dict]:
        """agent_machines.write_compose's services: one per agent, without network, the team volume in each with the
        team remote. Each is named by its agent, a lone agent's too (the run's `default`): no network, so no command
        in a machine sees a service's name."""
        volumes = {"volumes": [f"team:{TEAM_DIR}"]} if self.team else {}
        return {a: staging.compose_service(working_dir=WORKDIR, image=machines.IMAGE, **volumes) for a in self.agents}

    def volumes(self) -> dict[str, dict]:
        """The compose project's volumes: the team remote's, where the run had one."""
        return {"team": {}} if self.team else {}

    def image(self) -> dict:
        """The machines' image here and the run's; recipe says whether its label is today's python311.Dockerfile."""
        local = image_id(machines.IMAGE)
        label = (image_labels(machines.IMAGE) or {}).get(machines.IMAGE_LABEL)
        return {"tag": machines.IMAGE, "id": local, "run_id": self.block.get("image"),
                "own": bool(local) and local == self.block.get("image"),
                "recipe": label == recipe_sha256(machines.DOCKERFILE)}

    # ---------------------------------------------------------------- the setup
    def copies(self) -> dict[str, dict[str, bytes]]:
        """Each agent's copy of the row as ProteinDesign.copies builds it, from the seat's recorded pieces."""
        n = self.block.get("n_pieces")
        return {a: agent_files(self.row, seat.get("pieces"), n) for a, seat in self.seats.items()}

    def copy_problems(self, copies: dict[str, dict[str, bytes]]) -> list[str]:
        """Each staged path whose hash in a copy differs from run.json's, or that only one of them holds."""
        return sorted({f"{a}: {path}" for a, files in copies.items()
                       for path, _ in set(sha256s(files).items()) ^ set(self.seats[a]["staged"].items())})

    def setup(self, box: ReplaySandbox) -> dict:
        """The run's staging and repositories (_protein_scoring.prepare), then its probe (PROBE_COMMANDS) compared
        with run.json protein.probe once times are masked. Refused when the image's recipe is not today's or a copy
        differs from the recorded one."""
        copies = self.copies()
        if problems := self.copy_problems(copies):
            raise ReplayRefused("the staged files differ from the recorded ones: " + "; ".join(problems))
        if not self.image()["recipe"]:
            raise ReplayRefused(f"{machines.IMAGE}'s label is not the sha256 of today's python311.Dockerfile")
        mtimes = task_files.row_mtimes(self.row)
        for agent, files in copies.items():
            tar = machines.stage_tar_for(BUILD, self.row["id"], files, mtimes={
                d: mtimes.get(d, mtimes.get(self.row["payload_file"])) for d in files})
            box.must(agent, f"cat > {machines.STAGE_TAR}", f"{agent}: upload", stdin=Path(tar).read_bytes())
            box.must(agent, machines.SETUP_SCRIPT, f"{agent}: staging")
        if self.team:
            box.must(self.first, scoring.TEAM_REMOTE_INIT, "the team remote")
        for agent in self.agents:
            if not self.team:
                box.must_run(agent, ["git", "init", "--quiet", self.work], f"{agent}'s repository", **AS_AGENT)
                continue
            box.must(agent, f"git config --system --add safe.directory {TEAM_REMOTE}", f"{agent}: safe.directory")
            script = scoring.TEAM_REPO_INIT.format(branch=TEAM_BRANCH, work=shlex.quote(self.work),
                                                   remote=TEAM_REMOTE, name=shlex.quote(agent))
            box.must_run(agent, ["bash", "-c", script], f"{agent}'s repository", **AS_AGENT)
        self.probe = {a: self.probe_agent(box, a) for a in self.agents}
        return {"copies_match": True, "probe": self.probe}

    def probe_agent(self, box: ReplaySandbox, agent: str) -> dict[str, bool]:
        """Each probe command as run_command ran it (`bash -lc` as `user`, the output's tail kept), against the
        recorded one: rc equal and the texts equal once times are masked."""
        submitted = shlex.quote(str(self.block["payload_file"]).rsplit("/", 1)[0])
        fields = {"work": shlex.quote(self.work), "submitted": submitted,
                  "staged": " ".join(shlex.quote(p) for p in self.seats[agent]["staged"])}
        recorded = (self.block.get("probe") or {}).get(agent) or {}
        out = {}
        for name, command in scoring.PROBE_COMMANDS.items():
            done = box.run(agent, ["bash", "-lc", command.format(**fields)], user=AGENT_NAME, workdir=WORKDIR,
                           timeout=PROBE_TIMEOUT_S)
            got = {"rc": done.rc, "out": done.stdout.decode(errors="replace")[-KEEP_OUT:],
                   "err": done.stderr.decode(errors="replace")[-KEEP_ERR:]}
            want = recorded.get(name) or {}
            out[name] = got["rc"] == want.get("rc") and all(mask(got[k]) == mask(want.get(k) or "")
                                                            for k in ("out", "err"))
        return out

    # ---------------------------------------------------------------- the calls
    def in_sandbox(self, call: RecordedCall) -> bool:
        """A shell or python call, or a check_design call, that ran (recorded_calls.NEVER_RAN): the checker is re-run
        on the host, every other host tool keeps its recorded output. Since the round clock also job_output, job_stop
        and peer_shell (replay_jobs.py)."""
        if self.jobs is not None and self.jobs.handles(call):
            return True
        return call.tool in (*TOOLS, CHECKER) and call.error not in NEVER_RAN

    @staticmethod
    def service_of(agent: str) -> str:
        return agent

    def place(self, call: RecordedCall) -> Place:
        """Where the run's command ran since the round clock: the agent's seat (agent_tools.run), with the seat's reap
        sparing the commands still running."""
        seat = Seat.read(self.seats[call.agent])
        if call.tool in PYTHON_TOOLS:
            argv = ["bash", "--login", "-c", python_command(seat, {a: Seat.read(s) for a, s in self.seats.items()})]
            stdin = self.command(call).encode()
        else:
            argv, stdin = ["bash", "--login", "-c", self.command(call)], None
        user = seat.user

        def reap(box: ReplaySandbox, keep: list[str]) -> None:
            box.run(call.agent, script_command(hygiene.REAP, user, *keep), timeout=READ_TIMEOUT_S)
        return Place(call.agent, user, seat.workdir, {}, argv, stdin, reap if seat.reap else None)

    def shared(self, call: RecordedCall) -> bool:
        """A call that runs alone: one that may touch the team remote, and every check_design call, whose state is the
        owner's (the team's in shared_task) and keeps its calls in order."""
        return call.tool == CHECKER or (self.team and bool(SHARED.search(self.command(call))))

    @staticmethod
    def command(call: RecordedCall) -> str:
        return str(call.arguments.get("command") or call.arguments.get("code") or call.arguments.get("sequence") or "")

    def execute(self, box: ReplaySandbox, call: RecordedCall) -> tuple[str, bool]:
        """check_design by its owner's own state (make_call); shell as `bash --login -c`, run_python with the code on
        python's stdin, as `user` in /workspace with the tool's timeout, then the seat's reap."""
        if call.tool == CHECKER:
            return self.check(call), False
        if self.jobs is not None:
            return self.jobs.execute(box, call, self)
        seat = Seat.read(self.seats[call.agent])
        if call.tool in PYTHON_TOOLS:
            argv = ["bash", "--login", "-c", python_command(seat, {a: Seat.read(s) for a, s in self.seats.items()})]
            stdin = self.command(call).encode()
        else:
            argv, stdin = ["bash", "--login", "-c", self.command(call)], None
        output, timed_out = box.tool(call.agent, argv, TOOL_TIMEOUT_S, WORKDIR, stdin=stdin, user=AGENT_NAME)
        if seat.reap:
            box.run(call.agent, script_command(hygiene.REAP, AGENT_NAME), timeout=READ_TIMEOUT_S)
        return output, timed_out

    def check(self, call: RecordedCall) -> str:
        """The checker's call on the recorded arguments, in its owner's state; its text, as the tool returned it or
        raised it."""
        owner = self.block["owner_of"][call.agent]
        state, run_call = self.checkers.setdefault(owner, make_call(self.row))
        result = run_call(call.arguments)
        text = "".join(part.get("text") or "" for part in result.get("content") or [])
        self.checks.append({"agent": call.agent, "owner": owner, "tool": CHECKER, "arguments": call.arguments,
                            "text": text, "error": bool(result.get("isError"))})
        return text

    def initial(self, box: ReplaySandbox) -> dict[str, dict]:
        for agent in self.agents:
            self.last[agent] = self.snapshot(box, agent, None)
        return dict(self.last)

    def after(self, box: ReplaySandbox, call: RecordedCall, output: str) -> tuple[str, dict, list[dict], list[dict]]:
        """After the call: the commits it made given their recorded ids where a date in its window gives one, the
        output read with those ids, and the agent's state. Returns (output, state, dated, undated)."""
        agent = call.agent
        if call.tool == CHECKER:
            sequence = str(call.arguments.get("sequence") or "")
            return output, {**self.last[agent], "check": {
                "pieces": self.domain.pieces_seen(sequence), "design": self.domain.found(sequence, whole=True),
                "verdict": verdict({"text": output})}}, [], []
        state = self.snapshot(box, agent, self.last[agent], new_commits=True)
        mine = [sha for sha, who in state.pop("new") if who == agent and not self.ids.matching(sha)]
        dated, undated, renames, bodies = [], [], {}, {}
        local = RecordedIds([call.result])
        for sha in mine:
            body = reparent(box.must_run(agent, ["git", "-C", self.work, "cat-file", "commit", sha], f"read {sha}",
                                         **AS_AGENT), renames)
            found = find_date(body, lambda s: local.matching(s) or self.ids.matching(s), call.t0, call.t1)
            if found is None:
                undated.append({"n": call.n, "agent": agent, "k": call.k, "sha": sha})
                continue
            bodies[found.sha] = retime(body, found.seconds, found.tz.encode(), found.author_too)
            written = box.must_run(agent, ["bash", "-c", write_object_command(self.git_dir)], f"write {found.sha}",
                                   stdin=bodies[found.sha], **AS_AGENT).decode().strip()
            if written != found.sha:
                raise RuntimeError(f"git wrote {written} for the dated commit {found.sha}")
            renames[sha] = found.sha
            dated.append({"n": call.n, "agent": agent, "k": call.k, "old": sha, "new": found.sha,
                          "recorded": found.recorded, "seconds": found.seconds, "tz": found.tz,
                          "author_too": found.author_too})
        if renames:
            self.rename(box, agent, renames, bodies)
            state = self.snapshot(box, agent, state)
        self.last[agent] = state
        return renamed_text(output, renames), state, dated, undated

    def rename(self, box: ReplaySandbox, agent: str, renames: dict[str, str], bodies: dict[str, bytes]) -> None:
        """The agent's refs onto the dated commits; and the team remote's, where the call already pushed the old ones,
        written as `user` so the shared repository keeps its owners."""
        box.must_run(agent, ["bash", "-c", rename_command(self.git_dir, renames)], f"{agent}: dated refs", **AS_AGENT)
        if not self.team:
            return
        pushed = {old: new for old, new in renames.items()
                  if box.exec(agent, f"git --git-dir={TEAM_REMOTE} cat-file -e {old}^{{commit}}").rc == 0}
        for new in pushed.values():
            box.must_run(agent, ["bash", "-c", write_object_command(TEAM_REMOTE)], f"team copy of {new}",
                         stdin=bodies[new], **AS_AGENT)
        if pushed:
            box.must_run(agent, ["bash", "-c", rename_command(TEAM_REMOTE, pushed)], "team: dated refs", **AS_AGENT)

    # ---------------------------------------------------------------- the state
    def holding(self, file: dict) -> dict:
        """The pieces and the whole design a file's text holds (ProteinDomain.file_pieces and found), None for a file
        whose text the read did not keep; read once per content."""
        digest = file.get("sha256")
        if digest not in self.holding_of:
            text = file.get("text")
            self.holding_of[digest] = {"pieces": None, "design": None} if text is None else \
                {"pieces": self.domain.file_pieces(text), "design": self.domain.found(text, whole=True)}
        return self.holding_of[digest]

    def machine_files(self, box: ReplaySandbox, agent: str) -> list[dict]:
        """The files of the agent's machine that are new or changed since staging (sandbox/saved_work.py, as root)."""
        staged = json.dumps(self.seats[agent]["staged"])
        return [f for root in scoring.ROOTS for f in self.root_json(box, agent, scoring.SAVED_WORK, root, staged)
                if "error" not in f]

    def read_commits(self, box: ReplaySandbox, service: str, git_dir: str, shas: set[str], user: str) -> None:
        """Each commit's new or changed files (sandbox/team_binder.py) and their holdings, once per id."""
        with self.lock:
            todo = sorted(sha for sha in shas if sha not in self.held)
        if not todo:
            return
        found = last_json(box.must_run(service, script_command(scoring.TEAM_BINDER, git_dir, self.work,
                                                               json.dumps(self.staged), *todo),
                                       f"{service}: the commits' files", user=user, workdir=WORKDIR,
                                       timeout=READ_TIMEOUT_S))
        with self.lock:
            for sha in todo:
                self.held[sha] = {f["path"]: {**self.holding(f), "sha256": f["sha256"]}
                                  for f in (found.get(sha) or {}).get("files") or []}

    def ref_readings(self, refs: dict[str, str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """(ref_pieces, ref_designs): the pieces and the whole designs each ref's commit holds, refs holding none
        left out."""
        pieces, designs = {}, {}
        for name, sha in refs.items():
            held = self.held.get(sha) or {}
            if found := sorted({p for h in held.values() for p in h["pieces"] or []}):
                pieces[name] = found
            if found := sorted({h["design"] for h in held.values() if h["design"]}):
                designs[name] = found
        return pieces, designs

    def snapshot(self, box: ReplaySandbox, agent: str, previous: dict | None, new_commits: bool = False) -> dict:
        """The agent's state (the module's docstring); with new_commits the commits new since `previous` as "new"."""
        tips = sorted({*(previous or {}).get("refs", {}).values()}) if new_commits else None
        read = parse_snapshot(box.must_run(agent, ["bash", "-c", snapshot_command(self.work, tips)],
                                           f"{agent}'s snapshot", **AS_AGENT).decode(errors="replace"))
        files = self.machine_files(box, agent)
        self.read_commits(box, agent, self.git_dir, set(read["refs"].values()), AGENT_NAME)
        ref_pieces, ref_designs = self.ref_readings(read["refs"])
        work = {f["path"]: f for f in files if f["path"].startswith(self.work + "/")}
        head = {f"{self.work}/{path}": h["sha256"] for path, h in (self.held.get(read["head"]) or {}).items()}
        return {"head": read["head"], "branch": read["branch"], "refs": read["refs"],
                "tree_pieces": sorted({p for f in work.values() for p in self.holding(f)["pieces"] or []}),
                "ref_pieces": ref_pieces, "dirty": {p: f["sha256"] for p, f in work.items()} != head,
                "files": {f["path"]: self.holding(f) for f in files
                          if self.holding(f)["pieces"] or self.holding(f)["design"]},
                "ref_designs": ref_designs, **({"new": read["new"]} if new_commits else {})}

    def team_state(self, box: ReplaySandbox) -> dict:
        """The team remote's refs, read as root in the first machine, and the pieces and designs each holds."""
        if not self.team:
            return {"refs": {}, "ref_pieces": {}, "ref_designs": {}}
        out = box.must(self.first, f"git --git-dir={TEAM_REMOTE} for-each-ref --format='%(objectname) %(refname)'",
                       "team refs")
        refs = {name: sha for sha, _, name in (line.partition(" ") for line in out.splitlines()) if name}
        self.read_commits(box, self.first, TEAM_REMOTE, set(refs.values()), ROOT)
        ref_pieces, ref_designs = self.ref_readings(refs)
        return {"refs": refs, "ref_pieces": ref_pieces, "ref_designs": ref_designs}

    def line_state(self, call: RecordedCall, own: dict | None, standing: dict, team: dict) -> dict:
        """A line's state (the module's docstring): `standing` is the agent's state as it stands at the call, `team`
        the team remote's as the last shared call left it, `own` the call's own state when it was played, else None.
        The check comes from `own` alone."""
        return {**git_line(self.pieces, standing, team), "files": standing["files"],
                "ref_designs": standing["ref_designs"], "team_designs": team["ref_designs"],
                "check": (own or {}).get("check")}

    # ---------------------------------------------------------------- the end state
    def end_state(self, box: ReplaySandbox, keep: Path | None = None) -> dict:
        """The scorer's reading at the end against run.json protein: each agent's saved work and changed staged files,
        the team remote's branches, hand-in and log, every check_design call and each owner's state, and the setup's
        probe. "equal" is all of them. With `keep` the replayed readings are kept there as end.json."""
        parts: dict[str, bool] = {f"{a}.probe": all(self.probe.get(a, {}).values()) for a in self.agents}
        differences: dict[str, object] = {}
        readings: dict[str, object] = {}

        def compare(name: str, replayed, stored) -> None:
            parts[name] = replayed == stored
            readings[name] = replayed
            if not parts[name]:
                differences[name] = {"replayed": replayed, "stored": stored}

        stored = self.block
        for agent in self.agents:
            staged = self.seats[agent]["staged"]
            saved = self.root_json(box, agent, scoring.SAVED_WORK, self.work, json.dumps(staged))
            compare(f"{agent}.saved_work", triples(saved), triples((stored.get("saved_work") or {}).get(agent) or []))
            hashes = self.root_json(box, agent, machines.FILE_HASHES, stdin=json.dumps(sorted(staged)).encode())
            compare(f"{agent}.changed_files", sorted(p for p, d in staged.items() if hashes.get(p) != d),
                    (stored.get("changed_files") or {}).get(agent) or [])
        if self.team:
            record = stored.get("team") or {}
            branches = self.binders(box, [])
            compare("team.branches", {b: (f["commit"], triples(f["files"])) for b, f in branches.items()},
                    {b: (f.get("commit"), triples(f.get("files") or []))
                     for b, f in (record.get("branches") or {}).items()})
            hand_in = record.get("hand_in") or {}
            if hand_in.get("by") == "submit":
                mine = self.binders(box, [hand_in.get("commit")])[hand_in.get("commit")]
            else:
                mine = branches.get(TEAM_BRANCH, {"commit": None, "files": []})
            compare("team.hand_in", (mine["commit"], triples(mine["files"])),
                    (hand_in.get("commit"), triples(hand_in.get("files") or [])))
            log = box.must(self.first, TEAM_LOG, "team log")
            compare("team.log", log.splitlines(), list(record.get("log") or []))
        compare("checker.calls", self.checks, [recorded_check(c) for c in self.block.get("calls") or []])
        compare("checker.states", {o: copy.deepcopy(s) for o, (s, _) in self.checkers.items()},
                self.block.get("states") or {})
        if keep is not None:
            keep.mkdir(parents=True, exist_ok=True)
            (keep / "end.json").write_text(json.dumps(readings, indent=1, default=list))
        return {"equal": all(parts.values()), "parts": parts, "differences": differences}

    def binders(self, box: ReplaySandbox, revs: list[str]) -> dict:
        """team_binders as the scorer reads them: as root in the first machine, every branch when revs is empty."""
        return self.root_json(box, self.first, scoring.TEAM_BINDER, TEAM_REMOTE, self.work, json.dumps(self.staged),
                              *revs)

    @staticmethod
    def root_json(box: ReplaySandbox, service: str, script: str, *args: str, stdin: bytes | None = None):
        """A sandbox script's JSON as run_json reads it, run as root; RuntimeError when it fails."""
        done = box.run(service, script_command(script, *args), stdin=stdin, timeout=READ_TIMEOUT_S)
        if done.rc != 0:
            raise RuntimeError(f"a script failed in {service}: {(done.stderr or done.stdout).decode()[-800:]}")
        return last_json(done.stdout)

