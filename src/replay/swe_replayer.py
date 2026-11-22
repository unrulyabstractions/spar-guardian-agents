"""A stored SWE run played back in its sandbox: the setup checked against the record, each recorded call in its agent's
machine, the commits given their recorded ids, the state after every call and the end state against the stored one.
The commands are src/domains/swe/swe_replay.py's; the pieces are SweDomain.holds's (src/domains/swe/swe_ladder.py).

    replayer = SweReplayer(run, calls)
    replayer.setup(box)                       # ReplayRefused unless the refs and planted commits are the recorded ones
    output, timed_out = replayer.execute(box, call)
    output, state, dated, undated = replayer.after(box, call, output)
    line = replayer.line_state(call, own, standing, team)    # the state a line of steps.jsonl carries
    replayer.end_state(box, keep)             # {"equal": bool, ...} part by part

A piece is read on the agent's working tree (the tree the grader's `git add -A` takes) against the base, on each ref of
its repository but its tags and the upstream branches the setup left (pruned of the fix), and on each ref of the team
remote. A commit's pieces are read once per id.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.domains.swe import swe_replay as hooks
from src.domains.swe._swe_scoring import branch_file
from src.domains.swe.swe_ladder import SweDomain
from src.domains.swe.swe_settings import DIFF_KEEP_BYTES, TEAM_REPO, TOOL_TIMEOUT_S, WORKDIR
from src.measures.run_directory import RunDir
from src.measures.shell_commands import PYTHON_TOOLS, TOOLS
from src.replay.commit_dates import RecordedIds, find_date, renamed_text, reparent, retime
from src.replay.git_state import SHARED, git_line
from src.replay.random_content import CertificateSwap
from src.replay.recorded_calls import RecordedCall, ReplayRefused
from src.replay.replay_jobs import JobPlayer, Place
from src.replay.replay_sandbox import ReplaySandbox
from src.replay.warm_image import described, warm_image
from src.sandbox.agent_tools import PYTHON_COMMANDS
from src.sandbox.docker_images import image_id

UPSTREAM = "refs/remotes/origin/"


class SweReplayer:
    task = "software_engineering"

    def __init__(self, run: RunDir, calls: list[RecordedCall]) -> None:
        self.run, self.calls = run, calls
        self.swe = run.run_json["swe"]
        self.base = self.swe["base_commit"]
        self.agents = list(self.swe["agents"])
        self.team = hooks.has_team_remote(self.swe)
        self.domain = SweDomain(run.run_json, run.path / "swe" / "diffs")
        self.pieces = sorted(self.domain.prints.pieces)
        self.piece_refs = set(hooks.piece_refs(self.swe).values())
        final = self.swe.get("agents_final") or {}
        team = self.swe.get("team") or {}
        self.ids = RecordedIds([c.result for c in calls] + [team.get("log") or "", team.get("refs") or ""] +
                               [(final.get(a) or {}).get("log") or "" for a in self.agents] +
                               [(run.run_json.get("team_submission") or {}).get("snapshot") or ""])
        self.held: dict[str, list[str]] = {}       # commit id -> the pieces its diff against the base holds
        self.lock = threading.Lock()
        self.initial_refs: dict[str, dict[str, str]] = {}
        self.last: dict[str, dict] = {}
        self.warm: str | None = None               # the warm image's tag, once use_warm_image built it
        self.jobs = JobPlayer.of(run, calls, service_of=self.service_of)    # None before the round clock

    # ---------------------------------------------------------------- the sandbox
    def services(self) -> dict[str, dict]:
        services = hooks.services(self.swe)
        if self.warm:
            services = {name: {**service, "image": self.warm} for name, service in services.items()}
        return services

    def volumes(self) -> dict[str, dict]:
        """The compose project's volumes: the team remote's."""
        return {"team": {}}

    def image(self) -> dict:
        tag, recorded = hooks.image(self.swe), (self.swe.get("images") or {}).get("native_id")
        if self.warm:
            return described(tag, self.warm, recorded)
        local = image_id(tag)
        return {"tag": tag, "id": local, "run_id": recorded, "own": bool(local) and local == recorded}

    def use_warm_image(self, log=print) -> str:
        """Every machine on the run's image with Go's build cache filled (warm_image.py), built when missing."""
        self.warm = warm_image(hooks.image(self.swe), WORKDIR, log)
        return self.warm

    def certificate_swap(self) -> CertificateSwap:
        """The swap of drawn certificates and keys for the recorded ones (random_content.py): each agent's own stored
        diffs (its graded tree, its own diff, its branch on the team remote), and the record's every call, whose text
        no drawn line may appear in."""
        own = {agent: [f"{agent}.diff", f"{agent}.own.diff", branch_file(agent)] for agent in self.agents}
        record = "\n".join(text for call in self.calls
                           for text in (*map(str, call.arguments.values()), call.result))
        return CertificateSwap(self.run.path / "swe" / "diffs", own, record)

    def build(self, box: ReplaySandbox) -> dict:
        """prepare()'s setup with the record's values: {"base_time", "agents": {agent: {"refs", "planted"}},
        "team_main"}, each agent's refs as the setup's check lists them."""
        for step in hooks.grader_checks(self.swe):
            self._step(box, step)
        base_time = int(box.must(hooks.GRADER, hooks.base_time_command(self.swe), "base time").strip())
        with ThreadPoolExecutor(max_workers=len(self.agents)) as pool:
            planted = dict(zip(self.agents, pool.map(lambda a: self._agent_setup(box, a, base_time), self.agents)))
        for step in hooks.team_steps(self.swe):
            self._step(box, step)
        built = {"base_time": base_time, "agents": {
            a: {"refs": box.must(a, hooks.REFS, f"{a} refs").split(), "planted": planted[a]} for a in self.agents}}
        if self.team:
            built["team_main"] = box.must(hooks.GRADER, hooks.TEAM_MAIN, "team main").strip()
        return built

    def setup(self, box: ReplaySandbox) -> dict:
        """build(), then the check: each agent's refs and planted commits and the team's main must be the recorded ones
        (run.json swe.setup), else ReplayRefused."""
        built, recorded = self.build(box), self.swe.get("setup") or {}
        problems, evidence = [], {"base_time": built["base_time"], "agents": {}}
        for agent in self.agents:
            refs, planted = built["agents"][agent]["refs"], built["agents"][agent]["planted"]
            want = (recorded.get("agents") or {}).get(agent) or {}
            same_refs, same_planted = refs == want.get("refs"), planted == (want.get("planted") or {})
            evidence["agents"][agent] = {"refs": len(refs), "refs_recorded": len(want.get("refs") or []),
                                         "refs_match": same_refs, "planted": planted, "planted_match": same_planted}
            if not same_refs:
                problems.append(f"{agent}'s {len(refs)} refs differ from the {len(want.get('refs') or [])} recorded")
            if not same_planted:
                problems.append(f"{agent} planted {planted}, the run {want.get('planted')}")
        if self.team:
            evidence["team_main"] = built["team_main"]
            if recorded.get("team_main") and built["team_main"] != recorded["team_main"]:
                problems.append(f"team main {built['team_main']}, the run {recorded['team_main']}")
        if problems:
            raise ReplayRefused("the setup does not reproduce the run's: " + "; ".join(problems))
        return evidence

    def _step(self, box: ReplaySandbox, step: hooks.SetupStep) -> str:
        if step.upload:
            path, data = step.upload
            box.must(step.service, f"cat > {path}", f"{step.what}: upload", stdin=data)
        try:
            return box.must(step.service, step.command, step.what)
        except RuntimeError as error:
            raise ReplayRefused(f"setup step failed: {error}") from None

    def _agent_setup(self, box: ReplaySandbox, agent: str, base_time: int) -> dict[str, str]:
        planted = {}
        for step in hooks.agent_steps(self.swe, agent, base_time):
            out = self._step(box, step)
            if step.piece:
                planted[step.piece] = hooks.planted_last_line(out)
        return planted

    # ---------------------------------------------------------------- the calls
    def in_sandbox(self, call: RecordedCall) -> bool:
        """Whether the call ran in a machine: a shell or python call whose arguments the tool accepted; since the round
        clock also job_output, job_stop and peer_shell (replay_jobs.py)."""
        if self.jobs is not None and self.jobs.handles(call):
            return True
        return call.tool in TOOLS and call.error != "parsing"

    @staticmethod
    def service_of(agent: str) -> str:
        return agent

    def place(self, call: RecordedCall) -> Place:
        """Where the run's command ran since the round clock: the agent's machine as root in the image's directory
        (its seat names neither), shell as `bash --login -c`, run_python's code on python's stdin. No reap."""
        if call.tool in PYTHON_TOOLS:
            return Place(call.agent, None, None, {}, ["bash", "--login", "-c", PYTHON_COMMANDS[0]],
                         self.command(call).encode())
        return Place(call.agent, None, None, {}, ["bash", "--login", "-c", self.command(call)])

    def shared(self, call: RecordedCall) -> bool:
        return self.team and bool(SHARED.search(self.command(call)))

    @staticmethod
    def command(call: RecordedCall) -> str:
        return str(call.arguments.get("command") or call.arguments.get("code") or "")

    def execute(self, box: ReplaySandbox, call: RecordedCall) -> tuple[str, bool]:
        """The call as the agent's tool ran it: `bash --login -c` for shell, the code on python's stdin for
        run_python, in /app, with the tool's timeout; since the round clock through replay_jobs, with none."""
        if self.jobs is not None:
            return self.jobs.execute(box, call, self)
        if call.tool in PYTHON_TOOLS:
            return box.tool(call.agent, ["bash", "--login", "-c", PYTHON_COMMANDS[0]], TOOL_TIMEOUT_S, WORKDIR,
                            stdin=self.command(call).encode())
        return box.tool(call.agent, ["bash", "--login", "-c", self.command(call)], TOOL_TIMEOUT_S, WORKDIR)

    def initial(self, box: ReplaySandbox) -> dict[str, dict]:
        states = {}
        for agent in self.agents:
            states[agent] = self._snapshot(box, agent, None)
            self.initial_refs[agent] = dict(states[agent]["refs"])
            self.last[agent] = states[agent]
        return states

    def after(self, box: ReplaySandbox, call: RecordedCall, output: str) -> tuple[str, dict, list[dict], list[dict]]:
        """After the call: the commits it made given their recorded ids where a date in its window gives one, the
        output read with those ids, and the agent's state. Returns (output, state, dated, undated)."""
        agent = call.agent
        state = self._snapshot(box, agent, self.last[agent], new_commits=True)
        mine = [sha for sha, who in state.pop("new") if who == agent and not self.ids.matching(sha)]
        dated, undated, renames, bodies = [], [], {}, {}
        local = RecordedIds([call.result])
        for sha in mine:
            body = reparent(box.run(agent, hooks.body_command(sha).split()).stdout, renames)
            found = find_date(body, lambda s: local.matching(s) or self.ids.matching(s), call.t0, call.t1)
            if found is None:
                undated.append({"n": call.n, "agent": agent, "k": call.k, "sha": sha})
                continue
            bodies[found.sha] = retime(body, found.seconds, found.tz.encode(), found.author_too)
            written = box.must(agent, hooks.write_object_command(hooks.AGENT_GIT_DIR), f"write {found.sha}",
                               stdin=bodies[found.sha]).strip()
            if written != found.sha:
                raise RuntimeError(f"git wrote {written} for the dated commit {found.sha}")
            renames[sha] = found.sha
            dated.append({"n": call.n, "agent": agent, "k": call.k, "old": sha, "new": found.sha,
                          "recorded": found.recorded, "seconds": found.seconds, "tz": found.tz,
                          "author_too": found.author_too})
        if renames:
            self._rename(box, agent, renames, bodies)
            state = self._snapshot(box, agent, state)
        self.last[agent] = state
        return renamed_text(output, renames), state, dated, undated

    def _rename(self, box: ReplaySandbox, agent: str, renames: dict[str, str], bodies: dict[str, bytes]) -> None:
        """The agent's refs onto the dated commits; and the team remote's, where the call already pushed the old
        ones."""
        box.must(agent, hooks.rename_command(hooks.AGENT_GIT_DIR, renames), f"{agent} refs onto dated commits")
        box.must(agent, hooks.drop_loose_command(hooks.AGENT_GIT_DIR, list(renames)), f"{agent} old ids dropped")
        if not self.team:
            return
        pushed = {old: new for old, new in renames.items()
                  if box.exec(hooks.GRADER, hooks.has_object_command(TEAM_REPO, old)).rc == 0}
        for new in pushed.values():
            box.must(hooks.GRADER, hooks.write_object_command(TEAM_REPO), f"team copy of {new}", stdin=bodies[new])
        if pushed:
            box.must(hooks.GRADER, hooks.rename_command(TEAM_REPO, pushed), "team refs onto dated commits")
            box.must(hooks.GRADER, hooks.drop_loose_command(TEAM_REPO, list(pushed)), "team old ids dropped")

    # ---------------------------------------------------------------- the state
    def _snapshot(self, box: ReplaySandbox, agent: str, previous: dict | None, new_commits: bool = False) -> dict:
        """The agent's HEAD, branch, refs, tree id, the pieces its tree and its refs hold, and whether its tree
        differs from HEAD's; with new_commits the commits new since `previous` as "new"."""
        tips = sorted({*(previous or {}).get("refs", {}).values(), *([previous["head"]] if previous and
                                                                      previous.get("head") else [])})
        command = hooks.snapshot_command(self.base, (previous or {}).get("tree", ""), new_commits=new_commits)
        done = box.exec(agent, command, stdin="".join(f"^{t}\n" for t in tips).encode() if new_commits else None)
        read = hooks.parse_snapshot(done.stdout.decode(errors="replace"))
        if read["diff"] is not None:
            reading = self.domain.diff_reading(read["diff"])
            tree_pieces = sorted(p for p, (hit, whole) in reading.items() if self.domain.held_how(p, hit, whole))
            tree_lines = self.domain.tree_reading(reading)
        else:
            tree_pieces = (previous or {}).get("tree_pieces", [])
            tree_lines = (previous or {}).get("tree_lines", {})
        refs = {name: sha for name, sha in read["refs"].items() if not name.startswith("refs/tags/")}
        initial = self.initial_refs.get(agent, {})
        readable = {name: sha for name, sha in refs.items()
                    if not (name.startswith(UPSTREAM) and name not in self.piece_refs
                            and (not initial or initial.get(name) == sha))}
        self._read_pieces(box, agent, set(readable.values()), None)
        return {"head": read["head"], "branch": read["branch"], "refs": refs, "tree": read["tree"],
                "tree_pieces": tree_pieces, "tree_lines": tree_lines,
                "dirty": bool(read["tree"]) and read["tree"] != read["head_tree"],
                "ref_pieces": {name: self.held[sha] for name, sha in readable.items() if self.held.get(sha)},
                "new": read["new"]}

    def _read_pieces(self, box: ReplaySandbox, service: str, shas: set[str], git_dir: str | None) -> None:
        with self.lock:
            todo = sorted(sha for sha in shas if sha not in self.held)
        if not todo:
            return
        found = hooks.parse_diffs(box.exec(service, hooks.diffs_command(self.base, todo, git_dir)).stdout
                                  .decode(errors="replace"))
        with self.lock:
            for sha in todo:
                self.held[sha] = sorted(self.domain.holds(found.get(sha, "")))

    def team_state(self, box: ReplaySandbox) -> dict:
        """The team remote's refs, read through the grader's view, and the pieces each holds; {} without one."""
        if not self.team:
            return {"refs": {}, "ref_pieces": {}}
        refs = hooks.parse_refs(box.must(hooks.GRADER, hooks.TEAM_REFS, "team refs"))
        self._read_pieces(box, hooks.GRADER, set(refs.values()), hooks.TEAM_VIEW)
        return {"refs": refs, "ref_pieces": {name: self.held[sha] for name, sha in refs.items() if self.held.get(sha)}}

    def set_team_refs(self, box: ReplaySandbox, refs: dict[str, str]) -> None:
        """The team remote's refs made exactly `refs` (refname -> sha), for a call that read the remote before an
        overlapping write (replay_run.play_before). RuntimeError unless the grader then reads `refs`."""
        current = hooks.parse_refs(box.must(hooks.GRADER, hooks.TEAM_REFS, "team refs"))
        command = hooks.set_refs_command(TEAM_REPO, current, refs)
        if command:
            box.must(hooks.GRADER, command, "team refs set")
        now = hooks.parse_refs(box.must(hooks.GRADER, hooks.TEAM_REFS, "team refs"))
        if now != refs:
            raise RuntimeError(f"the team remote's refs read {now} after they were set to {refs}")

    def line_state(self, call: RecordedCall, own: dict | None, standing: dict, team: dict) -> dict:
        """A line's state (git_state.git_line): `standing` is the agent's state as it stands at the call, `team` the
        team remote's as the last shared call left it. `own` (the call's own state, when it was played) adds nothing."""
        return git_line(self.pieces, standing, team)

    # ---------------------------------------------------------------- the end state
    def read_end(self, box: ReplaySandbox) -> dict:
        """The grader's reading at the end of the run, as the export stores it: {"diffs": {file under swe/diffs: text},
        "team": {"refs", "log"}, "agents": {agent: {"log", "status", "branch"}}}."""
        diffs: dict[str, str] = {}
        team = {}
        if self.team:
            box.exec(hooks.GRADER, hooks.END_TEAM_FETCH)
            team = {"refs": box.exec(hooks.GRADER, hooks.END_TEAM_REFS).stdout.decode(errors="replace"),
                    "log": box.exec(hooks.GRADER, hooks.end_team_log(self.base)).stdout.decode(errors="replace")}
            for ref in box.exec(hooks.GRADER, hooks.END_TEAM_BRANCHES).stdout.decode().split():
                branch = ref.removeprefix("refs/grade/team/")
                done = box.exec(hooks.GRADER, hooks.end_ref_diff(self.base, ref))
                if done.rc == 0:
                    diffs["team.diff" if branch == "main" else branch_file(branch)] = kept(done.stdout)
        agents = {}
        for agent in self.agents:
            readings = {}
            for key, (command, file) in hooks.end_agent_commands(self.base, agent).items():
                done = box.exec(agent, command)
                if file is None:
                    readings[key] = done.stdout.decode(errors="replace")
                elif done.rc == 0:
                    diffs[f"{agent}.diff" if key == "diff" else f"{agent}.own.diff"] = \
                        kept(box.exec(agent, f"cat {file}").stdout)
            agents[agent] = {**readings, "branch": readings["branch"].strip()}
        return {"diffs": diffs, "team": team, "agents": agents}

    def end_state(self, box: ReplaySandbox, keep: Path) -> dict:
        """read_end against the stored end state: every diff of swe/diffs byte for byte, the team remote's refs and
        log, each agent's log, status and branch. "equal" is all of them. The reading is kept under `keep`: each diff
        as its file, the rest as end.json."""
        read = self.read_end(box)
        keep.mkdir(parents=True, exist_ok=True)
        for name, text in read["diffs"].items():
            (keep / name).write_text(text)
        (keep / "end.json").write_text(json.dumps({"team": read["team"], "agents": read["agents"]}, indent=1))
        texts, parts = read["diffs"], {}
        if self.team:
            team = self.swe.get("team") or {}
            parts["team_refs"] = read["team"]["refs"] == (team.get("refs") or "")
            parts["team_log"] = read["team"]["log"] == (team.get("log") or "")
        final = self.swe.get("agents_final") or {}
        for agent, readings in read["agents"].items():
            mine = final.get(agent) or {}
            parts.update({f"{agent}.{key}": readings[key] == (mine.get(key) or "") for key in ("log", "status",
                                                                                              "branch")})
        stored_dir = self.run.path / "swe" / "diffs"
        stored = {p.name: p.read_text() for p in sorted(stored_dir.glob("*.diff"))} if stored_dir.is_dir() else {}
        diffs = {name: {"identical": texts.get(name) == text, "stored_bytes": len(text.encode()),
                        "replay_bytes": None if name not in texts else len(texts[name].encode()),
                        **({} if texts.get(name) == text else {"files": differing_files(text, texts.get(name, ""))})}
                 for name, text in stored.items()}
        extra = sorted(set(texts) - set(stored))
        equal = bool(stored) and all(d["identical"] for d in diffs.values()) and not extra and all(parts.values())
        return {"equal": equal, "diffs": diffs, "extra_diffs": extra, "parts": parts}


def file_blocks(diff: str) -> dict[str, str]:
    """A diff's text per file it changes, by the file's path."""
    blocks: dict[str, str] = {}
    for block in re.split(r"(?m)^(?=diff --git )", diff):
        found = re.match(r"diff --git a/\S+ b/(\S+)", block)
        if found:
            blocks[found.group(1)] = block
    return blocks


def differing_files(stored: str, replayed: str) -> list[str]:
    """The files whose part of the diff differs between the stored diff and the replayed one."""
    a, b = file_blocks(stored), file_blocks(replayed)
    return sorted(path for path in set(a) | set(b) if a.get(path) != b.get(path))


def kept(data: bytes) -> str:
    """A diff as the export stores it: its first DIFF_KEEP_BYTES bytes as text."""
    return data[:DIFF_KEEP_BYTES].decode(errors="replace")
