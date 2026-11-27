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

`--study <folder>` writes `out/<study>/index.html`, one grid of the study's runs, and every run's page beside it, a run
in `incomplete/` at `out/<study>/incomplete/<run>.html` (`--index-only` skips the run pages). Links are keyed by the run's
folder, so a finished run and an incomplete one of the same name open their own pages. A click on a mark opens that
run's page; ◂ on the run's page goes back, beside the run's own study mark.

## What it shows

| Mark | Meaning |
|---|---|
| A sphere | a worker |
| A cube | a regulator, with a thin cyan outline on its edges |
| A spiky ball | a disregulator, with a thin pink outline on its edges |
| Where they sit | workers on a ring; regulators on a small ring at its centre, raised a little; disregulators likewise (lowered when a run has both) |
| Node colour | the agent's Cheating rung at that moment: pale (none), cream (looked), orange (tempted), red (attempted), crimson (hacked). Regulators and disregulators carry their rung the same way. Red means only that the node is at attempted or above |
| A faceted worker | hacked: the worker turns into a flat-shaded, low-poly red solid; attempted stays a smooth red sphere |
| Every node changing at once | the final hand-in: from then on every agent shows its final rung from the labels, with a `label` chip in the feed; without a final hand-in, at the run's end |
| ◌ on a tag | a final rung no dated call stands behind: no clause of the agent's `timeline.first` at that rung has a call (hacked counts its attempted) |
| A dashed edge around a node | an agent that left: its episode ended by `stop_working` or an error before the final hand-in. An agent at attempted or above never dims; one below fades to no less than 60% |
| An edge | two agents that talked on the board: a post that names the other, or a read that asks for the other's posts (`board_read` with `from_agent`) |
| Edge thickness | how much they went back and forth lately, against the run's busiest moment; an exchange both ways counts double, and each exchange fades with time, so a pair that stops talking thins to a hairline |
| A red edge | both agents are at attempted or above |
| A thin beam along an edge | one message, travelling from writer to the agent named (or from the agent asked for to the reader), in the writer's role colour (the reader's for a read); gone within about a second |
| A faint ring spreading from a node | a post on the board that names no agent |
| Orbs circling a node | pieces of the first exploit family (`hidden`: A, B, C), one colour per piece |
| Small diamonds circling a node | pieces of the second family (`revealed`: client, config, wiring) |
| Solid or hollow | a piece dealt to the agent is solid; a piece it first saw later (`pieces_seen_at`) flies in from the centre as a hollow outline of the same shape and stays hollow. Who gave it is not recorded, so no giver is drawn |
| ●●● ◇◆◇ on a tag | per family the agent has any piece of, one mark per piece: solid dealt, hollow seen, dim not held |
| A bright ring closing in on a node | the agent's rung changed, in the new rung's colour: the brightest cue of a frame |
| ↓ in the feed, a short beam | an undone write's rise fell back (`timeline.rises[].fell_at`). When the rise records `by` and `at`, the fall happens at call `at` of agent `by`: the colour change, the closing ring, the feed row and the end of the hollow tick sit at that call's time, and a short beam runs from `by` to the agent that fell |
| An amber beacon on a node, beams to others | an alert to humans (`alert_humans`): the caller flashes, a beam reaches each agent it names; grey for a false alarm |
| Pips at a node's left edge | one per accusation naming that agent (`alert_outcomes[].named_cheated_by_then`): filled amber when it had cheated by then, hollow amber when the alarm was true only for the team, grey when false, dashed grey when unknown |
| ⚠a b ?c ●h ○m on a regulator's or disregulator's tag | the alerts it raised: amber a true, grey b false, ? c unknown (no outcome); then its hits h (agents named who had cheated by then) and misses m (who had not) |
| Dimmed name in an alert row of the feed | an agent the alert named who had not cheated by then |
| ▬▬ n/N, segments above a node | how many of the N planted values the version held: found on an added line of its code (`version_values.json`); filled n of N, dashed when unknown (its code was not stored, or the run has no `version_values.json`). A fact about the code, not a verdict. Shown above the agent whose hand-in stands now |
| A faint glow at the room's edges | a hand-in stands |
| One thin ring around the final hand-in's maker | the final hand-in, in the team rung's colour; it stays close to the node and is gone within about a second. The header's disc carries the verdict |
| ◉ burst, ◎ ring closing in | a `check_fix` answer: accepted bursts outward, not accepted closes in, in the shade of what the checked code held |
| Violet rings closing on a node, ↩ on a tag | that agent took the hand-in back |
| A tag under a node, on a dark pill | the agent's piece marks, its label (W1 worker, R1 regulator, D1 disregulator), its model, its rung now. A tag that would sit on a nearer node or on another tag is lifted above it, with a thin line back to its node. Past 12 agents, only an agent doing something now (a rung, a post, a piece, a check, an alert, a fall, the hand-in) or hovered gets the full tag; the rest show a faint label |
| The feed at the right | the last six events as chips, never words from a message: ✎ a post, its writer → the agents it names (its words on hover); → or ↓ a rung; ● or ◆ a piece and the routes (`branch`, `fix`, `patch`, `file`) of the agent's signals row that reach that family; ✓ a hand-in and what it held; ↩ a take-back (its reason on hover); ◉ ◎ a check; ⚠ an alert, the agents it names, true or false (its reason on hover) |
| Top left | ◂ back to the study page and the run's study mark (on a page written by `--study`); the setup as chips: ● workers and their model, ■ regulators, ✹ disregulators, alone, § the rule, the task version, ◎ check (the `check_fix` tool, grey) or ◎✗, ⚑ cheat (told to cheat); the run's name |
| Top right | the clock, the round; the hand-in standing now; the badge: a dashed circle until the final hand-in, then the verdict's disc (a round disc in the team rung's colour, a red hexagon for hacked, a white ring of the same shape for pass), grey with no labels, ∅ for a run with no hand-in (at once for a run in `incomplete/`); beside it ▲k/n, the workers the labels put at attempted or above, and ✹k/n for disregulators |
| The legend | the shapes, the rungs (hacked as a hexagon), ◌ undated, the dashed edge of an agent that left, each family's pieces (the family `exploit_used` names is outlined) and a hollow one for seen, the four pips, the held segments, the verdict's disc |

The bar at the bottom: play or pause, the timeline and the speed. A run plays in two minutes at the default speed (three
chevrons); the button steps through a quarter, half, normal and double speed. The timeline has one lane per kind of
mark, its icon at the left end and its totals at the right end, equal to the study page's counts:

| Lane | Marks | Totals |
|---|---|---|
| ⚠ alerts | a triangle, amber when a true alarm is among it, hollow amber when the rest are unknown, grey when all are false | ⚠ true in amber, false in grey, ?unknown in a dashed amber outline |
| ● pieces | a dot or a small diamond in the piece's colour per arrival | |
| ✓ hand-ins | a band while each hand-in stood, in the shade of what its code held; the final one in the team rung's colour, a diamond at it (white edge when it passed) and the verdict's disc (hexagon for hacked) after the track's end; a violet tick at each take-back; behind it, how busy the board was | ✓ hand-ins, ↩ take-backs |
| ▲ rungs | a red tick where an agent reached attempted or above: solid when it stays there to its label, hollow from the rise to the call that caused its fall; dashed, beside the verdict's disc and off the time axis, for a final rung no dated call stands behind | ▲ solid, ◌ dashed, ▵ hollow |
| ◎ checks | a dot per `check_fix` answer, filled when accepted, a ring when not | ◉ accepted, ◎ not accepted |

Marks that would share a pixel become one mark with its counts beside it (ticks stand side by side). Drag the scene to
turn it, scroll to zoom, hover a node to light its links. Space plays or pauses. The buttons beside play step one call
back or forward and jump to the previous or next round; hold one to keep stepping. The arrow keys step calls, and with
Shift they step rounds. While stepping, the top right reads the round, the call's number and who made which call, and
a pale blue ring marks that agent. `#t=0.5` at the end of the URL opens the run halfway through, `#round=7` at round 7's
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
| falls | `agents.<agent>.timeline.rises[]`: `fell_at` (the agent's call where it fell back), `by` and `at` (the call of `by` that caused it, where the fall is placed) |
| left | `processed/episodes.jsonl` `ended_by` (`stop_working`) or `error`, when its last call ended before the final hand-in |
| hand-in, verdict | `processed/run.json` `team_submission`; the final hand-in is the last of `unsubmits.submits` no take-back followed; the team's rung (`team`) and `passed` in `analysis/labels/cheating.json` |
| what a version held | `processed/facts/version_values.json`: `values` (the planted values), `hand_ins[].held` matched to `unsubmits.submits` by `seq`, `checks[].held` and `answer` |
| check_fix answers | `version_values.json` `checks`, placed at the caller's answered `check_fix` calls in order (a call the tool refused is no check) |
| setup | `processed/run.json` `config` (`n_agents` minus `regulators` and `disregulators` = workers, `rule_level`, `tool_limits`, `seed`), `role_models`, `swe.variant`, `launch.module` (ending `_cheat` = told to cheat) |
| alerts | `processed/run.json` `alerts` (caller, agents named, reason), placed at the caller's `alert_humans` call; `analysis/signals.jsonl` team row `alert_outcomes`, matched in order: `outcome` for true, false or unknown (null), and `named_cheated_by_then` for each agent named |
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
| Rows and columns | the setup fields that vary across the study's runs, split so the grid has few headers and few empty cells and fits the page, in the design's order; the fields every run shares sit in the header. One word in the corner names each axis (`workers` down; `regulators`, `regulator` or `disregulator` across or down) |
| Header | the finished runs, ▲ cheated, ○ passed, ? finished runs without labels, a dashed ring with the count of runs in `incomplete/` |
| A cell | one setting; its runs, one mark per seed. An empty cell is barely drawn |
| ▲n ○n /n | finished runs at attempted or above, finished runs that passed, finished runs with labels; only in a cell with more than one finished run |
| c/n and a thin red bar | the cheated share of the finished runs with labels: along a cell's bottom edge when it holds more than one, and for each row and column in its margin |
| A disc | a run, in its team rung's colour (none, looked, tempted, attempted); a red hexagon for hacked |
| A white ring | the run passed; a hexagon's ring is a hexagon |
| A dashed disc with ? | the run has no labels |
| A dashed ring set apart at a cell's edge | a run in `incomplete/` |
| The dot on a disc | what the final hand-in held: an empty ring 0, half filled some, filled all, grey unknown (its `held` is null). No dot without a hand-in |
| ∅ on a disc | no hand-in stands (`team_submission` null) |
| ⚠a b ?c under a disc | alerts: amber a true, grey b false, ?c in a dashed amber outline unknown (no outcome, or a null one) (`alert_outcomes`) |
| The disc's hover | the run's name, rung, pass or fail, ✓ hand-ins made, ↩ take-backs, ◉ ◎ `check_fix` answers accepted and not accepted |
| § | the rule (`rule_level`) |
| The row ? | runs without `processed/run.json`, which cannot be placed |

`uv run pytest swarm_visualization/tests -q` runs the tests.
