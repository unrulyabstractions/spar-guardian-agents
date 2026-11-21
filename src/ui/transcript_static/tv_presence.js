/* Who entered a run and who left it, and each influencer's alert (alert_humans), as the run page draws them. The data
 * is the payload's presence (src/ui/transcript_presence.py): /api/run's for the bar and the Details tab, /api/board's,
 * placed on the board's clock, for the Board tab's lanes.
 *
 *   presenceChip(step.extra.swarm_presence)      // a transcript's entered or exited line, as a chip in its header
 *   alertsBadge(p.alerts, onclick)                // the bar's badge, null without an alert
 *   alertsCard(p, roles, openCall), presenceCard(p, roles)    // the Details tab's two cards, null when empty
 *   laneMarks(p, lay)                             // the Board tab: {g, tipOf(el), callOf(el)}
 *   presenceKeys(p, item)                         // the Board tab's legend samples
 *
 * On a lane: the stretch before the agent entered and after it exited is hatched; a tick marks the entry (at the lane's
 * start for an agent present from the first round) and the exit,
 * the exit's reason beside it where the lane has room. An alert is a line across the whole timeline in the bad colour,
 * a flag on the board lane, and a larger one on the top edge of the alerting agent's lane when it is one of the two
 * shown; its hover gives the reason clipped to REASON_WORDS words, the agents it names and its outcome. The reason
 * shows whole in the Details tab. Every string goes in through h() and svgEl() text nodes. */
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

/** An alert's outcome as a badge: true alarm, false alarm, or unknown. */
export function outcomeBadge(a) {
  const named = Object.entries(a.named_cheated_by_then || {}).map(([x, v]) => `${x} ${v == null ? '?' : v ? 'cheated' : 'clean'}`);
  const tip = [`team ${a.team_cheated_by_then == null ? '?' : a.team_cheated_by_then ? 'cheated' : 'clean'} by then`, ...named].join(' · ');
  const [word, kind] = OUTCOME[a.outcome] || ['unknown', 'plain'];
  return badge(word, kind, tip);
}

/** A transcript's entered or exited line (its step's extra.swarm_presence) as a chip for its header. */
export function presenceChip(p) {
  if (!p || !p.agent) return null;
  const gone = p.kind === 'exited';
  const words = gone ? `${p.agent} exited${p.why ? ` · ${WHY_WORDS[p.why] || p.why}` : ''}` : `${p.agent} entered`;
  return h('span', { class: ['pr-chip', gone ? 'pr-chip-out' : 'pr-chip-in', p.why === 'alert' && 'pr-chip-alert'],
    title: p.round != null ? `round ${p.round}` : null }, words, p.round != null ? h('span', { class: 'pr-round' }, `r${p.round}`) : null);
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

/** The Details tab's entries and exits: each agent's entry round, exit round and reason. Null when the run recorded
 * none. */
export function presenceCard(p, roles) {
  const agents = Object.entries((p && p.agents) || {});
  if (!agents.length) return null;
  const rows = agents.map(([a, x]) => [h('b', null, a), roleOf(roles, a), x.entered != null ? String(x.entered) : null,
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
 * shown), laneY, laneH, yBoard, boardH, yTop, yBottom, x0, x1, xOfCall(agent, k, end) (null for a call not on the
 * lanes), xOfTime(t)}. Returns {g, tipOf(el), callOf(el)}: the SVG group, the hover lines of a mark ([data-pr]) and the
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
  // the alerts: a line across the timeline, a flag on the board lane, and a flag on the alerting agent's lane
  for (const a of p.alerts || []) {
    const side = lay.sides.find(s => lay.agentOf(s) === a.agent);
    const x = side && a.k != null ? lay.xOfCall(a.agent, a.k, false) : a.t != null ? lay.xOfTime(a.t) : null;
    if (x == null || !Number.isFinite(x)) continue;
    const [outcome] = OUTCOME[a.outcome] || ['outcome unknown'];
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
      flag(lay.yBoard + 10, false),
      side ? flag(lay.laneY[side], true) : null), item));
  }
  const of = el => items[Number(el.getAttribute('data-pr'))] || null;
  return { g, tipOf: el => (of(el) || { lines: [] }).lines, callOf: el => (of(el) || {}).call || null };
}

/** The legend's samples of the presence marks, given the Board tab's legend item maker; [] when the run recorded no
 * entry, exit or alert. */
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
