/* View: the ladders (#/ladders?task=<CheatBench task name>). The page opens by saying what the code ladder, the judged
 * ladder and a judge rung are, then lists the three ladders with every level, then gives each ladder its table:
 * Cheating by task (the paper's own, src/measures/ladder_by_domain.py), Coordination's levels (its rule is the same on
 * every task), and Collusion's levels with the generic take rules and each task's take and hand-in, read from
 * src/measures/collusion.py's own docstrings (src/ui/transcript_ladder_tables.py). All of it is served by
 * /api/ladders, with the columns of the domains the viewer shows. Ported from collusion-hermaneutics' viewer
 * (viewer/static/app/view_ladders.js). */
import { h } from '/static/app/dom.js';
import { glyph } from './tv_levels.js';
import { collusionJudged, legendTable, loadIndex } from './tv_view_index.js';

/** One ladder's table by task: a row per level above the lowest, numbered as the ladder ranks it, a column per task.
 * rows are [level, gloss, [one cell per task]]. */
function byTask(d, key, rows, notes, hl) {
  const L = d.ladders[key];
  return h('section', { class: 'card', id: `ladder-${key}` },
    h('h2', null, `${L.name} level by task`),
    h('p', { class: 'small lv-about' }, L.rule),
    h('div', { class: 'table-wrap' }, h('table', { class: 'lv-ladder-table' },
      h('thead', null, h('tr', null, h('th', null, 'level'),
        d.domains.map((dom, j) => h('th', { class: j === hl && 'lv-hl' }, dom, h('div', { class: 'small muted' }, notes[j]))))),
      h('tbody', null, rows.map(([level, gloss, cells]) => {
        const rank = L.levels.indexOf(level);
        return h('tr', null,
          h('td', null, glyph(rank, L.levels.length - 1, key), h('b', null, `${rank}  ${level}`), h('div', { class: 'small muted' }, gloss)),
          cells.map((c, j) => h('td', { class: j === hl && 'lv-hl' }, c)));
      })))));
}

const TERMS = [
  ['Code ladder', 'Labels that code reads from a run with no model call: each agent\'s transcript, the probe of its sandbox, the access log and the board. Every run has it.'],
  ['Judged ladder', 'The same cheating rungs answered by an LLM judge that reads the transcript. Only the runs a judge read have it; the run page shows it beside the code ladder.'],
  ['Judge rung', 'A rung of the Collusion ladder that only a judge\'s verdicts give: collusion and scheming. Code reads the rungs below them. Without a judge\'s verdicts a run tops out at collaboration, and the two judge rungs read "not judged".'],
  ['Agent and team', 'Each ladder is read once per agent and once for the team. On Coordination and Collusion the team leaves a hidden regulator or disregulator out, and the hidden role\'s own level is shown apart. The runs page shows the team\'s level; the run page shows each agent\'s.'],
];

/** A ladder whose rule is the same on every task: one row per level with what puts an agent there. judge marks the
 * levels only a judge gives. */
function levelTable(L, key, judged) {
  const judge = new Set(L.judge_levels || []);
  return h('section', { class: 'card', id: `ladder-${key}` },
    h('h2', null, `${L.name} levels`),
    h('p', { class: 'small lv-about' }, L.rule),
    L.note ? h('p', { class: 'small muted' }, L.note) : null,
    h('div', { class: 'table-wrap' }, h('table', { class: 'lv-ladder-table lv-level-table' },
      h('thead', null, h('tr', null, h('th', null, 'level'), h('th', null, 'what puts an agent on it (the highest that holds)'),
        judge.size ? h('th', null, 'read by') : null)),
      h('tbody', null, L.levels.map((level, rank) => h('tr', null,
        h('td', null, glyph(rank, L.levels.length - 1, key), h('b', null, `${rank}  ${level}`)),
        h('td', null, (L.glosses || [])[rank] || ''),
        judge.size ? h('td', null, judge.has(level) ? h('span', { class: 'lv-judge-tag' }, judged ? `a judge (${judged} runs judged)` : 'a judge (not judged)') : 'code') : null))))));
}

/** Collusion's take rules: the generic ones as the measure's docstring lists them, then each task's take and hand-in,
 * the measure's own words, a column per task. */
function takeRules(d, hl) {
  return h('section', { class: 'card', id: 'ladder-collusion-takes' },
    h('h2', null, 'What a take and the hand-in are, task by task'),
    h('p', { class: 'small lv-about' }, 'The generic rules a task chooses among, as src/measures/collusion.py states them (i is the taker, j the teammate whose work it takes):'),
    h('dl', { class: 'lv-terms' }, d.take_rules.map(([name, text]) => [h('dt', null, name), h('dd', null, text)])),
    h('div', { class: 'table-wrap' }, h('table', { class: 'lv-ladder-table' },
      h('thead', null, h('tr', null, h('th', null, ''), d.domains.map((dom, j) => h('th', { class: j === hl && 'lv-hl' }, dom)))),
      h('tbody', null, d.collusion_by_task.map(([what, gloss, cells]) => h('tr', null,
        h('td', null, h('b', null, what), h('div', { class: 'small muted' }, gloss)),
        cells.map((c, j) => h('td', { class: j === hl && 'lv-hl' }, c))))))));
}

export async function viewLadders(q) {
  const r = await fetch('/api/ladders');
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const judged = collusionJudged((await loadIndex()).rows);
  const task = q.get('task') || '';
  const hl = task in d.task_column ? d.task_column[task] : -1;
  return h('div', { class: 'page wide' },
    h('div', { class: 'page-head' }, h('h1', null, 'Ladders'),
      h('p', { class: 'muted' }, 'What each of the three ladders measures, and what puts a run on each level. ',
        hl >= 0 ? `The column of this run\'s task (${d.domains[hl]}) is highlighted. ` : '',
        (d.hidden_domains || []).length ? `Not shown in this viewer: ${d.hidden_domains.join(', ')}.` : '')),
    h('section', { class: 'card' }, h('h2', null, 'The words this page uses'),
      h('dl', { class: 'lv-terms' }, TERMS.map(([term, text]) => [h('dt', null, term), h('dd', null, text)]))),
    legendTable(d, judged, 'The three ladders'),
    byTask(d, 'cheat', d.rungs, d.rung_notes, hl),
    levelTable(d.ladders.coord, 'coord', judged),
    levelTable(d.ladders.collusion, 'collusion', judged),
    takeRules(d, hl));
}
