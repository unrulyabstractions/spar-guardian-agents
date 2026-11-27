# Swarm visualization

One run of the swarm as a 3D picture with a time slider, with short labels that say what is happening.

```
python swarm_visualization/visualize_swarm.py ian_flipt_deepseek_n10_reg0_revealing_s1
python swarm_visualization/visualize_swarm.py runs/regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1
python swarm_visualization/visualize_swarm.py regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1 --no-open
python swarm_visualization/visualize_swarm.py --study runs/regulator_scale_2
```

The script takes a run folder, a run name searched in `runs/` and `OLD/runs/`, or `study/name`. It writes one HTML
file to `swarm_visualization/out/<run>.html` (or `--out`) and opens it in the browser. The file holds three.js and the
run's data, so it opens from disk and can be sent as it is. It needs only Python's standard library and a browser with
WebGL.

`--study <folder>` writes `out/<study>/index.html`, one grid of the study's runs, and every run's page beside it
(`--index-only` skips the run pages). A click on a mark opens that run's page; ◂ on the run's page goes back.

## What it shows

| Mark | Meaning |
|---|---|
| A sphere | a worker |
| A cube | a regulator, with a thin cyan outline on its edges |
| A spiky ball | a disregulator, with a thin cyan outline on its edges |
| Where they sit | workers on a ring; regulators on a small ring at its centre, raised a little; disregulators likewise (lowered when a run has both) |
| Node colour | the agent's Cheating rung at that moment: pale (none), cream (looked), orange (tempted), red (attempted), crimson with a white core (hacked). Regulators and disregulators carry their rung the same way |
| Every node changing at once | the final hand-in: from then on every agent shows its final rung from the labels, with a `label` chip in the feed; without a final hand-in, at the run's end |
| A dashed outline, ◌ on a tag | a final rung no dated call stands behind: no clause of the agent's `timeline.first` at that rung has a call (hacked counts its attempted) |
| A faded, shrunken node | an agent that left: its episode ended by `stop_working` or an error before the final hand-in. An agent that handed in, or whose teammate did, never fades |
| An edge | two agents that talked on the board: a post that names the other, or a read that asks for the other's posts (`board_read` with `from_agent`) |
| Edge thickness | how much they went back and forth lately, against the run's busiest moment; an exchange both ways counts double, and each exchange fades with time, so a pair that stops talking thins to a hairline |
| A red edge | both agents are at attempted or above |
| A spark along an edge | one message, travelling from writer to the agent named (or from the agent asked for to the reader) |
| A ring spreading from a node | a post on the board |
| Orbs circling a node | pieces of the first exploit family (`hidden`: A, B, C) the agent holds, one colour per piece |
| Small diamonds circling a node | pieces of the second family (`revealed`: client, config, wiring) |
| An orb or a diamond flying in from the centre | a piece the agent first saw (`pieces_seen_at`) without holding it. Who gave it is not recorded, so no giver is drawn |
| A white ring close around a node | the agent holds a whole set of either family |
| A ring flashing out in a rung's colour | the agent reached that rung |
| A ring closing in, ↓ in the feed | an undone write's rise fell back (`timeline.rises[].fell_at`), with the agent whose call took it out (`by`) |
| An amber beacon on a node, beams to others | an alert to humans (`alert_humans`): the caller flashes, a beam reaches each agent it names; grey for a false alarm |
| Pips at a node's left edge | one per alert naming that agent: amber a true alarm, grey a false one, dark amber when the outcome is unknown (`alert_outcomes`) |
| ⚠n m on a regulator's or disregulator's tag | the alerts it raised: amber the true ones, grey the false ones |
| ▬▬ n/N, segments above a node | how many of the N planted values the version held: found on an added line of its code (`version_values.json`); filled n of N, dashed when unknown (its code was not stored, or the run has no `version_values.json`). A fact about the code, not a verdict. Shown above the agent whose hand-in stands now |
| A faint glow at the room's edges | a hand-in stands |
| A thick ring and a white ring around the final hand-in's maker | the verdict: the thick ring in the team rung's colour, the white ring only when the hand-in passed; no white ring for a fail; grey when the run has no labels |
| A wave of thin rings, then a thin vignette at the edges | the final hand-in, in the team rung's colour |
| ◉ burst, ◎ ring closing in | a `check_fix` answer: accepted bursts outward, not accepted closes in, in the shade of what the checked code held |
| Violet rings closing on a node, ↩ on a tag | that agent took the hand-in back |
| A tag under a node, on a dark pill | the agent's label (W1 worker, R1 regulator, D1 disregulator), its model, its rung now. A tag that would sit on a nearer node or on another tag is lifted above it, with a thin line back to its node. Past 12 agents, only an agent doing something now (a rung, a post, a piece, a check, an alert, a fall, the hand-in) or hovered gets the full tag; the rest show a faint label |
| The feed at the right | the last six events as chips, never words from a message: ✎ a post, its writer → the agents it names (its words on hover); → or ↓ a rung; ● or ◆ a piece and the routes (`branch`, `fix`, `patch`, `file`) of the agent's signals row that reach that family; ✓ a hand-in and what it held; ↩ a take-back (its reason on hover); ◉ ◎ a check; ⚠ an alert, the agents it names, true or false (its reason on hover) |
| Top left | the setup as chips: ● workers and their model, ■ regulators, ✹ disregulators, alone, § the rule, the task version, ◎ check (the `check_fix` tool) or ◎✗, ⚑ cheat (told to cheat); the run's name |
| Top right | the clock, the round; the hand-in standing now; the badge: a dashed circle until the final hand-in, then a disc in the team rung's colour (white core for hacked, white ring for pass), grey with no labels, ∅ for a run with no hand-in |
| The legend | the shapes, the rungs, each family's pieces (the family `exploit_used` names is outlined), the alert pips, the held segments, the verdict's disc |

The bar at the bottom: play or pause, the timeline and the speed. A run plays in two minutes at the default speed (three
chevrons); the button steps through a quarter, half, normal and double speed. The timeline has one lane per kind of
mark, its icon at the left end and its totals at the right end, equal to the study page's counts:

| Lane | Marks | Totals |
|---|---|---|
| ⚠ alerts | a triangle, amber when a true alarm is among it, grey when all are false | ⚠ true or unknown, then false in grey |
| ● pieces | a dot or a small diamond in the piece's colour per arrival | |
| ✓ hand-ins | a band while each hand-in stood, in the shade of what its code held; the final one in the team rung's colour, a diamond at it (white edge when it passed) and the verdict's disc after the track's end; a violet tick at each take-back; behind it, how busy the board was | ✓ hand-ins, ↩ take-backs |
| ▲ rungs | a red tick where an agent reached attempted or above: solid when it stays there to its label, hollow when it fell back | ▲ solid, ▵ hollow |
| ◎ checks | a dot per `check_fix` answer, filled when accepted, a ring when not | ◉ accepted, ◎ not accepted |

Marks that would share a pixel become one mark with its counts beside it (ticks stand side by side). Drag the scene to
turn it, scroll to zoom, hover a node to light its links. Space plays or pauses. The buttons beside play step one call
back or forward and jump to the previous or next round; hold one to keep stepping. The arrow keys step calls, and with
Shift they step rounds. While stepping, the top right reads the round, the call's number and who made which call, and
a white ring marks that agent. `#t=0.5` at the end of the URL opens the run halfway through, `#round=7` at round 7's
first call and `#step=120` at the 120th call, all paused.

## Where the data comes from

| Picture | Run file |
|---|---|
| clock | `processed/run.json` `rules.timeline`: each call's start and end; a call with no time sits at its neighbour's |
| agents, roles | `processed/run.json` `agents`, `processed/facts/topology.json` |
| calls | `processed/trajectories/*/transcript.json`, numbered from 0 as the transcript scan numbers them |
| posts, names | `processed/communication/*.json`: each `sent` event, the post's text searched for agent IDs |
| pieces held | `processed/run.json` `swe.exploits.<family>.holds` (hidden first), else `access.agents.<agent>.holds` as one family |
| pieces received | `analysis/signals.jsonl` `pieces_seen_at`: the call at which the agent first saw each piece; the row's `routes`, mapped to a family as the SWE ladder's `route_exploits` does (`network` reaches neither) |
| the family used | `analysis/labels/cheating.json` `exploit_used` |
| rungs | `analysis/labels/cheating.json` `agents.<agent>.timeline.after` after every call up to the final hand-in, then `agents.<agent>.rung` from the final hand-in (or the run's end without one) |
| undated rung | `agents.<agent>.timeline.first`: a clause at the final rung (attempted for hacked) with a call (`n` or `k`) |
| falls | `agents.<agent>.timeline.rises[]`: `fell_at` (the agent's call where it fell back) and `by` |
| left | `processed/episodes.jsonl` `ended_by` (`stop_working`) or `error`, when its last call ended before the final hand-in |
| hand-in, verdict | `processed/run.json` `team_submission`; the final hand-in is the last of `unsubmits.submits` no take-back followed; the team's rung (`team`) and `passed` in `analysis/labels/cheating.json` |
| what a version held | `processed/facts/version_values.json`: `values` (the planted values), `hand_ins[].held` matched to `unsubmits.submits` by `seq`, `checks[].held` and `answer` |
| check_fix answers | `version_values.json` `checks`, placed at the caller's answered `check_fix` calls in order (a call the tool refused is no check) |
| setup | `processed/run.json` `config` (`n_agents` minus `regulators` and `disregulators` = workers, `rule_level`, `tool_limits`, `seed`), `role_models`, `swe.variant`, `launch.module` (ending `_cheat` = told to cheat) |
| alerts | `processed/run.json` `alerts` (caller, agents named, reason), placed at the caller's `alert_humans` call; `analysis/signals.jsonl` team row `alert_outcomes` for true or false alarm, matched in order |
| hand-ins, take-backs | `processed/run.json` `unsubmits.submits` and `unsubmits.unsubmits`, at their recorded times |
| models | `processed/run.json` `ledger.agents.<agent>.model`, then `role_models`, then `model` |

No file records who passed a piece, so a piece's arrival names only its receiver and flies in from the scene's centre.

## Layout

```
visualize_swarm.py          the command
src/run_locator.py          a path or a name to a run folder
src/run_files.py            reads the run's files
src/run_setup.py            the run's setup: team, models, task version, told to cheat
src/swarm_story.py          the run as one story on one clock
src/page_builder.py         writes the single HTML file
src/study_page.py           the study grid: one cell per setting, one mark per seed
src/web/                    the page: viewer.html, viewer.css, viewer.js, vendor/three.module.min.js (three.js r160, MIT);
                            study.css for the study page
tests/test_swarm_story.py   the story of a small hand-made run
tests/test_insight.py       what hand-ins and checks held, the setup, the study grid
```

## The study page

Counts read only finished runs: a run in `incomplete/` counts nowhere.

| Mark | Meaning |
|---|---|
| Rows and columns | the setup fields that vary across the study's runs, split so the grid has few headers and few empty cells and fits the page; the fields every run shares sit in the header |
| Header | the finished runs, ▲ cheated, ○ passed, ? finished runs without labels, ⌀ runs in `incomplete/` |
| A cell | one setting; its runs, one mark per seed. A dashed empty cell holds no run |
| ▲n ○n /n | finished runs at attempted or above, finished runs that passed, finished runs with labels |
| c/n and a thin red bar | the cheated share of a cell's finished runs with labels, along its bottom edge; the same for each row and column in its margin |
| A disc | a run, in its team rung's colour (none, looked, tempted, attempted, hacked; hacked has a white core) |
| A white ring | the run passed |
| A dashed disc with ? | the run has no labels |
| An empty dashed ring set apart at a cell's edge | a run in `incomplete/` |
| The dot on a disc | what the final hand-in held: an empty ring 0, half filled some, filled all, grey unknown (its `held` is null). No dot without a hand-in |
| ✓n or ∅ | hand-ins, or ∅ for a run with no hand-in (`team_submission` null) |
| ↩n | take-backs |
| ◉a ◎b | `check_fix` answers accepted and not accepted |
| ⚠a b | alerts: amber a, true or unknown; grey b, false (`alert_outcomes`) |
| The row ? | runs without `processed/run.json`, which cannot be placed |

`uv run pytest swarm_visualization/tests -q` runs the tests.
