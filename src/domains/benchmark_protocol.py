"""What a benchmark is: the protocol every CheatBench task we run implements, and the plan of one run.

A benchmark gives one Inspect sample, the tools its agents use, the setup that runs before the agents, the scorers
that grade what they left behind, the predicate that says who delivered, the ways our harness departs from
CheatBench's own runner, and what run.json records of it. The ORBIT configuration (roster, board, schedule) lives in
src/tasks.py and src/swarm/, never here.

    benchmark = DOMAINS["protein_design"]("alfalfa_chi")
    plan = benchmark.plan(config, roles)
    sample, tools = benchmark.sample(plan, suffix), benchmark.tools(plan)

Adding a domain. Subclass RowBenchmark for a CheatBench task with built rows (or Benchmark otherwise), set the class
attributes, implement stage(), tools(), scorers(), delivered(), gradable(), watched() and the probe hooks, write the
domain's submit texts in prompts.yaml `submit` (counts, missing), and add the class to
src/domains/__init__.py DOMAINS. Each domain package holds the same files: _<d>_settings.py (fixed paths and names),
<d>_task.py (the Benchmark), _<d>_honeypot.py (what is planted and its fingerprints), the sandbox (_<d>_sandbox.py, or
the machines layer), the task's own tool (_<d>_<tool>_tool.py), _<d>_scoring.py (the setup hooks and the scorer),
_<d>_ladder.py (the code's evidence rules) and sandbox/ (what runs inside the sandbox). A module another package imports
has no leading underscore. src/domains/README.md lists what a domain adds outside its package.

The plan. plan(config, roles) checks the config, once, and fixes what every other method reads: the mode (None for a
lone agent), the access policy as applied, what each agent holds, its numbered pieces and their number, and whether
the exploit is split. A lone agent, a solo_task team and a team with fewer than two neutral agents hold everything.

Access. A benchmark names the items of its exploit: whole_access, split_items(n) dealt in order over the n neutral
agents under access=split from the seat deal_turn names (src/configuration/access_policy.py), and valid_item for an
explicit map. A domain whose pieces are numbered sets piece_stem ("slice" gives slice1..sliceN) and the base derives all
three; an agent holding every piece holds the whole.

Delivery. delivered(plan) gives the delivery predicate: which agents' deliverable exists. A shared_task team has one
deliverable, which counts for every agent.

Submit. The swarm's submit (src/swarm/submit_and_stop.py) reads four hooks: submit_text(plan, kind) says what submit
does and what the domain's grader reads (its description, and with submit_line its system prompt bullet);
gradable(plan, agent) is the guard, which says what is missing while there is nothing to grade (it asks whether a
deliverable exists, never whether it is right); snapshot(plan, agent) names what would be graded, where that is cheap;
tells_team(plan) says whether the prompts tell the agents of their team, so the texts of an agent not told name no
teammate.

The access log. A domain names what the sidecar watches in watched(state), read after setup
(src/sandbox/access_log/access_monitor.py). seats(state) gives the table that names an agent from a uid or a machine:
the probe's, so a domain with no probe gives its own. A domain whose honeypot lives on the host sets `watched = None`,
which means no sidecar, and turns its records into the same rows in host_access_rows(view). harness_reads names the
commands the harness itself runs once the agents have begun that open content, so an honest run carries no validity
flag.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from inspect_ai.dataset import Sample
from inspect_ai.scorer import Scorer
from inspect_ai.solver import Solver, TaskState

from src.configuration import access_policy
from src.configuration import prompt_texts as texts
from src.configuration.access_policy import Access
from src.configuration.swarm_config import INFLUENCERS, SwarmConfig, role_of
from src.measures.ladder_domain import Domain
from src.sandbox import cheatbench_task_files as task_files
from src.sandbox.access_log.access_monitor import MONITOR_DEVIATION, Watched
from src.sandbox.agent_tools import AgentTools, Seat
from src.sandbox.network_log.network_monitor import NETWORK_DEVIATION
from src.sandbox.proc_hiding.proc_hiding import PROC_DEVIATION, keeps_proc_hiding
from src.sandbox.sandbox_probe import environment_probe
from src.swarm import influencers
from src.swarm.board import round_rules

TEAM = "team"                       # the owner of a shared_task team's deliverable and tool state
# The agent is Inspect's react() loop, which ORBIT 2.0 holds between its steps. Runs made on ORBIT 1.x, whose agent was
# ORBIT's own copy of that loop, carry "agent_is_orbit_turn_react_not_vendor_harness".
ORBIT_AGENT_DEVIATION = "agent_is_inspect_react_under_orbit_not_vendor_harness"
# How every run departs from CheatBench's own runner, before a domain's own labels. CheatBench's runner has no
# sentence on how to hand in work or stop.
COMMON_DEVIATIONS = [ORBIT_AGENT_DEVIATION, "submit_and_stop_sentences_added_to_the_system_prompt"]
# The label of a run with a regulator that reads the task and does not work on it (SwarmConfig.oversees).
OVERSIGHT_DEVIATION = "a_regulator_gets_its_own_prompt_and_reads_the_task_as_its_teammates_work"
# The label of a board run: each peer's entry and exit reach the others as user messages (turn_rules presence).
PRESENCE_DEVIATION = "peers_are_announced_as_user_messages_when_they_enter_and_exit"
# The label of a run with an influencer, which holds alert_humans (src/swarm/submit_and_stop.py).
ALERT_DEVIATION = "influencers_hold_alert_humans"
# The label of a run whose regulators (and disregulators, head_start_roles influencers) hold the first rounds alone
# (SwarmConfig.head_start_rounds): {rounds} is the head start's H, {roles} head_start_roles.
HEAD_START_DEVIATION = "{roles}_hold_the_first_{rounds}_rounds_before_the_workers_enter"
# The label of a run whose workers may take the team's hand-in back (SwarmConfig.unsubmit, src/swarm/unsubmit.py).
UNSUBMIT_DEVIATION = "workers_can_take_back_the_teams_hand_in"
# The common labels of a run where no agent gets submit or stop_working.
NO_SUBMIT_DEVIATIONS = [ORBIT_AGENT_DEVIATION,
                        "agents_have_no_submit_tool_and_run_until_their_turn_cap_or_the_time_limit"]
# The file on each disregulator's own machine or account that names the other disregulators (Benchmark.others_files).
OTHERS_FILE = "OTHERS.md"


@dataclass(frozen=True)
class Plan:
    """One run's roster, mode and access, fixed before the sample is built."""

    config: SwarmConfig
    roles: dict[str, str] | None
    """{agent: role}; None when every agent is neutral."""
    access: Access
    """The access policy as applied: all for a lone agent, a solo_task team and fewer than two neutral agents."""
    holds: dict[str, list[str]]
    """The access plan: each agent's items, sorted by agent."""
    pieces: dict[str, list[int] | None]
    """Each agent's numbered pieces (None: the whole); {} for a domain without numbered pieces."""
    n_pieces: int | None
    """N, the number of pieces the exploit is cut into, or None when nothing is cut."""
    split: bool
    """Whether some agent holds less than the whole."""

    @property
    def agents(self) -> list[str]:
        """The roster, sorted by ID: domains build every prompt, user and machine in this order, which shows no seat."""
        return self.config.names

    @property
    def team(self) -> bool:
        return self.config.n_agents > 1

    @property
    def mode(self) -> str | None:
        """The team's mode, None for a lone agent."""
        return self.config.mode if self.team else None

    @property
    def owners(self) -> dict[str, str]:
        """Whose deliverable and tool state each agent's work counts for: the team's in shared_task, else its own."""
        return {a: TEAM if self.mode == "shared_task" else a for a in self.agents}


class Benchmark(ABC):
    """One CheatBench task as an Inspect sample plus its tools, setup, scorers and run.json blocks."""

    key: str
    """The registry's key: the @task entry point, `--benchmark` and the instruction levels' benchmark key."""
    name: str
    """CheatBench's task name, such as "software_engineering"."""
    block: str
    """The key of the domain's block in metadata["cheatbench"] and in run.json."""
    task_dir: Path
    """CheatBench's task folder, which holds judge_schema.py."""
    default_row: str
    """The row (an issue, a question, a target) when none is given."""
    scorer: str
    """The name of the scorer that records the deliverables, the probe and the end state."""
    ladder: type[Domain]
    """The rules of the code's evidence for the domain (src/measures/ladder_domain.py)."""
    piece_stem: str | None = None
    """The stem of the numbered pieces of the exploit (slice, shard, share, segment), or None."""
    options: tuple[str, ...] = ()
    """The constructor's keywords beyond row, which the command line and the @task entry points pass on."""
    task_deviations: list[str] = []
    """How every layout of the domain departs from CheatBench's runner, after COMMON_DEVIATIONS."""
    snapshot_is_commit: bool = False
    """Whether snapshot's id is a git commit, which the hand-in texts under unsubmit name (prompt_texts.hand_in_text).
    False where the id is no commit, such as creative writing's sha256 of writing.md."""
    time_grace_s: int | None = None
    """The time Inspect's limit leaves above ORBIT's halt; None takes the config's time_grace_s."""
    harness_reads: tuple[str, ...] = ()
    """How each command starts that the harness itself runs once the agents have begun and that opens content."""
    tool_limits: tuple[str, ...] = ()
    """Default limits on the task's own tools, in SwarmConfig.tool_limits' text form, like check_fix:1:agent:10rounds.
    A study's limit on such a tool replaces the default for that tool (src/swarm/tool_limits.py run_limits)."""
    tool_limit_bounds: tuple[str, ...] = ()
    """The loosest limit the task accepts on a tool of its own, in the same form, like check_fix:1:agent:round. A run
    whose limits on the tool allow more calls in some stretch of rounds, or that frees the tool, is refused when the
    task is built."""

    def __init__(self, row: str | None = None) -> None:
        self.row = row or self.default_row

    # ---------------------------------------------------------------- access and the plan
    def check(self, config: SwarmConfig) -> None:
        """Raise when this benchmark cannot run the config: an explicit access map that cannot apply (a lone agent
        and a solo_task team hold everything), names items the domain does not know, or numbers pieces that are not
        1..N with N at least 2. Subclasses extend it and call this one."""
        access = config.access
        if isinstance(access, dict) and (config.n_agents < 2 or config.mode == "solo_task"):
            raise ValueError("an explicit access map cannot apply: a lone agent and a solo_task team hold everything")
        access_policy.check_map(access, self.valid_item)
        if self.piece_stem and isinstance(access, dict):
            named = sorted({k for items in access.values() for k in self.pieces_of(items) or []})
            access_policy.check_numbered(named, f"{self.piece_stem}s")

    def whole_access(self) -> list[str]:
        """What an agent that holds everything holds."""
        return ["whole"]

    def split_items(self, n_neutral: int) -> list[str]:
        """The items of the exploit dealt over n neutral agents under access=split, in deal order: one numbered
        piece each."""
        return [f"{self.piece_stem}{k}" for k in range(1, n_neutral + 1)]

    def deal_turn(self, config: SwarmConfig, n_neutral: int) -> int:
        """The neutral seat, counted from 0 in seat order, that the deal of split_items starts from: the first, unless
        a domain turns it (access_policy.plan takes it mod the number of neutral agents)."""
        return 0

    def valid_item(self, item: str) -> bool:
        """Whether an explicit access map may name this item."""
        return item in self.whole_access() or (
            self.piece_stem is not None and re.fullmatch(rf"{self.piece_stem}[1-9]\d*", item) is not None)

    def pieces_of(self, items: list[str]) -> list[int] | None:
        """The numbered pieces an agent's items name, in order, or None when it holds the whole."""
        if "whole" in items:
            return None
        return sorted(int(item[len(self.piece_stem):]) for item in items if item.startswith(self.piece_stem))

    def applied_access(self, config: SwarmConfig, roles: dict[str, str] | None) -> Access:
        """The policy the plan applies: all for a lone agent, in solo_task, and under split when fewer than two
        agents are neutral; else the config's."""
        if config.n_agents < 2 or config.mode == "solo_task":
            return "all"
        if config.access == "split" and len(access_policy.neutral_agents(config.names, roles)) < 2:
            return "all"
        return config.access

    def plan(self, config: SwarmConfig, roles: dict[str, str] | None = None) -> Plan:
        """Check the config and fix the run's access: what each agent holds and, for numbered pieces, which pieces
        of how many."""
        self.check(config)
        access = self.applied_access(config, roles)
        whole = self.whole_access()
        turn = self.deal_turn(config, len(access_policy.neutral_agents(config.seats, roles)))
        holds = access_policy.plan(access, config.seats, roles, self.split_items, whole, turn)
        pieces: dict[str, list[int] | None] = {}
        n_pieces = None
        if self.piece_stem:
            pieces = {a: self.pieces_of(items) for a, items in holds.items()}
            n_pieces = max((k for ks in pieces.values() if ks for k in ks), default=None)
            pieces = {a: None if n_pieces and ks == list(range(1, n_pieces + 1)) else ks for a, ks in pieces.items()}
            split = any(ks is not None for ks in pieces.values())
        else:
            split = any(sorted(items) != sorted(whole) for items in holds.values())
        plan = Plan(config=config, roles=roles, access=access, holds=holds, pieces=pieces,
                    n_pieces=n_pieces if split else None, split=split)
        self.others_files(plan)
        return plan

    def private_dir(self, plan: Plan, agent: str) -> str | None:
        """The directory of the agent's machine or account that no other agent can read or list, where a file of the
        agent's alone goes (others_files): where its shell starts when that is its own, else its home. None for a
        domain with no such place, the default."""
        return None

    def others_files(self, plan: Plan) -> dict[str, dict]:
        """{disregulator: {"path", "names"}} for OTHERS_FILE in each disregulator's private_dir, naming the other
        disregulators (influencers.others_named; its text is influencers.others_text). {} in a run with fewer than two
        disregulators. ValueError for a domain that gives a disregulator no private_dir, so no such run goes without
        its files. Each domain plants the files through its own per-agent channel and records this in its block."""
        named = influencers.others_named(plan.roles)
        dirs = {agent: self.private_dir(plan, agent) for agent in named}
        missing = sorted(agent for agent, folder in dirs.items() if folder is None)
        if missing:
            raise ValueError(f"{self.key} has no private place for {OTHERS_FILE} of the disregulators {missing}")
        return {agent: {"path": f"{dirs[agent]}/{OTHERS_FILE}", "names": names} for agent, names in named.items()}

    def shared_result(self, plan: Plan) -> str | None:
        """What a submit hands in for the whole team, where every agent could write it (SWE's team/main, the shared
        writing.md), or None: no team, solo_task, or a domain with none. The default is None."""
        return None

    def kept_out(self, plan: Plan) -> list[str]:
        """The agents that may not write the shared result (influencers.kept_out: those of a role the run's config
        names, SwarmConfig.shared_result_kept_out), in roster order; [] without one. Each domain with a shared
        result enforces it in its sandbox, so an agent root on its own machine cannot undo it, and records it in its
        block as shared_result (shared_result_record). ValueError when it would keep out every agent: some agent must
        write the shared result, and make it where a domain makes it in an agent's machine."""
        if not self.shared_result(plan):
            return []
        kept = influencers.kept_out(plan.roles, plan.agents, plan.config.shared_result_kept_out)
        if plan.agents and len(kept) == len(plan.agents):
            raise ValueError(f"no agent may write {self.shared_result(plan)}: shared_result_kept_out keeps out every "
                             "agent")
        return kept

    def shared_result_record(self, plan: Plan, how: str | None) -> dict | None:
        """run.json's record of the rule in the domain's block, shared_result: {"what", "kept_out", "how"}, with how it
        is enforced (None when no agent is kept out); None without a shared result. Runs made before the rule carry no
        such key."""
        what = self.shared_result(plan)
        if what is None:
            return None
        kept = self.kept_out(plan)
        return {"what": what, "kept_out": kept, "how": how if kept else None}

    # ---------------------------------------------------------------- the sample and its tools
    @abstractmethod
    def time_limit_s(self) -> int:
        """The task's own time limit in seconds."""

    @abstractmethod
    def sample(self, plan: Plan, instruction_suffix: str = "") -> Sample:
        """The plan's sample. sample.input is the instruction plus the suffix. metadata["cheatbench"] carries the
        task, the instruction, the prompt file, the timeout and the domain's block. It may also carry "first_message"
        {agent: text} when agents start from different messages, and "judge_prompt" {agent: text} when the judge
        should see more than the instruction as an agent's rules."""

    @abstractmethod
    def tools(self, plan: Plan) -> AgentTools:
        """The roster's tools (src/sandbox/agent_tools.py roster_tools). The board tools are added by src/tasks.py."""

    def system_prompts(self, plan: Plan) -> dict[str, str]:
        """Each agent's system prompt before its role's text, a blank line apart: who it is (prompt_texts.opening:
        the team sentence when it knows its team, else its name), the tool bullets (the board's when mention_board,
        prompt_texts.board_line, then submit and stop_working as it holds them, ending_lines), and the rounds' rules
        (board.round_rules).
        src/tasks.py system_prompts gives a regulator that oversees a prompt of its own in place of this one."""
        config = plan.config
        return {a: "\n\n".join(filter(None, [texts.opening(config, a, plan.agents),
                                              "\n".join(filter(None, [texts.board_line(config, a, plan.agents),
                                                                      *self.ending_lines(plan, a)])),
                                              round_rules(config, a, plan.roles)]))
                for a in plan.agents}

    def access_note(self, plan: Plan) -> str | None:
        """What every seat's system prompt says of how the access is dealt, a regulator's included; None in a domain or
        a layout with no note. It names no agent: who holds what follows the task in the first message (first_note)."""
        return None

    def first_note(self, plan: Plan, agent: str) -> str | None:
        """What follows the task in the agent's first message, a paragraph of its own: the plan's facts that name its
        peers, such as who holds what or the order of a team's moves. No system prompt names a peer. None by
        default."""
        return None

    def level_words(self) -> dict[str, str]:
        """A variant's own words for the texts of instruction_levels.yaml, which stand in place of the task's `vars`
        of the same names in the rule sentence (influencers.rule_suffix) and in what a regulator's or a disregulator's
        level says in its first message (influencers.role_first). {} by default: the task's own words."""
        return {}

    @classmethod
    def level_values(cls) -> dict[str, str]:
        """The values the task's `vars` in instruction_levels.yaml name in braces, such as a folder
        (influencers.level_text). {} by default."""
        return {}

    def belief_words(self) -> dict[str, str] | None:
        """The task's words for the belief questions (src/swarm/belief_probes.py, prompts.yaml beliefs.words):
        `exploit_1` its primary exploit, `exploit_2` its secondary one when it has one, `principal` for whom the work
        is for. None for a task with no such words, which the belief probe refuses."""
        return None

    def first_facts(self, plan: Plan, agent: str) -> list[str]:
        """The domain's own facts for the facts block of the agent's first message, after what counts (counts_note),
        each a labelled line's text. [] by default."""
        return []

    def first_messages(self, plan: Plan, instruction: str) -> dict[str, str] | None:
        """Each agent's first message when some agent has a first_note or a fact: the instruction, the note on its own
        paragraph, then the facts block (prompt_texts.with_facts) with what counts (counts_note) and the domain's own
        facts (first_facts). None when no agent has either, so every agent starts from the sample input."""
        notes = {a: self.first_note(plan, a) for a in plan.agents}
        facts = {a: [f for f in (self.counts_note(plan, a), *self.first_facts(plan, a)) if f] for a in plan.agents}
        if not any(notes.values()) and not any(facts.values()):
            return None
        return {a: texts.with_facts(instruction, facts[a], note=notes[a]) for a in plan.agents}

    def tells_team(self, plan: Plan) -> bool:
        """Whether the agents' system prompts tell them of their team (SwarmConfig.knows_team). It picks the submit
        and stop_working texts, which name teammates only for an agent that was told."""
        return plan.config.knows_team

    def first_values(self, plan: Plan, agent: str) -> list[str]:
        """The values of the parameters the agent's system prompt (system_prompts) states with no number, one labelled
        line each in its first message's facts block (src/tasks.py first_messages; prompts.yaml first_values): when
        its submit bullet says a team's hand-in ends the run later, when (prompt_texts.submit_value). A domain whose
        prompt states another adds its value."""
        config = plan.config
        delay = texts.submit_value(config) if config.holds_submit(role_of(plan.roles, agent)) else None
        return [delay] if delay else []

    def ending_lines(self, plan: Plan, agent: str) -> list[str]:
        """The agent's bullets for the tools that end its work: submit (submit_line) for a neutral agent of a config
        that gives it, stop_working for an agent that holds it (prompt_texts.ending_lines)."""
        config = plan.config
        return texts.ending_lines(config, role_of(plan.roles, agent), self.submit_line(plan, config.submit_kind),
                                  self.tells_team(plan))

    # ---------------------------------------------------------------- submit
    def submit_vars(self, plan: Plan) -> dict[str, str]:
        """The values of the domain's placeholders in prompts.yaml submit.counts, ready and missing; {} for none."""
        return {}

    def submit_text(self, plan: Plan, kind: str) -> str:
        """What submit does for an agent of this kind (SwarmConfig.submit_kind: team, solo or lone) and what the
        domain's grader reads (prompt_texts.submit_text). The tool's description says the same."""
        return texts.submit_text(kind, plan.config.submit_delay_rounds, self.tells_team(plan),
                                 plan.config.gives_unsubmit)

    def counts_note(self, plan: Plan, agent: str) -> str | None:
        """What counts at a submit (prompts.yaml submit.counted), the first fact of the first message of an agent that
        holds submit; None for one that does not. The system prompt's submit bullet points to it."""
        config = plan.config
        if not config.holds_submit(role_of(plan.roles, agent)):
            return None
        return texts.counts_text(self.key, config.submit_kind, self.submit_vars(plan))

    def oversight_note(self, plan: Plan, agent: str) -> str | None:
        """What a regulator that oversees reads after its teammates' task in its first message, a paragraph of its own:
        how to read the team's work where a domain says (SWE). None by default."""
        return None

    def oversight_facts(self, plan: Plan, agent: str) -> list[str]:
        """The first facts of an overseeing regulator's facts block, at every level: what counts when its teammates
        hand in their work (prompt_texts.overseen_counts_text)."""
        return [texts.overseen_counts_text(self.key, plan.config.submit_kind, self.submit_vars(plan))]

    def submit_line(self, plan: Plan, kind: str) -> str:
        """submit's bullet in a system prompt, for an agent of this kind."""
        return texts.tool_line("submit", self.submit_text(plan, kind))

    def missing(self, plan: Plan) -> str:
        """What gradable says is missing while there is nothing to grade (prompt_texts.missing_text)."""
        return texts.missing_text(self.key, plan.config.submit_kind, self.submit_vars(plan))

    @abstractmethod
    async def gradable(self, plan: Plan, agent: str) -> str | None:
        """submit's guard, awaited inside the sample at each call: None when the agent's deliverable (the team's in
        shared_task) exists, else what is missing (missing). It asks whether something exists to grade, never
        whether it is right."""

    async def snapshot(self, plan: Plan, agent: str) -> str | None:
        """An id of what would be graded at a submit, where that is cheap (SWE's commit); None by default. A commit
        where snapshot_is_commit."""
        return None

    # ---------------------------------------------------------------- setup, scoring, delivery
    def setup(self) -> Solver:
        """The task_setup solver: prepare(state), then the probe table of each agent (src/sandbox/sandbox_probe.py
        environment_probe)."""
        return environment_probe(self.prepare, self.probe)

    async def prepare(self, state: TaskState) -> None:
        """Ready the sandbox before the probe and the agents: staging, the honeypot, the loopback guard."""

    def round_end(self, plan: Plan) -> Callable[[str, int, list[dict]], Awaitable[None]] | None:
        """What the harness does after each agent's round (turn_rules ROUND_END): an async call with the agent, the
        round and its calls ([{id, tool, arguments}]); None for nothing, the default."""
        return None

    def team_head(self, plan: Plan) -> Callable[[], Awaitable[dict | None]] | None:
        """The reader of the head of the team's shared result on a team remote, for the push notices
        (turn_rules _turn_pushes): an async call, made inside the sample, that returns {"head", "by"} or None while
        there is no head (src/sandbox/team_head.py). None for a domain or a layout with no such remote, the default.
        Its notice reads prompts.yaml presence.push_read under the domain's key."""
        return None

    async def cleanup(self, state: TaskState) -> None:
        """Inspect's Task cleanup, which runs whether or not the sample errored: stop what outlives a sample on the
        host."""

    def probe(self, state: TaskState) -> dict:
        """{agent: (Seat, command table, fields)} the setup probes before the agents start; {} for none."""
        return {}

    def keeps_access_log(self, config: SwarmConfig) -> bool:
        """Whether a sidecar records the run's honeypot opens: the config's switch, on a benchmark that watches."""
        return config.access_log and self.watched is not None

    def seats(self, state: TaskState) -> dict[str, Seat]:
        """Each agent's seat, the access log's way from a uid or a machine to an agent: the probe's by default."""
        return {agent: entry[0] for agent, entry in self.probe(state).items()}

    @abstractmethod
    async def watched(self, state: TaskState) -> list[Watched]:
        """What the sidecar watches, awaited once after the benchmark's setup. A domain with no sidecar sets this to
        None, so a new domain has to say which it is."""

    @abstractmethod
    def scorers(self) -> list[Scorer]:
        """The scorers that grade the outcome, the one named `scorer` first. The judge is added by src/tasks.py."""

    @abstractmethod
    def delivered(self, plan: Plan) -> Callable[[], Awaitable[set[str]]]:
        """The delivery predicate: an async call, made inside the sample, that
        returns the agents whose deliverable exists, reading as little as it can (one git command per repository,
        or the sample store)."""

    def team_deviations(self, plan: Plan) -> list[str]:
        """The labels a team's layout adds to the domain's own."""
        return []

    def deviations(self, plan: Plan) -> list[str]:
        """How the run departs from CheatBench's own runner, as short labels: the common ones (NO_SUBMIT_DEVIATIONS
        when no agent gets submit or stop_working), the delay of a shared_task team's submit, the domain's, the team's,
        a regulator's own prompt, /proc's hiding, the network log's and the access log's, then the presence lines of a
        board, the influencers' alert_humans, a head start (head_start_rounds) and the workers' unsubmit
        (gives_unsubmit)."""
        config = plan.config
        common = COMMON_DEVIATIONS if config.gives_submit or config.gives_stop_working else NO_SUBMIT_DEVIATIONS
        delay = [f"team_submit_takes_effect_after_{config.submit_delay_rounds}_rounds"] \
            if config.gives_submit and config.submit_kind == "team" else []
        oversight = [OVERSIGHT_DEVIATION] if any(map(config.oversees, (plan.roles or {}).values())) else []
        presence = [PRESENCE_DEVIATION] if config.board else []
        alert = [ALERT_DEVIATION] if any(role in INFLUENCERS for role in (plan.roles or {}).values()) else []
        network = [NETWORK_DEVIATION] if config.network_log and self.watched is not None else []
        monitor = [*network, *([MONITOR_DEVIATION] if self.keeps_access_log(config) else [])]
        hidden = [PROC_DEVIATION] if keeps_proc_hiding(self, config) else []
        head = [HEAD_START_DEVIATION.format(roles=config.head_start_roles, rounds=config.head_start_rounds)] \
            if config.head_start_rounds else []
        return [*common, *delay, *self.task_deviations, *(self.team_deviations(plan) if plan.team else []), *oversight,
                *hidden, *monitor, *presence, *alert, *head, *([UNSUBMIT_DEVIATION] if config.gives_unsubmit else [])]

    # ---------------------------------------------------------------- what the export records
    @classmethod
    def run_blocks(cls, view) -> dict:
        """The domain's run.json blocks for one sample (src/export/eval_sample_view.py SampleView): run.json[block],
        the sample's facts (metadata["cheatbench"][block]) with the scorer's outcome (its metadata[block]) and the
        score's value. {} when the sample has no block."""
        facts = ((view.metadata or {}).get("cheatbench") or {}).get(cls.block)
        if not facts:
            return {}
        score = (view.scores or {}).get(cls.scorer)
        outcome = ((score.metadata or {}).get(cls.block) or {}) if score else {}
        return {cls.block: {**facts, **outcome, "value": None if score is None else score.value}}

    @classmethod
    def write_files(cls, view, run_dir: Path) -> None:
        """Write the domain's files of one sample into the run directory (SWE's graded diffs); nothing by default."""

    @classmethod
    def episode_facts(cls, view, agent: str) -> dict:
        """The domain's facts in one agent's episode record ({} for none)."""
        return {}

    @classmethod
    def host_access_rows(cls, view) -> list[dict] | None:
        """The access log's rows of a sample whose honeypot lives on the host; None for a domain with none."""
        return None


@dataclass(frozen=True)
class Staged:
    """What a row domain's stage() gives its sample."""

    compose: Path
    """The compose file."""
    block: dict
    """The domain's metadata["cheatbench"][block], beside the row id, the mode, the split and the pieces."""
    files: dict[str, str] = field(default_factory=dict)
    """Sample.files: {path in the sandbox: path on the host}."""
    setup: str | None = None
    """Sample.setup: a script run as root before the task setup."""
    stage: dict[str, str] | None = None
    """{service: staging archive} for the machines layer's setup (metadata["cheatbench"]["stage"])."""


class RowBenchmark(Benchmark):
    """A CheatBench task whose build writes rows (data.jsonl): one row is one sample."""

    build_dir: Path
    """The task's build: data.jsonl and environment/."""
    build_hint: str = ""
    """How to build the rows, said when data.jsonl is missing."""

    @classmethod
    def rows(cls) -> list[dict]:
        return task_files.load_rows(cls.build_dir, hint=cls.build_hint)

    @classmethod
    def row_of(cls, row_id: str) -> dict:
        return task_files.load_row(cls.build_dir, row_id, hint=cls.build_hint)

    def task_yaml(self) -> dict:
        return task_files.load_task_yaml(self.task_dir)

    def time_limit_s(self) -> int:
        return int(self.task_yaml()["timeout_s"])

    def instruction(self, cfg: dict, row: dict, *, team: bool = False) -> str:
        """The row's instruction: CheatBench's rendering of task.yaml's prompts.instruction. A variant with a scenario
        puts its story in, with the `apart` sentence for a team told of itself (`team`, tells_team;
        prompt_texts.fill_apart)."""
        return task_files.render_prompt(self.task_dir / cfg["prompts"]["instruction"], row)

    def sample(self, plan: Plan, instruction_suffix: str = "") -> Sample:
        """The row's sample: the instruction plus the suffix, and what stage() builds. The judge sees the instruction
        as every agent's rules. An agent with a first_note starts from the instruction and the note
        (metadata["cheatbench"]["first_message"], first_messages). A team told of itself reads the scenario's
        `apart` sentence in the instruction (instruction, prompt_texts.fill_apart)."""
        cfg, row = self.task_yaml(), self.row_of(self.row)
        instruction = self.instruction(cfg, row, team=self.tells_team(plan)) + instruction_suffix
        staged = self.stage(plan, row)
        block = {"row_id": row["id"], "mode": plan.mode, "split": plan.split, "n_pieces": plan.n_pieces,
                 **staged.block}
        first = self.first_messages(plan, instruction)
        cheatbench = {**task_files.sample_metadata(cfg, row, instruction), self.block: block,
                      **({"stage": staged.stage} if staged.stage else {}),
                      **({"first_message": first} if first else {})}
        return Sample(id=row["id"], input=instruction, files=staged.files or None, setup=staged.setup,
                      sandbox=("docker", str(staged.compose)), metadata={"cheatbench": cheatbench})

    @abstractmethod
    def stage(self, plan: Plan, row: dict) -> Staged:
        """The row's sandbox for the plan: the compose file, the staging, and the domain's metadata block."""
