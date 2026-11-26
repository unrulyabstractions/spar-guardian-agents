# Swarm visualization

One run of the swarm as a 3D picture with a time slider, with short labels that say what is happening.

```
python swarm_visualization/visualize_swarm.py ian_flipt_deepseek_n10_reg0_revealing_s1
python swarm_visualization/visualize_swarm.py runs/regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1
python swarm_visualization/visualize_swarm.py regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1 --no-open
```

The script takes a run folder, a run name searched in `runs/` and `OLD/runs/`, or `study/name`. It writes one HTML
file to `swarm_visualization/out/<run>.html` (or `--out`) and opens it in the browser. The file holds three.js and the
run's data, so it opens from disk and can be sent as it is. It needs only Python's standard library and a browser with
WebGL.

## What it shows

| Mark | Meaning |
|---|---|
| A sphere | a worker |
| A sphere in a wire cage | a regulator |
| A spiky star | a disregulator |
| Node colour | the agent's Cheating rung at that moment: pale (none), cream (looked), orange (tempted), red (attempted), pulsing crimson (hacked) |
| A faded, shrunken node | an agent that has left the run |
| An edge | two agents that talked on the board: a post that names the other, or a read that asks for the other's posts (`board_read` with `from_agent`) |
| Edge thickness | how much they went back and forth, against the run's busiest pair; an exchange both ways counts double |
| A red edge | both agents have cheated |
| A spark along an edge | one message, travelling from writer to the agent named (or from the agent asked for to the reader) |
| A ring spreading from a node | a post on the board |
| Coloured orbs circling a node | the exploit's pieces the agent holds, one colour per piece |
| A coloured orb flying between nodes | a piece reaching an agent that did not hold it |
| A white ring around a node | the agent holds every piece |
| A wave across the swarm | the team's hand-in; red when the team cheated, and the room stays red after it |
| An amber beacon on a node, beams to others | an alert to humans (`alert_humans`): the caller flashes, a beam reaches each agent it names; grey for a false alarm |
| An amber ring, ⚠ on a tag | an agent alerts have named, and how many times (false alarms not counted) |
| A tag under a node | the agent's label (W1 worker, R1 regulator, D1 disregulator), its model, its rung now |
| The feed at the right | the last six events: posts with their opening words, rungs reached, pieces passed, the hand-in |
| Top left, top right | the cast (models and roles), the run's name; the clock |

The bar at the bottom: play or pause, the timeline and the speed (one to four chevrons). The timeline shows how busy
the board is, a dot of the piece's colour where a piece was passed, a red tick where an agent began to cheat and a
diamond at the hand-in. Drag the scene to turn it, scroll to zoom, hover a node to light its links. Space plays or
pauses and the arrow keys step. `#t=0.5` at the end of the URL opens the run halfway through, paused.

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
| hand-in | `processed/run.json` `team_submission`, and the team's rung |
| alerts | `processed/run.json` `alerts` (caller, agents named, reason), placed at the caller's `alert_humans` call; `analysis/signals.jsonl` team row `alert_outcomes` for true or false alarm |
| models | `processed/run.json` `ledger.agents.<agent>.model`, then `role_models`, then `model` |

No file records who passed a piece. A flying orb starts at the holder of that piece the receiver had talked with most,
then the holder who posted last, then the first holder. An orb from outside the swarm is a piece no agent held.

## Layout

```
visualize_swarm.py          the command
src/run_locator.py          a path or a name to a run folder
src/run_files.py            reads the run's files
src/swarm_story.py          the run as one story on one clock
src/page_builder.py         writes the single HTML file
src/web/                    the page: viewer.html, viewer.css, viewer.js, vendor/three.module.min.js (three.js r160, MIT)
tests/test_swarm_story.py   the story of a small hand-made run
```

`uv run pytest swarm_visualization/tests -q` runs the tests.
