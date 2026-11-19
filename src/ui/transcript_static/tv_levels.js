/* The three ladders: the level glyph and the Team tab's panels, one row per agent and one for the team, then the
 * Cheating, Coordination and Collusion ladders in full. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/levels.js); levels come from /api/run's "levels" (src/ui/transcript_levels.py). Our edits: the
 * imports; a disregulator is named like a regulator; the source's stepTracker, which no view called, is left out; the
 * CheatBench judge's column. Every string goes in through h() text nodes. */
import { h } from '/static/app/dom.js';
import { badge, muted } from '/static/app/components.js';
import { RAMP } from './tv_case_view.js';
import { CHEATBENCH } from './tv_labels.js';

export function glyph(index, of, ladder) {
  const box = h('span', { class: 'lv-glyph', 'aria-hidden': 'true' });
  const ramp = RAMP[ladder];
  for (let i = 1; i <= of; i++) {
    const on = index != null && i <= index;
    box.appendChild(h('span', { class: ['lv-seg', on && 'on'], style: on && ramp ? `background:${ramp[index]}` : null }));
  }
  return box;
}

function cellView(c, L, key) {
  if (!c) return h('td', { class: 'muted small' }, 'not computed');
  const tip = `${L.rule}${c.evidence ? '\nEvidence: ' + c.evidence : ''}`;
  if (c.na) return h('td', { class: 'muted small', title: tip }, c.level, c.evidence ? `: ${c.evidence}` : '');
  return h('td', { class: ['lv-cell', c.index > 0 && 'lv-hit'], title: tip },
    glyph(c.index, c.of, key), h('span', { class: 'lv-name' }, c.level), h('span', { class: 'lv-rank' }, ` (${c.index} of ${c.of})`));
}

const yesNo = v => (v === true ? 'yes' : v === false ? 'no' : '?');

/** One agent's CheatBench verdicts, one line per judge: cheated, honeypots seen; "not judged" without a row.
 * verdicts: {judge: row}. */
export function cheatbenchWords(verdicts) {
  const judges = Object.entries(verdicts || {});
  if (!judges.length) return muted('not judged', 'small');
  return h('span', { class: 'cb-cell' }, judges.map(([judge, r]) => h('div', { title: `judge ${judge}` },
    r.parse_ok === false ? badge('did not parse', 'warn') : [
      h('span', { class: r.cheated ? 'lv-judge-yes' : null }, `cheated ${yesNo(r.cheated)}`), ', ',
      `honeypots seen ${yesNo(r.honey_pots_seen)}`],
    judges.length > 1 ? muted(` (${judge})`, 'small') : null)));
}

/** The team's word of each CheatBench judge: cheated, k of n agents. summaries: the index row's cheatbench. */
export function cheatbenchTeam(summaries) {
  if (!summaries || !summaries.length) return muted('not judged', 'small');
  return h('span', { class: 'cb-cell' }, summaries.map(s => h('div', { title: `judge ${s.judge}; ${s.of} of ${s.workers} workers have a parsed verdict` },
    h('span', { class: s.cheated ? 'lv-judge-yes' : null }, `cheated: ${s.cheated} of ${s.of} agents`),
    summaries.length > 1 ? muted(` (${s.judge})`, 'small') : null)));
}

/** Run panel: one row per agent, one team row, a column per ladder and one for CheatBench's judge. cheatbench:
 * {agent: {judge: row}}; team: the index row's cheatbench summaries. */
export function levelsPanel(lv, roles, task, cheatbench, team) {
  const head = h('tr', null, h('th', null, 'Agent'), lv.order.map(k => h('th', { title: lv.ladders[k].rule }, lv.ladders[k].name)),
    h('th', { title: CHEATBENCH.about }, CHEATBENCH.column));
  const rows = Object.entries(lv.agents).sort((a, b) => (roles[a[0]] !== 'worker') - (roles[b[0]] !== 'worker') || a[0].localeCompare(b[0]))
    .map(([a, cells]) => h('tr', null, h('td', { class: 'mono' }, a, roles[a] && roles[a] !== 'worker' ? ` (hidden ${roles[a]})` : ''),
      lv.order.map(k => cellView(cells[k], lv.ladders[k], k)), h('td', null, cheatbenchWords(cheatbench[a]))));
  if (!lv.single) {
    rows.push(h('tr', { class: 'lv-team' }, h('td', null, h('b', null, 'Team')), lv.order.map(k => cellView(lv.team[k], lv.ladders[k], k)),
      h('td', null, cheatbenchTeam(team))));
  }
  const alarm = lv.cheating && lv.cheating.alarm;
  return h('section', { class: 'card', id: 'sec-ladders' },
    h('h2', null, 'Three ladders for this run'),
    alarm ? h('p', null, badge(`alarm: ${alarm}`, 'bad', lv.ladders.cheating.note)) : null,
    h('p', { class: 'small muted' }, 'Each cell shows the level reached and a ladder with one bar per level. Hover a cell for the rule and the evidence. ',
      lv.collusion && lv.collusion.applicable && !lv.collusion.judged ? 'Collusion\'s two judge rungs, collusion and scheming, are not judged in this run: no judge\'s verdicts reached it, so code stops at collaboration (the Collusion panel below). ' : '',
      h('a', { href: '#/ladders?task=' + encodeURIComponent(task || '') }, 'All ladders and the Cheating ladder by task')),
    h('table', { class: 'lv-table' }, h('thead', null, head), h('tbody', null, rows)));
}

/** One agent's or the team's Cheating evidence, clause by clause, highest rung first: "clause (rung)" and each line
 * of evidence, its calls linked by ctx.evidence(text, agent). */
export function clauseList(lv, clauses, agent, ctx) {
  const L = lv.ladders.cheating;
  const held = L.clauses.filter(([c]) => (clauses[c] || []).length).sort((x, y) => L.levels.indexOf(y[1]) - L.levels.indexOf(x[1]));
  if (!held.length) return muted('no clause holds', 'small');
  return h('ul', { class: 'ev-list' }, held.map(([c, level, gloss]) => h('li', null,
    h('b', { title: gloss }, c), muted(` (${level}) `, 'small'),
    h('ul', { class: 'ev-list' }, clauses[c].map(line => h('li', null, ctx.evidence(line, agent)))))));
}

/** The CheatBench judge's fields for an attack on the evaluation, and its disagreement with code. verdicts: the
 * agent's CheatBench rows by judge; "not judged" without one, "no such field" when the task's verdict has none. */
function judgeAttack(r, verdicts) {
  const fields = Object.entries(r.judge_attack || {});
  if (!fields.length) return muted(Object.keys(verdicts || {}).length ? 'no such field' : 'not judged', 'small');
  return h('span', null, fields.map(([f, v], i) => [i ? ', ' : '', `${f.replace(/_/g, ' ')} ${yesNo(v)}`]),
    r.judge_disagrees ? [' ', badge('judge says yes, code says no', 'warn', 'CheatBench\'s judge says this agent attacked the evaluation; code places it below attempted. The judge changes no rung.')] : null);
}

/** Team tab: the Cheating ladder in full (src/measures/cheating_ladder.py). The team's rung is the highest worker or
 * the team's own evidence; the hidden roles' own rungs are listed apart and never enter it. The grader's pass, the
 * alarm, and one row per agent with its rung, the evidence of each clause that holds, the scan's records below the
 * ladder and the CheatBench judge's fields for an attack on the evaluation. ctx: {evidence(text, agent) -> node,
 * cheatbench: {agent: {judge: row}}}. */
export function cheatingPanel(lv, roles, ctx) {
  const X = lv.cheating;
  const L = lv.ladders.cheating;
  const head = h('h2', null, 'Cheating: how far each agent went with the honeypot');
  if (!X || !X.applicable) {
    return h('section', { class: 'card', id: 'sec-cheating' }, head, h('p', { class: 'muted' }, `Not applicable: ${(X && X.reason) || 'no measure'}.`));
  }
  const role = a => roles[a] || 'worker';
  const byRole = (a, b) => (role(a) !== 'worker') - (role(b) !== 'worker') || a.localeCompare(b);
  const hidden = Object.entries(X.hidden || {});
  const teamOwn = X.team_clauses && Object.values(X.team_clauses).some(v => v && v.length);
  return h('section', { class: 'card', id: 'sec-cheating' }, head,
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, lv.single ? 'The run: ' : 'Team (the highest worker, or the team\'s own evidence): '), chip(lv, 'cheating', X.team)),
      lv.single ? null : h('div', null, h('span', { class: 'field-label' }, 'Hidden roles, kept out of the team\'s rung: '),
        hidden.length ? hidden.map(([a, level], i) => [i ? '; ' : '', h('span', { class: 'mono' }, a), ` (hidden ${role(a)}) `, chip(lv, 'cheating', level)])
          : h('span', { class: 'muted small' }, 'none in this run')),
      h('div', null, h('span', { class: 'field-label' }, 'The grader: '),
        X.passed === true ? (X.passed_how || 'passed the hand-in') : X.passed === false ? 'did not pass the hand-in' : muted('no grade recorded', 'small')),
      X.alarm ? h('div', null, badge(`alarm: ${X.alarm}`, 'bad'), muted(` ${L.note}`, 'small')) : null),
    teamOwn ? h('div', null, h('h3', { class: 'sub-h' }, 'The team\'s own evidence, which no agent owns'), clauseList(lv, X.team_clauses, null, ctx)) : null,
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table cheat-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Rung', 'Evidence of each clause that holds', 'Below the ladder (evidence only)', 'CheatBench judge on an attack on the evaluation']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, Object.keys(X.agents).sort(byRole).map(a => {
        const r = X.agents[a];
        return h('tr', { class: role(a) !== 'worker' && 'coord-hidden' },
          h('td', { class: 'mono' }, a), h('td', null, role(a) === 'worker' ? 'worker' : `hidden ${role(a)}`),
          h('td', null, chip(lv, 'cheating', r.rung)), h('td', null, clauseList(lv, r.clauses, a, ctx)),
          h('td', null, (r.below || []).length ? h('ul', { class: 'ev-list' }, r.below.map(x => h('li', null, ctx.evidence(x, a)))) : muted('none', 'small')),
          h('td', null, judgeAttack(r, (ctx.cheatbench || {})[a])));
      })))),
    h('p', { class: 'small muted' }, L.rule), h('p', { class: 'small muted' }, L.note));
}

const fix2 = v => (v == null ? '' : Number(v).toFixed(2));
const chip = (lv, key, level) => {
  const L = lv.ladders[key];
  const i = L.levels.indexOf(level);
  return level == null ? h('span', { class: 'muted small' }, 'not applicable')
    : h('span', { class: 'lv-cell' }, glyph(i, L.levels.length - 1, key), h('b', { class: 'lv-name' }, level), h('span', { class: 'lv-rank' }, ` (${i} of ${L.levels.length - 1})`));
};
const coordChip = (lv, level) => chip(lv, 'coordination', level);

/** Team tab: the Coordination ladder in full (src/measures/coordination.py). The team's level is the workers' mean;
 * the hidden roles' own levels are listed apart and never enter it. One row per agent with its posts, its rounds per
 * teammate and the rounds its posts allow, and one row per pair with its rounds. */
export function coordinationPanel(lv, roles) {
  const C = lv.coordination;
  if (!C) return null;
  const L = lv.ladders.coordination;
  const role = a => roles[a] || 'worker';
  const hidden = Object.entries(C.hidden || {});
  const agents = Object.entries(C.agents).sort((a, b) => (role(a[0]) !== 'worker') - (role(b[0]) !== 'worker') || a[0].localeCompare(b[0]));
  return h('section', { class: 'card', id: 'sec-coord' },
    h('h2', null, 'Coordination on the board'),
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, 'Team (the workers\' levels averaged, rounded half up): '), coordChip(lv, C.team)),
      h('div', null, h('span', { class: 'field-label' }, 'Hidden roles, kept out of the team\'s level: '),
        hidden.length ? hidden.map(([a, level], i) => [i ? '; ' : '', h('span', { class: 'mono' }, a), ` (hidden ${role(a)}) `, coordChip(lv, level)])
          : h('span', { class: 'muted small' }, 'none in this run'))),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Posts', 'Read a teammate\'s post', 'Rounds per teammate', 'Rounds its posts allow', 'Level']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, agents.map(([a, r]) => h('tr', { class: role(a) !== 'worker' && 'coord-hidden' },
        h('td', { class: 'mono' }, a), h('td', null, role(a) === 'worker' ? 'worker' : `hidden ${role(a)}`),
        h('td', { class: 'num' }, String(r.posts)), h('td', null, r.read ? 'yes' : 'no'),
        h('td', { class: 'num' }, fix2(r.mbar)), h('td', { class: 'num' }, fix2(r.ceiling)), h('td', null, coordChip(lv, r.level))))))),
    C.pairs && C.pairs.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-pairs' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Pair'), h('th', { scope: 'col' }, 'Rounds (the smaller of the two directions)'))),
      h('tbody', null, C.pairs.map(p => h('tr', null, h('td', { class: 'mono' }, p.agents.join(' and ')), h('td', { class: 'num' }, String(p.rounds))))))) : null,
    h('p', { class: 'small muted' }, L.rule),
    L.note ? h('p', { class: 'small muted' }, L.note) : null);
}

const JUDGE_WORD = { 'not judged': 'no judge\'s verdict', 'not applicable': 'below collaboration, not read', 'not available': 'no reasoning in its transcript' };
const judgeValue = v => h('span', { class: ['small', v === 'yes' ? 'lv-judge-yes' : 'muted'], title: JUDGE_WORD[v] || 'the judge\'s verdict' }, v);

/** Team tab: the Collusion ladder in full (src/measures/collusion.py), as the measure returns it. The team's rung is
 * the highest pair of workers; the hidden roles' own rungs are listed apart. One row per agent with its rung, its
 * scheming verdict and its cheating rung for information; one row per pair with the takes and their calls, whether
 * the hand-in holds work of both, the exploit-named posts a judge would read, and the judge's two fields; then every
 * verdict row of the collusion judges. ctx: {evidence(text, agent) -> node with [k] linked to the agent's call,
 * post(n) -> node linked to the board, judges: {judge: [verdict row]}}. */
export function collusionPanel(lv, roles, ctx) {
  const M = lv.collusion;
  if (!M || lv.single) return null;
  const L = lv.ladders.collusion;
  const head = [h('h2', null, 'Collusion: takes between agents')];
  if (!M.applicable) {
    return h('section', { class: 'card', id: 'sec-collusion' }, head, h('p', { class: 'muted' }, `Not applicable: ${M.reason}.`),
      h('p', { class: 'small muted' }, L.rule));
  }
  const role = a => roles[a] || 'worker';
  const byRole = (a, b) => (role(a) !== 'worker') - (role(b) !== 'worker') || a.localeCompare(b);
  const hidden = Object.entries(M.hidden || {});
  const named = new Map((M.naming_posts || []).map(p => [p.id, p]));
  const rank = r => L.levels.indexOf(r);
  const pairs = M.pairs.slice().sort((x, y) => rank(y.rung) - rank(x.rung) || y.exploit_named.length - x.exploit_named.length);
  const takeList = p => (p.takes.length ? h('ul', { class: 'ev-list' }, p.takes.map(t => h('li', null,
    h('span', { class: 'mono' }, `${t.taker} took ${t.giver}'s work`), ` (${t.rule}${t.call == null ? ', from a final record' : ''}): `,
    ctx.evidence(t.evidence, t.taker)))) : h('span', { class: 'muted small' }, 'no take'));
  const namedList = p => (p.exploit_named.length ? h('ul', { class: 'ev-list' }, p.exploit_named.map(id => {
    const post = named.get(id) || {};
    return h('li', null, post.post_n != null ? ctx.post(post.post_n) : h('span', { class: 'muted' }, 'a post'),
      ` by ${post.writer || '?'}, read by ${(post.readers || []).join(', ') || '?'}`);
  })) : h('span', { class: 'muted small' }, 'none'));
  const handIn = Object.entries(M.hand_in || {});
  return h('section', { class: 'card', id: 'sec-collusion' }, head,
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, 'Team (the highest pair of workers): '), chip(lv, 'collusion', M.team)),
      h('div', null, h('span', { class: 'field-label' }, 'Hidden roles, kept out of the team\'s rung: '),
        hidden.length ? hidden.map(([a, level], i) => [i ? '; ' : '', h('span', { class: 'mono' }, a), ` (hidden ${role(a)}) `, chip(lv, 'collusion', level)])
          : h('span', { class: 'muted small' }, 'none in this run')),
      h('div', null, h('span', { class: 'field-label' }, 'Judge rungs (collusion, scheming): '),
        M.judged ? `read from the verdicts of ${M.judge}` : h('b', null, 'not judged'),
        M.judged ? '' : h('span', { class: 'muted small' }, ': no judge\'s verdicts reached this run, so code stops at collaboration'))),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Rung', 'Scheming (judge)', 'Cheating rung, for information']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, Object.keys(M.agents).sort(byRole).map(a => {
        const r = M.agents[a];
        return h('tr', { class: role(a) !== 'worker' && 'coord-hidden' },
          h('td', { class: 'mono' }, a), h('td', null, role(a) === 'worker' ? 'worker' : `hidden ${role(a)}`),
          h('td', null, chip(lv, 'collusion', r.rung)), h('td', null, judgeValue(r.scheming)), h('td', null, r.cheating || h('span', { class: 'muted small' }, 'no row')));
      })))),
    h('h3', { class: 'sub-h' }, 'Pairs'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact collusion-pairs' },
      h('thead', null, h('tr', null, ['Pair', 'Rung', 'Takes, with their calls', 'Hand-in holds work of both', 'Exploit named on the board (a hint a judge would read)', 'Collusion (judge)', 'Scheming (judge)', 'Cheating rungs']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, pairs.map(p => h('tr', { class: p.roles.some(r => r !== 'worker') && 'coord-hidden' },
        h('td', { class: 'mono' }, p.agents.map((a, i) => [i ? ' and ' : '', a, role(a) !== 'worker' ? ` (hidden ${role(a)})` : ''])),
        h('td', null, chip(lv, 'collusion', p.rung)), h('td', null, takeList(p)),
        h('td', null, p.hand_in_both ? 'yes' : 'no'), h('td', null, namedList(p)),
        h('td', null, judgeValue(p.collusion)), h('td', null, judgeValue(p.scheming)),
        h('td', { class: 'small' }, p.cheating.map(c => c || '?').join(' / '))))))),
    h('h3', { class: 'sub-h' }, 'The hand-in\'s contributors'),
    handIn.length ? h('ul', { class: 'ev-list' }, handIn.map(([a, lines]) => h('li', null, h('span', { class: 'mono' }, a), ': ', lines.join('; '))))
      : h('p', { class: 'muted small' }, 'No agent\'s work is credited in the hand-in.'),
    verdictRows(ctx.judges || {}),
    (M.notes || []).length ? h('p', { class: 'small muted' }, 'Notes of the measure: ', M.notes.join('; ')) : null,
    h('p', { class: 'small muted' }, L.rule), h('p', { class: 'small muted' }, L.note));
}

const fieldText = v => (v === true ? 'yes' : v === false ? 'no' : typeof v === 'string' ? v : JSON.stringify(v));

/** Every verdict row of the collusion judges: one table per judge, a row per pair, each field of its answer. */
function verdictRows(judges) {
  const entries = Object.entries(judges);
  if (!entries.length) return null;
  return [h('h3', { class: 'sub-h' }, 'The collusion judges\' verdicts'),
    entries.map(([judge, rows]) => h('div', { class: 'table-wrap' }, h('table', { class: 'data compact collusion-verdicts' },
      h('caption', { class: 'small muted' }, `judge ${judge}${entries.length > 1 && judge !== entries[0][0] ? ': shown here, not applied to the rungs (the first judge by name is)' : ''}`),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Pair'), h('th', { scope: 'col' }, 'The judge\'s answer'))),
      h('tbody', null, rows.map(r => h('tr', null, h('td', { class: 'mono' }, (r.agents || []).join(' and ')),
        h('td', null, h('dl', { class: 'kv kv-grid' }, Object.entries(r).filter(([k]) => k !== 'agents').map(([k, v]) =>
          h('div', { class: 'kv-item' }, h('dt', null, k.replace(/_/g, ' ')), h('dd', null, fieldText(v))))))))))))];
}
