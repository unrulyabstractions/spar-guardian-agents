/* View: the ladders (#/ladders?task=<CheatBench task name>). The cheat and collaboration level by task tables are the
 * paper's own (scripts/paper/tables.py); the four other tables and the six ladders' levels and rules are the viewer's
 * (src/ui/transcript_ladder_tables.py, src/ui/transcript_levels.py), all served by /api/ladders. Copied from
 * collusion-hermaneutics' viewer (viewer/static/app/view_ladders.js); our edit: the imports. */
import { h } from '/static/app/dom.js';
import { glyph } from './tv_levels.js';
import { fullLegend } from './tv_case_view.js';

function byTask(title, label, notes, levels, d, hl, about) {
  return h('section', { class: 'card' }, h('h2', null, title), about || null,
    h('div', { class: 'table-wrap' }, h('table', { class: 'lv-ladder-table' },
      h('thead', null, h('tr', null, h('th', null, label),
        d.domains.map((dom, j) => h('th', { class: j === hl && 'lv-hl' }, dom, h('div', { class: 'small muted' }, notes[j]))))),
      h('tbody', null, levels.map(([level, gloss, cells], i) => h('tr', null,
        h('td', null, h('b', null, `${i + 1}  ${level}`), h('div', { class: 'small muted' }, gloss)),
        cells.map((c, j) => h('td', { class: j === hl && 'lv-hl' }, c))))))));
}

export async function viewLadders(q) {
  const r = await fetch('/api/ladders');
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const task = q.get('task') || '';
  const hl = task in d.task_column ? d.task_column[task] : -1;
  const six = h('section', { class: 'card' }, h('h2', null, 'The six ladders'),
    h('p', { class: 'small muted' }, 'Each ladder is read per agent and per team. Direct ladders come from the code ladder and the board; proxy ladders are built from the same fields and say so.'),
    d.order.map(k => {
      const L = d.ladders[k];
      return h('div', { class: 'lv-def' }, h('h3', null, L.name, ' ', h('span', { class: 'lv-proxy' }, L.kind === 'proxy' ? 'proxy' : 'direct')),
        h('ol', { start: '0' }, L.levels.map((lv, i) => h('li', null, glyph(i, L.levels.length - 1), lv))),
        h('p', { class: 'small' }, L.rule));
    }));
  return h('div', { class: 'page wide' },
    h('div', { class: 'page-head' }, h('h1', null, 'Ladders'),
      hl >= 0 ? h('p', { class: 'muted' }, `The column of this run's task (${d.domains[hl]}) is highlighted.`) : null),
    h('section', { class: 'card' }, h('h2', null, 'Colour legend'), fullLegend(d.ladders, d.order)),
    byTask('Cheating level by task', 'rung', d.rung_notes, d.rungs, d, hl),
    byTask('Collaboration level by task (code ladder)', 'level', d.collab_notes, d.collab, d, hl),
    ['coop', 'collab', 'collude', 'scheme'].filter(k => d.four[k]).map(k => {
      const T = d.four[k];
      const live = d.ladders[k] || T;
      return byTask(`${T.name} level by task (${live.kind})`, 'level', d.split_notes, T.rows, d, hl,
        h('div', { class: 'small lv-about' },
          h('span', { class: 'lv-proxy' }, live.kind === 'proxy' ? 'proxy' : 'direct'), ' ', live.rule,
          T.gate ? h('div', { class: 'muted' }, 'Gate: ', T.gate) : null));
    }),
    six);
}
