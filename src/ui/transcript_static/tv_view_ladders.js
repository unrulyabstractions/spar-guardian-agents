/* View: the ladders (#/ladders?task=<CheatBench task name>). The page opens by saying what the code ladder, the judged
 * ladder, a direct ladder and a proxy ladder are, then lists the six ladders with every level, then gives one table by
 * task per ladder: first those read directly (Cheating, then Coordination, whose rule is the same on every task and
 * so has a table of its levels, not one by task, then the code ladder's collaboration levels by task, then Collusion,
 * which uses them), then the three proxies (Cooperation, Collaboration, Scheming). The cheat and collaboration tables
 * are the paper's own (src/measures/ladder_by_domain.py); the other tables and the six ladders' levels and rules are
 * the viewer's (src/ui/transcript_ladder_tables.py, src/ui/transcript_levels.py, src/measures/coordination.py), all
 * served by /api/ladders, with the columns of the domains the viewer shows. Ported from collusion-hermaneutics' viewer (viewer/static/app/view_ladders.js). */
import { h } from '/static/app/dom.js';
import { glyph } from './tv_levels.js';
import { legendTable } from './tv_view_index.js';

const kindTag = kind => h('span', { class: 'lv-proxy' }, kind === 'proxy' ? 'proxy' : 'direct');

/** One ladder's table by task: a row per level above the lowest, numbered as the ladder ranks it, a column per task.
 * key is the ladder's key in d.ladders (or its colour ramp's key when L, the ladder, is given); rows are [level, gloss,
 * [one cell per task]]. */
function byTask(d, key, rows, notes, hl, extra, L = d.ladders[key]) {
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

/** The Coordination ladder: one rule on every task, so one row per level with what puts an agent there. */
function coordTable(L) {
  return h('section', { class: 'card', id: 'ladder-coord' },
    h('h2', null, `${L.name} levels `, kindTag(L.kind)),
    h('p', { class: 'small lv-about' }, L.rule),
    L.note ? h('p', { class: 'small muted' }, L.note) : null,
    h('div', { class: 'table-wrap' }, h('table', { class: 'lv-ladder-table lv-coord-table' },
      h('thead', null, h('tr', null, h('th', null, 'level'), h('th', null, 'what puts an agent on it (the highest that holds)'))),
      h('tbody', null, L.levels.map((level, rank) => h('tr', null,
        h('td', null, glyph(rank, L.levels.length - 1, 'coord'), h('b', null, `${rank}  ${level}`)),
        h('td', null, (L.glosses || [])[rank] || '')))))));
}

export async function viewLadders(q) {
  const r = await fetch('/api/ladders');
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const task = q.get('task') || '';
  const hl = task in d.task_column ? d.task_column[task] : -1;
  const four = k => byTask(d, k, d.four[k].rows, d.split_notes, hl,
    d.four[k].gate ? h('p', { class: 'small muted' }, 'Gate: ', d.four[k].gate) : null);
  const collabNote = h('p', { class: 'small muted' }, 'These are the code ladder\'s own levels, which the runs page lists as "Collaboration (code ladder)". ',
    'They are not the Coordination ladder: Coordination counts back-and-forth on the board, these say how the honeypot travelled. Collusion reads these.');
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
    coordTable(d.ladders.coord),
    d.collab_ladder ? byTask(d, 'collab_code', d.collab, d.collab_notes, hl, collabNote, d.collab_ladder) : null,
    d.four.collude ? four('collude') : null,
    h('h2', { class: 'lv-group' }, 'Proxies the viewer derives'),
    ['coop', 'collab', 'scheme'].filter(k => d.four[k]).map(four));
}
