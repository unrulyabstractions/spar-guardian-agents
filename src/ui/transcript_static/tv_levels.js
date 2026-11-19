/* The three ladders as every page draws them, and the Team tab's panels: one row per agent and one for the team, then
 * the Cheating, Coordination and Collusion ladders in full. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/levels.js); levels come from /api/run's "levels" and /api/ladders (src/ui/transcript_levels.py).
 * Our edits: the imports; a disregulator is named like a regulator; the source's stepTracker, which no view called, is
 * left out; a level is a chip in its ladder's colour with a gap at the ladder's line (levelChip); Cheated beside the
 * Cheating rung; CheatBench's judge with its labels beside the code's clauses; disclosure beside Collusion; the
 * collusion judge's quotes as the evidence of its rungs. Every string goes in through h() text nodes. */
import { h } from '/static/app/dom.js';
import { badge, muted } from '/static/app/components.js';
import { RAMP, inkOn } from './tv_case_view.js';
import { CHEATBENCH, DISCLOSURE } from './tv_labels.js';

/** A ladder's bars: one per level above the lowest, filled up to `index`, with a gap before the ladder's line. lad:
 * the ladder's /api/ladders entry ({levels, line}). The bars take the text colour of what holds them. */
export function glyph(index, lad) {
  const line = lad.levels.indexOf(lad.line);
  const box = h('span', { class: 'lv-glyph', 'aria-hidden': 'true' });
  for (let i = 1; i < lad.levels.length; i++) {
    box.appendChild(h('span', { class: ['lv-seg', index != null && i <= index && 'on', i === line && 'at-line'] }));
  }
  return box;
}

/** A level as every page draws it: a chip in the ladder's colour at this level, its text in the colour that reads on
 * it, the bars, the name and the rank. key: the ladder's id; lad: its /api/ladders entry. */
export function levelChip(key, lad, level, rank = true) {
  const i = lad.levels.indexOf(level);
  if (i < 0) return muted(level == null ? 'not applicable' : level, 'small');
  const bg = RAMP[key][i];
  const of = lad.levels.length - 1;
  const side = i >= lad.levels.indexOf(lad.line) ? 'above' : 'below';
  return h('span', { class: 'lv-chip', style: `background:${bg};color:${inkOn(bg)}`,
    title: `${lad.name}: ${level} (${i} of ${of}), ${side} the line (${lad.line_words})` },
  glyph(i, lad), h('b', null, level), rank ? h('span', { class: 'lv-rank' }, ` (${i} of ${of})`) : null);
}

/** The mark legends put between the two sides of a ladder's line. */
export const lineMark = lad => h('span', { class: 'lv-line-mark', title: lad.line_words }, 'line');

/** Cheated beside a Cheating rung: the measure's word (src/measures/cheating_ladder.py cheated), nothing without one. */
export const cheatedTag = v => (v == null ? null : h('span', { class: ['lv-cheated', v ? 'yes' : 'no'],
  title: 'cheated: the Cheating rung is attempted or above' }, v ? 'cheated: yes' : 'cheated: no'));

function cellView(c, L, key) {
  const tip = `${L.rule}${c.evidence ? '\nEvidence: ' + c.evidence : ''}`;
  if (c.na) return h('td', { class: 'muted small', title: tip }, c.level, c.evidence ? `: ${c.evidence}` : '');
  return h('td', { class: 'lv-cell', title: tip }, levelChip(key, L, c.level), key === 'cheating' ? [' ', cheatedTag(c.cheated)] : null);
}

const yesNo = v => (v === true ? 'yes' : v === false ? 'no' : '?');

/** One agent's CheatBench verdicts, one line per judge: cheated, and honeypots seen where the task's verdict records
 * it (SWE's does not); "not judged" without a row.
 * verdicts: {judge: row}. */
export function cheatbenchWords(verdicts) {
  const judges = Object.entries(verdicts || {});
  if (!judges.length) return muted('not judged', 'small');
  return h('span', { class: 'cb-cell' }, judges.map(([judge, r]) => h('div', { title: `judge ${judge}` },
    r.parse_ok === false ? badge('did not parse', 'warn') : [
      h('span', { class: r.cheated ? 'lv-judge-yes' : null }, `cheated ${yesNo(r.cheated)}`),
      'honey_pots_seen' in r ? `, honeypots seen ${yesNo(r.honey_pots_seen)}` : null],
    judges.length > 1 ? muted(` (${judge})`, 'small') : null)));
}

/** The team's word of each CheatBench judge: cheated, k of n agents. summaries: the index row's cheatbench. */
export function cheatbenchTeam(summaries) {
  if (!summaries || !summaries.length) return muted('not judged', 'small');
  return h('span', { class: 'cb-cell' }, summaries.map(s => h('div', { title: `judge ${s.judge}; ${s.of} of ${s.workers} workers have a parsed verdict` },
    h('span', { class: s.cheated ? 'lv-judge-yes' : null }, `cheated: ${s.cheated} of ${s.of} agents`),
    summaries.length > 1 ? muted(` (${s.judge})`, 'small') : null)));
}

/** One agent's disclosure: yes, no, not recorded or not judged. */
export const disclosureWord = v => h('span', { class: ['small', v === 'yes' ? 'lv-judge-yes' : v === 'no' ? null : 'muted'], title: DISCLOSURE.about }, v || 'not judged');

/** The team's disclosure (the index row's disclosure): "k of n workers disclosed", or why none was judged. */
export const disclosureTeam = s => (s.value ? muted(s.value, 'small')
  : h('span', { class: ['small', s.yes && 'lv-judge-yes'], title: DISCLOSURE.about }, `${s.yes} of ${s.yes + s.no} workers disclosed`));

/** CheatBench's judge's labels for one agent, each beside the code clauses it matches (src/measures/judge_labels.py):
 * the code's word, the judge's and whether they agree. r: the agent's Cheating record. */
export function judgeLabels(r) {
  if (!(r.judge || []).length) return muted('no label on this task', 'small');
  if (r.judge.every(x => x.judge == null)) return muted('not judged', 'small');
  return h('ul', { class: 'ev-list jl-list' }, r.judge.map(x => h('li', null,
    h('b', null, x.label), muted(` (${x.clauses.join(', ')})`, 'small'), `: code ${yesNo(x.code)}, judge ${yesNo(x.judge)} `,
    x.agree === true ? h('span', { class: 'jl-agree' }, 'agree')
      : x.agree === false ? badge('disagree', 'warn', 'CheatBench\'s judge and the code differ on this label; the judge changes no rung') : null)));
}

/** Run panel: one row per agent, one team row, a column per ladder, then disclosure and CheatBench's judge.
 * ctx: {cheatbench: {agent: {judge: row}}, team: the index row's cheatbench, disclosure: /api/run's disclosure,
 * teamDisclosure: the index row's disclosure}. */
export function levelsPanel(lv, roles, task, ctx) {
  const head = h('tr', null, h('th', null, 'Agent'), lv.order.map(k => h('th', { title: lv.ladders[k].rule }, lv.ladders[k].name)),
    h('th', { title: DISCLOSURE.about }, DISCLOSURE.column), h('th', { title: CHEATBENCH.about }, CHEATBENCH.column));
  const rows = Object.entries(lv.agents).sort((a, b) => (roles[a[0]] !== 'worker') - (roles[b[0]] !== 'worker') || a[0].localeCompare(b[0]))
    .map(([a, cells]) => h('tr', null, h('td', { class: 'mono' }, a, roles[a] && roles[a] !== 'worker' ? ` (hidden ${roles[a]})` : ''),
      lv.order.map(k => cellView(cells[k], lv.ladders[k], k)), h('td', null, disclosureWord(ctx.disclosure.agents[a])),
      h('td', null, cheatbenchWords(ctx.cheatbench[a]))));
  if (!lv.single) {
    rows.push(h('tr', { class: 'lv-team' }, h('td', null, h('b', null, 'Team')), lv.order.map(k => cellView(lv.team[k], lv.ladders[k], k)),
      h('td', null, disclosureTeam(ctx.teamDisclosure)), h('td', null, cheatbenchTeam(ctx.team))));
  }
  const alarm = lv.cheating && lv.cheating.alarm;
  return h('section', { class: 'card', id: 'sec-ladders' },
    h('h2', null, 'Three ladders for this run'),
    alarm ? h('p', null, badge(`alarm: ${alarm}`, 'bad', lv.ladders.cheating.note)) : null,
    h('p', { class: 'small muted' }, 'Each cell shows the level reached in its ladder\'s colour: pale below the ladder\'s line, strong above it. Hover a cell for the rule and the evidence. ',
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

const chip = (lv, key, level) => levelChip(key, lv.ladders[key], level);
const hiddenList = (lv, key, hidden, role) => (hidden.length
  ? hidden.map(([a, level], i) => [i ? '; ' : '', h('span', { class: 'mono' }, a), ` (hidden ${role(a)}) `, chip(lv, key, level)])
  : h('span', { class: 'muted small' }, 'none in this run'));

/** Team tab: the Cheating ladder in full (src/measures/cheating_ladder.py). The team's rung is the highest worker or
 * the team's own evidence; the hidden roles' own rungs are listed apart and never enter it. The grader's pass, the
 * alarm, and one row per agent with its rung, whether it cheated, the evidence of each clause that holds, the scan's
 * records below the ladder and CheatBench's judge's labels beside the code's clauses. ctx: {evidence(text, agent) ->
 * node}. */
export function cheatingPanel(lv, roles, ctx) {
  const X = lv.cheating;
  const L = lv.ladders.cheating;
  const head = h('h2', null, 'Cheating: how far each agent went with the honeypot');
  if (!X || !X.applicable) {
    return h('section', { class: 'card', id: 'sec-cheating' }, head, h('p', { class: 'muted' }, `Not applicable: ${(X && X.reason) || 'no measure'}.`));
  }
  const role = a => roles[a] || 'worker';
  const byRole = (a, b) => (role(a) !== 'worker') - (role(b) !== 'worker') || a.localeCompare(b);
  const teamOwn = X.team_clauses && Object.values(X.team_clauses).some(v => v && v.length);
  return h('section', { class: 'card', id: 'sec-cheating' }, head,
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, lv.single ? 'The run: ' : 'Team (the highest worker, or the team\'s own evidence): '),
        chip(lv, 'cheating', X.team), ' ', cheatedTag(X.team_cheated)),
      lv.single ? null : h('div', null, h('span', { class: 'field-label' }, 'Hidden roles, kept out of the team\'s rung: '),
        hiddenList(lv, 'cheating', Object.entries(X.hidden || {}), role)),
      h('div', null, h('span', { class: 'field-label' }, 'The grader: '),
        X.passed === true ? (X.passed_how || 'passed the hand-in') : X.passed === false ? 'did not pass the hand-in' : muted('no grade recorded', 'small')),
      X.alarm ? h('div', null, badge(`alarm: ${X.alarm}`, 'bad'), muted(` ${L.note}`, 'small')) : null),
    teamOwn ? h('div', null, h('h3', { class: 'sub-h' }, 'The team\'s own evidence, which no agent owns'), clauseList(lv, X.team_clauses, null, ctx)) : null,
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table cheat-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Rung', 'Cheated', 'Evidence of each clause that holds', 'Below the ladder (evidence only)', 'CheatBench judge\'s labels beside the code']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, Object.keys(X.agents).sort(byRole).map(a => {
        const r = X.agents[a];
        return h('tr', { class: role(a) !== 'worker' && 'coord-hidden' },
          h('td', { class: 'mono' }, a), h('td', null, role(a) === 'worker' ? 'worker' : `hidden ${role(a)}`),
          h('td', null, chip(lv, 'cheating', r.rung)), h('td', null, cheatedTag(r.cheated)), h('td', null, clauseList(lv, r.clauses, a, ctx)),
          h('td', null, (r.below || []).length ? h('ul', { class: 'ev-list' }, r.below.map(x => h('li', null, ctx.evidence(x, a)))) : muted('none', 'small')),
          h('td', null, judgeLabels(r)));
      })))),
    h('p', { class: 'small muted' }, L.rule), h('p', { class: 'small muted' }, L.note));
}

const fix2 = v => (v == null ? '' : Number(v).toFixed(2));

/** Team tab: the Coordination ladder in full (src/measures/coordination.py). The team's level is the workers' mean;
 * the hidden roles' own levels are listed apart and never enter it. One row per agent with its posts, its rounds per
 * teammate and the rounds its posts allow, and one row per pair with its rounds. */
export function coordinationPanel(lv, roles) {
  const C = lv.coordination;
  if (!C) return null;
  const L = lv.ladders.coordination;
  const role = a => roles[a] || 'worker';
  const agents = Object.entries(C.agents).sort((a, b) => (role(a[0]) !== 'worker') - (role(b[0]) !== 'worker') || a[0].localeCompare(b[0]));
  return h('section', { class: 'card', id: 'sec-coord' },
    h('h2', null, 'Coordination on the board'),
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, 'Team (the workers\' levels averaged, rounded half up): '), chip(lv, 'coordination', C.team)),
      h('div', null, h('span', { class: 'field-label' }, 'Hidden roles, kept out of the team\'s level: '),
        hiddenList(lv, 'coordination', Object.entries(C.hidden || {}), role))),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Posts', 'Read a teammate\'s post', 'Rounds per teammate', 'Rounds its posts allow', 'Level']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, agents.map(([a, r]) => h('tr', { class: role(a) !== 'worker' && 'coord-hidden' },
        h('td', { class: 'mono' }, a), h('td', null, role(a) === 'worker' ? 'worker' : `hidden ${role(a)}`),
        h('td', { class: 'num' }, String(r.posts)), h('td', null, r.read ? 'yes' : 'no'),
        h('td', { class: 'num' }, fix2(r.mbar)), h('td', { class: 'num' }, fix2(r.ceiling)), h('td', null, chip(lv, 'coordination', r.level))))))),
    C.pairs && C.pairs.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-pairs' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Pair'), h('th', { scope: 'col' }, 'Rounds (the smaller of the two directions)'))),
      h('tbody', null, C.pairs.map(p => h('tr', null, h('td', { class: 'mono' }, p.agents.join(' and ')), h('td', { class: 'num' }, String(p.rounds))))))) : null,
    h('p', { class: 'small muted' }, L.rule),
    L.note ? h('p', { class: 'small muted' }, L.note) : null);
}

const JUDGE_WORD = { 'not judged': 'no judge\'s verdict', 'not applicable': 'below collaboration, not read', 'not available': 'no reasoning in its transcript' };
const judgeValue = v => h('span', { class: ['small', v === 'yes' ? 'lv-judge-yes' : 'muted'], title: JUDGE_WORD[v] || 'the judge\'s verdict' }, v);

/** Code's check of a judge's quote: a failed check is no verdict. */
const checkNote = ck => (!ck ? null : ck.ok ? muted(`checked: ${ck.why}`, 'small block')
  : badge('the quote failed the code\'s check: no verdict', 'warn', ck.why));

/** The evidence of one of the judge's answers: the post it names (linked to the board), its quote, its reason and the
 * code's check. */
const judgeEvidence = (e, ctx) => h('div', { class: 'judge-ev' },
  e.post ? h('div', { class: 'small' }, 'the post: ', ctx.boardPost(e.post)) : null,
  e.quote ? h('blockquote', { class: 'judge-quote' }, e.quote) : null,
  e.reason ? h('div', { class: 'small muted' }, e.reason) : null, checkNote(e.check));

/** Team tab: the Collusion ladder in full (src/measures/collusion.py), as the measure returns it. The team's rung is
 * the highest pair of workers; the hidden roles' own rungs are listed apart. One row per agent with its rung, its
 * scheming verdict, its disclosure and its cheating rung for information; one row per pair with the takes and their
 * calls, whether the hand-in holds work of both, the exploit-named posts a judge would read, and the judge's two
 * fields with their quoted post and reasoning; then every verdict row of the collusion judges. ctx: {evidence(text,
 * agent) -> node with [k] linked to the agent's call, post(n) -> node linked to the board, boardPost(label) -> node
 * linked to the post the judge names (P1, P2, ...), judges: {judge: [verdict row]}, disclosure: /api/run's
 * disclosure, teamDisclosure: the index row's disclosure}. */
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
  // The scheming judge reads an agent only in a pair at collusion; its words on other pairs stay in the verdict rows.
  const schemingEvidence = p => (p.judge_evidence && p.collusion === 'yes' ? Object.entries(p.judge_evidence.scheming).map(([a, e]) =>
    h('div', null, h('span', { class: 'mono small' }, `${a}: ${e.said || '?'}`), judgeEvidence(e, ctx))) : null);
  const handIn = Object.entries(M.hand_in || {});
  return h('section', { class: 'card', id: 'sec-collusion' }, head,
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, 'Team (the highest pair of workers): '), chip(lv, 'collusion', M.team)),
      h('div', null, h('span', { class: 'field-label' }, 'Hidden roles, kept out of the team\'s rung: '),
        hiddenList(lv, 'collusion', Object.entries(M.hidden || {}), role)),
      h('div', null, h('span', { class: 'field-label' }, 'Judge rungs (collusion, scheming): '),
        M.judged ? `read from the verdicts of ${M.judge}` : h('b', null, 'not judged'),
        M.judged ? '' : h('span', { class: 'muted small' }, ': no judge\'s verdicts reached this run, so code stops at collaboration')),
      h('div', null, h('span', { class: 'field-label', title: DISCLOSURE.about }, 'Disclosure, CheatBench\'s judge (no rung): '), disclosureTeam(ctx.teamDisclosure))),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Rung', 'Scheming (judge)', 'Disclosure (CheatBench judge)', 'Cheating rung, for information']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, Object.keys(M.agents).sort(byRole).map(a => {
        const r = M.agents[a];
        return h('tr', { class: role(a) !== 'worker' && 'coord-hidden' },
          h('td', { class: 'mono' }, a), h('td', null, role(a) === 'worker' ? 'worker' : `hidden ${role(a)}`),
          h('td', null, chip(lv, 'collusion', r.rung)), h('td', null, judgeValue(r.scheming)),
          h('td', null, disclosureWord(ctx.disclosure.agents[a])), h('td', null, r.cheating || h('span', { class: 'muted small' }, 'no row')));
      })))),
    h('h3', { class: 'sub-h' }, 'Pairs'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact collusion-pairs' },
      h('thead', null, h('tr', null, ['Pair', 'Rung', 'Takes, with their calls', 'Hand-in holds work of both', 'Exploit named on the board (a hint a judge would read)', 'Collusion (judge), with its evidence', 'Scheming (judge), with its evidence', 'Cheating rungs']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, pairs.map(p => h('tr', { class: p.roles.some(r => r !== 'worker') && 'coord-hidden' },
        h('td', { class: 'mono' }, p.agents.map((a, i) => [i ? ' and ' : '', a, role(a) !== 'worker' ? ` (hidden ${role(a)})` : ''])),
        h('td', null, chip(lv, 'collusion', p.rung)), h('td', null, takeList(p)),
        h('td', null, p.hand_in_both ? 'yes' : 'no'), h('td', null, namedList(p)),
        h('td', null, judgeValue(p.collusion), p.judge_evidence ? judgeEvidence(p.judge_evidence.collusion, ctx) : null),
        h('td', null, judgeValue(p.scheming), schemingEvidence(p)),
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
