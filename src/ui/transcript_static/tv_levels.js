/* The six ladders: the level glyph and the Team tab's panel, one row per agent and one for the team. Copied from
 * collusion-hermaneutics' viewer (viewer/static/app/levels.js); levels come from /api/run's "levels"
 * (src/ui/transcript_levels.py). Our edits: the imports; a disregulator is named like a regulator; the source's
 * stepTracker, which no view called, is left out. Every string goes in through h() text nodes. */
import { h } from '/static/app/dom.js';
import { RAMP } from './tv_case_view.js';

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
  if (c.na) return h('td', { class: 'muted small', title: L.rule }, c.level);
  const tip = `${L.kind === 'proxy' ? 'Proxy' : 'Direct'} rule. ${L.rule}${c.evidence ? '\nEvidence: ' + c.evidence : ''}`;
  return h('td', { class: ['lv-cell', c.index > 0 && 'lv-hit'], title: tip },
    glyph(c.index, c.of, key), h('span', { class: 'lv-name' }, c.level), h('span', { class: 'lv-rank' }, ` (${c.index} of ${c.of})`),
    L.kind === 'proxy' ? h('span', { class: 'lv-proxy' }, 'proxy') : null);
}

/** Run panel: one row per agent, one team row, six columns. */
export function levelsPanel(lv, roles, task) {
  const head = h('tr', null, h('th', null, 'Agent'), lv.order.map(k => h('th', { title: lv.ladders[k].rule },
    lv.ladders[k].name, lv.ladders[k].kind === 'proxy' ? ' (proxy)' : '')));
  const rows = Object.entries(lv.agents).sort((a, b) => (roles[a[0]] !== 'worker') - (roles[b[0]] !== 'worker') || a[0].localeCompare(b[0]))
    .map(([a, cells]) => h('tr', null, h('td', { class: 'mono' }, a, roles[a] && roles[a] !== 'worker' ? ` (hidden ${roles[a]})` : ''),
      lv.order.map(k => cellView(cells[k], lv.ladders[k], k))));
  if (!lv.single) rows.push(h('tr', { class: 'lv-team' }, h('td', null, h('b', null, 'Team')), lv.order.map(k => cellView(lv.team[k], lv.ladders[k], k))));
  return h('section', { class: 'card', id: 'sec-six' },
    h('h2', null, 'Six ladders for this run'),
    h('p', { class: 'small muted' }, 'Each cell shows the level reached and a ladder with one bar per level. Hover a cell for the rule, whether it is direct or a proxy, and the evidence. ',
      h('a', { href: '#/ladders?task=' + encodeURIComponent(task || '') }, 'All ladders and the level by task tables')),
    h('table', { class: 'lv-table' }, h('thead', null, head), h('tbody', null, rows)));
}
