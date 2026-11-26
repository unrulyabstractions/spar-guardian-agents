/* Who entered a run and who left it, each influencer's alert (alert_humans), and the team's hand-ins (submit) and
 * take-backs (unsubmit), as the run page draws them. The data is the payload's presence and hand_ins
 * (src/ui/transcript_presence.py): /api/run's for the bar and the Details tab, /api/board's, placed on the board's
 * clock, for the Board tab's lanes.
 *
 *   presenceEvents(step.extra)                   // the presence events a user step holds: a round's message's events,
 *                                                // or the one event of a presence line before 2026-11-22
 *   presenceChip(event)                          // one presence event, as a chip in the step's header
 *   roundChip(step.extra.swarm_round), boardChip(step.extra.swarm_notice)   // a round's message's other chips
 *   paragraphChip(extra)                         // a board notice that ends a tool result or a user message
 *   alertsBadge(p.alerts, onclick)                // the bar's badge, null without an alert
 *   alertsCard(p, roles, openCall), presenceCard(p, roles, mark)    // the Details tab's two cards, null when empty
 *   laneMarks(p, lay)                             // the Board tab: {g, tipOf(el), callOf(el)}
 *   presenceKeys(p, item)                         // the Board tab's legend samples
 *   finalOf(hi, agent), starOf(hi, agent), starText(hi, agent)   // the final hand-in if `agent` made it; its star
 *   alertNames(p, roles), namedMark(names, agent), namedText(names, agent)   // the workers the alerts name; the flag
 *   handInsBadge(hi, onclick), handInsCard(hi, openCall, v)   // the bar's badge and the Details tab's strip and checks
 *   handInChip(tool, event, hi)                   // a submit or unsubmit call's chip: what the call did
 *   heldOf(held, values), heldWords(x), heldChip(held, values)   // what a version held of the planted values
 *   checkChip(check, values), answerWords(check)  // a check_fix call's chip: its answer and what its code held
 *   handInKeys(hi, item)                          // the Board tab's legend samples of the hand-in marks
 *
 * On a lane: the stretch before the agent entered and after it exited is hatched; a tick marks the entry (at the lane's
 * start for an agent present from the first round) and the exit,
 * the exit's reason beside it where the lane has room. An alert is a line across the whole timeline in the bad colour,
 * a flag on the board (at lay.yFlag), and a larger one on the top edge of the alerting agent's lane when it is one of the two
 * shown; its hover gives the reason clipped to REASON_WORDS words, the agents it names and its outcome (true alarm,
 * false alarm or undecided). The reason
 * shows whole in the Details tab. The hand-ins strip holds a row per round, each hand-in and take-back a chip in the
 * team's order: the agent, a hand-in's commit (SHORT_SHA) and what it held, the final hand-in starred; a take-back's
 * hover holds its reason whole, and a chip's click opens its call. Under the strip a table lists the grader checks
 * (check_fix): round, agent, commit, what the code held, answer. What a version held is k of n of the run's planted
 * values (/api/run versions, src/ui/transcript_monitoring.py): all, some, none, or no saved copy. A worker an alert
 * names carries a flag wherever the page names it, with how many alerts name it and the first one's round on hover.
 * Every string goes in through h() and svgEl() text nodes. */
import { h, plural } from '/static/app/dom.js';
import { badge, dataTable, muted } from '/static/app/components.js';
import { svgEl } from '/static/app/charts.js';
import { ROLE_LABEL } from './tv_labels.js';

export const WHY_WORDS = { submit: 'submit', stop: 'stop', rounds: 'out of rounds', budget: 'budget', alert: 'alert',
  context: 'context limit', delivered: 'delivered' };
export const OUTCOME = { true_alarm: ['true alarm', 'good'], false_alarm: ['false alarm', 'warn'] };
const REASON_WORDS = 12;
const LABEL_FONT = '600 10px';

/** The first n words of a text, with an ellipsis when it had more. */
export function clipWords(text, n = REASON_WORDS) {
  const words = String(text == null ? '' : text).split(/\s+/).filter(Boolean);
  return words.length > n ? `${words.slice(0, n).join(' ')} …` : words.join(' ');
}

/** An exit's reason in a word or two; a team's hand-in adds the run's last round. */
export function exitWords(e) {
  const why = WHY_WORDS[e.why] || e.why || 'exited';
  return e.why === 'submit' && e.hand_in === 'team' && e.ends_after_round != null
    ? `${why} · run ends after round ${e.ends_after_round}` : why;
}

/** An alert's outcome as a badge: true alarm, false alarm, or undecided. */
export function outcomeBadge(a) {
  const named = Object.entries(a.named_cheated_by_then || {}).map(([x, v]) => `${x} ${v == null ? '?' : v ? 'cheated' : 'clean'}`);
  const tip = [`team ${a.team_cheated_by_then == null ? '?' : a.team_cheated_by_then ? 'cheated' : 'clean'} by then`, ...named].join(' · ');
  const [word, kind] = OUTCOME[a.outcome] || ['undecided', 'plain'];
  return badge(word, kind, tip);
}

/** The presence events of a user step (its extra.swarm_presence, src/swarm/turn_rules/_turn_messages.py): a round's
 * message holds {round, events}; a presence line before 2026-11-22 was one event. [] without. */
export function presenceEvents(extra) {
  const p = extra && extra.swarm_presence;
  if (!p) return [];
  return Array.isArray(p.events) ? p.events : p.agent ? [p] : [];
}

const EVENT_WORDS = { entered: 'entered', exited: 'exited', alert: 'alerted', held: 'hand-in held', push: 'pushed' };

/** One presence event as a chip for its step's header: who, what, and its round. An entry folded into its exit in the
 * same message (folded) was never said, so it gets no chip. */
export function presenceChip(p) {
  if (!p || !p.kind || p.folded) return null;
  const gone = p.kind === 'exited';
  const what = EVENT_WORDS[p.kind] || p.kind;
  const words = [p.agent, what].filter(Boolean).join(' ') + (gone && p.why ? ` · ${WHY_WORDS[p.why] || p.why}` : '');
  return h('span', { class: ['pr-chip', gone ? 'pr-chip-out' : p.kind === 'entered' ? 'pr-chip-in' : 'pr-chip-event',
    (p.why === 'alert' || p.kind === 'alert') && 'pr-chip-alert'],
  title: p.round != null ? `round ${p.round}` : null }, words, p.round != null ? h('span', { class: 'pr-round' }, `r${p.round}`) : null);
}

/** The fewest agents entering in one round that share one chip. */
const ENTERED_TOGETHER = 4;
/** The names a chip's hover lists before it counts the rest. */
const HOVER_NAMES = 9;
/** A step's presence events as its header's chips: one chip an event, but four or more agents that entered in one
 * round share a chip ("19 agents entered"), whose hover names them. */
export function presenceChips(events) {
  const said = events.filter(p => p && p.kind && !p.folded);
  const together = new Map();
  for (const p of said) if (p.kind === 'entered') together.set(p.round, [...(together.get(p.round) || []), p.agent]);
  const drawn = new Set();
  return said.map(p => {
    const agents = p.kind === 'entered' ? together.get(p.round) : [];
    if (agents.length < ENTERED_TOGETHER) return presenceChip(p);
    if (drawn.has(p.round)) return null;
    drawn.add(p.round);
    const names = agents.slice(0, HOVER_NAMES).join(', ')
      + (agents.length > HOVER_NAMES ? ` +${agents.length - HOVER_NAMES}` : '');
    return h('span', { class: ['pr-chip', 'pr-chip-in'], title: names }, `${agents.length} agents entered`,
      p.round != null ? h('span', { class: 'pr-round' }, `r${p.round}`) : null);
  });
}

/** A round's message's round chip (its extra.swarm_round). A run from ec68304 tags the agent's own budget: the chip
 * is the run's round and its hover the calls the agent has left of its budget. An older run tags rounds: the round of
 * R, its rounds left and calls on hover. */
export function roundChip(r) {
  if (!r || r.round == null) return null;
  if (r.budget != null) {
    return h('span', { class: ['pr-chip', 'pr-chip-round'], title: callsLeft(r) }, `round ${r.round}`);
  }
  const tip = r.left != null ? `${plural(r.left, 'round')} left · up to ${plural(r.calls, 'call')}` : null;
  return h('span', { class: ['pr-chip', 'pr-chip-round'], title: tip }, `round ${r.round}${r.of != null ? ` of ${r.of}` : ''}`);
}

/** "280 of 300 tool calls left" for a round tag that holds the agent's own budget (from ec68304), else null. */
export function callsLeft(r) {
  return r && r.budget != null && r.left != null ? `${r.left} of ${plural(r.budget, 'tool call')} left` : null;
}

/** A round's message's board chip (its extra.swarm_notice): the posts by others since the agent was last told, every
 * one new while it had been told of none (first; an older run's first round). */
export function boardChip(n) {
  if (!n) return null;
  const first = n.first ?? n.activation === 1;
  return h('span', { class: ['pr-chip', 'pr-chip-board'],
    title: first ? 'posts on the board' : 'board changed since the agent was last told' }, 'board');
}

/** The board notices the provider got at the end of a tool result or a user message (extra.swarm_notice_paragraph,
 * src/export/atif_transcripts.py), as one chip; null without. */
export function paragraphChip(extra) {
  const added = extra && extra.swarm_notice_paragraph;
  if (!added || !added.length) return null;
  return h('span', { class: ['pr-chip', 'pr-chip-board'], title: added.join('\n\n') }, 'board notice');
}

/** The bar's badge: how many alerts the run has, their reasons clipped on hover; null without one. */
export function alertsBadge(alerts, onclick) {
  if (!alerts || !alerts.length) return null;
  const tip = alerts.map(a => `${a.agent} r${a.round}: ${clipWords(a.reason)}`).join('\n');
  return h('button', { type: 'button', class: ['badge', 'b-bad', 'pr-alert-badge'], title: tip, onclick },
    h('span', { class: 'pr-bang', 'aria-hidden': 'true' }, '!'), plural(alerts.length, 'alert'));
}

const roleOf = (roles, a) => ROLE_LABEL[roles[a]] || roles[a] || '';

/** The Details tab's alerts: who, the round, the agents named, the outcome, the reason whole and its call. openCall(agent,
 * k) opens the alert_humans call in the agent's tab. Null without an alert. */
export function alertsCard(p, roles, openCall) {
  const alerts = (p && p.alerts) || [];
  if (!alerts.length) return null;
  const rows = alerts.map(a => [h('b', null, a.agent), roleOf(roles, a.agent), String(a.round ?? ''),
    (a.agents || []).length ? a.agents.join(', ') : muted('none', 'small'), outcomeBadge(a),
    h('span', { class: 'pr-reason' }, a.reason || ''),
    a.k != null ? h('button', { type: 'button', class: 'linkish ev-link', title: `open call ${a.k} in ${a.agent}'s transcript`,
      onclick: () => openCall(a.agent, a.k) }, `call ${a.k}`) : muted('no call', 'small')]);
  return h('section', { class: 'card pr-alerts', id: 'sec-alerts' }, h('h2', null, h('span', { class: 'pr-bang' }, '!'), ' Alerts'),
    dataTable(['Agent', 'Role', 'Round', 'Named', 'Outcome', 'Reason', 'Call'], rows, { cls: 'pr-table' }));
}

/** The Details tab's entries and exits: each agent's entry round, exit round and reason, the marks the page puts
 * beside its name (mark(agent): the final hand-in's star, the flag of a worker an alert names). Null when the run
 * recorded none. */
export function presenceCard(p, roles, mark) {
  const agents = Object.entries((p && p.agents) || {});
  if (!agents.length) return null;
  const rows = agents.map(([a, x]) => [h('b', null, a, mark(a)), roleOf(roles, a), x.entered != null ? String(x.entered) : null,
    x.exited ? String(x.exited.round) : muted('present at the end', 'small'),
    x.exited ? h('span', { class: ['pr-why', `pr-why-${x.exited.why}`] }, exitWords(x.exited)) : null]);
  return h('section', { class: 'card pr-presence' }, h('h2', null, 'Entries and exits'),
    dataTable(['Agent', 'Role', 'Entered', 'Exited', 'Why'], rows, { cls: 'pr-table' }));
}

let measureCtx = null;
function textWidth(text) {
  measureCtx = measureCtx || document.createElement('canvas').getContext('2d');
  if (!measureCtx) return text.length * 6;
  measureCtx.font = `${LABEL_FONT} sans-serif`;
  return Math.ceil(measureCtx.measureText(text).width) + 2;
}

/** The Board tab's presence marks for the pair shown. lay: {sides ['a', 'b'], agentOf(side) (null for a side not
 * shown), laneY, laneH, yBoard, boardH, yFlag (where an alert's flag stands across the board: the team lane's alert
 * row, or the board lane's top), yTop, yBottom, x0, x1, xOfCall(agent, k, end) (null for a call not on the lanes),
 * xOfTime(t)}. Returns {g, tipOf(el), callOf(el)}: the SVG group, the hover lines of a mark ([data-pr]) and the
 * call it opens ({agent, k} or null). */
export function laneMarks(p, lay) {
  const g = svgEl('g', { class: 'pr-marks' });
  const items = [];
  const add = (el, item) => { el.setAttribute('data-pr', String(items.length)); items.push(item); return el; };
  if (!p) return { g, tipOf: () => [], callOf: () => null };
  const uid = Math.random().toString(36).slice(2, 8);
  g.appendChild(svgEl('defs', null, svgEl('pattern', { id: `pr-hatch-${uid}`, width: 6, height: 6,
    patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' },
  svgEl('line', { class: 'pr-hatch-line', x1: 0, y1: 0, x2: 0, y2: 6 }))));
  const hatch = `url(#pr-hatch-${uid})`;
  for (const side of lay.sides) {
    const agent = lay.agentOf(side);
    const x = agent && p.agents[agent];
    if (!x) continue;
    const y = lay.laneY[side];
    const H = lay.laneH;
    const xIn = !x.entry ? null : x.entry.start ? lay.x0
      : x.entry.k != null ? lay.xOfCall(agent, x.entry.k, false) : null;
    const xOut = x.exit && x.exit.k != null ? lay.xOfCall(agent, x.exit.k, true) : null;
    if (xIn != null && xIn > lay.x0 + 1) {
      g.appendChild(svgEl('rect', { class: 'pr-absent', x: lay.x0, y, width: xIn - lay.x0, height: H, fill: hatch }));
    }
    if (xOut != null && xOut < lay.x1 - 1) {
      g.appendChild(svgEl('rect', { class: 'pr-absent', x: xOut, y, width: lay.x1 - xOut, height: H, fill: hatch }));
    }
    if (xIn != null) {
      const label = `entered r${x.entered}`;
      const w = textWidth(label);
      g.appendChild(add(svgEl('g', { class: 'pr-tick pr-in' },
        svgEl('line', { x1: xIn, x2: xIn, y1: y - 3, y2: y + H + 3 }),
        svgEl('path', { d: `M${xIn},${y - 3} l7,4 l-7,4 z` }),
        xIn - lay.x0 > w + 6 ? svgEl('text', { x: xIn - 4, y: y + H / 2 + 3.5, 'text-anchor': 'end' }, label) : null,
        svgEl('rect', { class: 'pr-hit', x: xIn - 4, y: y - 4, width: 8, height: H + 8 })),
      { lines: [`${agent} entered`, `round ${x.entered}`], call: { agent, k: x.entry.k } }));
    }
    if (xOut != null) {
      const why = x.exited.why;
      const label = `exited · ${WHY_WORDS[why] || why}`;
      const w = textWidth(label);
      g.appendChild(add(svgEl('g', { class: ['pr-tick', 'pr-out', `pr-out-${why}`] },
        svgEl('line', { x1: xOut, x2: xOut, y1: y - 3, y2: y + H + 3 }),
        svgEl('path', { d: `M${xOut},${y - 3} l-7,4 l7,4 z` }),
        lay.x1 - xOut > w + 6 ? svgEl('text', { x: xOut + 4, y: y + H / 2 + 3.5 }, label) : null,
        svgEl('rect', { class: 'pr-hit', x: xOut - 4, y: y - 4, width: 8, height: H + 8 })),
      { lines: [`${agent} exited`, `round ${x.exited.round} · ${exitWords(x.exited)}`], call: { agent, k: x.exit.k } }));
    }
  }
  // the alerts: a line across the timeline, a flag at yFlag, and a flag on the alerting agent's lane
  for (const a of p.alerts || []) {
    const side = lay.sides.find(s => lay.agentOf(s) === a.agent);
    const x = side && a.k != null ? lay.xOfCall(a.agent, a.k, false) : a.t != null ? lay.xOfTime(a.t) : null;
    if (x == null || !Number.isFinite(x)) continue;
    const [outcome] = OUTCOME[a.outcome] || ['undecided'];
    const item = { lines: [`alert · ${a.agent} · round ${a.round}`, clipWords(a.reason),
      (a.agents || []).length ? `names ${a.agents.join(', ')}` : 'names no agent', outcome],
    call: a.k != null ? { agent: a.agent, k: a.k } : null };
    const flag = (fy, big) => {
      const r = big ? 9 : 8;
      return svgEl('g', { class: ['pr-flag', big && 'pr-flag-big'] },
        svgEl('path', { d: `M${x},${fy - r} L${x + r},${fy} L${x},${fy + r} L${x - r},${fy} z` }),
        svgEl('text', { x, y: fy + 4, 'text-anchor': 'middle' }, '!'));
    };
    g.appendChild(add(svgEl('g', { class: 'pr-alert' },
      svgEl('line', { class: 'pr-alert-line', x1: x, x2: x, y1: lay.yTop, y2: lay.yBottom }),
      flag(lay.yFlag, false),
      side ? flag(lay.laneY[side], true) : null), item));
  }
  const of = el => items[Number(el.getAttribute('data-pr'))] || null;
  return { g, tipOf: el => (of(el) || { lines: [] }).lines, callOf: el => (of(el) || {}).call || null };
}

/** The legend's samples of the presence marks, given the Board tab's legend item maker; [] when the run recorded no
 * entry, exit or alert. */
// ---- hand-ins and take-backs (the payload's hand_ins: {events, final, refused})
const SHORT_SHA = 7;
/** A commit's short name, "58073e9"; null without one. */
export const shortSha = sha => (sha ? String(sha).slice(0, SHORT_SHA) : null);
/** The final hand-in when `agent` made it, else null. */
export const finalOf = (hi, agent) => (hi && hi.final && hi.final.agent === agent ? hi.final : null);
/** The star of the agent that made the final hand-in, with its round on hover; null for any other agent. */
export function starOf(hi, agent) {
  const f = finalOf(hi, agent);
  return f ? h('span', { class: 'hi-star', title: `final hand-in, round ${f.round}` }, '★') : null;
}
/** The star as text, for an option or an SVG label: " ★", or "" for any other agent. */
export const starText = (hi, agent) => (finalOf(hi, agent) ? ' ★' : '');

const count = (hi, kind) => ((hi && hi.events) || []).filter(e => e.kind === kind).length;

/** The bar's badge: the hand-ins, the take-backs and who made the final hand-in; null without a hand-in. */
export function handInsBadge(hi, onclick) {
  if (!hi || !(hi.events || []).length) return null;
  const n = count(hi, 'submit');
  const back = count(hi, 'unsubmit');
  const f = hi.final;
  return h('button', { type: 'button', class: ['badge', 'b-plain', 'hi-badge'],
    title: f ? `final hand-in, round ${f.round}${f.snapshot ? `, ${shortSha(f.snapshot)}` : ''}` : 'no hand-in stood', onclick },
  f ? [h('span', { class: 'hi-star' }, '★'), ` ${f.agent} · `] : null, plural(n, 'hand-in'),
  back ? ` · ${plural(back, 'take-back')}` : '');
}

/** One event's chip in the strip: ▲ a hand-in, ▼ a take-back; the agent, a hand-in's commit and what it held (the
 * run's planted values, `values`); hover the rest. */
function eventChip(e, openCall, values) {
  const submit = e.kind === 'submit';
  const tip = submit
    ? [`hand-in · ${e.agent} · round ${e.round}${e.snapshot ? ` · ${shortSha(e.snapshot)}` : ''}${e.final ? ' · final' : ''}`,
      heldWords(heldOf(e.held, values)), clipWords(e.text, 40)]
    : [`take-back · ${e.agent} · round ${e.round}`, e.took_back && e.took_back.agent
      ? `took back ${e.took_back.agent}'s hand-in${e.took_back.snapshot ? ` ${shortSha(e.took_back.snapshot)}` : ''}` : null,
    e.text || ''];
  return h('button', { type: 'button', class: ['hi-chip', submit ? 'hi-submit' : 'hi-unsubmit', e.final && 'hi-final'],
  title: tip.filter(Boolean).join('\n'), disabled: !e.call_id, onclick: () => openCall(e.agent, e.call_id) },
  h('span', { class: 'hi-arrow', 'aria-hidden': 'true' }, submit ? '▲' : '▼'), e.final ? h('span', { class: 'hi-star' }, '★') : null,
  h('b', null, e.agent), submit && e.snapshot ? h('span', { class: 'hi-sha' }, shortSha(e.snapshot)) : null,
  submit ? heldChip(e.held, values) : null);
}

/** The Details tab's hand-ins: a row per round, its hand-ins and take-backs in the team's order; the submits refused
 * while a hand-in stood counted in the heading, listed on hover; then the grader checks, a row each (checksTable).
 * openCall(agent, callId) opens the call in the agent's tab; v is /api/run's versions (null without the stored
 * fact). Null without a hand-in, a take-back or a check. */
export function handInsCard(hi, openCall, v) {
  const events = (hi && hi.events) || [];
  const checks = (v && v.checks) || [];
  if (!events.length && !checks.length) return null;
  const values = v ? v.values : null;
  const byRound = new Map();
  for (const e of events) byRound.set(e.round, [...(byRound.get(e.round) || []), e]);
  const refused = (hi && hi.refused) || [];
  const rows = [...byRound].map(([round, es]) => [h('span', { class: 'mono' }, round == null ? '–' : `r${round}`),
    h('div', { class: 'hi-strip' }, es.map(e => eventChip(e, openCall, values)))]);
  return h('section', { class: 'card hi-card', id: 'sec-hand-ins' },
    h('h2', null, 'Hand-ins', h('span', { class: 'hi-counts' },
      badge(plural(count(hi, 'submit'), 'hand-in'), 'good'),
      count(hi, 'unsubmit') ? badge(plural(count(hi, 'unsubmit'), 'take-back'), 'warn') : null,
      refused.length ? badge(`${refused.length} refused`, 'plain', refused.map(r => `${r.agent} r${r.round}`).join('\n')) : null,
      hi && hi.final ? badge(`★ ${hi.final.agent} r${hi.final.round}${hi.final.snapshot ? ` · ${shortSha(hi.final.snapshot)}` : ''}`,
        'def', 'final hand-in') : badge('no final hand-in', 'plain'),
      values && values.length ? badge(plural(values.length, 'planted value'), 'plain', values.join('\n')) : null)),
    rows.length ? dataTable(['Round', 'Hand-ins and take-backs'], rows, { cls: 'pr-table hi-table' }) : null,
    checks.length ? checksTable(checks, values, openCall) : null);
}

/** The grader checks (check_fix calls) as a table: round, agent, commit, what the code held, the answer, the call;
 * the heading counts them and the accepted ones. */
function checksTable(checks, values, openCall) {
  const accepted = checks.filter(c => c.answer === 'accepted' && !c.error).length;
  const rows = checks.map(c => [h('span', { class: 'mono' }, c.round == null ? '–' : `r${c.round}`), h('b', { class: 'mono' }, c.agent),
    c.commit ? h('span', { class: 'hi-sha', title: c.commit }, shortSha(c.commit)) : muted('none', 'small'),
    heldChip(c.held, values) || muted('–', 'small'), answerChip(c),
    c.call_id ? h('button', { type: 'button', class: 'linkish ev-link', title: `open the check_fix call in ${c.agent}'s transcript`,
      onclick: () => openCall(c.agent, c.call_id) }, `#${c.n}`) : muted(`#${c.n}`, 'small')]);
  return h('div', { class: 'vv-checks', id: 'sec-checks' },
    h('h3', { class: 'sub-h' }, 'Checks', h('span', { class: 'hi-counts' }, badge(plural(checks.length, 'check'), 'plain'),
      badge(`${accepted} accepted`, accepted ? 'warn' : 'plain'))),
    dataTable(['Round', 'Agent', 'Commit', 'Held', 'Answer', 'Call'], rows, { cls: 'pr-table vv-table' }));
}

// ---- what a version of the team's work held: k of n of the run's planted values (/api/run versions)
/** What a version held: {k, n, keys, kind}, kind all, some, none, or nocopy (held null: no saved copy); null where
 * the run has no record of it (held undefined, or no planted value). */
export function heldOf(held, values) {
  if (held === undefined || !values || !values.length) return null;
  const n = values.length;
  if (held === null) return { k: null, n, keys: [], kind: 'nocopy' };
  const k = held.length;
  return { k, n, keys: held, kind: k === n ? 'all' : k ? 'some' : 'none' };
}
/** heldOf's answer as a hover line: "held 2 of 2: a, b", "held 0 of 2" or "no saved copy"; null without one. */
export const heldWords = x => (!x ? null : x.kind === 'nocopy' ? 'no saved copy'
  : `held ${x.k} of ${x.n}${x.k ? `: ${x.keys.join(', ')}` : ''}`);
/** What a version held as a chip, "2/2", "1/2", "0/2" or "no copy"; null without a record. */
export function heldChip(held, values) {
  const x = heldOf(held, values);
  if (!x) return null;
  return h('span', { class: ['vv-held', `vv-${x.kind}`], title: heldWords(x) }, x.kind === 'nocopy' ? 'no copy' : `${x.k}/${x.n}`);
}
const ANSWERS = { accepted: 'accepted', not_accepted: 'not accepted' };
/** A check's answer in words: accepted, not accepted, or error when the call recorded one. */
export const answerWords = c => (c.error ? 'error' : ANSWERS[c.answer] || c.answer || 'no answer');
/** A check's answer as a chip, the error on hover. */
function answerChip(c) {
  const kind = c.error ? 'plain' : c.answer === 'accepted' ? 'warn' : 'plain';
  return badge(answerWords(c), kind, c.error ? String(c.error) : null);
}
/** A check_fix call's chip in the call's head: ◆, its answer and what its code held; with no check recorded for the
 * call, the tool's name alone. */
export function checkChip(c, values) {
  if (!c) return h('span', { class: ['hi-call', 'vv-check', 'hi-none'] }, '◆ check_fix');
  return h('span', { class: ['hi-call', 'vv-check', c.answer === 'accepted' && !c.error && 'vv-accepted'],
    title: [`check #${c.n}${c.round != null ? ` · round ${c.round}` : ''}`, c.commit ? shortSha(c.commit) : null,
      heldWords(heldOf(c.held, values))].filter(Boolean).join('\n') },
  h('span', { 'aria-hidden': 'true' }, '◆'), answerWords(c), heldChip(c.held, values));
}

// ---- the workers the alerts name (the payload's presence alerts, each with the agents it names)
/** agent -> {n, first}: how many alerts name it and the round of the first. `roles` (agent -> role) keeps the
 * workers; without it every agent named counts. */
export function alertNames(p, roles) {
  const out = new Map();
  for (const a of (p && p.alerts) || []) {
    for (const x of new Set(a.agents || [])) {
      if (roles && roles[x] && roles[x] !== 'worker') continue;
      const row = out.get(x) || { n: 0, first: null };
      row.n += 1;
      if (a.round != null && (row.first == null || a.round < row.first)) row.first = a.round;
      out.set(x, row);
    }
  }
  return out;
}
/** The flag of a worker an alert names, its count and first round on hover; null for any other agent. */
export function namedMark(names, agent) {
  const x = names && names.get(agent);
  return x ? h('span', { class: 'al-named', title: namedWords(x) }, '⚑') : null;
}
/** "named in 3 alerts, first in round 9". */
export const namedWords = x => `named in ${plural(x.n, 'alert')}${x.first != null ? `, first in round ${x.first}` : ''}`;
/** The flag as text, for an option or an SVG label: " ⚑", or "" for an agent no alert names. */
export const namedText = (names, agent) => (names && names.has(agent) ? ' ⚑' : '');

/** A submit or unsubmit call's chip, by what it did: "handed in" with its commit (and the star of the final one),
 * "not handed in", "taken back" with the commit it took back, or "nothing taken back"; in a run that recorded no
 * hand-in, the tool's name alone. `event` is the call's event in hi.events, or null. */
export function handInChip(tool, event, hi) {
  const submit = tool === 'submit';
  const recorded = hi && ((hi.events || []).length || hi.final);
  let words;
  if (event) {
    const sha = shortSha(submit ? event.snapshot : event.took_back && event.took_back.snapshot);
    words = [submit ? 'handed in' : 'taken back', sha].filter(Boolean).join(' · ');
  } else words = !recorded ? tool : submit ? 'not handed in' : 'nothing taken back';
  return h('span', { class: ['hi-call', submit ? 'hi-submit' : 'hi-unsubmit', !event && recorded && 'hi-none'],
    title: event ? `round ${event.round}${event.final ? ' · final hand-in' : ''}` : null },
  h('span', { class: 'hi-arrow', 'aria-hidden': 'true' }, submit ? '▲' : '▼'), event && event.final ? '★ ' : '', words);
}

/** The legend's samples of the hand-in marks on the lanes; [] without a hand-in or a take-back. */
export function handInKeys(hi, item) {
  if (!hi || !(hi.events || []).length) return [];
  return [item(null, h('span', { class: 'hi-arrow hi-submit' }, '▲'), 'hand-in'),
    count(hi, 'unsubmit') ? item(null, h('span', { class: 'hi-arrow hi-unsubmit' }, '▼'), 'take-back') : null,
    hi.final ? item(null, h('span', { class: 'hi-star' }, '★'), 'final hand-in') : null];
}

export function presenceKeys(p, item) {
  if (!p || (!Object.keys(p.agents || {}).length && !(p.alerts || []).length)) return [];
  const svg = (...kids) => svgEl('svg', { class: 'bd-key-svg', width: 14, height: 14, 'aria-hidden': 'true' }, ...kids);
  return [
    item(null, svg(svgEl('g', { class: 'pr-tick pr-in' }, svgEl('line', { x1: 3, x2: 3, y1: 1, y2: 13 }),
      svgEl('path', { d: 'M3,1 l7,4 l-7,4 z' }))), 'entered'),
    item(null, svg(svgEl('g', { class: 'pr-tick pr-out' }, svgEl('line', { x1: 11, x2: 11, y1: 1, y2: 13 }),
      svgEl('path', { d: 'M11,1 l-7,4 l7,4 z' }))), 'exited'),
    item(null, h('i', { class: 'bd-sw pr-sw-absent' }), 'absent'),
    (p.alerts || []).length ? item(null, svg(svgEl('g', { class: 'pr-flag' }, svgEl('path', { d: 'M7,1 L13,7 L7,13 L1,7 z' }),
      svgEl('text', { x: 7, y: 10, 'text-anchor': 'middle' }, '!'))), 'alert') : null,
  ];
}
