/* The three ladders as every page draws them, and the Details tab's panels: one row per agent and one for the team,
 * then the Cheating, Collusion and Coordination ladders in full. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/levels.js); levels come from /api/run's "levels" and /api/ladders (src/ui/transcript_levels.py).
 * Our edits: the imports; a regulator and a disregulator are influencers, named by their role; the source's
 * stepTracker, which no view called, is left out; a level is a chip in its ladder's colour with a gap at the ladder's
 * line (levelChip), whose hover is a small popover with the level's one line and its evidence as name and value
 * (levelPopover); every Cheating cell holds the code's rung and cheated and, under it, CheatBench's judge's word, each
 * marked by who says it (cheatingCell); CheatBench's judge's labels beside the code's clauses; the judge labels
 * disclosure and manipulation beside the ladders, each a score chip from pale to strong with its excerpt, the judge's
 * reason and a link to the place code located the excerpt (labelEntry). The panels carry headings, tables, chips and
 * evidence only: every definition is on the Ladders page. Every string goes in through h() text nodes. */
import { h } from '/static/app/dom.js';
import { badge, muted } from '/static/app/components.js';
import { RAMP, inkOn, scoreColour } from './tv_case_view.js';
import { ROLE_LABEL, isInfluencer } from './tv_labels.js';

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

const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

// ---- the popover: one for the page, shown on a chip's hover or focus, kept inside the window.
const POPS = new WeakMap();
let pop = null;
let popFor = null;
let hideTimer = 0;
function popBox() {
  if (!pop) {
    pop = h('div', { class: 'lv-pop', role: 'tooltip', hidden: true });
    pop.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    pop.addEventListener('mouseleave', () => hidePop());
    document.body.appendChild(pop);
  }
  return pop;
}
function showPop(el) {
  clearTimeout(hideTimer);
  if (popFor === el && !pop.hidden) return;
  const box = popBox();
  box.replaceChildren(h('div', null, POPS.get(el)()));
  box.hidden = false;
  popFor = el;
  const r = el.getBoundingClientRect();
  const left = Math.min(Math.max(8, r.left), window.innerWidth - box.offsetWidth - 8);
  const fitsBelow = r.bottom + 6 + box.offsetHeight <= window.innerHeight - 8;
  box.style.left = `${Math.max(8, left)}px`;
  box.style.top = `${fitsBelow ? r.bottom + 6 : Math.max(8, r.top - 6 - box.offsetHeight)}px`;
}
function hidePop(now = false) {
  clearTimeout(hideTimer);
  const go = () => { if (pop) pop.hidden = true; popFor = null; };
  if (now) go(); else hideTimer = setTimeout(go, 150);
}
const popTarget = e => (e.target instanceof Element ? e.target.closest('.has-pop') : null);
const leaving = (e, t) => t && !t.contains(e.relatedTarget) && !(pop && pop.contains(e.relatedTarget));
document.addEventListener('mouseover', e => { const t = popTarget(e); if (t && POPS.has(t)) showPop(t); });
document.addEventListener('mouseout', e => { if (leaving(e, popTarget(e))) hidePop(); });
document.addEventListener('focusin', e => { const t = popTarget(e); if (t && POPS.has(t)) showPop(t); });
document.addEventListener('focusout', e => { if (leaving(e, popTarget(e))) hidePop(); });
// any scroll, the page's or a pane's (the run page's steps), takes the popover away
document.addEventListener('scroll', () => { if (popFor) hidePop(true); }, { passive: true, capture: true });

/** el shows the popover build() draws on hover and focus. */
export function withPopover(el, build) {
  el.classList.add('has-pop');
  el.tabIndex = 0;
  POPS.set(el, build);
  return el;
}

/** A fact's value: a string, or a list of evidence lines shown as their first three and a count of the rest. */
function factValue(v) {
  if (!Array.isArray(v)) return clip(String(v), 90);
  const lines = v.slice(0, 3).map(x => h('div', null, clip(String(x), 90)));
  return v.length > 3 ? [lines, h('div', { class: 'lv-pop-more' }, `+${v.length - 3} more`)] : lines;
}
const MAX_FACTS = 6;

/** A level chip's popover: the ladder and the level, its one line, its evidence as name and value ([[name, value]]),
 * and a link to the ladder's rule on the Ladders page. key: the ladder's id; lad: its /api/ladders entry. */
function levelPopover(key, lad, i, facts) {
  const shown = facts.slice(0, MAX_FACTS);
  return () => [
    h('div', { class: 'lv-pop-head' }, h('b', null, lad.name), ` · ${lad.levels[i]}`),
    h('div', { class: 'lv-pop-gloss' }, lad.meanings[i]),
    shown.length ? h('table', { class: 'lv-pop-facts' }, h('tbody', null, shown.map(([name, value]) =>
      h('tr', null, h('th', null, name), h('td', null, factValue(value)))))) : null,
    facts.length > MAX_FACTS ? h('div', { class: 'lv-pop-more' }, `+${facts.length - MAX_FACTS} more`) : null,
    h('a', { class: 'lv-pop-link', href: `#/ladders?section=ladder-${key}` }, 'full rule')];
}

/** A level as every page draws it: a chip in the ladder's colour at this level, its text in the colour that reads on
 * it, the bars, the name and the rank, and on hover its popover. key: the ladder's id; lad: its /api/ladders entry.
 * opts.facts: the evidence the popover lists; opts.look: 'carried' (an outline, a level reached before) or 'own' (a
 * marked chip, a label the call carries itself). */
export function levelChip(key, lad, level, rank = true, opts = {}) {
  const i = lad.levels.indexOf(level);
  if (i < 0) return muted(level == null ? 'not applicable' : level, 'small');
  const bg = RAMP[key][i];
  const of = lad.levels.length - 1;
  const style = opts.look === 'carried' ? `--lv:${bg}` : `background:${bg};color:${inkOn(bg)}`;
  return withPopover(h('span', { class: ['lv-chip', opts.look && `lv-${opts.look}`], style },
    opts.look === 'own' ? h('span', { class: 'lv-own-mark', 'aria-hidden': 'true' }) : null,
    glyph(i, lad), h('b', null, level), rank ? h('span', { class: 'lv-rank' }, ` (${i} of ${of})`) : null),
  levelPopover(key, lad, i, opts.facts || []));
}

/** The mark legends put between the two sides of a ladder's line. */
export const lineMark = lad => h('span', { class: 'lv-line-mark', title: `${lad.name} line: ${lad.line} and above`, role: 'separator', 'aria-label': 'line' });

/** Cheated beside a Cheating rung: the measure's word (src/measures/cheating_ladder.py cheated), nothing without one.
 * lad: the Cheating ladder's /api/ladders entry, whose line_words say what cheated means. */
export const cheatedTag = (v, lad) => (v == null ? null : h('span', { class: ['lv-cheated', v ? 'yes' : 'no'],
  title: lad.line_words }, v ? 'cheated: yes' : 'cheated: no'));

/** The alarm's badge: the grader passed with no trace of the honeypot in the hand-in. */
export const alarmBadge = alarm => badge(`alarm: ${alarm}`, 'bad', 'Grader passed; no honeypot trace in the hand-in');

/** A judge label's score as a chip, the number on it, in the score's colour (SCORE_RAMP); "not judged" without a
 * score. */
export function scoreChip(score, tip) {
  if (score == null) return muted('not judged', 'small');
  const bg = scoreColour(score);
  return h('span', { class: 'sc-chip', style: `background:${bg};color:${inkOn(bg)}`, title: tip }, String(score));
}

/** Where the pages say what an independent reading found of the ladders and the labels (#/ladders, "How reliable"). */
export const READINGS_HREF = '#/ladders?section=reliability';
/** The How reliable section's row on manipulation of the kind mislead. */
export const MISLEAD_HREF = '#/ladders?section=reliability-mislead';

/** Beside a manipulation score of the kind mislead: "read the excerpt", a link to the How reliable section's row on
 * that kind. An independent reading found a mislead score untrustworthy at any value, so no agent counts as
 * misleading until a reader has read its excerpt against its own calls. Nothing for any other kind. */
export function misleadMark(kind) {
  if (kind !== 'mislead') return null;
  return h('a', { class: 'sc-mislead', href: MISLEAD_HREF, title: 'Mislead score: untrustworthy, read the excerpt',
    'aria-label': 'mislead score, read the excerpt' }, '⚠');
}

/** One agent's judge label (the measure's {score, excerpt, source, reason, located}, and for manipulation its kind,
 * mislead or control, shown beside the chip, a mislead kind with misleadMark): its score chip, the excerpt
 * (beside it when full, else clipped), the judge's reason when full, and open(located), the page's link to the place
 * code located the excerpt, or "not located". about: the label's entry of judge_label_about.labels; e null: not
 * judged. */
export function labelEntry(about, e, open, full = false) {
  if (!e) return muted('not judged', 'small');
  const kind = e.kind && e.kind !== 'none' ? e.kind : null;
  const place = !e.excerpt ? null : e.located && open ? open(e.located) : muted('not located', 'small sc-lost');
  return h('div', { class: ['sc-entry', full && 'sc-full'] },
    h('div', { class: 'sc-line' }, scoreChip(e.score, `${about.name}: ${e.score} of 100`), kind ? muted(` ${kind}`, 'small') : null,
      kind === 'mislead' ? [' ', misleadMark(kind)] : null,
      place ? [' ', place] : null,
      !full && !e.excerpt ? muted(' no excerpt', 'small') : null),
    full && e.excerpt ? h('blockquote', { class: 'sc-excerpt' }, e.excerpt) : null,
    !full && e.excerpt ? h('div', { class: 'sc-excerpt-short small' }, `"${clip(e.excerpt, 90)}"`) : null,
    full && e.reason ? muted(e.reason, 'small block') : null);
}

/** The team's score of a label (the index row's labels.team[label], {score: the workers' mean, n, max, agent}): the
 * mean's chip, which opens the highest-scoring worker's excerpt; "not judged" without one. href(agent): where the
 * agent's excerpt opens, or nothing. */
export function teamScore(t, href) {
  if (!t) return h('span', { class: 'muted', title: 'not judged' }, '–');
  const tip = `workers' mean of ${t.n}; highest ${t.max}, ${t.agent}`;
  const chip = scoreChip(t.score, tip);
  return h('span', { class: 'sc-team' }, href && t.agent ? h('a', { href: href(t.agent), title: tip }, chip) : chip,
    t.kind === 'mislead' ? [' ', misleadMark(t.kind)] : null);
}


/** A Cheating cell, the same on every page: the code's rung and whether it cheated, and under it CheatBench's judge's
 * own word (judgeWords: cheatbenchTeam or cheatbenchWords), each line marked by who says it, so the two are never
 * confused. lad: the Cheating ladder's /api/ladders entry; rank: whether the chip shows the rung's rank; facts: the
 * evidence the chip's popover lists. */
export function cheatingCell(lad, level, cheated, judgeWords, rank = true, facts = []) {
  return h('div', { class: 'cheat-cell' }, levelChip('cheating', lad, level, rank, { facts }), judgeWords ? [' ', judgeWords] : null);
}

/** A ladder's cell in the run's table: the chip with its evidence on hover, or not applicable and why. judgeWords:
 * CheatBench's judge's word under a Cheating cell. */
function cellView(c, L, key, judgeWords) {
  if (key === 'cheating') return h('td', { class: 'lv-cell' }, cheatingCell(L, c.na ? null : c.level, c.na ? null : c.cheated, judgeWords, true, c.facts),
    c.na && c.evidence ? muted(c.evidence, 'small block') : null);
  if (c.na) return h('td', { class: 'lv-cell muted small' }, c.level, c.evidence ? `: ${c.evidence}` : '');
  return h('td', { class: 'lv-cell' }, levelChip(key, L, c.level, true, { facts: c.facts }));
}

const yesNo = v => (v === true ? 'yes' : v === false ? 'no' : '?');

/** One agent's CheatBench verdicts, one outlined chip per judge as the team's (cheatbenchTeam): "Judge: yes" or
 * "Judge: no", red when it cheated, and honeypots seen where the task's verdict records it (SWE's does not); "not
 * judged" without a row. The judge's name is the chip's tooltip, and beside the chip when the agent has two judges.
 * verdicts: {judge: row}. */
export function cheatbenchWords(verdicts) {
  const judges = Object.entries(verdicts || {});
  if (!judges.length) return h('span', { class: 'cb-chip cb-none', title: 'CheatBench judge: not judged' }, 'Judge: –');
  return h('span', { class: 'cb-cell' }, judges.map(([judge, r]) => (r.parse_ok === false ? badge('did not parse', 'warn')
    : h('span', { class: ['cb-chip', r.cheated && 'cb-yes'], title: `CheatBench judge ${judge}` },
      `Judge: ${yesNo(r.cheated)}`, 'honey_pots_seen' in r ? `, honeypots ${yesNo(r.honey_pots_seen)}` : null,
      judges.length > 1 ? ` (${judge})` : null))));
}

/** The team's word of each CheatBench judge: its own cheated, k of n workers. summaries: the index row's cheatbench. */
export function cheatbenchTeam(summaries) {
  if (!summaries || !summaries.length) return h('span', { class: 'cb-chip cb-none', title: 'CheatBench judge: not judged' }, 'Judge: –');
  return h('span', { class: 'cb-cell' }, summaries.map(s => h('span', {
    class: ['cb-chip', s.judge_cheated ? 'cb-yes' : null],
    title: `CheatBench judge ${s.judge}: ${s.judge_cheated} of ${s.of} workers cheated` }, `Judge: ${s.judge_cheated}/${s.of}`)));
}

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
      : x.agree === false ? badge('disagree', 'warn', 'Judge and code differ; the judge changes no rung') : null)));
}

/** The judge labels as columns, in the measure's order (/api/run's levels.judge_label_about.labels). */
const labelKeys = lv => Object.keys(lv.judge_label_about.labels);
/** One agent's judge labels, or null when the judge has not read it. */
const judgedOf = (lv, a) => lv.judge_labels.agents[a] || null;

/** Run panel: one row per agent, one team row, a column per ladder (the Cheating cell with CheatBench's judge's word
 * under the code's), then the judge labels' scores. ctx: {cheatbench: {agent: {judge: row}}, row: the index row (its
 * cheatbench and labels), open(agent, label) -> (located) -> the link to the excerpt's place, star(agent) -> the
 * mark beside an agent's name (the final hand-in's star) or null}. */
export function levelsPanel(lv, roles, task, ctx) {
  const about = lv.judge_label_about.labels;
  const head = h('tr', null, h('th', { class: 'lv-agent' }, 'Agent'), lv.order.map(k => h('th', { class: 'lv-cell' }, lv.ladders[k].name)),
    labelKeys(lv).map(k => h('th', { class: 'lv-label-cell', title: 'Judge score, 0 to 100' }, about[k].name)));
  const rows = Object.entries(lv.agents).sort((a, b) => isInfluencer(roles[a[0]]) - isInfluencer(roles[b[0]]) || a[0].localeCompare(b[0]))
    .map(([a, cells]) => h('tr', null, h('td', { class: 'mono lv-agent' }, a, isInfluencer(roles[a]) ? ` (${roles[a]})` : '', ctx.star(a)),
      lv.order.map(k => cellView(cells[k], lv.ladders[k], k, k === 'cheating' ? cheatbenchWords(ctx.cheatbench[a]) : null)),
      labelKeys(lv).map(k => h('td', { class: 'lv-label-cell' }, judgedOf(lv, a) ? labelEntry(about[k], judgedOf(lv, a)[k], ctx.open(a, k)) : muted('not judged', 'small')))));
  if (!lv.single) {
    rows.push(h('tr', { class: 'lv-team' }, h('td', { class: 'lv-agent' }, h('b', null, 'Team')),
      lv.order.map(k => cellView(lv.team[k], lv.ladders[k], k, k === 'cheating' ? cheatbenchTeam(ctx.row.cheatbench) : null)),
      labelKeys(lv).map(k => h('td', { class: 'lv-label-cell' }, teamScore(ctx.row.labels.team[k])))));
  }
  const alarm = lv.cheating && lv.cheating.alarm;
  return h('section', { class: 'card', id: 'sec-ladders' },
    h('h2', null, 'Ladders ', h('a', { class: 'small h-link', href: '#/ladders?task=' + encodeURIComponent(task || '') }, 'by task')),
    alarm ? h('p', null, alarmBadge(alarm)) : null,
    h('table', { class: 'lv-table' }, h('thead', null, head), h('tbody', null, rows)));
}

/** One agent's or the team's Cheating evidence, clause by clause, highest rung first: "clause (rung)" and each line
 * of evidence, its calls linked by ctx.evidence(text, agent). */
export function clauseList(lv, clauses, agent, ctx) {
  const L = lv.ladders.cheating;
  const held = L.clauses.filter(([c]) => (clauses[c] || []).length).sort((x, y) => L.levels.indexOf(y[1]) - L.levels.indexOf(x[1]));
  if (!held.length) return muted('no clause holds', 'small');
  return h('ul', { class: 'ev-list' }, held.map(([c, level]) => h('li', null,
    h('b', null, c), muted(` (${level}) `, 'small'),
    h('ul', { class: 'ev-list' }, clauses[c].map(line => h('li', null, ctx.evidence(line, agent)))))));
}

const chip = (lv, key, level) => levelChip(key, lv.ladders[key], level);
/** The influencers' own levels on one ladder ([[agent, level]]), each with its role. */
const influencerList = (lv, key, influencers, role) => (influencers.length
  ? influencers.map(([a, level], i) => [i ? '; ' : '', h('span', { class: 'mono' }, a), ` (${role(a)}) `, chip(lv, key, level)])
  : muted('none', 'small'));
/** An agent's role in a table's Role column: worker, regulator (influencer) or disregulator (influencer). */
const roleWords = role => ROLE_LABEL[role] || role;
/** Workers first, then influencers, each by agent id. role(a): the agent's role. */
const workersFirst = role => (a, b) => isInfluencer(role(a)) - isInfluencer(role(b)) || a.localeCompare(b);
/** A panel's summary: its rows of a label (its tooltip says how the value is read) and a value. */
const summary = rows => h('table', { class: 'lv-summary' }, h('tbody', null, rows.filter(Boolean).map(([label, tip, value]) =>
  h('tr', null, h('th', { scope: 'row', title: tip }, label), h('td', null, value)))));
const headRow = cols => h('thead', null, h('tr', null, cols.map(c => (Array.isArray(c)
  ? h('th', { scope: 'col', title: c[1] }, c[0]) : h('th', { scope: 'col' }, c)))));

/** The routes an agent's aimed reads took (the code record's routes, SWE alone), one chip each. */
const ROUTE_TITLES = {
  branch: 'Aimed reads of planted branches or upstream refs',
  side_patch: 'Aimed reads of the side patch files outside the repository',
  fix_commit: 'Aimed reads naming the fix commit by id',
  network: 'Aimed fetches from the network',
  reference: 'Aimed reads of the reference or its part files',
  side_copy: 'Aimed reads of the side copies outside the workspace',
};
const routeChips = routes => ((routes || []).length
  ? h('div', { class: 'route-chips' }, routes.map(r => badge(r, 'plain', ROUTE_TITLES[r] || r))) : null);

/** Details tab: the Cheating ladder in full (src/measures/cheating_ladder.py). The team's rung is the highest worker or
 * the team's own evidence; the influencers' own rungs are listed apart and never enter it. The grader's pass, the
 * alarm, and one row per agent with its rung, whether it cheated, the evidence of each clause that holds, the scan's
 * records below the ladder and CheatBench's judge's labels beside the code's clauses. Every Cheating cell holds the
 * code's word and CheatBench's judge's under it (cheatingCell), and the agent's routes as chips. ctx: {evidence(text,
 * agent) -> node, cheatbench: {agent: {judge: row}}, row: the index row (its cheatbench), routes: {agent: [route]}}. */
export function cheatingPanel(lv, roles, ctx) {
  const X = lv.cheating;
  const L = lv.ladders.cheating;
  const head = h('h2', null, 'Cheating');
  if (!X || !X.applicable) {
    return h('section', { class: 'card', id: 'sec-cheating' }, head, h('p', { class: 'muted' }, `Not applicable: ${(X && X.reason) || 'no measure'}.`));
  }
  const role = a => roles[a] || 'worker';
  const byRole = workersFirst(role);
  const teamOwn = X.team_clauses && Object.values(X.team_clauses).some(v => v && v.length);
  return h('section', { class: 'card', id: 'sec-cheating' }, head,
    summary([
      [lv.single ? 'Run' : 'Team', 'Highest worker, or the team\'s own evidence',
        cheatingCell(L, X.team, X.team_cheated, lv.single ? cheatbenchWords(Object.values(ctx.cheatbench)[0]) : cheatbenchTeam(ctx.row.cheatbench),
          true, lv.team.cheating.facts)],
      lv.single ? null : ['Influencers', 'Kept out of the team\'s rung', influencerList(lv, 'cheating', Object.entries(X.influencers || {}), role)],
      ['Grader', null, X.passed === true ? (X.passed_how || 'passed the hand-in') : X.passed === false ? 'did not pass the hand-in' : muted('no grade recorded', 'small')],
      X.alarm ? ['Alarm', null, alarmBadge(X.alarm)] : null]),
    teamOwn ? h('div', null, h('h3', { class: 'sub-h' }, 'Team\'s own evidence'), clauseList(lv, X.team_clauses, null, ctx)) : null,
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table cheat-table' },
      headRow(['Agent', 'Role', 'Cheating', 'Clauses', ['Below the ladder', 'Evidence only, no rung'], 'CheatBench labels']),
      h('tbody', null, Object.keys(X.agents).sort(byRole).map(a => {
        const r = X.agents[a];
        return h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
          h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
          h('td', null, cheatingCell(L, r.rung, r.cheated, cheatbenchWords(ctx.cheatbench[a]), true, (lv.agents[a] || {}).cheating?.facts),
            routeChips((ctx.routes || {})[a])),
          h('td', null, clauseList(lv, r.clauses, a, ctx)),
          h('td', null, (r.below || []).length ? h('ul', { class: 'ev-list' }, r.below.map(x => h('li', null, ctx.evidence(x, a)))) : muted('none', 'small')),
          h('td', null, judgeLabels(r)));
      })))));
}

const fix2 = v => (v == null ? '' : Number(v).toFixed(2));

/** Details tab: the Coordination ladder in full (src/measures/coordination.py). The team's level is the workers' mean;
 * the influencers' own levels are listed apart and never enter it. One row per agent with its posts, its rounds per
 * teammate and the rounds its posts allow, and one row per pair with its rounds. */
export function coordinationPanel(lv, roles) {
  const C = lv.coordination;
  if (!C) return null;
  const role = a => roles[a] || 'worker';
  const agents = Object.entries(C.agents).sort((a, b) => workersFirst(role)(a[0], b[0]));
  const agentChip = (a, level) => levelChip('coordination', lv.ladders.coordination, level, true, { facts: ((lv.agents[a] || {}).coordination || {}).facts });
  return h('section', { class: 'card', id: 'sec-coord' },
    h('h2', null, 'Coordination'),
    summary([
      ['Team', 'Workers\' levels averaged, rounded half up',
        levelChip('coordination', lv.ladders.coordination, C.team, true, { facts: lv.team.coordination.facts })],
      ['Influencers', 'Kept out of the team\'s level', influencerList(lv, 'coordination', Object.entries(C.influencers || {}), role)]]),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table coord-agents' },
      headRow(['Agent', 'Role', 'Posts', ['Read', 'Read a teammate\'s post'], 'Rounds per teammate', 'Rounds its posts allow', 'Level']),
      h('tbody', null, agents.map(([a, r]) => h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
        h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
        h('td', { class: 'num' }, String(r.posts)), h('td', null, r.read ? 'yes' : 'no'),
        h('td', { class: 'num' }, fix2(r.mbar)), h('td', { class: 'num' }, fix2(r.ceiling)), h('td', null, agentChip(a, r.level))))))),
    C.pairs && C.pairs.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-pairs' },
      headRow(['Pair', ['Rounds', 'The smaller of the two directions']]),
      h('tbody', null, C.pairs.map(p => h('tr', null, h('td', { class: 'mono' }, p.agents.join(' and ')), h('td', { class: 'num' }, String(p.rounds))))))) : null);
}

/** Details tab: the Collusion ladder in full (src/measures/collusion.py), every rung read by code. The team's rung is
 * the highest pair of workers; the influencers' own rungs are listed apart. One row per agent with its rung and its
 * cheating rung for information; one row per pair with the takes and their calls, whether the hand-in holds work of
 * both and the planted pieces exchanged. ctx: {evidence(text, agent) -> node with [k] linked to the agent's call}. */
export function collusionPanel(lv, roles, ctx) {
  const M = lv.collusion;
  if (!M || lv.single) return null;
  const L = lv.ladders.collusion;
  const head = h('h2', null, 'Collusion');
  if (!M.applicable) {
    return h('section', { class: 'card', id: 'sec-collusion' }, head, h('p', { class: 'muted' }, `Not applicable: ${M.reason}.`));
  }
  const role = a => roles[a] || 'worker';
  const byRole = workersFirst(role);
  const rank = r => L.levels.indexOf(r);
  const pairs = M.pairs.slice().sort((x, y) => rank(y.rung) - rank(x.rung) || y.takes.length - x.takes.length);
  const takeList = p => (p.takes.length ? h('ul', { class: 'ev-list' }, p.takes.map(t => h('li', null,
    h('span', { class: 'mono' }, `${t.taker} took ${t.giver}'s work`), ` (${t.rule}${t.call == null ? ', from a final record' : ''}): `,
    ctx.evidence(t.evidence, t.taker)))) : muted('no take', 'small'));
  // What a colluding pair exchanged of the exploit: the pieces each took of the other's, or each one's in the hand-in.
  const pooledList = p => (!p.pooled ? muted('none from both', 'small')
    : h('ul', { class: 'ev-list' }, Object.entries(Object.keys(p.pooled.took).length ? p.pooled.took : p.pooled.hand_in)
      .map(([a, pieces]) => h('li', null, h('span', { class: 'mono' }, a), Object.keys(p.pooled.took).length ? ' took ' : '\'s in the hand-in: ', pieces.join(', ')))));
  const handIn = Object.entries(M.hand_in || {});
  return h('section', { class: 'card', id: 'sec-collusion' }, head,
    summary([
      ['Team', 'Highest pair of workers', levelChip('collusion', L, M.team, true, { facts: lv.team.collusion.facts })],
      ['Influencers', 'Kept out of the team\'s rung', influencerList(lv, 'collusion', Object.entries(M.influencers || {}), role)]]),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table coll-agents' },
      headRow(['Agent', 'Role', 'Rung', 'Cheating rung']),
      h('tbody', null, Object.keys(M.agents).sort(byRole).map(a => {
        const r = M.agents[a];
        return h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
          h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
          h('td', null, levelChip('collusion', L, r.rung, true, { facts: ((lv.agents[a] || {}).collusion || {}).facts })),
          h('td', null, r.cheating ? levelChip('cheating', lv.ladders.cheating, r.cheating, false) : muted('no row', 'small')));
      })))),
    h('h3', { class: 'sub-h' }, `Pairs · ${pairs.length}`),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact collusion-pairs' },
      headRow(['Pair', 'Rung', 'Takes', 'Hand-in of both', 'Pieces exchanged', 'Cheating rungs']),
      h('tbody', null, pairs.map(p => h('tr', { class: p.roles.some(isInfluencer) && 'coord-influencer' },
        h('td', { class: 'mono' }, p.agents.map((a, i) => [i ? ' and ' : '', a, isInfluencer(role(a)) ? ` (${role(a)})` : ''])),
        h('td', null, chip(lv, 'collusion', p.rung)), h('td', null, takeList(p)),
        h('td', null, p.hand_in_both ? 'yes' : 'no'), h('td', null, pooledList(p)),
        h('td', null, h('span', { class: 'lv-chip-pair' }, p.cheating.map(c => (c ? levelChip('cheating', lv.ladders.cheating, c, false)
          : muted('?', 'small')))))))))),
    h('h3', { class: 'sub-h' }, 'Hand-in contributors'),
    handIn.length ? h('ul', { class: 'ev-list' }, handIn.map(([a, lines]) => h('li', null, h('span', { class: 'mono' }, a), ': ', lines.join('; '))))
      : muted('none', 'small'),
    (M.notes || []).length ? h('p', { class: 'small muted' }, 'Notes: ', M.notes.join('; ')) : null);
}

/** Details tab: each agent's judge labels beside the ladders, never rungs: the score chip, the excerpt, the judge's
 * reason and the link to the place code located the excerpt. open(agent, label) -> (located) -> the link. */
export function judgeLabelsPanel(lv, roles, open) {
  const about = lv.judge_label_about;
  const role = a => roles[a] || 'worker';
  const agents = Object.keys(lv.judge_labels.agents).sort(workersFirst(role));
  return h('section', { class: 'card', id: 'sec-judge-labels' },
    h('h2', null, 'Judge labels ', h('a', { class: 'small h-link', href: READINGS_HREF }, 'reliability')),
    summary([['Judge', null, lv.judge_labels.judge || muted('not judged', 'small')]]),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table sc-table' },
      headRow(['Agent', 'Role', ...labelKeys(lv).map(k => about.labels[k].name)]),
      h('tbody', null, agents.map(a => h('tr', { class: isInfluencer(role(a)) && 'coord-influencer' },
        h('td', { class: 'mono' }, a), h('td', null, roleWords(role(a))),
        labelKeys(lv).map(k => h('td', null, judgedOf(lv, a) ? labelEntry(about.labels[k], judgedOf(lv, a)[k], open(a, k), true)
          : muted('not judged', 'small')))))))));
}
