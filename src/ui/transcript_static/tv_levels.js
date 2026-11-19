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

const fix2 = v => (v == null ? '' : Number(v).toFixed(2));
const coordChip = (lv, level) => {
  const L = lv.ladders.coord;
  const i = L.levels.indexOf(level);
  return level == null ? h('span', { class: 'muted small' }, 'not applicable')
    : h('span', { class: 'lv-cell' }, glyph(i, L.levels.length - 1, 'coord'), h('b', { class: 'lv-name' }, level), h('span', { class: 'lv-rank' }, ` (${i} of ${L.levels.length - 1})`));
};

/** Team tab: the Coordination ladder in full (src/measures/coordination.py). The team's level is the workers' mean;
 * the hidden roles' own levels are listed apart and never enter it. One row per agent with its posts, its rounds per
 * teammate and the rounds its posts allow, and one row per pair with its rounds. */
export function coordinationPanel(lv, roles) {
  const C = lv.coordination;
  if (!C) return null;
  const L = lv.ladders.coord;
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
