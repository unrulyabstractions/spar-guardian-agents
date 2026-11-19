/* The run page's Board tab: two agents of the swarm on one clock with the board between them, and below it the whole
 * message board filling as the position moves. Its data is /api/board?id= (src/ui/transcript_board.py).
 *
 *   import { renderBoard } from './tv_view_board.js';
 *   renderBoard(el, payload, { q, base: '#/run/<collection>/<run key>' });
 *
 * q (URLSearchParams) may carry a and b (the two agents), mode (time | step) and pos (seconds on the time clock, a
 * step number in step mode); the tab writes them back into the page's hash as they change, so a link or a screenshot
 * opens at the same place. The timeline is one SVG, drawn again only when the pair, the mode or the width changes;
 * a move of the position updates the cursor, the shade over what is still to come, the outlined current calls, the
 * arrows shown, the header and the board's shown posts, and nothing else. A post's arrows show for the post at the
 * position and the RECENT posts before it, and for a post under the pointer; every other post keeps its box alone.
 * The ladders' colours and chips are the page's own (tv_case_view.js RAMP, tv_levels.js levelChip and lineMark), each
 * ladder parted at its line. Every string goes in through h() and svgEl() text nodes.
 * It returns {moveTo(position), showPost(label), state}: showPost moves to post P<n> (the board's own numbering). */
import { appendAll, enc, fmtSecs, h, plural } from '/static/app/dom.js';
import { badge, longText, muted } from '/static/app/components.js';
import { COUNT_STEPS, TIME_STEPS, fmtTick, svgEl, ticksFor, tip } from '/static/app/charts.js';
import { RAMP } from './tv_case_view.js';
import { ROLE_LABEL } from './tv_labels.js';
import { levelChip, lineMark } from './tv_levels.js';

const GUTTER = 150;
const PAD_R = 16;
const LANE_H = 44;
const BOARD_H = 104;
const TOP = 8;
const POST_H = 15;
const POST_GAP = 3;
const RECENT = 3;             // the posts before the one at the position that keep their arrows
const PLAY_SECONDS = 90;      // a whole run plays in this long on the time clock
const STEP_MS = 220;          // one step at a time in step mode
const PLAIN = 'var(--bd-plain)';
const SIDES = ['a', 'b'];
const BOARD_LEVEL = { read: 'read', post: 'write' };   // a board call's own Coordination level, labelled or not

// A call's colour is the viewer's ramp at its highest label (RAMP, pale below each ladder's line and strong above):
// a Cheating label first, then a take (Collusion), then the Coordination level of a board read or post, else plain
// work. A Cheating label at or above the Cheating line fills the lane's whole height. ramp: {cheatLine, board: {read,
// post} -> the Coordination level's index}.
function callStyle(c, ramp) {
  const top = ladder => c.labels.filter(l => l.ladder === ladder).reduce((m, l) => Math.max(m, l.index), 0);
  const cheat = top('cheating');
  if (cheat) return { fill: RAMP.cheating[cheat], strong: cheat >= ramp.cheatLine, kind: 'cheating' };
  const coll = top('collusion');
  if (coll) return { fill: RAMP.collusion[coll], strong: false, kind: 'collusion' };
  const coord = top('coordination') || ramp.board[c.board] || 0;
  if (coord) return { fill: RAMP.coordination[coord], strong: false, kind: 'board' };
  return { fill: PLAIN, strong: false, kind: 'plain' };
}

const lastAtOrBefore = (keys, p) => {
  let lo = 0;
  let hi = keys.length - 1;
  let at = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (keys[mid] <= p) { at = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return at;
};

// The arrow keys step the Board tab on screen. One listener serves the page: it calls the handler of the latest
// tab rendered, kept by its element, so a tab the page dropped is freed and a tab hidden behind another ignores keys.
const KEYS = new WeakMap();
let shownTab = null;
document.addEventListener('keydown', ev => {
  const el = shownTab && shownTab.deref();
  if (el && el.isConnected) KEYS.get(el)(ev);
});

export function renderBoard(el, d, opts) {
  const byAgent = new Map(d.agents.map(a => [a.agent, a]));
  const timed = d.clock === 'eval_log';
  const cheat = d.ladders.cheating;
  const coord = d.ladders.coordination;
  const ramp = { cheatLine: cheat.levels.indexOf(cheat.line),
    board: Object.fromEntries(Object.entries(BOARD_LEVEL).map(([k, lv]) => [k, coord.levels.indexOf(lv)])) };
  const q = opts.q || new URLSearchParams();
  const pick = (v, fallback) => (byAgent.has(v) ? v : fallback);
  const state = {
    a: pick(q.get('a'), d.pair[0]),
    b: pick(q.get('b'), d.pair[1] || d.pair[0]),
    mode: q.get('mode') === 'step' || !timed ? 'step' : 'time',
    pos: q.get('pos') != null ? Number(q.get('pos')) : null,
    playing: false,
  };
  const callHref = (agent, k) => `${opts.base}?agent=${enc(agent)}&call=${k}`;
  const callLink = (agent, k) => h('a', { class: 'bd-call-link', href: callHref(agent, k),
    title: `open call ${k} in ${agent}'s tab` }, `call ${k}`);

  // ---- controls
  const agentOption = a => h('option', { value: a.agent },
    `${a.agent} (${ROLE_LABEL[a.role] || a.role}, ${plural(a.calls.length, 'call')})`);
  const agentSelect = (side, label) => h('select', { class: ['bd-sel', `bd-sel-${side}`], 'aria-label': label,
    onchange: e => choose(side, e.target.value) }, d.agents.map(agentOption));
  const selA = agentSelect('a', 'first agent');
  const selB = agentSelect('b', 'second agent');
  const modeSel = h('select', { 'aria-label': 'how the position moves',
    onchange: e => { const t = timeNow(); state.mode = e.target.value; build(t); } },
  h('option', { value: 'time', disabled: !timed }, timed ? 'elapsed time' : 'elapsed time (no times in this run)'),
  h('option', { value: 'step' }, 'step by model call'));
  const button = (text, title, onclick, cls) => h('button',
    { type: 'button', class: ['btn', 'btn-small', cls], title, onclick }, text);
  const back = button('◀ step', 'step back (left arrow)', () => step(-1));
  const playBtn = button('▶ play', 'play or pause', () => (state.playing ? pause() : play()), 'bd-play');
  const fwd = button('step ▶', 'step forward (right arrow)', () => step(1));
  const slider = h('input', { type: 'range', class: 'bd-slider', 'aria-label': 'position', min: '0',
    oninput: e => { pause(); moveTo(Number(e.target.value)); } });
  const posText = h('span', { class: 'bd-pos' });
  const nowA = h('div', { class: 'bd-now bd-now-a' });
  const nowB = h('div', { class: 'bd-now bd-now-b' });
  const endA = h('div', { class: 'bd-now bd-end bd-now-a' });
  const endB = h('div', { class: 'bd-now bd-end bd-now-b' });
  const svgHost = h('div', { class: 'bd-svg-host' });
  const boardList = h('ol', { class: 'bd-posts' });
  const empty = muted('No post yet at this position.', 'small block');
  const boardBox = h('div', { class: 'bd-board-box' }, empty, boardList);
  const boardHead = h('div', { class: 'bd-board-head' });

  // ---- the posts as the board shows them, built once; a change of the pair changes their classes only
  const items = d.posts.map((p, i) => {
    const readers = new Map();
    for (const r of p.reads) {
      if (!readers.has(r.agent)) readers.set(r.agent, []);
      readers.get(r.agent).push(r.k);
    }
    // The scheming judge's verdicts that quote this post: one per pair, the verdict that stands and the code's check.
    const marks = p.scheming.map(m => h('div', { class: ['bd-mark', m.verdict !== 'yes' && 'bd-mark-weak'] },
      h('div', null, badge(`scheming: ${m.which}`, 'bad', m.reason), ` the scheming judge on ${m.pair.join(' and ')}`,
        m.verdict === 'yes' ? '' : muted(` · the verdict did not stand: ${(m.scheming_check && m.scheming_check.why) || m.verdict}`, 'small')),
      m.quote ? h('blockquote', { class: 'bd-quote' }, m.quote) : null,
      m.reason ? h('div', { class: 'small muted' }, m.reason) : null));
    const readBy = [...readers].map(([agent, ks], j) => [j ? ', ' : '', agent, ' (', callLink(agent, ks[0]),
      ks.length > 1 ? h('span', { title: `every call of ${agent} that read it: ${ks.join(', ')}` },
        ` and ${ks.length - 1} more`) : null, ')']);
    const li = h('li', { class: ['bd-post', p.kind === 'system' && 'bd-system', p.scheming.length && 'bd-has-mark'],
      dataset: { i: String(i) }, hidden: true },
    h('div', { class: 'bd-post-head' },
      h('b', { class: 'bd-label' }, p.label),
      p.kind === 'system' ? badge('system notice', 'notice', 'written by the harness, not by an agent')
        : h('span', { class: 'bd-writer' }, p.writer),
      p.role && p.role !== 'worker' ? badge(ROLE_LABEL[p.role] || p.role, 'plain') : null,
      muted(timed ? ` at ${fmtSecs(p.t)}` : ` at call ${p.t + 1} of the run`, 'small'),
      p.call != null ? [' · ', callLink(p.writer, p.call)] : null,
      p.scheming.length ? badge('quoted by the scheming judge', 'bad',
        'the scheming judge quotes this post as showing the pair\'s awareness of cheating or deception') : null,
      h('span', { class: 'spacer' }),
      readers.size ? h('span', { class: 'small muted' }, 'first read by ', readBy)
        : muted('read by no teammate', 'small')),
      marks,
      longText(p.text, { lines: 8, chars: 1600, cls: 'bd-text' }));
    li.addEventListener('click', e => { if (!e.target.closest('a, button')) { pause(); moveTo(keyOf(p)); } });
    li.addEventListener('pointerenter', () => hover(i));
    li.addEventListener('pointerleave', () => hover(-1));
    boardList.appendChild(li);
    return { p, li };
  });

  // ---- what build() lays out for the current pair and mode
  let L = null;
  let shown = 0;
  let latest = -1;
  let hovered = -1;
  let lit = new Set();
  let current = {};
  let width = 0;
  const keyOf = x => (state.mode === 'time' ? x.t : x.u);

  function units() {
    // The stops of the position: each model call of the two agents (its calls together), and each post another agent
    // or the harness wrote. On the time clock a stop is a time; in step mode the stops are evenly spaced.
    const out = [];
    for (const side of SIDES) {
      const groups = new Map();
      for (const c of byAgent.get(state[side]).calls) {
        const g = c.m != null ? c.m : `k${c.k}`;
        if (!groups.has(g)) { groups.set(g, { t: c.t, agent: state[side], calls: [] }); out.push(groups.get(g)); }
        groups.get(g).calls.push(c);
      }
      if (state.a === state.b) break;
    }
    for (const p of d.posts) if (p.writer !== state.a && p.writer !== state.b) out.push({ t: p.t, post: p, calls: [] });
    out.sort((x, y) => x.t - y.t || String(x.agent).localeCompare(String(y.agent)));
    out.forEach((u, i) => { for (const c of u.calls) c.u = i; if (u.post) u.post.u = i; });
    const written = new Map(SIDES.flatMap(s => byAgent.get(state[s]).calls.map(c => [`${state[s]}:${c.k}`, c.u])));
    for (const p of d.posts) {
      if (p.writer === state.a || p.writer === state.b) p.u = written.get(`${p.writer}:${p.call}`) ?? 0;
      for (const r of p.reads) r.u = written.get(`${r.agent}:${r.k}`);
    }
    return out;
  }

  function build(time) {
    pause();
    selA.value = state.a;
    selB.value = state.b;
    modeSel.value = state.mode;
    width = Math.max(900, Math.floor(svgHost.clientWidth || el.clientWidth || 1200));
    const stops = units();
    const n = Math.max(1, stops.length);
    const span = state.mode === 'time' ? Math.max(d.total, 1e-6) : n;
    const x0 = GUTTER;
    const x1 = width - PAD_R;
    const X = v => x0 + (v / span) * (x1 - x0);
    const unitW = Math.max(1.5, (x1 - x0) / n - 1);
    const W = c => (state.mode === 'time' ? Math.max(1.5, X(c.t + c.dur) - X(c.t)) : unitW);
    const left = c => X(state.mode === 'time' ? c.t : c.u);      // a call's left edge
    const mid = x => (state.mode === 'time' ? X(x.t) : X(x.u) + unitW / 2);   // a post's or a read's middle
    const yA = TOP;
    const yBoard = yA + LANE_H + 8;
    const yB = yBoard + BOARD_H + 8;
    const yAxis = yB + LANE_H + 4;
    const H = yAxis + 24;
    const svg = svgEl('svg', { class: 'bd-svg', width, height: H, viewBox: `0 0 ${width} ${H}`, role: 'img',
      'aria-label': `the calls of ${state.a} and ${state.b} with the board between them` });
    const uid = Math.random().toString(36).slice(2, 8);
    svg.appendChild(svgEl('defs', null, ...SIDES.map(s => svgEl('marker', { id: `bd-arrow-${s}-${uid}`,
      viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' },
    svgEl('path', { d: 'M0,0 L8,4 L0,8 z', class: `bd-arrowhead-${s}` })))));

    // lanes: the two agents and the board between them
    const laneY = { a: yA, b: yB };
    const lanes = svgEl('g', { class: 'bd-lanes' });
    for (const side of SIDES) {
      const ag = byAgent.get(state[side]);
      const y = laneY[side];
      lanes.append(svgEl('rect', { class: `bd-lane bd-lane-${side}`, x: x0, y, width: x1 - x0, height: LANE_H }),
        svgEl('rect', { class: `bd-swatch-${side}`, x: 6, y: y + 8, width: 6, height: LANE_H - 16, rx: 2 }),
        svgEl('text', { x: 18, y: y + 19, class: 'bd-name' }, ag.agent),
        svgEl('text', { x: 18, y: y + 33, class: 'bd-role' },
          `${ROLE_LABEL[ag.role] || ag.role}, ${plural(ag.calls.length, 'call')}`));
    }
    lanes.append(svgEl('rect', { class: 'bd-lane bd-lane-board', x: x0, y: yBoard, width: x1 - x0, height: BOARD_H }),
      svgEl('text', { x: 18, y: yBoard + 20, class: 'bd-name' }, 'board'),
      svgEl('text', { x: 18, y: yBoard + 34, class: 'bd-role' }, plural(d.posts.length, 'post')));
    svg.appendChild(lanes);

    // axis
    const axis = svgEl('g', { class: 'bd-axis' });
    axis.appendChild(svgEl('line', { x1: x0, x2: x1, y1: yAxis, y2: yAxis }));
    const most = Math.floor((x1 - x0) / 80);
    const ticks = state.mode === 'time' ? ticksFor(0, span, most, timed ? TIME_STEPS : COUNT_STEPS)
      : ticksFor(0, n - 1, most, COUNT_STEPS);
    const tickText = v => (state.mode === 'step' ? `step ${v + 1}` : timed ? fmtTick(v) : `call ${v + 1}`);
    for (const v of ticks) {
      const x = mid({ t: v, u: v });
      axis.append(svgEl('line', { x1: x, x2: x, y1: yAxis, y2: yAxis + 4 }),
        svgEl('line', { class: 'bd-grid', x1: x, x2: x, y1: yA, y2: yAxis }),
        svgEl('text', { x, y: yAxis + 16, 'text-anchor': 'middle' }, tickText(v)));
    }
    axis.appendChild(svgEl('text', { x: 18, y: yAxis + 16, class: 'bd-role' },
      state.mode === 'step' ? 'step' : timed ? 'elapsed' : 'call order'));
    svg.insertBefore(axis, lanes.nextSibling);

    // calls: a thin bar for ordinary work, a taller one for a labelled call, the lane's full height past the
    // Cheating line
    const callsG = svgEl('g', { class: 'bd-calls' });
    const keys = { a: [], b: [] };
    const calls = { a: [], b: [] };
    for (const side of SIDES) {
      const ag = byAgent.get(state[side]);
      const end = side === 'a' ? endA : endB;
      end.replaceChildren();
      if (ag.uncited.length && (side === 'a' || state.a !== state.b)) appendAll(end, endLine(side, ag.uncited));
      end.hidden = !end.childNodes.length;
      for (const c of ag.calls) {
        const st = callStyle(c, ramp);
        const tall = st.strong ? LANE_H - 4 : st.kind === 'plain' ? 14 : 24;
        callsG.appendChild(svgEl('rect', { class: ['bd-call', st.strong && 'bd-strong'],
          x: left(c), y: laneY[side] + (LANE_H - tall) / 2, width: W(c), height: tall, fill: st.fill,
          'data-side': side, 'data-k': c.k }));
        keys[side].push(state.mode === 'time' ? c.t : c.u);
        calls[side].push(c);
      }
    }
    svg.appendChild(callsG);

    // posts on the board lane, packed into rows so none hides another; then each post's arrows, from its writing call
    // and to each of the two agents' first read, in a group of its own that showArrows() shows or hides
    const postsG = svgEl('g', { class: 'bd-board-posts' });
    const arrowsG = svgEl('g', { class: 'bd-arrows' });
    const rows = Math.floor((BOARD_H - 8) / (POST_H + POST_GAP));
    const pack = boxW => {
      const ends = new Array(rows).fill(-Infinity);
      const at = [];
      let ok = true;
      for (const p of d.posts) {
        const from = Math.min(Math.max(x0, mid(p) - boxW / 2), x1 - boxW);
        let r = ends.findIndex(e => e + POST_GAP <= from);
        if (r < 0) { ok = false; r = ends.indexOf(Math.min(...ends)); }
        ends[r] = from + boxW;
        at.push({ from, r });
      }
      return { at, ok };
    };
    let boxW = 30;
    let packed = pack(boxW);
    if (!packed.ok) { boxW = 22; packed = pack(boxW); }
    if (!packed.ok) { boxW = 9; packed = pack(boxW); }
    const postBox = new Map();
    const callOf = { a: new Map(byAgent.get(state.a).calls.map(c => [c.k, c])),
      b: new Map(byAgent.get(state.b).calls.map(c => [c.k, c])) };
    const laneEdge = s => (s === 'a' ? yA + LANE_H : yB);
    const arrow = (into, s, cls, x1, y1, x2, y2) => into.appendChild(svgEl('line', {
      class: ['bd-arrow', cls, `bd-arrow-${s}`], x1, y1, x2, y2, 'marker-end': `url(#bd-arrow-${s}-${uid})` }));
    d.posts.forEach((p, i) => {
      const { from, r } = packed.at[i];
      const y = yBoard + 4 + r * (POST_H + POST_GAP);
      const side = p.writer === state.a ? 'a' : p.writer === state.b ? 'b' : null;
      const by = side ? `bd-by-${side}` : p.kind === 'system' ? 'bd-by-system' : 'bd-by-other';
      const g = svgEl('g', { class: ['bd-pbox', 'bd-ahead', by, p.scheming.length && 'bd-pbox-mark'], 'data-i': i },
        svgEl('rect', { x: from, y, width: boxW, height: POST_H, rx: 3 }),
        boxW >= 20 ? svgEl('text', { x: from + boxW / 2, y: y + POST_H - 4, 'text-anchor': 'middle' }, p.label) : null,
        p.scheming.length ? svgEl('path', { class: 'bd-mark-glyph',
          d: `M${from + boxW},${y + POST_H / 2 - 6} l6,6 l-6,6 l-6,-6 z` }) : null);
      postsG.appendChild(g);
      const arrows = svgEl('g', { visibility: 'hidden' });
      arrowsG.appendChild(arrows);
      postBox.set(i, { from, y, w: boxW, g, arrows });
      const cx = from + boxW / 2;
      const edge = s => (s === 'a' ? y : y + POST_H);
      const writing = side && callOf[side].get(p.call);
      if (writing) arrow(arrows, side, null, left(writing) + W(writing) / 2, laneEdge(side), cx, edge(side));
      for (const s of state.a === state.b ? ['a'] : SIDES) {
        const first = p.writer !== state[s] && p.reads.find(x => x.agent === state[s]);
        const reading = first && callOf[s].get(first.k);
        if (reading) arrow(arrows, s, 'bd-read', cx, edge(s), left(reading) + W(reading) / 2, laneEdge(s));
      }
    });
    svg.appendChild(arrowsG);

    // what moves: the shade over what is still to come, the cursor, the outlined current calls and latest post
    const shade = svgEl('rect', { class: 'bd-future', x: x0, y: yA - 2, width: 0, height: yAxis - yA + 2 });
    const cursor = svgEl('line', { class: 'bd-cursor', x1: x0, x2: x0, y1: yA - 4, y2: yAxis + 2 });
    const outlineAt = y => svgEl('rect', { class: 'bd-current', x: 0, y: y - 1, width: 0, height: LANE_H + 2,
      visibility: 'hidden' });
    const outline = { a: outlineAt(yA), b: outlineAt(yB) };
    const latestBox = svgEl('rect', { class: 'bd-latest-box', x: 0, y: 0, width: 0, height: POST_H + 4, rx: 4,
      visibility: 'hidden' });
    // the shade covers the calls and arrows still to come; a post still to come is faded by its class, so a post
    // the position has reached shows whole even where its box reaches past the cursor
    svg.append(shade, postsG, cursor, outline.a, outline.b, latestBox);
    svg.addEventListener('pointermove', ev => {
      const t = ev.target.closest('[data-k], [data-i]');
      hover(t && t.hasAttribute('data-i') ? Number(t.getAttribute('data-i')) : -1);
      if (!t) { tip.hide(); return; }
      if (t.hasAttribute('data-k')) {
        const side = t.getAttribute('data-side');
        const c = callOf[side].get(Number(t.getAttribute('data-k')));
        tip.show(ev, [`${state[side]}: call ${c.k} (${c.tool})`,
          timed ? `at ${fmtSecs(c.t)}, for ${fmtSecs(c.dur)}` : `call ${c.t + 1} of the run`, c.summary,
          ...c.labels.map(l => `${d.ladders[l.ladder].name}: ${l.level}${l.evidence ? ` (${l.evidence})` : ''}`),
          'click to open the call in its tab']);
      } else {
        const p = d.posts[Number(t.getAttribute('data-i'))];
        tip.show(ev, [`${p.label} by ${p.writer}`, timed ? `at ${fmtSecs(p.t)}` : null, p.text.slice(0, 220),
          ...p.scheming.map(m => `scheming judge on ${m.pair.join(' and ')}: ${m.verdict}, "${m.quote.slice(0, 160)}"`), 'click to move here']);
      }
    });
    svg.addEventListener('pointerleave', () => { tip.hide(); hover(-1); });
    svg.addEventListener('click', ev => {
      const t = ev.target.closest('[data-k], [data-i]');
      if (!t) return;
      if (t.hasAttribute('data-k')) {
        location.hash = callHref(state[t.getAttribute('data-side')], Number(t.getAttribute('data-k')));
      }
      else { pause(); moveTo(keyOf(d.posts[Number(t.getAttribute('data-i'))])); }
    });
    svgHost.replaceChildren(svg);

    // the board's events in order (each post written, and each read by one of the two), for the latest post
    const events = [];
    d.posts.forEach((p, i) => {
      events.push({ key: keyOf(p), i });
      for (const r of p.reads) if (r.agent === state.a || r.agent === state.b) events.push({ key: keyOf(r), i });
    });
    events.sort((x, y) => x.key - y.key);
    for (const { p, li } of items) {
      li.classList.toggle('bd-mine-a', p.writer === state.a);
      li.classList.toggle('bd-mine-b', p.writer === state.b && state.a !== state.b);
    }
    const postKeys = d.posts.map(keyOf);
    const order = d.posts.map((_, i) => i).sort((x, y) => postKeys[x] - postKeys[y]);
    L = { X, W, left, unitW, stops, n, span, keys, calls, shade, cursor, outline, latestBox, postBox, events, order, x1,
      orderKeys: order.map(i => postKeys[i]), eventKeys: events.map(x => x.key),
      stopKeys: stops.map((u, i) => (state.mode === 'time' ? u.t : i)) };
    slider.max = String(state.mode === 'time' ? span : n - 1);
    slider.step = state.mode === 'time' ? String(span / 2000) : '1';
    shown = 0;
    latest = -1;
    hovered = -1;
    lit = new Set();   // the new SVG's arrows start hidden
    current = {};      // undefined, so the first move writes both header lines
    for (const { li } of items) li.hidden = true;   // the new SVG's post boxes start faded (bd-ahead)
    boardHead.replaceChildren(h('h3', null, 'The message board'),
      muted(` ${plural(d.posts.length, 'post')} by every agent, shown as the position reaches them. Posts by `,
        'small'),
      h('span', { class: 'bd-key-a' }, state.a), muted(' and ', 'small'), h('span', { class: 'bd-key-b' }, state.b),
      muted(' carry their lane\'s colour; the outlined post is the latest written or read by them at the position. '
        + 'Click a post to move there.', 'small'));
    const at = time == null ? state.pos : state.mode === 'time' ? time : lastAtOrBefore(stops.map(u => u.t), time);
    moveTo(at != null && Number.isFinite(at) ? Math.max(0, at) : 0);
  }

  // ---- moving the position
  function moveTo(p) {
    const max = state.mode === 'time' ? L.span : L.n - 1;
    state.pos = Math.min(max, Math.max(0, state.mode === 'step' ? Math.round(p) : p));
    const P = state.pos;
    const x = state.mode === 'time' ? L.X(P) : L.X(P) + L.unitW;
    L.cursor.setAttribute('x1', x);
    L.cursor.setAttribute('x2', x);
    L.shade.setAttribute('x', x);
    L.shade.setAttribute('width', Math.max(0, L.x1 - x));
    slider.value = String(P);
    posText.textContent = state.mode === 'time'
      ? `${timed ? fmtSecs(P) : `call ${Math.floor(P) + 1}`} of ${timed ? fmtSecs(L.span) : plural(L.span, 'call')}`
      : `step ${P + 1} of ${L.n}${timed ? ` (${fmtSecs(L.stops[P].t)} of ${fmtSecs(d.total)})` : ''}`;
    for (const side of SIDES) {
      const i = lastAtOrBefore(L.keys[side], P);
      const c = i >= 0 ? L.calls[side][i] : null;
      if (c === current[side]) continue;
      current[side] = c;
      const o = L.outline[side];
      if (c) {
        o.setAttribute('x', L.left(c) - 2);
        o.setAttribute('width', L.W(c) + 4);
        o.setAttribute('visibility', 'visible');
      } else o.setAttribute('visibility', 'hidden');
      const line = side === 'a' ? nowA : nowB;
      line.replaceChildren();
      appendAll(line, nowLine(side, c));
    }
    // the board: show each post the position has reached, hide the rest
    const k = lastAtOrBefore(L.orderKeys, P) + 1;
    const reveal = (i, on) => { items[i].li.hidden = !on; L.postBox.get(i).g.classList.toggle('bd-ahead', !on); };
    while (shown < k) reveal(L.order[shown++], true);
    while (shown > k) reveal(L.order[--shown], false);
    empty.hidden = shown > 0;
    showArrows();
    const e = lastAtOrBefore(L.eventKeys, P);
    const now = e >= 0 ? L.events[e].i : -1;
    if (now !== latest) {
      if (latest >= 0) items[latest].li.classList.remove('bd-latest');
      latest = now;
      if (now >= 0) {
        const li = items[now].li;
        li.classList.add('bd-latest');
        const box = L.postBox.get(now);
        L.latestBox.setAttribute('x', box.from - 2);
        L.latestBox.setAttribute('y', box.y - 2);
        L.latestBox.setAttribute('width', box.w + 4);
        L.latestBox.setAttribute('visibility', 'visible');
        const top = li.offsetTop;
        if (top < boardBox.scrollTop || top + li.offsetHeight > boardBox.scrollTop + boardBox.clientHeight) {
          boardBox.scrollTop = Math.max(0, top - boardBox.clientHeight / 3);
        }
      } else L.latestBox.setAttribute('visibility', 'hidden');
    }
    remember();
  }

  // The arrows shown: the post at the position, the RECENT posts before it, and the post under the pointer.
  function showArrows() {
    const want = new Set(L.order.slice(Math.max(0, shown - 1 - RECENT), shown));
    if (hovered >= 0) want.add(hovered);
    for (const i of lit) if (!want.has(i)) L.postBox.get(i).arrows.setAttribute('visibility', 'hidden');
    for (const i of want) if (!lit.has(i)) L.postBox.get(i).arrows.setAttribute('visibility', 'visible');
    lit = want;
  }
  function hover(i) {
    if (i === hovered || !L) return;
    hovered = i;
    showArrows();
  }

  const chip = l => h('span', { class: 'bd-chip', title: l.evidence || '' },
    `${d.ladders[l.ladder].name} `, levelChip(l.ladder, d.ladders[l.ladder], l.level, false));

  // The levels an agent reached whose evidence cites no call (a record of the run: the access log, the remote's
  // pushes, the hand-in, or a count over the whole run), which no bar of its lane can carry.
  function endLine(side, levels) {
    return [h('b', { class: `bd-key-${side}` }, state[side]),
      muted(' also reached, on evidence that cites no call (hover for it): ', 'small'), ...levels.map(chip)];
  }

  function nowLine(side, c) {
    const who = h('b', { class: `bd-key-${side}` }, state[side]);
    if (!c) return [who, muted(' has made no call yet', 'small')];
    return [who, ' ', callLink(state[side], c.k), muted(` ${c.tool}`, 'small'), ' ',
      c.summary !== c.tool ? h('span', { class: 'bd-sum' }, c.summary) : null, ...c.labels.map(chip)];
  }

  function step(dir) {
    pause();
    const ks = L.stopKeys;
    const P = state.pos;
    if (state.mode === 'step') return moveTo(P + dir);
    const i = dir > 0 ? ks.findIndex(v => v > P + 1e-6) : lastAtOrBefore(ks, P - 1e-6);
    return moveTo(i < 0 ? (dir > 0 ? L.span : 0) : ks[i]);
  }

  let raf = 0;
  let timer = 0;
  function play() {
    if (state.pos >= (state.mode === 'time' ? L.span : L.n - 1)) moveTo(0);
    state.playing = true;
    playBtn.textContent = '❚❚ pause';
    if (state.mode === 'step') {
      timer = setInterval(() => {
        if (state.pos >= L.n - 1 || !el.isConnected) pause();
        else moveTo(state.pos + 1);
      }, STEP_MS);
      return;
    }
    let last = performance.now();
    const tick = now => {
      if (!state.playing || !el.isConnected) return;
      moveTo(state.pos + ((now - last) / 1000) * (L.span / PLAY_SECONDS));
      last = now;
      if (state.pos >= L.span) pause(); else raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }
  function pause() {
    state.playing = false;
    cancelAnimationFrame(raf);
    clearInterval(timer);
    playBtn.textContent = '▶ play';
    remember(true);
  }

  // The tab's place in the page's hash: a, b, mode and pos, written at most five times a second.
  let wrote = 0;
  let pending = 0;
  function remember(now) {
    if (!opts.base || !L) return;
    const write = () => {
      wrote = performance.now();
      const p = new URLSearchParams({ tab: 'board', a: state.a, b: state.b, mode: state.mode,
        pos: state.mode === 'time' ? String(Math.round(state.pos * 10) / 10) : String(state.pos) });
      history.replaceState(null, '', `${opts.base}?${p}`);
    };
    clearTimeout(pending);
    if (now || performance.now() - wrote > 200) write();
    else pending = setTimeout(write, 200);
  }

  function choose(side, agent) {
    const t = timeNow();
    state[side] = agent;
    build(t);
  }
  // The position as a time, kept when the pair or the mode changes.
  const timeNow = () => (state.mode === 'time' ? state.pos : (L.stops[state.pos] || { t: 0 }).t);

  KEYS.set(el, ev => {
    const typing = ev.target instanceof Element && ev.target.closest('input, select, textarea, [contenteditable]');
    if (!el.offsetParent || typing || (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight')) return;
    ev.preventDefault();
    step(ev.key === 'ArrowLeft' ? -1 : 1);
  });
  shownTab = new WeakRef(el);

  // ---- legend
  const sw = (fill, cls) => h('i', { class: ['bd-sw', cls], style: fill ? `background:${fill}` : null });
  const legend = h('div', { class: 'bd-legend' },
    h('span', { class: 'bd-lg' }, sw(null, 'bd-sw-a'), sw(null, 'bd-sw-b'), 'the two agents\' lanes and their posts'),
    h('span', { class: 'bd-lg' }, sw(PLAIN), 'ordinary work'),
    h('span', { class: 'bd-lg' }, 'board read or post: ',
      Object.values(BOARD_LEVEL).map(lv => [levelChip('coordination', coord, lv, false), ' '])),
    h('span', { class: 'bd-lg' }, 'took a teammate\'s work: ',
      levelChip('collusion', d.ladders.collusion, d.ladders.collusion.levels[1], false)),
    h('span', { class: 'bd-lg', title: `${cheat.line_words}: a call at the line or above fills its lane's height` },
      'Cheating label: ', cheat.levels.slice(1).map(lv => [lv === cheat.line ? lineMark(cheat) : null,
        levelChip('cheating', cheat, lv, false)])),
    h('span', { class: 'bd-lg' }, h('i', { class: 'bd-sw bd-sw-mark' }), 'the scheming judge quotes the post'),
    h('span', { class: 'bd-lg' }, h('i', { class: 'bd-sw bd-sw-latest' }), 'latest post written or read by the two'),
    h('span', { class: 'bd-lg' }, `→ from the writing call to its post, and from a post to each agent's first read `
      + `of it: for the post at the position, the ${RECENT} before it and the post under the pointer`));
  const notes = (d.notes || []).length ? ` ${d.notes.join(' ')}` : '';

  const header = h('div', { class: 'bd-head' },
    h('div', { class: 'bd-controls' },
      h('label', { class: 'bd-pick' }, sw(null, 'bd-sw-a'), selA),
      h('label', { class: 'bd-pick' }, sw(null, 'bd-sw-b'), selB),
      back, playBtn, fwd, modeSel, slider, posText),
    nowA, nowB, endA, endB);
  const note = muted(`Clock: ${d.clock_about}. Default pair: ${d.pair.join(' and ')}, ${d.pair_why}.${notes}`,
    'small block bd-note');
  el.replaceChildren(h('div', { class: 'bd-tab' },
    h('section', { class: 'card bd-timeline' }, header, svgHost, legend, note),
    h('section', { class: 'card bd-board' }, boardHead, boardBox)));

  let resizeTimer = 0;
  new ResizeObserver(() => {
    const w = Math.floor(svgHost.clientWidth);
    if (!L || !w || Math.abs(Math.max(900, w) - width) < 8) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => build(), 120);
  }).observe(svgHost);
  build();
  // showPost(label): post P<n> of the board, the position moved to it and its entry flashed
  function showPost(label) {
    const i = d.posts.findIndex(p => p.label === label);
    if (i < 0) return null;
    pause();
    moveTo(keyOf(d.posts[i]));
    const li = items[i].li;
    li.scrollIntoView({ block: 'center' });
    li.classList.remove('flash');
    void li.offsetWidth;
    li.classList.add('flash');
    return li;
  }
  return { moveTo, showPost, state };
}
