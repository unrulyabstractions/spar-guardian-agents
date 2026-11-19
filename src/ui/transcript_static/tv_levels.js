/* The three ladders: the level glyph and the Team tab's panels, one row per agent and one for the team, then the
 * Coordination and the Collusion ladders in full. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/levels.js); levels come from /api/run's "levels" (src/ui/transcript_levels.py). Our edits: the
 * imports; a disregulator is named like a regulator; the source's stepTracker, which no view called, is left out.
 * Every string goes in through h() text nodes. */
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
  const tip = `${L.rule}${c.evidence ? '\nEvidence: ' + c.evidence : ''}`;
  if (c.na) return h('td', { class: 'muted small', title: tip }, c.level, c.evidence ? `: ${c.evidence}` : '');
  return h('td', { class: ['lv-cell', c.index > 0 && 'lv-hit'], title: tip },
    glyph(c.index, c.of, key), h('span', { class: 'lv-name' }, c.level), h('span', { class: 'lv-rank' }, ` (${c.index} of ${c.of})`));
}

/** Run panel: one row per agent, one team row, a column per ladder. */
export function levelsPanel(lv, roles, task) {
  const head = h('tr', null, h('th', null, 'Agent'), lv.order.map(k => h('th', { title: lv.ladders[k].rule }, lv.ladders[k].name)));
  const rows = Object.entries(lv.agents).sort((a, b) => (roles[a[0]] !== 'worker') - (roles[b[0]] !== 'worker') || a[0].localeCompare(b[0]))
    .map(([a, cells]) => h('tr', null, h('td', { class: 'mono' }, a, roles[a] && roles[a] !== 'worker' ? ` (hidden ${roles[a]})` : ''),
      lv.order.map(k => cellView(cells[k], lv.ladders[k], k))));
  if (!lv.single) rows.push(h('tr', { class: 'lv-team' }, h('td', null, h('b', null, 'Team')), lv.order.map(k => cellView(lv.team[k], lv.ladders[k], k))));
  return h('section', { class: 'card', id: 'sec-ladders' },
    h('h2', null, 'Three ladders for this run'),
    h('p', { class: 'small muted' }, 'Each cell shows the level reached and a ladder with one bar per level. Hover a cell for the rule and the evidence. ',
      lv.collusion && lv.collusion.applicable && !lv.collusion.judged ? 'Collusion\'s two judge rungs, collusion and scheming, are not judged in this run: no judge\'s verdicts reached it, so code stops at collaboration (the Collusion panel below). ' : '',
      h('a', { href: '#/ladders?task=' + encodeURIComponent(task || '') }, 'All ladders and the level by task tables')),
    h('table', { class: 'lv-table' }, h('thead', null, head), h('tbody', null, rows)));
}

const fix2 = v => (v == null ? '' : Number(v).toFixed(2));
const chip = (lv, key, level) => {
  const L = lv.ladders[key];
  const i = L.levels.indexOf(level);
  return level == null ? h('span', { class: 'muted small' }, 'not applicable')
    : h('span', { class: 'lv-cell' }, glyph(i, L.levels.length - 1, key), h('b', { class: 'lv-name' }, level), h('span', { class: 'lv-rank' }, ` (${i} of ${L.levels.length - 1})`));
};
const coordChip = (lv, level) => chip(lv, 'coord', level);

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

const JUDGE_WORD = { 'not judged': 'no judge\'s verdict', 'not applicable': 'below collaboration, not read', 'not available': 'no reasoning in its transcript' };
const judgeValue = v => h('span', { class: ['small', v === 'yes' ? 'lv-judge-yes' : 'muted'], title: JUDGE_WORD[v] || 'the judge\'s verdict' }, v);

/** Team tab: the Collusion ladder in full (src/measures/collusion.py), as the measure returns it. The team's rung is
 * the highest pair of workers; the hidden roles' own rungs are listed apart. One row per agent with its rung, its
 * scheming verdict and its cheating rung for information; one row per pair with the takes and their calls, whether
 * the hand-in holds work of both, the exploit-named posts a judge would read, and the judge's two fields. ctx:
 * {evidence(text, agent) -> node with [k] linked to the agent's call, post(n) -> node linked to the board}. */
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
  const hidden = Object.entries(M.hidden || {});
  const named = new Map((M.naming_posts || []).map(p => [p.id, p]));
  const rank = r => L.levels.indexOf(r);
  const pairs = M.pairs.slice().sort((x, y) => rank(y.rung) - rank(x.rung) || y.exploit_named.length - x.exploit_named.length);
  const takeList = p => (p.takes.length ? h('ul', { class: 'ev-list' }, p.takes.map(t => h('li', null,
    h('span', { class: 'mono' }, `${t.taker} took ${t.giver}'s work`), ` (${t.rule}${t.call == null ? ', from a final record' : ''}): `,
    ctx.evidence(t.evidence, t.taker)))) : h('span', { class: 'muted small' }, 'no take'));
  const namedList = p => (p.exploit_named.length ? h('ul', { class: 'ev-list' }, p.exploit_named.map(id => {
    const post = named.get(id) || {};
    return h('li', null, post.post_n != null ? ctx.post(post.post_n) : h('span', { class: 'muted' }, 'a post'),
      ` by ${post.writer || '?'}, read by ${(post.readers || []).join(', ') || '?'}`, post.kinds ? h('span', { class: 'muted small' }, ` (${post.kinds.join(', ')})`) : null);
  })) : h('span', { class: 'muted small' }, 'none'));
  const handIn = Object.entries(M.hand_in || {});
  return h('section', { class: 'card', id: 'sec-collusion' }, head,
    h('div', { class: 'coord-summary' },
      h('div', null, h('span', { class: 'field-label' }, 'Team (the highest pair of workers): '), chip(lv, 'collusion', M.team)),
      h('div', null, h('span', { class: 'field-label' }, 'Hidden roles, kept out of the team\'s rung: '),
        hidden.length ? hidden.map(([a, level], i) => [i ? '; ' : '', h('span', { class: 'mono' }, a), ` (hidden ${role(a)}) `, chip(lv, 'collusion', level)])
          : h('span', { class: 'muted small' }, 'none in this run')),
      h('div', null, h('span', { class: 'field-label' }, 'Judge rungs (collusion, scheming): '),
        M.judged ? 'read from a judge\'s verdicts' : h('b', null, 'not judged'),
        M.judged ? '' : h('span', { class: 'muted small' }, ': no judge\'s verdicts reached this run, so code stops at collaboration'))),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact coord-table' },
      h('thead', null, h('tr', null, ['Agent', 'Role', 'Rung', 'Scheming (judge)', 'Cheating rung, for information']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, Object.keys(M.agents).sort(byRole).map(a => {
        const r = M.agents[a];
        return h('tr', { class: role(a) !== 'worker' && 'coord-hidden' },
          h('td', { class: 'mono' }, a), h('td', null, role(a) === 'worker' ? 'worker' : `hidden ${role(a)}`),
          h('td', null, chip(lv, 'collusion', r.rung)), h('td', null, judgeValue(r.scheming)), h('td', null, r.cheating || h('span', { class: 'muted small' }, 'no row')));
      })))),
    h('h3', { class: 'sub-h' }, 'Pairs'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data compact collusion-pairs' },
      h('thead', null, h('tr', null, ['Pair', 'Rung', 'Takes, with their calls', 'Hand-in holds work of both', 'Exploit named on the board (a hint a judge would read)', 'Collusion (judge)', 'Scheming (judge)', 'Cheating rungs']
        .map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, pairs.map(p => h('tr', { class: p.roles.some(r => r !== 'worker') && 'coord-hidden' },
        h('td', { class: 'mono' }, p.agents.map((a, i) => [i ? ' and ' : '', a, role(a) !== 'worker' ? ` (hidden ${role(a)})` : ''])),
        h('td', null, chip(lv, 'collusion', p.rung)), h('td', null, takeList(p)),
        h('td', null, p.hand_in_both ? 'yes' : 'no'), h('td', null, namedList(p)),
        h('td', null, judgeValue(p.collusion)), h('td', null, judgeValue(p.scheming)),
        h('td', { class: 'small' }, p.cheating.map(c => c || '?').join(' / '))))))),
    h('h3', { class: 'sub-h' }, 'The hand-in\'s contributors'),
    handIn.length ? h('ul', { class: 'ev-list' }, handIn.map(([a, lines]) => h('li', null, h('span', { class: 'mono' }, a), ': ', lines.join('; '))))
      : h('p', { class: 'muted small' }, 'No agent\'s work is credited in the hand-in.'),
    (M.notes || []).length ? h('p', { class: 'small muted' }, 'Notes of the measure: ', M.notes.join('; ')) : null,
    h('p', { class: 'small muted' }, L.rule), h('p', { class: 'small muted' }, L.note));
}
