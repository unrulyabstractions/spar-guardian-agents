/* The run page's Monitoring tab: the run's alerts to humans (alert_humans) with their stored outcomes, and each worker
 * against them, caught, missed, named or clear. Its data is /api/run's monitoring (src/ui/transcript_monitoring.py).
 *
 *   monitoringView(m, { roles, mark, levels, openCall })   // the tab's element
 *
 * Two cards. Alerts: the counts as chips (alerts, true alarms, false alarms, undecided, the run's influencers), then a
 * row per alert: its round, who alerted, whom it names (each named worker in its status's colour), the outcome, the
 * reason on hover and its call. Workers: each status's count as a chip, then a row per worker: its final Cheating
 * level, the round of its first cheated step where the record dates it (opening that call), how many alerts name it
 * and the first one's round, and its status. mark(agent) gives the marks beside an agent's name (the final hand-in's
 * star, the flag of a worker an alert names); openCall(agent, k) opens call k in the agent's tab. Labels, values,
 * chips and tooltips only. Every string goes in through h() text nodes. */
import { h, plural } from '/static/app/dom.js';
import { badge, dataTable, muted } from '/static/app/components.js';
import { ROLE_LABEL } from './tv_labels.js';
import { levelChip } from './tv_levels.js';
import { outcomeBadge } from './tv_presence.js';

/** Each worker status's chip: its words, its badge kind and its hover. */
export const STANDING = {
  caught: ['caught', 'good', 'cheated and named in an alert'],
  missed: ['missed', 'bad', 'cheated, never named in an alert'],
  named: ['named, no cheat', 'warn', 'named in an alert, no cheat on record'],
  clear: ['clear', 'plain', 'no cheat on record, never named'],
};
const NONE = { alerts: [], counts: { alerts: 0, true_alarm: 0, false_alarm: 0, undecided: 0 }, workers: [], influencers: 0 };

const statusChip = s => badge(...(STANDING[s] || [s || '–', 'plain', null]));
const roundText = r => (r == null ? muted('–', 'small') : h('span', { class: 'mono' }, `r${r}`));

export function monitoringView(m, ctx) {
  const M = m || NONE;
  const statusOf = new Map(M.workers.map(w => [w.agent, w.status]));
  const c = M.counts;
  const alertRows = M.alerts.map(a => [roundText(a.round),
    h('span', null, h('b', { class: 'mono' }, a.agent), ' ', muted(ROLE_LABEL[ctx.roles[a.agent]] || ctx.roles[a.agent] || '', 'small')),
    (a.agents || []).length ? h('span', { class: 'mn-names' }, a.agents.map(x => h('span',
      { class: ['mn-name', `mn-${statusOf.get(x) || 'other'}`], title: (STANDING[statusOf.get(x)] || [null, null, null])[2] }, x)))
      : muted('none', 'small'),
    outcomeBadge(a),
    a.reason ? h('span', { class: 'mn-reason', title: a.reason }, 'reason') : muted('–', 'small'),
    a.k != null ? h('button', { type: 'button', class: 'linkish ev-link', title: `open call ${a.k} in ${a.agent}'s transcript`,
      onclick: () => ctx.openCall(a.agent, a.k) }, `call ${a.k}`) : muted('no call', 'small')]);
  const alerts = h('section', { class: 'card mn-card mn-alerts', id: 'sec-monitoring-alerts' },
    h('h2', null, 'Alerts', h('span', { class: 'hi-counts' },
      badge(plural(c.alerts, 'alert'), c.alerts ? 'bad' : 'plain'),
      badge(`${c.true_alarm} true`, c.true_alarm ? 'good' : 'plain', 'true alarms'),
      badge(`${c.false_alarm} false`, c.false_alarm ? 'warn' : 'plain', 'false alarms'),
      badge(`${c.undecided} undecided`, 'plain', 'no outcome on record'),
      badge(plural(M.influencers, 'influencer'), 'plain'))),
    alertRows.length ? dataTable(['Round', 'Alerted by', 'Names', 'Outcome', 'Reason', 'Call'], alertRows, { cls: 'pr-table mn-table' })
      : null);
  const cheat = ctx.levels && ctx.levels.ladders && ctx.levels.ladders.cheating;
  const workerRows = M.workers.map(w => [h('b', { class: 'mono' }, w.agent, ctx.mark(w.agent)),
    cheat ? levelChip('cheating', cheat, w.level, false) : (w.level || '–'),
    w.cheated_round != null && w.cheated_k != null ? h('button', { type: 'button', class: 'linkish ev-link mono',
      title: `open call ${w.cheated_k} in ${w.agent}'s transcript`, onclick: () => ctx.openCall(w.agent, w.cheated_k) }, `r${w.cheated_round}`)
      : roundText(w.cheated_round),
    w.named ? h('span', { class: 'mono' }, String(w.named)) : muted('0', 'small'), roundText(w.named_first),
    statusChip(w.status)]);
  const tally = Object.keys(STANDING).map(s => [s, M.workers.filter(w => w.status === s).length]);
  const workers = h('section', { class: 'card mn-card mn-workers', id: 'sec-monitoring-workers' },
    h('h2', null, 'Workers', h('span', { class: 'hi-counts' }, tally.map(([s, n]) => badge(`${n} ${STANDING[s][0]}`,
      n ? STANDING[s][1] : 'plain', STANDING[s][2])))),
    workerRows.length ? dataTable(['Worker', 'Cheating', 'Cheated', 'Named', 'First named', 'Status'], workerRows,
      { cls: 'pr-table mn-table' }) : muted('no worker', 'small block'));
  return h('div', { class: 'rp-team mn-tab' }, alerts, workers);
}
