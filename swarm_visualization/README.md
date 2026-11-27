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
| A cube | a regulator |
| A spiky ball | a disregulator |
| Where they sit | workers on a ring; regulators on a small ring at its centre, raised a little; disregulators likewise (lowered when a run has both) |
| Node colour | the agent's Cheating rung at that moment: pale (none), cream (looked), orange (tempted), red (attempted), pulsing crimson (hacked) |
| A faded, shrunken node | an agent that has left the run |
| An edge | two agents that talked on the board: a post that names the other, or a read that asks for the other's posts (`board_read` with `from_agent`) |
| Edge thickness | how much they went back and forth lately, against the run's busiest moment; an exchange both ways counts double, and each exchange fades with time, so a pair that stops talking thins to a hairline |
| A red edge | both agents have cheated |
| A spark along an edge | one message, travelling from writer to the agent named (or from the agent asked for to the reader) |
| A ring spreading from a node | a post on the board |
| Coloured orbs circling a node | the exploit's pieces the agent holds, one colour per piece |
| A coloured orb flying between nodes | a piece reaching an agent that did not hold it |
| A white ring around a node | the agent holds every piece |
| A wave across the swarm | the final hand-in, in the verdict's colour; the room keeps that colour after it |
| An amber beacon on a node, beams to others | an alert to humans (`alert_humans`): the caller flashes, a beam reaches each agent it names; grey for a false alarm |
| An amber ring, ⚠ on a tag | an agent alerts have named, and how many times (false alarms not counted) |
| ◈ 0/2, 1/2, 2/2 | how many of the planted values the version held: found on an added line of its code (`version_values.json`). A fact about the code, not a verdict |
| Held colours | steel blue 0 held, pink some, magenta all, grey unknown (its code was not stored, or the run has no `version_values.json`) |
| A tinted room, a hexagonal ring and ✓ | a hand-in stands; room and ring take the colour of what its code held, and the agent who made it wears the ring |
| A second, outer ring on the final hand-in | the verdict: green when the team's rung is below attempted, red from attempted up, grey when the run has no labels; the room turns green or red |
| ◉ burst, ◎ ring closing in | a `check_fix` answer: accepted bursts outward, not accepted closes in; both in the colour of what the checked code held, ◉ or ◎ with ◈ on the tag |
| Violet rings closing on a node, ↩ on a tag | that agent took the hand-in back; the room goes back to dark |
| A tag under a node | the agent's label (W1 worker, R1 regulator, D1 disregulator), its model, its rung now. Past 12 agents, only an agent doing something now (a rung reached, a post, a piece, a check, an alert, the hand-in) or hovered gets the full tag; the rest show a faint label |
| The feed at the right | the last six events as icons and chips: ✎ a post and its opening words, → a rung, ● a piece and ← who gave it, ✓ a hand-in and ◈ what it held, ↩ a take-back, ◉ ◎ a check, ⚠ an alert |
| Top left | the setup as chips: ● workers and their model, ■ regulators, ✹ disregulators, alone, § the rule, the task version, ◎ check (the `check_fix` tool) or ◎✗, ⚑ cheat (told to cheat); the run's name |
| Top right | the clock, the round; the hand-in standing now: ✓, who, ◈ what it held, and on the final one the team's rung and pass or fail |

The bar at the bottom: play or pause, the timeline and the speed. A run plays in two minutes at the default speed (three
chevrons); the button steps through a quarter, half, normal and double speed. The timeline shows how busy
the board is, a dot of the piece's colour where a piece was passed, a red tick where an agent began to cheat, a band
while each hand-in stood (in the colour of what it held, the final one in the verdict's colour with what it held on
top, a violet tick at each take-back), a dot on the bottom lane per `check_fix` answer (filled accepted, ring not
accepted, in the colour of what it held) and a diamond at the final hand-in. Drag the scene to turn it, scroll to zoom, hover a node to light its links. Space plays or
pauses. The buttons beside play step one call back or forward and jump to the previous or next round; hold one
to keep stepping. The arrow keys step calls, and with Shift they step rounds. While stepping, the top right reads
the round, the call's number and who made which call, and a white ring marks that agent. `#t=0.5` at the end of
the URL opens the run halfway through, `#round=7` at round 7's first call and `#step=120` at the 120th call, all
paused.

## Where the data comes from

| Picture | Run file |
|---|---|
| clock | `processed/run.json` `rules.timeline`: each call's start and end; a call with no time sits at its neighbour's |
| agents, roles | `processed/run.json` `agents`, `processed/facts/topology.json` |
| calls | `processed/trajectories/*/transcript.json`, numbered from 0 as the transcript scan numbers them |
| posts, names | `processed/communication/*.json`: each `sent` event, the post's text searched for agent IDs |
| pieces held | `processed/run.json` `access.agents.<agent>.holds` |
| pieces received | `analysis/signals.jsonl` `pieces_seen_at`: the call at which the agent first saw each piece |
| rungs | `analysis/labels/cheating.json` `agents.<agent>.timeline.after` after every call, then the final rung at the run's end |
| hand-in | `processed/run.json` `team_submission`, and the team's rung and `passed` in `analysis/labels/cheating.json` |
| what a version held | `processed/facts/version_values.json`: `values` (the planted values), `hand_ins[].held` matched to `unsubmits.submits` by `seq`, `checks[].held` and `answer` |
| check_fix answers | `version_values.json` `checks`, placed at the caller's answered `check_fix` calls in order (a call the tool refused is no check) |
| setup | `processed/run.json` `config` (`n_agents` minus `regulators` and `disregulators` = workers, `rule_level`, `tool_limits`, `seed`), `role_models`, `swe.variant`, `launch.module` (ending `_cheat` = told to cheat) |
| alerts | `processed/run.json` `alerts` (caller, agents named, reason), placed at the caller's `alert_humans` call; `analysis/signals.jsonl` team row `alert_outcomes` for true or false alarm |
| hand-ins, take-backs | `processed/run.json` `unsubmits.submits` and `unsubmits.unsubmits`, at their recorded times |
| models | `processed/run.json` `ledger.agents.<agent>.model`, then `role_models`, then `model` |

No file records who passed a piece. A flying orb starts at the holder of that piece the receiver had talked with most,
then the holder who posted last, then the first holder. An orb from outside the swarm is a piece no agent held.

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

| Mark | Meaning |
|---|---|
| Rows and columns | the setup fields that vary across the study's runs, split so the grid has few headers and few empty cells and fits the page; the fields every run shares sit in the header |
| A cell | one setting; its runs, one mark per seed. Its red tint is the share of runs whose team rung is attempted or above |
| ▲n ○n /n | runs at attempted or above, runs that passed, runs with labels |
| A disc | a run, in its team rung's colour (none, looked, tempted, attempted, hacked; hacked has a white core) |
| A white ring | the run passed |
| A dashed disc with ? | the run has no labels |
| A faded disc in a dashed ring | the run is in `incomplete/` |
| The small dot on a disc | what the final hand-in held: steel blue 0, pink some, magenta all, grey unknown |
| ✓n ↩n ◎a/n ⚠n | hand-ins, take-backs, accepted / all `check_fix` answers, alerts |
| The row ? | runs without `processed/run.json`, which cannot be placed |

`uv run pytest swarm_visualization/tests -q` runs the tests.
