/* The run page's Board tab: two agents of the swarm on one clock with the board between them, and below it the whole
 * message board filling as the position moves. Its data is /api/board?id= (src/ui/transcript_board.py).
 *
 *   import { renderBoard } from './tv_view_board.js';
 *   renderBoard(el, payload, { q, base: '#/run/<collection>/<run key>', judged });
 *
 * q (URLSearchParams) may carry a and b (the two agents), mode (time | step), pos (seconds on the time clock, a step
 * number in step mode) and lens (LENSES); the tab writes them back into the page's hash as they change, so a link or a
 * screenshot opens at the same place. judged: the run page's judge labels by agent (levels.judge_labels.agents, as the
 * Details tab reads them). Top to bottom: one control row, with the lens; the lens's key, chips and a count; the
 * timeline; the slider under its time axis, as wide as the lanes, its handle under the moment it shows; a card per
 * agent (agentCard); the legend, folded; then the message board. The page carries no sentence: words are labels,
 * chips and short hovers (UI_TEXT_DIET). A click or a drag on the timeline, off a call or a post, moves the position
 * too. The two selectors never hold
 * one agent: choosing the other's agent swaps them. The timeline is one SVG, drawn again only when the pair, the mode,
 * the lens or the width changes; a move of the position updates the cursor, the shade over what is still to come, the
 * outlined current calls, the arrows shown, the cards and the board's shown posts, and nothing else. A post's arrows
 * show for the post at the position and the RECENT posts before it, and for a post under the pointer; every other post
 * keeps its box alone. A read's arrow shows how the read was made (the payload's form): the newest posts, thin and
 * dashed; the posts of a named writer, solid and heavier with the agent asked for at its end; a form the record does
 * not give, a plain dotted line.
 * The ladders' colours and chips are the page's own (tv_case_view.js RAMP, tv_levels.js levelChip and lineMark), each
 * ladder parted at its line. A lens colours every call by one ladder's marks, or by the judge labels' scores
 * (callStyle), and each post by its writing call's marks or its own located score (postFill). A post that holds a
 * judge label's excerpt, as code located it, shows the label's score chip, name and kind, the judge's rationale on one
 * line (a click shows it whole), and its text in full with the excerpt marked. Every string goes in through h() and
 * svgEl() text nodes. It returns {moveTo(position), showPost(label),
 * state}: showPost moves to post P<n> (the board's own numbering) and, when the post holds a judge's excerpt, brings
 * the marked excerpt into view. */
import { appendAll, enc, firstLine, fmtSecs, h, plural } from '/static/app/dom.js';
import { badge, longText, muted } from '/static/app/components.js';
import { COUNT_STEPS, TIME_STEPS, fmtTick, svgEl, ticksFor, tip, withTip } from '/static/app/charts.js';
import { RAMP, inkOn, scoreColour } from './tv_case_view.js';
import { excerptSpan, markedNodes } from './tv_excerpt.js';
import { ROLE_LABEL } from './tv_labels.js';
import { levelChip, lineMark, misleadMark, scoreChip } from './tv_levels.js';

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
const THUMB = 14;             // the slider handle's width (tv_board.css .bd-slider)
const READ_CLASS = { latest: 'bd-read-latest', from: 'bd-read-from', plain: 'bd-read-plain' };
const HOVER_LINES = 4;        // the most marks, facts or names a hover lists
const CLIP = 70;              // the longest line of a hover

// At most HOVER_LINES of a list, then how many more.
const fewOf = list => [...list.slice(0, HOVER_LINES), list.length > HOVER_LINES ? `+${list.length - HOVER_LINES} more`
  : null];

// The lenses: All colours each call by the labels the run page draws; each other lens colours every call by that
// lens's marks alone (the payload's marks: each record that names the call) and dims the calls it has none for.
const LENSES = ['all', 'cheating', 'coordination', 'collusion', 'judge'];
const topOf = (marks, key) => marks.reduce((m, x) => Math.max(m, x[key]), 0);

// A call's colour. Under All, the viewer's ramp at its highest label (RAMP, pale below each ladder's line and strong
// above): a Cheating label first, then a take (Collusion), then the Coordination level of a board read or post, else
// plain work; a Cheating label at or above the Cheating line fills the lane's whole height. Under a ladder's lens, the
// ramp at its highest mark: full height at or above the Cheating line, for a post that took up a teammate's new post,
// and for a take of a planted piece, which takes the Collusion line's colour; under Cheating a call with no mark of its
// own takes a quiet tint of the rung reached so far (sofar). Under Judge labels, the highest score's colour. kind:
// plain, mark, sofar or dim. ramp: {cheatLine, collLine, board: {read, post} -> the Coordination level's index}.
function callStyle(c, ramp, lens, sofar) {
  if (lens === 'all') {
    const top = ladder => topOf(c.labels.filter(l => l.ladder === ladder), 'index');
    const cheat = top('cheating');
    if (cheat) return { fill: RAMP.cheating[cheat], strong: cheat >= ramp.cheatLine, kind: 'mark' };
    const coll = top('collusion');
    if (coll) return { fill: RAMP.collusion[coll], strong: false, kind: 'mark' };
    const coord = top('coordination') || ramp.board[c.board] || 0;
    if (coord) return { fill: RAMP.coordination[coord], strong: false, kind: 'mark' };
    return { fill: PLAIN, strong: false, kind: 'plain' };
  }
  const marks = c.marks[lens];
  if (lens === 'judge' && marks.length) {
    return { fill: scoreColour(topOf(marks, 'score')), strong: false, kind: 'mark' };
  }
  if (lens === 'collusion' && marks.some(m => m.planted)) {
    return { fill: RAMP.collusion[ramp.collLine], strong: true, kind: 'mark' };
  }
  const top = lens === 'judge' ? 0 : topOf(marks, 'index');
  if (top) {
    const strong = lens === 'cheating' ? top >= ramp.cheatLine
      : lens === 'coordination' && marks.some(m => (m.took_up || []).length);
    return { fill: RAMP[lens][top], strong, kind: 'mark' };
  }
  if (lens === 'cheating' && sofar) return { fill: RAMP.cheating[sofar], strong: false, kind: 'sofar' };
  return { fill: PLAIN, strong: false, kind: 'dim' };
}

// One mark as a short line of a call's hover.
function markWords(lens, m, d) {
  if (lens === 'cheating') return `${m.clause} · ${m.level}`;
  if (lens === 'collusion') return `took ${m.giver}'s ${m.planted ? 'piece' : 'work'} · ${m.rule}`;
  if (lens === 'judge') return `${d.label_names[m.label]} ${m.score} · ${m.where}`;
  return firstLine(m.evidence, CLIP);
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
  const coll = d.ladders.collusion;
  const ramp = { cheatLine: cheat.levels.indexOf(cheat.line), collLine: coll.levels.indexOf(coll.line),
    board: Object.fromEntries(Object.entries(BOARD_LEVEL).map(([k, lv]) => [k, coord.levels.indexOf(lv)])) };
  const q = opts.q || new URLSearchParams();
  const pick = (v, fallback) => (byAgent.has(v) ? v : fallback);
  const state = {
    a: pick(q.get('a'), d.pair[0]),
    b: pick(q.get('b'), d.pair[1] || d.pair[0]),
    mode: q.get('mode') === 'step' || !timed ? 'step' : 'time',
    pos: q.get('pos') != null ? Number(q.get('pos')) : null,
    lens: LENSES.includes(q.get('lens')) ? q.get('lens') : 'all',
    playing: false,
  };
  // the two selectors never hold one agent, unless the run has one
  if (state.a === state.b) state.b = (d.agents.find(x => x.agent !== state.a) || { agent: state.a }).agent;
  const callHref = (agent, k) => `${opts.base}?agent=${enc(agent)}&call=${k}`;
  const callLink = (agent, k) => h('a', { class: 'bd-call-link', href: callHref(agent, k),
    title: `open call ${k} in ${agent}'s tab` }, `call ${k}`);

  // ---- controls
  const agentOption = a => h('option', { value: a.agent },
    `${a.agent} (${ROLE_LABEL[a.role] || a.role}, ${plural(a.calls.length, 'call')})`);
  const agentSelect = (side, label) => h('select', { class: ['bd-sel', `bd-sel-${side}`], 'aria-label': label,
    title: d.pair_why, onchange: e => choose(side, e.target.value) }, d.agents.map(agentOption));
  const selA = agentSelect('a', 'first agent');
  const selB = agentSelect('b', 'second agent');
  const modeSel = h('select', { 'aria-label': 'clock', title: `clock: ${d.clock_about}`,
    onchange: e => { const t = timeNow(); state.mode = e.target.value; build(t); } },
  h('option', { value: 'time', disabled: !timed }, 'time'),
  h('option', { value: 'step' }, 'steps'));
  const lensName = k => (k === 'all' ? 'All' : k === 'judge' ? 'Judge labels' : d.ladders[k].name);
  const lensButtons = new Map(LENSES.map(k => [k, h('button', { type: 'button', class: 'seg-btn',
    title: k === 'all' ? 'each call by its highest label' : `${lensName(k)} alone`,
    onclick: () => { state.lens = k; build(timeNow()); } }, lensName(k))]));
  const lensBar = h('span', { class: 'bd-pick' }, muted('colour by ', 'small'),
    h('span', { class: 'seg', role: 'group', 'aria-label': 'colour the calls by' }, [...lensButtons.values()]));
  const lensKeyRow = h('div', { class: 'bd-lens-key small' });
  const button = (text, title, onclick, cls) => h('button',
    { type: 'button', class: ['btn', 'btn-small', cls], title, onclick }, text);
  const back = button('◀ step', 'step back (left arrow)', () => step(-1));
  const playBtn = button('▶ play', 'play or pause', () => (state.playing ? pause() : play()), 'bd-play');
  const fwd = button('step ▶', 'step forward (right arrow)', () => step(1));
  const slider = h('input', { type: 'range', class: 'bd-slider', 'aria-label': 'position',
    oninput: e => { pause(); moveTo(Number(e.target.value)); } });
  const posText = h('span', { class: 'bd-pos' });
  const scrub = h('div', { class: 'bd-scrub' }, h('span', { class: 'bd-scrub-name small muted' }, 'position'),
    slider, posText);
  const cards = { a: agentCard('a'), b: agentCard('b') };
  const cardRow = h('div', { class: 'bd-cards' }, cards.a.el, cards.b.el);
  const svgHost = h('div', { class: 'bd-svg-host' });
  const boardList = h('ol', { class: 'bd-posts' });
  const empty = muted('no post yet', 'small block');
  const boardBox = h('div', { class: 'bd-board-box' }, empty, boardList);
  const boardHead = h('h3', { class: 'bd-board-head' }, `Message board · ${plural(d.posts.length, 'post')}`);

  // ---- the posts as the board shows them, built once; a change of the pair changes their classes only
  const items = d.posts.map((p, i) => {
    const readers = new Map();
    for (const r of p.reads) {
      if (!readers.has(r.agent)) readers.set(r.agent, []);
      readers.get(r.agent).push(r.k);
    }
    // The judge labels whose excerpt code located in this post: the score chip, the label's name and kind, whose label
    // when not the writer's, and the judge's rationale on one line, whole after a click; the first excerpt the post's
    // text holds is marked in it, and the text then shows whole.
    const judged = p.labels.map(x => {
      const name = d.label_names[x.label];
      const kind = x.kind && x.kind !== 'none' ? x.kind : null;
      const reason = h('span', { class: 'bd-reason muted', title: 'rationale: click to expand',
        onclick: () => reason.classList.toggle('open') }, x.reason || '');
      return h('div', { class: 'bd-judged small' }, scoreChip(x.score, `${name}: ${x.score} of 100`),
        h('b', null, name), kind ? h('span', { class: 'bd-kind' }, kind) : null,
        kind === 'mislead' ? misleadMark(kind) : null, x.agent !== p.writer ? muted(x.agent, 'small') : null,
        x.reason ? reason : null);
    });
    const span = p.labels.map(x => excerptSpan(p.text, x.excerpt, x.start, x.end)).find(Boolean);
    const readBy = [...readers].map(([agent, ks]) => h('a', { class: 'bd-reader', href: callHref(agent, ks[0]),
      title: `read at call${ks.length > 1 ? 's' : ''} ${fewOf(ks).filter(Boolean).join(', ')}` }, agent));
    const li = h('li', { class: ['bd-post', p.kind === 'system' && 'bd-system', p.labels.length && 'bd-has-label'],
      dataset: { i: String(i) }, hidden: true },
    h('div', { class: 'bd-post-head' },
      h('b', { class: 'bd-label' }, p.label),
      p.kind === 'system' ? badge('notice', 'notice', 'written by the harness')
        : h('span', { class: 'bd-writer' }, p.writer),
      p.role && p.role !== 'worker' ? badge(ROLE_LABEL[p.role] || p.role, 'plain') : null,
      h('span', { class: 'small muted', title: timed ? 'elapsed' : 'place in the run\'s call order' },
        timed ? fmtSecs(p.t) : `#${p.t + 1}`),
      p.call != null ? callLink(p.writer, p.call) : null,
      h('span', { class: 'spacer' }),
      readers.size ? h('span', { class: 'bd-readers small' }, muted('read by', 'small'), readBy)
        : muted('unread', 'small')),
      judged,
      span ? h('pre', { class: 'pre bd-text' }, markedNodes(p.text, span))
        : longText(p.text, { lines: 8, chars: 1600, cls: 'bd-text' }));
    li.addEventListener('click', e => {
      if (!e.target.closest('a, button, .bd-reason')) { pause(); moveTo(keyOf(p)); }
    });
    li.addEventListener('pointerenter', () => hover(i));
    li.addEventListener('pointerleave', () => hover(-1));
    boardList.appendChild(li);
    return { p, li };
  });

  // A post's fill under a lens: under Judge labels its highest located score's colour; under a ladder's lens the colour
  // of the call that wrote it, when that call has a mark of the lens; null (dimmed) otherwise; undefined under All.
  function postFill(p, top) {
    if (state.lens === 'all') return undefined;
    if (state.lens === 'judge') return top != null ? scoreColour(top) : null;
    const c = p.call != null && byAgent.has(p.writer) ? byAgent.get(p.writer).calls.find(x => x.k === p.call) : null;
    const st = c ? callStyle(c, ramp, state.lens, 0) : null;
    return st && st.kind === 'mark' ? st.fill : null;
  }

  // ---- what build() lays out for the current pair, mode and lens
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
    for (const [k, b] of lensButtons) {
      b.classList.toggle('active', k === state.lens);
      b.setAttribute('aria-pressed', String(k === state.lens));
    }
    width =Math.max(900, Math.floor(svgHost.clientWidth || el.clientWidth || 1200));
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
    const svg = svgEl('svg', { class: ['bd-svg', state.lens !== 'all' && 'bd-lensed'], width, height: H,
      viewBox: `0 0 ${width} ${H}`, role: 'img',
      'aria-label': `the calls of ${state.a} and ${state.b} with the board between them` });
    const uid = Math.random().toString(36).slice(2, 8);
    svg.appendChild(svgEl('defs', null, ...SIDES.map(s => svgEl('marker', { id: `bd-arrow-${s}-${uid}`,
      viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 8, markerHeight: 8, markerUnits: 'userSpaceOnUse',
      orient: 'auto-start-reverse' }, svgEl('path', { d: 'M0,0 L8,4 L0,8 z', class: `bd-arrowhead-${s}` })))));
    // the slider spans the lanes: its handle's middle sits at x0 at the lowest value and at x1 at the highest
    scrub.style.width = `${width}px`;
    slider.style.left = `${x0 - THUMB / 2}px`;
    slider.style.width = `${x1 - x0 + THUMB}px`;
    posText.style.right = `${width - x1}px`;

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

    // calls: a thin bar for ordinary work, a taller one for a labelled or marked call, the lane's full height for a
    // strong one (callStyle). Under the Cheating lens a line marks the call where the agent first reaches each rung,
    // heavier where it crosses the Cheating line, with the rung's name.
    const callsG = svgEl('g', { class: 'bd-calls' });
    const reachG = svgEl('g', { class: 'bd-reaches' });
    const keys = { a: [], b: [] };
    const calls = { a: [], b: [] };
    const reached = { a: [], b: [] };   // per call: each ladder's highest level index of the agent's calls up to it
    const marked = { a: 0, b: 0 };
    for (const side of SIDES) {
      const ag = byAgent.get(state[side]);
      cards[side].fill(ag);
      cards[side].el.hidden = side === 'b' && state.a === state.b;
      const top = {};
      let sofar = 0;
      for (const c of ag.calls) {
        for (const l of c.labels) top[l.ladder] = Math.max(top[l.ladder] || 0, l.index);
        reached[side].push({ ...top });
        const st = callStyle(c, ramp, state.lens, sofar);
        const tall = st.strong ? LANE_H - 4 : st.kind === 'mark' ? 28 : 14;
        callsG.appendChild(svgEl('rect', { class: ['bd-call', st.strong && 'bd-strong', `bd-${st.kind}`],
          x: left(c), y: laneY[side] + (LANE_H - tall) / 2, width: W(c), height: tall, fill: st.fill,
          'data-side': side, 'data-k': c.k }));
        if (st.kind === 'mark') marked[side] += 1;
        const now = state.lens === 'cheating' ? topOf(c.marks.cheating, 'index') : 0;
        if (now > sofar) {
          const x = left(c);
          const crosses = now >= ramp.cheatLine && sofar < ramp.cheatLine;
          reachG.append(svgEl('line', { class: ['bd-reach', crosses && 'bd-reach-line'], x1: x, x2: x,
            y1: laneY[side], y2: laneY[side] + LANE_H }),
          svgEl('text', { class: 'bd-reach-text', x: x + 3, y: laneY[side] + 9 }, cheat.levels[now]));
          sofar = now;
        }
        keys[side].push(state.mode === 'time' ? c.t : c.u);
        calls[side].push(c);
      }
    }
    svg.append(callsG, reachG);

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
      const top = p.labels.length ? Math.max(...p.labels.map(x => x.score)) : null;
      const fill = postFill(p, top);
      const g = svgEl('g', { class: ['bd-pbox', 'bd-ahead', by, fill === null && 'bd-pdim'], 'data-i': i },
        svgEl('rect', { x: from, y, width: boxW, height: POST_H, rx: 3, style: fill ? `fill:${fill}` : null }),
        boxW >= 20 ? svgEl('text', { x: from + boxW / 2, y: y + POST_H - 4, 'text-anchor': 'middle',
          style: fill ? `fill:${inkOn(fill)}` : null }, p.label) : null,
        top != null ? svgEl('circle', { class: 'bd-judged-dot', cx: from + boxW, cy: y + POST_H / 2, r: 5,
          fill: scoreColour(top) }) : null);
      postsG.appendChild(g);
      const arrows = svgEl('g', { visibility: 'hidden' });
      arrowsG.appendChild(arrows);
      postBox.set(i, { from, y, w: boxW, g, arrows });
      const cx = from + boxW / 2;
      const edge = s => (s === 'a' ? y : y + POST_H);
      const writing = side && callOf[side].get(p.call);
      if (writing) arrow(arrows, side, null, left(writing) + W(writing) / 2, laneEdge(side), cx, edge(side));
      for (const s of state.a === state.b ? ['a'] : SIDES) {
        // the agent's first read of the post in each form it read it
        const first = new Map();
        for (const r of p.reads) {
          if (r.agent === state[s] && !first.has(r.form || 'plain')) first.set(r.form || 'plain', r);
        }
        for (const [form, r] of p.writer === state[s] ? [] : first) {
          const reading = callOf[s].get(r.k);
          if (!reading) continue;
          const ex = left(reading) + W(reading) / 2;
          arrow(arrows, s, READ_CLASS[form], cx, edge(s), ex, laneEdge(s));
          if (form === 'from') {
            const flip = ex > x1 - 90;   // near the right end the label sits left of the arrow's end
            arrows.appendChild(svgEl('text', { class: ['bd-asked', `bd-asked-${s}`], x: flip ? ex - 6 : ex + 6,
              y: s === 'a' ? laneEdge(s) + 12 : laneEdge(s) - 5, 'text-anchor': flip ? 'end' : 'start' },
            `from ${r.asked}`));
          }
        }
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
    const posAt = ev => {
      const f = (ev.clientX - svg.getBoundingClientRect().left - x0) / (x1 - x0);
      return state.mode === 'time' ? f * span : Math.round(f * n) - 1;
    };
    let dragging = false;
    svg.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || ev.target.closest('[data-k], [data-i]')) return;
      ev.preventDefault();
      pause();
      dragging = true;
      tip.hide();
      svg.setPointerCapture(ev.pointerId);
      moveTo(posAt(ev));
    });
    const stopDrag = () => { dragging = false; };
    svg.addEventListener('pointerup', stopDrag);
    svg.addEventListener('pointercancel', stopDrag);
    svg.addEventListener('pointermove', ev => {
      if (dragging) { moveTo(posAt(ev)); return; }
      const t = ev.target.closest('[data-k], [data-i]');
      hover(t && t.hasAttribute('data-i') ? Number(t.getAttribute('data-i')) : -1);
      if (!t) { tip.hide(); return; }
      if (t.hasAttribute('data-k')) {
        const side = t.getAttribute('data-side');
        const c = callOf[side].get(Number(t.getAttribute('data-k')));
        tip.show(ev, [`${state[side]} · call ${c.k} · ${c.tool}`,
          timed ? `${fmtSecs(c.t)} · ${fmtSecs(c.dur)}` : `#${c.t + 1}`, firstLine(c.summary, CLIP),
          ...fewOf(state.lens === 'all' ? c.labels.map(l => `${d.ladders[l.ladder].name} ${l.level}`)
            : c.marks[state.lens].map(m => markWords(state.lens, m, d)))]);
      } else {
        const p = d.posts[Number(t.getAttribute('data-i'))];
        tip.show(ev, [`${p.label} · ${p.writer}${timed ? ` · ${fmtSecs(p.t)}` : ''}`, firstLine(p.text, CLIP),
          ...p.labels.map(x => `${d.label_names[x.label]} ${x.score}`)]);
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
    L = { X, W, left, unitW, stops, n, span, keys, calls, reached, shade, cursor, outline, latestBox, postBox, events,
      order, x1,
      orderKeys: order.map(i => postKeys[i]), eventKeys: events.map(x => x.key),
      stopKeys: stops.map((u, i) => (state.mode === 'time' ? u.t : i)) };
    // in step mode the cursor stands at the end of step pos, so the slider's lowest value, -1, is the lanes' start
    slider.min = state.mode === 'time' ? '0' : '-1';
    slider.max = String(state.mode === 'time' ? span : n - 1);
    slider.step = state.mode === 'time' ? String(span / 2000) : '1';
    shown = 0;
    latest = -1;
    hovered = -1;
    lit = new Set();   // the new SVG's arrows start hidden
    current = {};      // undefined, so the first move fills both cards
    for (const { li } of items) li.hidden = true;   // the new SVG's post boxes start faded (bd-ahead)
    lensKeyRow.replaceChildren();
    appendAll(lensKeyRow, lensKey(marked));
    lensKeyRow.hidden = state.lens === 'all';
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
      cards[side].at(c, i >= 0 ? L.reached[side][i] : {});
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

  // A level of a card's grid: the page's chip, or a dash for a ladder that does not apply to the agent.
  const cardChip = (key, lad, level) => (lad.levels.includes(level) ? levelChip(key, lad, level, false)
    : h('span', { class: 'muted', title: level || 'no level' }, '–'));
  // A fact of a final level as one short hover line: "name value", an evidence list as its first line and a count.
  const factLine = ([name, v]) => (Array.isArray(v)
    ? `${name} · ${firstLine(v[0], CLIP)}${v.length > 1 ? ` (+${v.length - 1})` : ''}`
    : `${name} · ${firstLine(v, CLIP)}`);
  // A judge label's score in a card: the label's name, its chip, its kind; the chip opens the excerpt where code
  // located it: a post here, on this tab; a model call's reasoning in the agent's tab.
  function cardScore(agent, key, e) {
    const name = d.label_names[key];
    if (!e) return h('span', { class: 'bd-score' }, muted(name, 'small'), muted('–', 'small'));
    const kind = e.kind && e.kind !== 'none' ? e.kind : null;
    const at = e.located;
    const chip = scoreChip(e.score, `${name}: ${e.score} of 100`);
    const toPost = ev => { ev.preventDefault(); showPost(at.post); };
    const inTab = `${opts.base}?agent=${enc(agent)}&label=${enc(key)}`;
    const open = !at ? chip
      : at.where === 'post' ? h('a', { href: '#', title: `open ${at.post}`, onclick: toPost }, chip)
        : h('a', { href: inTab, title: `open model call ${at.call}` }, chip);
    return h('span', { class: 'bd-score' }, muted(name, 'small'), open, kind ? h('span', { class: 'bd-kind' }, kind)
      : null, kind === 'mislead' ? misleadMark(kind) : null);
  }

  // An agent's card in its lane's colour (BOARD_CARD): fill(agent) once per pair, at(call, reached) at each move. A
  // head line (the agent, its role, its call at the position); a grid with a row per ladder and two columns, "now",
  // the level its calls reached up to the position, and "end", its level at the end of the run (the payload's final,
  // with its evidence on hover), muted when the two agree; then its judge labels' scores.
  function agentCard(side) {
    const head = h('div', { class: 'bd-card-head' });
    const grid = h('div', { class: 'bd-card-grid' });
    const labels = h('div', { class: 'bd-card-labels' });
    const el = h('div', { class: ['bd-card', `bd-card-${side}`] }, head, grid, labels);
    const cells = new Map();
    let ag = null;
    function fill(a) {
      ag = a;
      cells.clear();
      grid.replaceChildren(h('span'), h('span', { class: 'bd-col', title: 'at the position' }, 'now'),
        h('span', { class: 'bd-col', title: 'at the end of the run' }, 'end'));
      for (const [key, lad] of Object.entries(d.ladders)) {
        const f = ag.final[key] || { level: null, facts: [] };
        const chip = cardChip(key, lad, f.level);
        chip.removeAttribute('title');
        const end = withTip(h('span', { class: 'bd-end' }, chip),
          () => [`${lad.name}: ${f.level || 'no level'}`, ...fewOf(f.facts.map(factLine))]);
        const now = h('span', { class: 'bd-now' });
        cells.set(key, { now, end, final: f.level });
        grid.append(h('span', { class: 'bd-ladder' }, lad.name), now, end);
      }
      const row = (opts.judged || {})[ag.agent];
      labels.replaceChildren(...(row ? Object.keys(d.label_names).map(key => cardScore(ag.agent, key, row[key]))
        : [muted('not judged', 'small')]));
    }
    function at(c, top) {
      head.replaceChildren(h('b', { class: `bd-key-${side}` }, ag.agent),
        muted(ROLE_LABEL[ag.role] || ag.role, 'small'),
        ...(c ? [callLink(state[side], c.k), muted(c.tool, 'small'),
          c.summary !== c.tool ? h('span', { class: 'bd-sum', title: firstLine(c.summary, CLIP) }, c.summary) : null]
          : [muted('no call yet', 'small')]).filter(Boolean));
      for (const [key, lad] of Object.entries(d.ladders)) {
        const cell = cells.get(key);
        const level = lad.levels.includes(cell.final) ? lad.levels[top[key] || 0] : cell.final;
        cell.now.replaceChildren(cardChip(key, lad, level));
        cell.end.classList.toggle('bd-same', level === cell.final);
      }
    }
    return { el, fill, at };
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
        pos: state.mode === 'time' ? String(Math.round(state.pos * 10) / 10) : String(state.pos), lens: state.lens });
      history.replaceState(null, '', `${opts.base}?${p}`);
    };
    clearTimeout(pending);
    if (now || performance.now() - wrote > 200) write();
    else pending = setTimeout(write, 200);
  }

  // An agent already in the other selector swaps the two, so the selectors never hold one agent.
  function choose(side, agent) {
    const t = timeNow();
    const other = side === 'a' ? 'b' : 'a';
    if (state[other] === agent) state[other] = state[side];
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

  // ---- legend: chips and line samples with a word or two each
  const sw = (fill, cls) => h('i', { class: ['bd-sw', cls], style: fill ? `background:${fill}` : null });
  const item = (title, ...kids) => h('span', { class: 'bd-lg', title }, ...kids);
  const arrowKey = (cls, word) => item(null,
    svgEl('svg', { class: 'bd-key-svg', width: 34, height: 12, 'aria-hidden': 'true' },
      svgEl('line', { class: ['bd-arrow', cls, 'bd-arrow-a'], x1: 1, y1: 6, x2: 33, y2: 6 })), word);
  const chip = (key, lad, lv) => levelChip(key, lad, lv, false);
  const cheatChips = () => cheat.levels.slice(1).map(lv => [lv === cheat.line ? lineMark(cheat) : null,
    chip('cheating', cheat, lv)]);
  const formless = d.posts.some(p => p.reads.some(r => !r.form));
  const legend = h('details', { class: 'bd-legend' }, h('summary', null, 'Legend'),
    h('div', { class: 'bd-legend-body' },
      item(null, sw(null, 'bd-sw-a'), sw(null, 'bd-sw-b'), 'lanes'),
      item(null, sw(PLAIN), 'work'),
      item(null, h('i', { class: 'bd-sw bd-sw-post' }), 'post'),
      item('board read or post', Object.values(BOARD_LEVEL).map(lv => chip('coordination', coord, lv))),
      item('took a teammate\'s work', chip('collusion', coll, coll.levels[1])),
      item(cheat.line_words, cheatChips()),
      item(cheat.line_words, sw(RAMP.cheating[ramp.cheatLine], 'bd-sw-tall'), 'over the line'),
      item(null, h('i', { class: 'bd-sw bd-sw-judged' }), 'judge excerpt'),
      item('latest post written or read by the two', h('i', { class: 'bd-sw bd-sw-latest' }), 'latest'),
      arrowKey(null, 'write'),
      arrowKey(READ_CLASS.latest, 'latest read'),
      arrowKey(READ_CLASS.from, 'from read'),
      formless ? arrowKey(READ_CLASS.plain, 'other read') : null));

  // The key of the lens in force, under the controls: its colours as chips and samples, how many of the two agents'
  // calls it marks, and how many of its records name no call (the payload's unplaced; the names on hover).
  function lensKey(marked) {
    const lens = state.lens;
    if (lens === 'all') return [];
    const shown = state.a === state.b ? [state.a] : [state.a, state.b];
    const total = shown.reduce((n, a) => n + byAgent.get(a).calls.length, 0);
    const count = state.a === state.b ? marked.a : marked.a + marked.b;
    const missed = new Map();
    for (const a of shown) {
      for (const [what, n] of Object.entries(byAgent.get(a).unplaced[lens])) {
        missed.set(what, (missed.get(what) || 0) + n);
      }
    }
    const tick = (cls, word, title) => item(title, h('i', { class: ['bd-tick', cls] }), word);
    const keys = {
      cheating: [cheatChips(), item('the rung reached so far', sw(RAMP.cheating[ramp.cheatLine - 1], 'bd-sw-sofar'),
        'so far'), tick(null, 'first reach'), tick('bd-tick-line', 'crossed line')],
      coordination: [Object.values(BOARD_LEVEL).map(lv => chip('coordination', coord, lv)),
        item('a post that took up a teammate\'s new post', sw(RAMP.coordination[ramp.board.post], 'bd-sw-tall'),
          'took up')],
      collusion: [chip('collusion', coll, coll.levels[1]),
        item('a take of a planted piece', sw(RAMP.collusion[ramp.collLine], 'bd-sw-tall'), 'planted piece')],
      judge: [0, 25, 50, 75, 100].map(s => scoreChip(s, `score ${s}`)),
    };
    const n = [...missed.values()].reduce((x, y) => x + y, 0);
    return [keys[lens], h('span', { class: 'bd-count', title: 'calls marked' }, `${count} of ${total}`),
      n ? h('span', { class: 'bd-count bd-missed',
        title: fewOf([...missed].map(([what, k]) => `${what} ${k}`)).filter(Boolean).join(' · ') },
      `no call ${n}`) : null];
  }

  const notes = d.notes || [];
  const controls = h('div', { class: 'bd-controls' },
    h('label', { class: 'bd-pick' }, sw(null, 'bd-sw-a'), selA),
    h('label', { class: 'bd-pick' }, sw(null, 'bd-sw-b'), selB),
    modeSel, back, playBtn, fwd, lensBar,
    notes.length ? h('span', { class: 'bd-count', title: fewOf(notes).filter(Boolean).join('\n') },
      plural(notes.length, 'note')) : null);
  el.replaceChildren(h('div', { class: 'bd-tab' },
    h('section', { class: 'card bd-timeline' }, controls, lensKeyRow, svgHost, scrub, cardRow, legend),
    h('section', { class: 'card bd-board' }, boardHead, boardBox)));

  let resizeTimer = 0;
  new ResizeObserver(() => {
    const w = Math.floor(svgHost.clientWidth);
    if (!L || !w || Math.abs(Math.max(900, w) - width) < 8) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => build(), 120);
  }).observe(svgHost);
  build();
  // showPost(label): post P<n> of the board, the position moved to it and its entry flashed, or, when the post holds
  // a judge's excerpt, the marked excerpt brought to the middle and flashed
  function showPost(label) {
    const i = d.posts.findIndex(p => p.label === label);
    if (i < 0) return null;
    pause();
    moveTo(keyOf(d.posts[i]));
    const li = items[i].li;
    const target = li.querySelector('mark.xl-mark') || li;
    target.scrollIntoView({ block: 'center' });
    target.classList.remove('flash');
    void target.offsetWidth;
    target.classList.add('flash');
    return li;
  }
  return { moveTo, showPost, state };
}
