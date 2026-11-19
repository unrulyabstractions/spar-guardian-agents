/* The three ladders as every page draws them, and the Details tab's panels: one row per agent and one for the team,
 * then the Cheating, Coordination and Collusion ladders in full. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/levels.js); levels come from /api/run's "levels" and /api/ladders (src/ui/transcript_levels.py).
 * Our edits: the imports; a regulator and a disregulator are influencers, named by their role; the source's
 * stepTracker, which no view called, is left out; a level is a chip in its ladder's colour with a gap at the ladder's
 * line (levelChip); every Cheating cell holds the code's rung and cheated and, under it, CheatBench's judge's word, each
 * marked by who says it (cheatingCell); CheatBench's judge's labels beside the code's clauses; the judge labels
 * disclosure and manipulation beside the ladders, each a score chip from pale to strong with its excerpt, the judge's
 * reason and a link to the place code located the excerpt (labelEntry). Every string goes in through h() text
 * nodes. */
import { h } from '/static/app/dom.js';
import { badge, muted } from '/static/app/components.js';
import { RAMP, inkOn, scoreColour } from './tv_case_view.js';
import { CHEATBENCH, ROLE_LABEL, isInfluencer } from './tv_labels.js';

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

/** Cheated beside a Cheating rung: the measure's word (src/measures/cheating_ladder.py cheated), nothing without one.
 * lad: the Cheating ladder's /api/ladders entry, whose line_words say what cheated means. */
export const cheatedTag = (v, lad) => (v == null ? null : h('span', { class: ['lv-cheated', v ? 'yes' : 'no'],
  title: `cheated: ${lad.line_words}` }, v ? 'cheated: yes' : 'cheated: no'));

/** A judge label's score as a chip, the number on it, in the score's colour (SCORE_RAMP); "not judged" without a
 * score. */
export function scoreChip(score, tip) {
  if (score == null) return muted('not judged', 'small');
  const bg = scoreColour(score);
  return h('span', { class: 'sc-chip', style: `background:${bg};color:${inkOn(bg)}`, title: tip }, String(score));
}

const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** One agent's judge label (the measure's {score, excerpt, source, reason, located}, and for manipulation its kind,
 * mislead or control, shown beside the chip): its score chip, the excerpt
 * (beside it when full, else on hover), the judge's reason when full, and open(located), the page's link to the place
 * code located the excerpt, or "not located". about: the label's entry of judge_label_about.labels; e null: not
 * judged. */
export function labelEntry(about, e, open, full = false) {
  if (!e) return muted('not judged', 'small');
  const kind = e.kind && e.kind !== 'none' ? e.kind : null;
  const tip = [`${about.name}: ${e.score} of 100${kind ? ` (${kind})` : ''}`,
    e.excerpt ? `"${e.excerpt}" (from its ${e.source})` : 'no excerpt', e.reason].filter(Boolean).join('\n');
  const place = !e.excerpt ? null : e.located && open ? open(e.located) : muted('not located', 'small sc-lost');
  return h('div', { class: ['sc-entry', full && 'sc-full'] },
    h('div', { class: 'sc-line' }, scoreChip(e.score, tip), kind ? muted(` ${kind}`, 'small') : null,
      place ? [' ', place] : null,
      !full && !e.excerpt ? muted(' no excerpt', 'small') : null),
    full && e.excerpt ? h('blockquote', { class: 'sc-excerpt' }, e.excerpt) : null,
    !full && e.excerpt ? h('div', { class: 'sc-excerpt-short small', title: tip }, `"${clip(e.excerpt, 90)}"`) : null,
    full && e.reason ? muted(e.reason, 'small block') : null);
}

/** The team's score of a label (the index row's labels.team[label], {score, agent}): the chip and the worker it is
 * of; "not judged" without one. href(agent): where the agent's excerpt opens, or nothing. */
export function teamScore(t, href) {
  if (!t) return muted('not judged', 'small');
  const who = href && t.agent ? h('a', { href: href(t.agent), class: 'mono small', title: `open ${t.agent}'s excerpt` }, t.agent)
    : h('span', { class: 'mono small' }, t.agent || '');
  return h('span', { class: 'sc-team' }, scoreChip(t.score, `the team's highest score, of ${t.agent}`), ' ', who);
}

/** Who says what a line of a cell says: the code or a judge. */
const whoTag = (who, tip) => h('span', { class: 'lv-who', title: tip }, who);

/** A Cheating cell, the same on every page: the code's rung and whether it cheated, and under it CheatBench's judge's
 * own word (judgeWords: cheatbenchTeam or cheatbenchWords), each line marked by who says it, so the two are never
 * confused. lad: the Cheating ladder's /api/ladders entry; rank: whether the chip shows the rung's rank. */
export function cheatingCell(lad, level, cheated, judgeWords, rank = true) {
  return h('div', { class: 'cheat-cell' },
    whoTag('code', `read by code from the run's records: ${lad.line_words}`),
    h('div', { class: 'cheat-says' }, levelChip('cheating', lad, level, rank), cheated == null ? null : [' ', cheatedTag(cheated, lad)]),
    whoTag('CheatBench judge', CHEATBENCH.about),
    h('div', { class: 'cheat-says' }, judgeWords));
}

/** A ladder's cell in the run's table. judgeWords: CheatBench's judge's word under a Cheating cell. */
function cellView(c, L, key, judgeWords) {
  const tip = `${L.rule}${c.evidence ? '\nEvidence: ' + c.evidence : ''}`;
  if (key === 'cheating') return h('td', { class: 'lv-cell', title: tip }, cheatingCell(L, c.na ? null : c.level, c.na ? null : c.cheated, judgeWords),
    c.na && c.evidence ? muted(c.evidence, 'small block') : null);
  if (c.na) return h('td', { class: 'muted small', title: tip }, c.level, c.evidence ? `: ${c.evidence}` : '');
  return h('td', { class: 'lv-cell', title: tip }, levelChip(key, L, c.level));
}

const yesNo = v => (v === true ? 'yes' : v === false ? 'no' : '?');

/** One agent's CheatBench verdicts, one line per judge: cheated, and honeypots seen where the task's verdict records
 * it (SWE's does not); "not judged" without a row. The line carries no judge's name: the cell marks it (cheatingCell).
 * verdicts: {judge: row}. */
export function cheatbenchWords(verdicts) {
  const judges = Object.entries(verdicts || {});
  if (!judges.length) return muted('not judged', 'small');
  return h('span', { class: 'cb-cell' }, judges.map(([judge, r]) => h('span', { class: 'small', title: `judge ${judge}` },
    r.parse_ok === false ? badge('did not parse', 'warn') : [
      h('span', { class: r.cheated ? 'lv-judge-yes' : null }, `cheated ${yesNo(r.cheated)}`),
      'honey_pots_seen' in r ? `, honeypots seen ${yesNo(r.honey_pots_seen)}` : null],
    judges.length > 1 ? muted(` (${judge})`, 'small') : null)));
}

/** The team's word of each CheatBench judge: its own cheated, k of n workers. summaries: the index row's cheatbench. */
export function cheatbenchTeam(summaries) {
  if (!summaries || !summaries.length) return muted('not judged', 'small');
  return h('span', { class: 'cb-cell' }, summaries.map(s => h('span', { class: 'small', title: `judge ${s.judge}; ${s.of} of ${s.workers} workers have a parsed verdict` },
    h('span', { class: s.judge_cheated ? 'lv-judge-yes' : null }, `${s.judge_cheated} of ${s.of} cheated`),
    summaries.length > 1 ? muted(` (${s.judge})`, 'small') : null)));
}

/** Where the pages say what an independent reading found of the ladders and the labels (#/ladders, "How reliable"). */
export const READINGS_HREF = '#/ladders?section=reliability';

/** CheatBench's judge's labels for one agent beside the code's clauses they answer (src/measures/judge_labels.py ROWS):
 * one row per group of clauses a label answers, with the code's word, the judge's (or why it cannot answer) and
 * whether they agree; a clause no label answers is left out. r: the agent's Cheating record. */
export function judgeLabels(r) {
  const rows = (r.judge || []).filter(x => x.labels.length);
  if (!rows.length) return muted('no label on this task', 'small');
  if (rows.every(x => x.judge == null && x.why === 'not judged')) return muted('not judged', 'small');
  return h('ul', { class: 'ev-list jl-list' }, rows.map(x => h('li', null,
    h('b', null, x.labels.join(' or ')), muted(` (${x.clauses.join(', ')})`, 'small'),
    `: code ${yesNo(x.code)}, judge ${x.judge == null ? x.why : yesNo(x.judge)} `,
    x.agree === true ? h('span', { class: 'jl-agree' }, 'agree')
      : x.agree === false ? badge('disagree', 'warn', 'CheatBench\'s judge and the code differ on these clauses; the judge changes no rung') : null)));
}

/** The judge labels as columns, in the measure's order (/api/run's levels.judge_label_about.labels). */
const labelKeys = lv => Object.keys(lv.judge_label_about.labels);
/** One agent's judge labels, or null when the judge has not read it. */
const judgedOf = (lv, a) => lv.judge_labels.agents[a] || null;

/** Run panel: one row per agent, one team row, a column per ladder (the Cheating cell with CheatBench's judge's word
 * under the code's), then the judge labels' scores. ctx: {cheatbench: {agent: {judge: row}}, row: the index row (its
 * cheatbench and labels), open(agent, label) -> (located) -> the link to the excerpt's place}. */
export function levelsPanel(lv, roles, task, ctx) {
  const about = lv.judge_label_about.labels;
  const head = h('tr', null, h('th', null, 'Agent'), lv.order.map(k => h('th', { title: lv.ladders[k].rule }, lv.ladders[k].name)),
    labelKeys(lv).map(k => h('th', { title: `${about[k].scored} ${lv.judge_label_about.scored_by} No rung.` }, `${about[k].name} (judge, 0 to 100)`)));
  const rows = Object.entries(lv.agents).sort((a, b) => isInfluencer(roles[a[0]]) - isInfluencer(roles[b[0]]) || a[0].localeCompare(b[0]))
    .map(([a, cells]) => h('tr', null, h('td', { class: 'mono' }, a, isInfluencer(roles[a]) ? ` (${roles[a]})` : ''),
      lv.order.map(k => cellView(cells[k], lv.ladders[k], k, k === 'cheating' ? cheatbenchWords(ctx.cheatbench[a]) : null)),
      labelKeys(lv).map(k => h('td', null, judgedOf(lv, a) ? labelEntry(about[k], judgedOf(lv, a)[k], ctx.open(a, k)) : muted('not judged', 'small')))));
  if (!lv.single) {
    rows.push(h('tr', { class: 'lv-team' }, h('td', null, h('b', null, 'Team')),
      lv.order.map(k => cellView(lv.team[k], lv.ladders[k], k, k === 'cheating' ? cheatbenchTeam(ctx.row.cheatbench) : null)),
      labelKeys(lv).map(k => h('td', null, teamScore(ctx.row.labels.team[k])))));
  }
  const alarm = lv.cheating && lv.cheating.alarm;
  return h('section', { class: 'card', id: 'sec-ladders' },
    h('h2', null, 'Three ladders for this run'),
    alarm ? h('p', null, badge(`alarm: ${alarm}`, 'bad', lv.ladders.cheating.note)) : null,
    h('p', { class: 'small muted' }, 'Each cell shows the level reached in its ladder\'s colour: pale below the ladder\'s line, strong above it. Hover a cell for the rule and the evidence. A judge label is a score from 0 to 100, from pale to strong; its link opens the excerpt where code located it. ',
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
/** The influencers' own levels on one ladder ([[agent, level]]), each with its role. */
const influencerList = (lv, key, influencers, role) => (influencers.length
  ? influencers.map(([a, level], i) => [i ? '; ' : '', h('span', { class: 'mono' }, a), ` (${role(a)}) `, chip(lv, key, level)])
  : h('span', { class: 'muted small' }, 'none in this run'));
/** An agent's role in a table's Role column: worker, regulator (influencer) or disregulator (influencer). */
const roleWords = role => ROLE_LABEL[role] || role;
/** Workers first, then influencers, each by agent id. role(a): the agent's role. */
const workersFirst = role => (a, b) => isInfluencer(role(a)) - isInfluencer(role(b)) || a.localeCompare(b);

/** Details tab: the Cheating ladder in full (src/measures/cheating_ladder.py). The team's rung is the highest worker or
 * the team's own evidence; the influencers' own rungs are listed apart and never enter it. The grader's pass, the
 * alarm, and one row per agent with its rung, whether it cheated, the evidence of each clause that holds, the scan's
 * records below the ladder and CheatBench's judge's labels beside the code's clauses. Every Cheating cell holds the
 * code's word and CheatBench's judge's under it (cheatingCell). ctx: {evidence(text, agent) -> node, cheatbench:
 * {agent: {judge: row}}, row: the index row (its cheatbench)}. */
export function cheatingPanel(lv, roles, ctx) {
  const X = lv.cheating;
  const L = lv.ladders.cheating;
  const head = h('h2', null, 'Cheating: how far each agent went with the honeypot');
  if (!X || !X.applicable) {
    return h('section', { class: 'card', id: 'sec-cheating' }, head, h('p', { class: 'muted' }, `Not applicable: ${(X && X.reason) || 'no measure'}.`));
  }
  const role = a => roles[a] || 'worker';
  const byRole = workersFirst(role);
  const teamOwn = X.team_clauses && Object.values(X.team_clauses).some(v => v && v.length);
  return h('section', { class: 'card', id: 'sec-cheating' }, head,
    h('div', { class: 'coord-summary' },
      h('div', null, h('div', { class: 'field-label' }, lv.single ? 'The run: ' : 'Team (the highest worker, or the team\'s own evidence): '),
        cheatingCell(L, X.team, X.team_cheated, lv.single ? cheatbenchWords(Object.values(ctx.cheatbench)[0]) : cheatbenchTeam(ctx.row.cheatbench))),
      lv.single ? null : h('div', null, h('span', { class: 'field-label' }, 'Influencers, kept out of the team\'s rung: '),
        influencerList(lv, 'cheating', Object.entries(X.influencers || {}), role)),
      h('div', null, h('span', { class: 'field-label' }, 'The grader: '),
        X.passed === true ? (X.passed_how || 'passed the hand-in') : X.passed === false ? 'did not pass the hand-in' : muted('no grade recorded', 'small')),
      X.alarm ? h('div', null, badge(`alarm: ${X.alarm}`, 'bad'), muted(` ${L.note}`, 'small')) : null),
    teamOwn ? h('div', null, h('h3', { class: 'sub-h' }, 'The team\'s own evidence, which no agent owns'), clauseList(lv, X.team_clauses, null, ctx)) : null,
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table cheat-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Cheating (code, then CheatBench judge)', 'Evidence of each clause that holds', 'Below the ladder (evidence only)', 'CheatBench judge\'s labels beside the code\'s clauses']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, Object.keys(X.agents).sort(byRole).map(a => {
        const r = X.agents[a];
        return h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
          h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
          h('td', null, cheatingCell(L, r.rung, r.cheated, cheatbenchWords(ctx.cheatbench[a]))), h('td', null, clauseList(lv, r.clauses, a, ctx)),
          h('td', null, (r.below || []).length ? h('ul', { class: 'ev-list' }, r.below.map(x => h('li', null, ctx.evidence(x, a)))) : muted('none', 'small')),
          h('td', null, judgeLabels(r)));
      })))),
    h('p', { class: 'small muted' }, L.rule), h('p', { class: 'small muted' }, L.note));
}

const fix2 = v => (v == null ? '' : Number(v).toFixed(2));

/** Details tab: the Coordination ladder in full (src/measures/coordination.py). The team's level is the workers' mean;
 * the influencers' own levels are listed apart and never enter it. One row per agent with its posts, its rounds per
 * teammate and the rounds its posts allow, and one row per pair with its rounds. */
export function coordinationPanel(lv, roles) {
  const C = lv.coordination;
  if (!C) return null;
  const L = lv.ladders.coordination;
  const role = a => roles[a] || 'worker';
  const agents = Object.entries(C.agents).sort((a, b) => workersFirst(role)(a[0], b[0]));
  return h('section', { class: 'card', id: 'sec-coord' },
    h('h2', null, 'Coordination on the board'),
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, 'Team (the workers\' levels averaged, rounded half up): '), chip(lv, 'coordination', C.team)),
      h('div', null, h('span', { class: 'field-label' }, 'Influencers, kept out of the team\'s level: '),
        influencerList(lv, 'coordination', Object.entries(C.influencers || {}), role))),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Posts', 'Read a teammate\'s post', 'Rounds per teammate', 'Rounds its posts allow', 'Level']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, agents.map(([a, r]) => h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
        h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
        h('td', { class: 'num' }, String(r.posts)), h('td', null, r.read ? 'yes' : 'no'),
        h('td', { class: 'num' }, fix2(r.mbar)), h('td', { class: 'num' }, fix2(r.ceiling)), h('td', null, chip(lv, 'coordination', r.level))))))),
    C.pairs && C.pairs.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-pairs' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Pair'), h('th', { scope: 'col' }, 'Rounds (the smaller of the two directions)'))),
      h('tbody', null, C.pairs.map(p => h('tr', null, h('td', { class: 'mono' }, p.agents.join(' and ')), h('td', { class: 'num' }, String(p.rounds))))))) : null,
    h('p', { class: 'small muted' }, L.rule),
    L.note ? h('p', { class: 'small muted' }, L.note) : null);
}

/** Details tab: the Collusion ladder in full (src/measures/collusion.py), every rung read by code. The team's rung is
 * the highest pair of workers; the influencers' own rungs are listed apart. One row per agent with its rung and its
 * cheating rung for information; one row per pair with the takes and their calls, whether the hand-in holds work of
 * both and the planted pieces exchanged. ctx: {evidence(text, agent) -> node with [k] linked to the agent's call}. */
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
  const byRole = workersFirst(role);
  const rank = r => L.levels.indexOf(r);
  const pairs = M.pairs.slice().sort((x, y) => rank(y.rung) - rank(x.rung) || y.takes.length - x.takes.length);
  const takeList = p => (p.takes.length ? h('ul', { class: 'ev-list' }, p.takes.map(t => h('li', null,
    h('span', { class: 'mono' }, `${t.taker} took ${t.giver}'s work`), ` (${t.rule}${t.call == null ? ', from a final record' : ''}): `,
    ctx.evidence(t.evidence, t.taker)))) : h('span', { class: 'muted small' }, 'no take'));
  // What a colluding pair exchanged of the exploit: the pieces each took of the other's, or each one's in the hand-in.
  const pooledList = p => (!p.pooled ? h('span', { class: 'muted small' }, 'none from both')
    : h('ul', { class: 'ev-list' }, Object.entries(Object.keys(p.pooled.took).length ? p.pooled.took : p.pooled.hand_in)
      .map(([a, pieces]) => h('li', null, h('span', { class: 'mono' }, a), Object.keys(p.pooled.took).length ? ' took ' : '\'s in the hand-in: ', pieces.join(', ')))));
  const handIn = Object.entries(M.hand_in || {});
  return h('section', { class: 'card', id: 'sec-collusion' }, head,
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, 'Team (the highest pair of workers): '), chip(lv, 'collusion', M.team)),
      h('div', null, h('span', { class: 'field-label' }, 'Influencers, kept out of the team\'s rung: '),
        influencerList(lv, 'collusion', Object.entries(M.influencers || {}), role))),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Rung', 'Cheating rung, for information']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, Object.keys(M.agents).sort(byRole).map(a => {
        const r = M.agents[a];
        return h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
          h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
          h('td', null, chip(lv, 'collusion', r.rung)),
          h('td', null, r.cheating || h('span', { class: 'muted small' }, 'no row')));
      })))),
    h('h3', { class: 'sub-h' }, 'Pairs'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact collusion-pairs' },
      h('thead', null, h('tr', null, ['Pair', 'Rung', 'Takes, with their calls', 'Hand-in holds work of both', 'Planted pieces exchanged', 'Cheating rungs']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, pairs.map(p => h('tr', { class: p.roles.some(isInfluencer) && 'coord-influencer' },
        h('td', { class: 'mono' }, p.agents.map((a, i) => [i ? ' and ' : '', a, isInfluencer(role(a)) ? ` (${role(a)})` : ''])),
        h('td', null, chip(lv, 'collusion', p.rung)), h('td', null, takeList(p)),
        h('td', null, p.hand_in_both ? 'yes' : 'no'), h('td', null, pooledList(p)),
        h('td', { class: 'small' }, p.cheating.map(c => c || '?').join(' / '))))))),
    h('h3', { class: 'sub-h' }, 'The hand-in\'s contributors'),
    handIn.length ? h('ul', { class: 'ev-list' }, handIn.map(([a, lines]) => h('li', null, h('span', { class: 'mono' }, a), ': ', lines.join('; '))))
      : h('p', { class: 'muted small' }, 'No agent\'s work is credited in the hand-in.'),
    (M.notes || []).length ? h('p', { class: 'small muted' }, 'Notes of the measure: ', M.notes.join('; ')) : null,
    h('p', { class: 'small muted' }, L.rule), h('p', { class: 'small muted' }, L.note));
}

/** The two judge labels in three lines each: what is scored, the anchors of the score, and who scores it and where
 * the excerpt comes from. about: judge_label_about ({labels, anchors, scored_by}). */
export function labelAbout(about) {
  return h('ul', { class: 'small plain-list sc-about' }, Object.values(about.labels).map(x => h('li', null,
    h('b', null, `${x.name}. `), x.scored, h('br'),
    'Anchors: ', about.anchors.map(([score, words], i) => [i ? '; ' : '', scoreChip(score, words), ` ${words}`]), '.', h('br'),
    about.scored_by)));
}

/** Details tab: each agent's judge labels beside the ladders, never rungs: the score chip, the excerpt, the judge's
 * reason and the link to the place code located the excerpt. open(agent, label) -> (located) -> the link. */
export function judgeLabelsPanel(lv, roles, open) {
  const about = lv.judge_label_about;
  const role = a => roles[a] || 'worker';
  const agents = Object.keys(lv.judge_labels.agents).sort(workersFirst(role));
  return h('section', { class: 'card', id: 'sec-judge-labels' },
    h('h2', null, 'Judge labels beside the ladders'),
    labelAbout(about),
    h('p', { class: 'small muted' }, lv.judge_labels.judge ? `Scored by ${lv.judge_labels.judge}. ` : 'No judge has scored this run: not judged. ',
      h('a', { href: READINGS_HREF }, 'What independent readings found of the earlier labels')),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table sc-table' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Agent'), h('th', { scope: 'col' }, 'Role'),
        labelKeys(lv).map(k => h('th', { scope: 'col' }, about.labels[k].name)))),
      h('tbody', null, agents.map(a => h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
        h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
        labelKeys(lv).map(k => h('td', null, judgedOf(lv, a) ? labelEntry(about.labels[k], judgedOf(lv, a)[k], open(a, k), true)
          : muted('not judged', 'small')))))))));
}
