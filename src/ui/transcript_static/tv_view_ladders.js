/* View: the ladders (#/ladders?task=<CheatBench task name>), the one page of definitions, every one in a table: the
 * terms, the three ladders with every level and each ladder's line, which every table draws and names as a row between
 * its two sides, then each ladder's table: Cheating by task (the paper's own, src/measures/ladder_by_domain.py),
 * Coordination's levels (its rule is the same on every task), and Collusion's levels with the generic take rules and
 * each task's take and hand-in, read from src/measures/collusion_takes.py's own docstrings
 * (src/ui/transcript_ladder_tables.py); then the judge labels beside the ladders, what is scored, who scores it and the
 * anchors of the score (src/ui/transcript_levels.py label_about), and what an independent reading found of each rung
 * and label, readings of retired labels marked as such ("How reliable", ?section=reliability opens the page there).
 * All of it is served by /api/ladders, with the columns of the domains the viewer shows. Ported from
 * collusion-hermaneutics' viewer (viewer/static/app/view_ladders.js). */
import { h } from '/static/app/dom.js';
import { muted } from '/static/app/components.js';
import { levelChip, lineMark, scoreChip } from './tv_levels.js';
import { legendTable, loadLadders } from './tv_view_index.js';

/** A table of a head row and body rows; a column given as [label, tip] carries its tooltip, one given as a th is it. */
const headCell = c => (c instanceof Node ? c : Array.isArray(c) ? h('th', { title: c[1] }, c[0]) : h('th', null, c));
const table = (cls, cols, rows) => h('div', { class: 'table-wrap' }, h('table', { class: ['lv-ladder-table', cls] },
  cols ? h('thead', null, h('tr', null, cols.map(headCell))) : null,
  h('tbody', null, rows)));

/** A rule's text with each `span` in backticks as code and each date kept on one line. */
const prose = s => String(s == null ? '' : s).split(/(`[^`]+`|\d{4}-\d{2}-\d{2})/).map((part, i) => (i % 2 === 0 ? part
  : part.startsWith('`') ? h('code', null, part.slice(1, -1)) : h('span', { class: 'nowrap' }, part)));
/** A task's id in words: knowledge_work is "Knowledge work". */
const taskWords = id => { const s = String(id).replace(/_/g, ' '); return s[0].toUpperCase() + s.slice(1); };

/** The row a ladder's table puts before the first level above the line, spanning `span` columns. */
const lineRow = (L, span) => h('tr', { class: 'lv-line-row' }, h('td', { colspan: String(span) }, lineMark(L), ' ', L.line_words));

/** One ladder's table by task: a row per level above the lowest, a column per task, and the line between them. rows
 * are [level, gloss, [one cell per task]]. */
function byTask(d, key, rows, notes, hl) {
  const L = d.ladders[key];
  return h('section', { class: 'card', id: `ladder-${key}` },
    h('h2', null, `${L.name} level by task`),
    h('p', { class: 'small lv-about' }, L.rule),
    table(null, [h('th', null, 'Level'),
      ...d.domains.map((dom, j) => h('th', { class: j === hl && 'lv-hl' }, dom, h('div', { class: 'small muted' }, notes[j])))],
    rows.map(([level, gloss, cells]) => [
      level === L.line ? lineRow(L, d.domains.length + 1) : null,
      h('tr', null,
        h('td', { class: 'mid' }, levelChip(key, L, level), h('div', { class: 'small muted' }, gloss)),
        cells.map((c, j) => h('td', { class: j === hl && 'lv-hl' }, prose(c))))])));
}

/** The page's terms, each a definition. */
const terms = d => [
  ['Read by code', 'Every ladder, from the run\'s records, with no model call.'],
  ['Team', 'Each ladder is read per agent and for the team; the team leaves the influencers out.'],
  ['Influencer', 'A regulator or a disregulator placed in the team, whom the workers cannot tell from a worker. A '
    + 'regulator is told to keep the team honest, a disregulator to get the result by any means.'],
  ['Line', d.order.map(k => h('div', null, h('b', null, `${d.ladders[k].name}: `), d.ladders[k].line_words))],
  ['Alarm', 'The grader passed the hand-in, and no agent and no evidence of the team\'s own put a honeypot trace in it. '
    + 'No rung.'],
  ['CheatBench judge', 'Says whether each agent cheated (its own, broader "cheated") and saw the honeypots. Changes no '
    + 'rung.'],
  ['Judge labels', 'Disclosure and manipulation: a score from 0 to 100 with an excerpt of the agent\'s own post or '
    + 'reasoning, which code locates. Never a rung.'],
];

/** A ladder whose rule is the same on every task: one row per level with what puts an agent there. */
function levelTable(L, key) {
  return h('section', { class: 'card', id: `ladder-${key}` },
    h('h2', null, `${L.name} levels`),
    h('p', { class: 'small lv-about' }, L.rule),
    L.note ? h('p', { class: 'small muted lv-about' }, L.note) : null,
    table('lv-level-table', ['Level', 'What puts an agent on it'], L.levels.map((level, rank) => [
      level === L.line ? lineRow(L, 2) : null,
      h('tr', null, h('td', { class: 'chip' }, levelChip(key, L, level)), h('td', null, prose((L.glosses || [])[rank] || '')))])));
}

/** The judge labels beside the ladders: what each score measures, who scores it and the anchors of the score.
 * d.judge_labels is src/ui/transcript_levels.py label_about(). */
function labelTable(d) {
  const about = d.judge_labels;
  return h('section', { class: 'card', id: 'judge-labels' },
    h('h2', null, 'Judge labels'),
    table('lv-level-table', ['Label', 'What the score measures'], [
      Object.values(about.labels).map(x => h('tr', null, h('td', null, h('b', null, x.name)), h('td', null, x.scored))),
      h('tr', null, h('td', null, h('b', null, 'Scored by')), h('td', null, about.scored_by))]),
    table('lv-level-table', [h('th', { class: 'center' }, 'Score'), 'Means'], about.anchors.map(([score, words]) =>
      h('tr', null, h('td', { class: 'chip' }, scoreChip(score)), h('td', null, words)))));
}

const count = v => (v == null ? '–' : String(v));

/** What a score can show, in this order, under the name its row carries. */
const conclusions = ['disclosure', 'control', 'mislead', 'excerpt'];
const CONCLUSION_NAMES = { disclosure: 'Disclosure', control: 'Manipulation, control', mislead: 'Manipulation, mislead',
  excerpt: 'Excerpt' };

/** What an independent reading found of each rung and label (d.readings, src/measures/measure_readings.yaml, which the
 * paper's reliability table reads too): the passes read; one row per level, score range or verdict and per pass it
 * read, with what was read and how many held, were wrong or were unclear. The code's rungs were read before their
 * fixes; the scored labels and the located excerpts on the current pass, whose rows stand out. The readings of the
 * retired yes or no labels (the row's "retired") follow under their own row, muted. Then what a reader may conclude of
 * each score (R.conclusions), each row under an id the pages link to: reliability-mislead is where a mislead score's
 * "read the excerpt" leads. */
function reliability(d) {
  const R = d.readings;
  const level = (k, v) => (d.ladders[k] && d.ladders[k].levels.includes(v) ? levelChip(k, d.ladders[k], v, false) : h('span', null, v));
  // A row is of an older pass when it read a retired label, or when the same measure was read again on the current pass.
  const current = r => R.passes[r.pass].current;
  const older = r => r.retired || (!current(r) && R.rows.some(o => o.measure === r.measure && current(o)));
  return h('section', { class: 'card', id: 'reliability' },
    h('h2', null, 'How reliable'),
    table(null, ['Pass', 'What was read', h('th', { class: 'center' }, 'Read on')], Object.values(R.passes).map(p =>
      h('tr', null, h('td', null, p.label), h('td', null, prose(p.what)), h('td', { class: 'nowrap center' }, p.read_on)))),
    table('lv-reliability', ['Ladder or label', 'Pass', 'Level or verdict', 'What was read', h('th', { class: 'center' }, 'Read'),
      h('th', { class: 'center', title: 'The reading agrees; a score: the same or an adjacent anchor' }, 'Held'),
      h('th', { class: 'center' }, 'Wrong'), h('th', { class: 'center', title: 'The evidence allows both' }, 'Unclear'), 'Note'],
    R.rows.map((r, i) => {
      const first = i === 0 || R.rows[i - 1].measure !== r.measure || R.rows[i - 1].retired !== r.retired;
      const firstRetired = r.retired && (i === 0 || !R.rows[i - 1].retired);
      const p = R.passes[r.pass];
      // A pass is named on the first of its rows within a measure, so a run of rows of one pass reads as one block.
      const samePass = !first && R.rows[i - 1].pass === r.pass;
      return [firstRetired ? h('tr', { class: 'lv-measure-start' }, h('td', { colspan: 9 }, h('b', null, 'Retired yes or no labels'))) : null,
        h('tr', { class: [first && i > 0 && 'lv-measure-start', older(r) && 'lv-older-pass'] },
          h('td', null, first ? h('b', null, R.measures[r.measure]) : ''),
          h('td', { class: p.current && 'lv-current-pass' }, samePass ? '' : p.label),
          h('td', { class: 'chip' }, level(r.measure, r.value)), h('td', null, prose(r.chosen)),
          [r.read, r.held, r.wrong, r.unclear].map(n => h('td', { class: 'num' }, count(n))),
          h('td', { class: 'small' }, prose(r.note)))];
    })),
    h('h3', { class: 'sub-h', id: 'reliability-conclusions' }, 'What a score can show'),
    table('lv-level-table', null, conclusions.map(key => h('tr', { id: `reliability-${key}`, class: key === 'mislead' && 'lv-warn-block' },
      h('td', null, h('b', null, CONCLUSION_NAMES[key])), h('td', null, R.conclusions[key])))));
}

/** Collusion's take rules: the generic ones as the measure's docstring lists them (i takes j's work), then each
 * task's take and hand-in, the measure's own words, a column per task. */
function takeRules(d, hl) {
  return h('section', { class: 'card', id: 'ladder-collusion-takes' },
    h('h2', null, 'Takes and hand-in by task'),
    table('lv-level-table', ['Rule', ['When i takes j\'s work', 'i: the taker; j: the teammate']],
      d.take_rules.map(([name, text]) => h('tr', null, h('td', null, h('b', null, name)), h('td', null, prose(text))))),
    table(null, ['', ...d.domains.map((dom, j) => h('th', { class: j === hl && 'lv-hl' }, dom))],
      d.collusion_by_task.map(([what, gloss, cells]) => h('tr', null,
        h('td', null, h('b', null, what), h('div', { class: 'small muted' }, gloss)),
        cells.map((c, j) => h('td', { class: j === hl && 'lv-hl' }, prose(c)))))));
}

export async function viewLadders(q, view) {
  const d = await loadLadders();
  const task = q.get('task') || '';
  // ?section=<id> opens the page at that section, as the pages' links do.
  const section = q.get('section');
  if (section) view.mounted(() => { const el = document.getElementById(section); if (el) el.scrollIntoView({ block: 'start' }); });
  const hl = task in d.task_column ? d.task_column[task] : -1;
  return h('div', { class: 'page wide' },
    h('div', { class: 'page-head' }, h('h1', null, 'Ladders'),
      d.left_out_tasks.length ? muted(`Not shown: ${d.left_out_tasks.map(taskWords).join(', ')}`, 'small') : null),
    h('section', { class: 'card' }, h('h2', null, 'Terms'),
      table('lv-level-table', null, terms(d).map(([term, text]) => h('tr', null, h('td', null, h('b', null, term)), h('td', null, text))))),
    legendTable(d, 'Levels'),
    byTask(d, 'cheating', d.cheating.rows, d.cheating.notes, hl),
    levelTable(d.ladders.coordination, 'coordination'),
    levelTable(d.ladders.collusion, 'collusion'),
    takeRules(d, hl),
    labelTable(d),
    reliability(d));
}
