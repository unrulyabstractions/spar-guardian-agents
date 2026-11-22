/* Each regulator's measures, as the run page's Details tab draws them. The data is /api/run's regulators
 * (the code record's team row, src/measures/regulator_measures.py).
 *
 *   regulatorsCard(regs, openCall)     // one card for the run's regulators, null without one
 *
 * Chips and values only; each tooltip is a short label. openCall(agent, k) opens the regulator's call k in its tab. */
import { h } from '/static/app/dom.js';
import { badge, dataTable, muted } from '/static/app/components.js';

const at = w => (w ? `r${w.round ?? '?'} · call ${w.k}` : null);

function callChip(label, when, agent, openCall, tip) {
  if (!when) return badge(`${label} none`, 'plain', tip);
  return h('button', { type: 'button', class: ['badge', 'b-def', 'linkish'], title: tip,
    onclick: () => openCall(agent, when.k) }, `${label} ${at(when)}`);
}

function handInChip(m) {
  if (m.hand_in_tainted === false) return badge('hand-in clean', 'good', 'no honeypot piece in the hand-in');
  if (m.hand_in_tainted == null) return badge('hand-in ?', 'plain', 'not measured');
  if (m.tainted_after_sight) return badge('tainted hand-in after sight', 'bad', 'a piece reached the hand-in after the regulator saw the taint');
  return m.saw_taint ? badge('tainted hand-in before sight', 'bad', 'the hand-in came before the regulator saw the taint')
    : badge('tainted hand-in unseen', 'bad', 'a piece reached the hand-in; the regulator never saw the taint');
}

function regulatorRows(agent, m, openCall) {
  const o = m.oversight || {};
  const most = Math.max(0, ...(o.rounds || []).map(r => r[1] + r[2]));
  const chips = h('div', { class: 'verdict-flags rg-chips' },
    callChip('first post', m.first_post, agent, openCall, 'its first board post'),
    callChip('first accusation', m.first_targeted_post, agent, openCall, 'first post naming a worker and an act'),
    badge(`${m.true_accusations} true`, m.true_accusations ? 'good' : 'plain', 'the named worker made the act'),
    badge(`${m.false_accusations} false`, m.false_accusations ? 'warn' : 'plain', 'the named worker never made the act'),
    callChip('saw taint', m.saw_taint, agent, openCall, 'first oversight result showing a tainted call'),
    handInChip(m),
    badge(`${m.pushes} pushes · ${m.reverts} reverts`, 'plain', 'its pushes to the team remote, its reverts and resets'),
    badge(`${m.alerts} alerts · ${m.true_alarms} true · ${m.false_alarms} false`, m.alerts ? 'def' : 'plain',
      'alert_humans calls against the Cheating ladder'),
    badge(`peer ${o.peer || 0} · peer_shell ${o.peer_shell || 0}`, 'plain', 'oversight calls in all'),
    badge(`max ${most} per round`, 'plain', 'most oversight calls in one round'),
    m.order === 'rounds' ? badge('order by round', 'warn', 'no replay: before means same round or earlier') : null);
  const rows = (m.accusations || []).map(a => [String(a.round ?? ''),
    h('button', { type: 'button', class: 'linkish ev-link', title: 'open the post', onclick: () => openCall(agent, a.k) }, `call ${a.k}`),
    h('b', null, a.agent),
    h('span', { class: 'mono small' }, (a.terms || []).join(' · ')),
    a.true ? badge('true', 'good', 'the named worker made the act') : badge('false', 'warn', 'the named worker never made the act'),
    (a.misnamed || []).length ? a.misnamed.join(', ') : null]);
  return [h('h3', { class: 'sub-h' }, agent), chips,
    rows.length ? dataTable(['Round', 'Post', 'Named', 'Act', 'Made', 'Made by'], rows, { cls: 'pr-table' })
      : muted('no accusation', 'small block')];
}

/** The Details tab's Regulator card: each regulator's chips and its accusations. Null without a regulator. */
export function regulatorsCard(regs, openCall) {
  const list = Object.entries(regs || {});
  if (!list.length) return null;
  return h('section', { class: 'card rg-card', id: 'sec-regulators' }, h('h2', null, 'Regulator'),
    list.flatMap(([agent, m]) => regulatorRows(agent, m, openCall)));
}
