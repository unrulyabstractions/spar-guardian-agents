/* View: the ladders (#/ladders?task=<CheatBench task name>). The page opens by saying what the code ladder, the judged
 * ladder, a direct ladder and a proxy ladder are, then lists the six ladders with every level, then gives one table by
 * task per ladder: first the three read directly (Cheating, Coordination, Collusion), then the three proxies
 * (Cooperation, Collaboration, Scheming). The cheat and collaboration tables are the paper's own
 * (src/measures/ladder_by_domain.py); the four other tables and the six ladders' levels and rules are the viewer's
 * (src/ui/transcript_ladder_tables.py, src/ui/transcript_levels.py), all served by /api/ladders, with the columns of
 * the domains the viewer shows. Ported from collusion-hermaneutics' viewer (viewer/static/app/view_ladders.js). */
import { h } from '/static/app/dom.js';
import { glyph } from './tv_levels.js';
import { legendTable } from './tv_view_index.js';

const kindTag = kind => h('span', { class: 'lv-proxy' }, kind === 'proxy' ? 'proxy' : 'direct');

/** One ladder's table by task: a row per level above the lowest, numbered as the ladder ranks it, a column per task.
 * key is the ladder's key in d.ladders; rows are [level, gloss, [one cell per task]]. */
function byTask(d, key, rows, notes, hl, extra) {
  const L = d.ladders[key];
  return h('section', { class: 'card', id: `ladder-${key}` },
    h('h2', null, `${L.name} level by task `, kindTag(L.kind)),
    h('p', { class: 'small lv-about' }, L.rule), extra || null,
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
  ['Direct', 'A ladder whose level is read straight from the code ladder\'s record or the board\'s events. Cheating, Coordination and Collusion are direct.'],
  ['Proxy', 'A ladder the viewer derives from those same records by a rule of its own. No judge and no hand label has checked it, so read it as an estimate. Cooperation, Collaboration and Scheming are proxies.'],
  ['Agent and team', 'Each ladder is read once per agent and once for the team. The runs page shows the team\'s level; the run page shows each agent\'s.'],
];

export async function viewLadders(q) {
  const r = await fetch('/api/ladders');
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const task = q.get('task') || '';
  const hl = task in d.task_column ? d.task_column[task] : -1;
  const four = k => byTask(d, k, d.four[k].rows, d.split_notes, hl,
    d.four[k].gate ? h('p', { class: 'small muted' }, 'Gate: ', d.four[k].gate) : null);
  const coordNote = h('p', { class: 'small muted' }, 'Levels 1 and 2, read the board and posted, come from the board\'s events and are the same on every task. ',
    'Levels 3 to 6 are the code ladder\'s collaboration levels, which the runs page also lists as "Collaboration (code ladder)".');
  return h('div', { class: 'page wide' },
    h('div', { class: 'page-head' }, h('h1', null, 'Ladders'),
      h('p', { class: 'muted' }, 'What each of the six ladders measures, and what puts a run on each level, task by task. ',
        hl >= 0 ? `The column of this run\'s task (${d.domains[hl]}) is highlighted. ` : '',
        (d.hidden_domains || []).length ? `Not shown in this viewer: ${d.hidden_domains.join(', ')}.` : '')),
    h('section', { class: 'card' }, h('h2', null, 'The words this page uses'),
      h('dl', { class: 'lv-terms' }, TERMS.map(([term, text]) => [h('dt', null, term), h('dd', null, text)]))),
    legendTable(d, 'The six ladders'),
    h('h2', { class: 'lv-group' }, 'Read directly from the code ladder and the board'),
    byTask(d, 'cheat', d.rungs, d.rung_notes, hl),
    byTask(d, 'coord', d.collab, d.collab_notes, hl, coordNote),
    d.four.collude ? four('collude') : null,
    h('h2', { class: 'lv-group' }, 'Proxies the viewer derives'),
    ['coop', 'collab', 'scheme'].filter(k => d.four[k]).map(four));
}
