/* One agent's whole episode in the case-study layout: a sticky left column "The whole episode" (the three ladders, one
 * lane each, one row per tool call, and beside them the labels each call carries itself) and the transcript on the
 * right, one block per step, a tool call's labels under its head line. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/case_view.js, after arena-capstone script/visualize_case.html). Our edits: readableReasoning,
 * which the source imported from its transcript.js, sits here verbatim; the imports point at this viewer's files; the
 * source's sentence lens is gone and each call shows its labels (src/ui/transcript_levels.py _agent_labels) in its
 * place; the strip follows the page as it scrolls. Every string goes in through h() text nodes. */
import { h, plural } from '/static/app/dom.js';

// Colour ramps per ladder, level 0 first.
export const RAMP = {
  // none, looked, tempted, attempted, hacked (src/measures/cheating_ladder.py LEVELS), light to dark
  cheating: ['#eeede8', '#f9c396', '#e8714a', '#d0452e', '#8c1a17'],
  // none, read, write, low, medium, high, extreme (src/measures/coordination.py LEVELS), light to dark
  coordination: ['#eeede8', '#e9e7f6', '#d3cff0', '#b1aae3', '#8b80d2', '#6152b9', '#3a2a8f'],
  // none, cooperation, collaboration (code, greens), collusion, scheming (a judge's, reds): src/measures/collusion.py
  collusion: ['#eeede8', '#cde8dc', '#5aa889', '#d0452e', '#7a1f12'],
};
const SHORT = { cheating: 'Ch', coordination: 'Cd', collusion: 'Cl' };
// Milestone wording: what the strip writes beside the call that first reached a level.
const WORDS = {
  cheating: { looked: 'looked', tempted: 'tempted', attempted: 'attempted', hacked: 'hacked' },
  coordination: { read: 'read a teammate', write: 'posted', low: 'low back-and-forth', medium: 'medium back-and-forth', high: 'high back-and-forth', extreme: 'extreme back-and-forth' },
  collusion: { cooperation: 'cooperation', collaboration: 'collaboration', collusion: 'collusion (judge)', scheming: 'scheming (judge)' },
};

// The page has one episode on screen at a time. One scroll listener for the page asks that episode to move its strip,
// and a WeakMap holds the episodes, so an episode the page dropped is freed.
const FOLLOW = new WeakMap();
function followPage() {
  const shown = document.querySelector('.case');
  const follow = shown && FOLLOW.get(shown);
  if (follow) follow();
}
window.addEventListener('scroll', followPage, { passive: true });
window.addEventListener('resize', followPage, { passive: true });

/** Reasoning as text, or a note when the provider hid it. */
export function readableReasoning(raw) {
  if (raw == null || raw === '') return null;
  const s = typeof raw === 'string' ? raw : JSON.stringify(raw);
  const t = s.trim();
  if (t === '[redacted reasoning]') return { hidden: 'reasoning hidden by the provider (redacted)' };
  if (t.startsWith('[{')) {
    try {
      const blocks = JSON.parse(t);
      if (Array.isArray(blocks)) {
        const part = b => {
          if (!b || typeof b !== 'object') return '';
          if (typeof b.text === 'string') return b.text;
          if (typeof b.summary === 'string') return b.summary;
          if (Array.isArray(b.summary)) return b.summary.map(x => (x && x.text) || '').join('\n');
          return '';
        };
        const text = blocks.map(part).filter(Boolean).join('\n\n');
        return text ? { text } : { hidden: `reasoning hidden by the provider (${plural(blocks.length, 'encrypted block')})` };
      }
    } catch (e) { /* not JSON: show as text */ }
  }
  return { text: s };
}

/** Full colour legend: one row per ladder, every level as a swatch in ladder order. */
export function fullLegend(ladders, order) {
  return h('div', { class: 'cs-full-legend' }, order.map(L => h('div', { class: 'cfl-row' },
    h('b', null, `${SHORT[L]} ${ladders[L].name}:`),
    ladders[L].levels.map((v, i) => h('span', { class: 'cfl-item' }, h('i', { style: `background:${RAMP[L][i]}` }), v)))));
}

function asStr(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  return JSON.stringify(v, null, 2);
}
function argText(a) {
  let x = a;
  if (typeof x === 'string') { try { x = JSON.parse(x); } catch (e) { return x; } }
  if (x && typeof x === 'object' && !Array.isArray(x)) {
    const keys = Object.keys(x);
    if (keys.length === 1) return asStr(x[keys[0]]);
    return keys.map(k => `${k}: ${asStr(x[k])}`).join('\n');
  }
  return asStr(x);
}
function resultText(r) {
  const c = r.content;
  if (Array.isArray(c)) return c.map(p => (p && p.text) || asStr(p)).join('\n');
  return asStr(c) || asStr(r.extra && r.extra.error);
}
function blk(label, text, input, kind) {
  const el = h('div', { class: ['blk', input && 'inp', input && 'fold', kind && 'k-' + kind] }, h('div', { class: 'lab' }, label), h('div', { class: 'txt' }, String(text || '')));
  if (input) el.addEventListener('click', () => el.classList.remove('fold'));
  return el;
}

/**
 * ctx: {agent, levels (the run's three-ladder payload, with its labels by call), isRegulator, regulatorCalls: Set of
 * tool_call_ids that read a hidden regulator's post, or that posted as the regulator}.
 * Returns {el, findCall, findToolCall, showAll, nCalls}.
 */
export function renderCase(t, ctx) {
  const lv = ctx.levels;
  const steps = (t && t.trajectory && t.trajectory.steps) || [];
  // 1. tool calls in order, with the step's reasoning and text on its first call
  const calls = [];
  const loose = [];
  for (const s of steps) {
    const src = s.source || 'unknown';
    const rs = (s.observation && s.observation.results) || [];
    const reasoning = readableReasoning(s.reasoning_content);
    const msg = asStr(s.message);
    if (src === 'agent' && (s.tool_calls || []).length) {
      s.tool_calls.forEach((tc, j) => {
        let res = rs.filter(r => r.source_call_id === tc.tool_call_id);
        if (!res.length && rs[j] && !rs[j].source_call_id) res = [rs[j]];
        calls.push({ k: calls.length, tc, res, reasoning: j === 0 && reasoning && reasoning.text, text: j === 0 ? msg : '', step: s.step_id });
      });
    } else {
      loose.push({ before: calls.length, src, text: msg, reasoning: reasoning && reasoning.text, step: s.step_id });
    }
  }
  const n = calls.length;
  const idOf = new Map(calls.map(c => [c.tc.tool_call_id, c.k]));
  // 2. level per ladder per call (monotone), and milestones
  const order = lv ? lv.order : ['cheating'];
  const evs = (lv && lv.events && lv.events[ctx.agent]) || [];
  const at = new Map();
  const endEv = [];
  for (const e of evs) {
    const k = e.k != null ? e.k : e.call_id ? idOf.get(e.call_id) : null;
    if (k == null) { endEv.push(e); continue; }
    if (!at.has(k)) at.set(k, []);
    at.get(k).push(e);
  }
  const level = order.map(() => new Array(n).fill(0));
  const cur = Object.fromEntries(order.map(L => [L, 0]));
  const fresh = new Map();
  for (let k = 0; k < n; k++) {
    for (const e of at.get(k) || []) if (e.index > cur[e.ladder]) { cur[e.ladder] = e.index; if (!fresh.has(k)) fresh.set(k, []); fresh.get(k).push(e); }
    order.forEach((L, i) => { level[i][k] = cur[L]; });
  }
  const finalIx = Object.fromEntries(order.map(L => [L, (lv && lv.agents[ctx.agent] && lv.agents[ctx.agent][L] && lv.agents[ctx.agent][L].index) || 0]));
  const single = lv ? lv.single : true;
  const miles = [];
  for (const [k, es] of fresh) for (const e of es) {
    const w = (WORDS[e.ladder] || {})[e.level];
    if (w) miles.push({ k, t: `${w}, call ${k}`, head: `${w},`, tail: `call ${k}`, ladder: e.ladder, tip: e.evidence });
  }
  const regAt = new Set([...(ctx.regulatorCalls || [])].map(id => idOf.get(id)).filter(k => k != null));
  if (regAt.size) {
    const k0 = Math.min(...regAt);
    miles.push({ k: k0, t: `regulator warned, call ${k0}`, head: 'regulator warned,', tail: `call ${k0}`, ladder: 'reg', tip: ctx.isRegulator ? 'this agent\'s first post as the hidden regulator' : 'the first call that read a hidden regulator\'s post' });
  }
  for (const e of endEv) {
    const w = (WORDS[e.ladder] || {})[e.level];
    if (w) miles.push({ k: n, t: `${w} (end of run)`, head: w, tail: '(end of run)', ladder: e.ladder, tip: e.evidence, end: true });
  }
  miles.sort((a, b) => a.k - b.k);
  // 3. the labels of each call itself: every call that is evidence for a level, where an event is only the first
  const own = new Map();
  let unplaced = 0;
  for (const l of (lv && lv.labels && lv.labels[ctx.agent]) || []) {
    const k = l.k != null ? l.k : idOf.get(l.call_id);
    if (k == null || k < 0 || k >= n) { unplaced++; continue; }
    if (!own.has(k)) own.set(k, []);
    own.get(k).push(l);
  }
  for (const ls of own.values()) ls.sort((a, b) => order.indexOf(a.ladder) - order.indexOf(b.ladder) || b.index - a.index);
  const labelled = k => own.has(k) || regAt.has(k);
  const nLabelled = calls.filter(c => labelled(c.k)).length;

  // 4. right: one block per step. Its head line says how far each ladder had got; its labels say what the call did.
  const name = L => lv.ladders[L].name;
  const soFar = (vals, newAt) => {
    const chips = order.map((L, i) => {
      const v = vals[i];
      if (!v || (single && L !== 'cheating')) return null;
      const isNew = newAt.has(L);
      return h('span', { class: ['cs-chip', isNew && 'cs-new'], title: `${name(L)}: ${lv.ladders[L].levels[v]} (${v} of ${lv.ladders[L].levels.length - 1}), reached ${isNew ? 'at this call' : 'before this step'}` },
        h('i', { style: `background:${RAMP[L][v]}` }), `${name(L)} `, h('b', null, lv.ladders[L].levels[v]));
    }).filter(Boolean);
    return chips.length ? h('span', { class: 'cs-strip', title: 'the level reached so far on each ladder' }, h('span', { class: 'cs-sofar' }, 'so far'), chips) : null;
  };
  const valsAt = k => order.map((L, i) => (k < 0 ? 0 : level[i][Math.min(k, n - 1)] || 0));
  const line = (swatch, cls, ladder, what, why, tip) => h('div', { class: ['cs-label', cls], title: tip },
    h('i', { style: swatch }), h('span', { class: 'cs-lname' }, ladder), h('b', null, what), why ? h('span', { class: 'cs-why' }, why) : null);
  function labelsOf(k) {
    const lines = (own.get(k) || []).map(l => line(`background:${RAMP[l.ladder][l.index]}`, null, name(l.ladder), l.level, l.evidence,
      `${name(l.ladder)}: ${l.level} (${l.index} of ${lv.ladders[l.ladder].levels.length - 1})${l.evidence ? '\n' + l.evidence : ''}`));
    if (regAt.has(k)) {
      lines.push(line('', 'cs-reg', 'Regulator', ctx.isRegulator ? 'posted as the hidden regulator' : 'read a hidden regulator\'s post', '',
        ctx.isRegulator ? 'this call posted on the board as the hidden regulator' : 'this call read a post the hidden regulator wrote'));
    }
    return lines.length ? h('div', { class: 'cs-labels' }, h('div', { class: 'lab' }, 'Labels of this step'), lines) : null;
  }
  const blocks = [];
  const right = h('div', { class: 'right' });
  let li = 0;
  const looseBlock = x => h('div', { class: 'call msg' },
    h('div', { class: 'ch' }, h('span', { class: 'cn' }, x.src === 'agent' ? 'Message' : x.src === 'user' ? 'User' : 'System'),
      x.step != null ? h('span', null, `step ${x.step}`) : null, lv ? soFar(valsAt(x.before - 1), new Set()) : null),
    h('div', { class: 'cb' }, x.reasoning ? blk('Reasoning', x.reasoning, false, 'reasoning') : null,
      x.text ? blk(x.src === 'agent' ? 'Text' : 'Prompt', x.text, x.src !== 'agent', x.src === 'agent' ? 'reasoning' : 'input') : null));
  for (const c of calls) {
    while (li < loose.length && loose[li].before <= c.k) right.appendChild(looseBlock(loose[li++]));
    const k = c.k;
    const labs = own.get(k) || [];
    const el = h('div', { class: ['call', labelled(k) && 'has-label', labs.some(l => l.ladder === 'coordination') && 'has-board'], id: `a-${ctx.agent}-call-${k}`,
      dataset: { call: String(k) }, style: labs.length ? `--step:${RAMP[labs[0].ladder][labs[0].index]}` : null },
      h('div', { class: 'ch' }, h('span', { class: 'cn' }, `Call ${k}`), c.step != null ? h('span', null, `step ${c.step}`) : null,
        lv ? soFar(valsAt(k), new Set((fresh.get(k) || []).map(e => e.ladder))) : null,
        h('span', { class: 'mz' }, c.tc.function_name || '')),
      h('div', { class: 'cb' }, lv ? labelsOf(k) : null,
        c.reasoning ? blk('Reasoning', c.reasoning, false, 'reasoning') : null,
        c.text && c.text.trim() ? blk('Text', c.text, false, 'reasoning') : null,
        blk(`Tool call: ${c.tc.function_name || '?'}`, argText(c.tc.arguments), false, 'tool'),
        c.res.length ? c.res.map(r => blk('Tool result', resultText(r), true, 'tool')) : null));
    blocks.push(el);
    right.appendChild(el);
  }
  while (li < loose.length) right.appendChild(looseBlock(loose[li++]));

  // 5. left: the whole episode, one row per call. A lane per ladder for the level so far, one per ladder for the
  // call's own labels, milestones in the margin.
  const NS = 'http://www.w3.org/2000/svg';
  const LB = 160, LW = 10, GAP = 1, TOP = 26;
  const lanesW = order.length * (LW + GAP) - GAP;
  const RX = LB + lanesW + 8, RW = Math.max(lanesW, 44), W = RX + RW + 2;
  // A milestone label wider than the margin breaks before its call or "(end of run)" onto a second line.
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = '600 10px "IBM Plex Sans", system-ui, sans-serif';
  const fits = t => measure.measureText(t).width <= LB - 14;
  const maxH = Math.max(320, Math.min(window.innerHeight - 220, 900));
  const rh = Math.max(2, Math.min(9, maxH / Math.max(n, 1)));
  const H = Math.ceil(n * rh) + TOP + 6 + 14;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('class', 'cs-svg');
  const mk = (tag, a) => { const e = document.createElementNS(NS, tag); for (const [x, y] of Object.entries(a)) e.setAttribute(x, y); return e; };
  const text = (x, y, cls, str, tip) => {
    const e = mk('text', { x, y, 'text-anchor': 'middle', class: cls });
    e.textContent = str;
    if (tip) { const ttl = document.createElementNS(NS, 'title'); ttl.textContent = tip; e.appendChild(ttl); }
    svg.appendChild(e);
  };
  text(LB + lanesW / 2, 9, 'cs-lane-g', 'so far', 'the level each ladder had reached by this call');
  text(RX + RW / 2, 9, 'cs-lane-g', 'this step', 'the labels this call carries itself');
  order.forEach((L, i) => {
    const x = LB + i * (LW + GAP);
    const na = single && L !== 'cheating';
    text(x + LW / 2, 21, 'cs-lane-h', SHORT[L], lv ? name(L) : L);
    if (lanesW <= RW) text(RX + i * (LW + GAP) + LW / 2, 21, 'cs-lane-h', SHORT[L], lv ? name(L) : L);
    for (let k = 0; k < n; k++) {
      svg.appendChild(mk('rect', { x, y: TOP + k * rh, width: LW, height: rh + 0.3, fill: na || !level[i][k] ? 'var(--line)' : RAMP[L][level[i][k]], class: 'cs-cell', 'data-call': k }));
    }
    // end-of-run level (no call recorded) as a final band
    const v = finalIx[L];
    svg.appendChild(mk('rect', { x, y: TOP + n * rh + 4, width: LW, height: 8, fill: na || !v ? 'var(--line)' : RAMP[L][v] }));
  });
  svg.appendChild(mk('rect', { x: RX, y: TOP, width: RW, height: n * rh, fill: 'none', stroke: 'var(--line)', 'stroke-width': 0.5 }));
  for (let k = 0; k < n; k++) {
    for (const l of own.get(k) || []) {
      const i = order.indexOf(l.ladder);
      // sorted highest level first, so the first label of a ladder at this call is the one drawn
      if (i < 0 || (own.get(k).find(o => o.ladder === l.ladder) !== l)) continue;
      svg.appendChild(mk('rect', { x: RX + i * (LW + GAP), y: TOP + k * rh, width: LW, height: Math.max(rh, 3), fill: RAMP[l.ladder][l.index], stroke: 'var(--line-2)', 'stroke-width': 0.5, 'data-call': k }));
    }
    if (regAt.has(k)) svg.appendChild(mk('rect', { x: RX + RW - 3, y: TOP + k * rh, width: 3, height: Math.max(rh, 3), fill: 'var(--bad)', 'data-call': k }));
  }
  let last = -99;
  const placed = [];
  for (const m of miles) {
    const y0 = m.end ? TOP + n * rh + 8 : TOP + m.k * rh + rh / 2;
    const ty = Math.max(y0 + 3, last + 12);
    const two = !fits(m.t);
    last = two ? ty + 11 : ty;
    placed.push(ty);
    if (two) placed.push(ty + 11);
    const col = m.ladder === 'reg' ? 'var(--bad)' : m.ladder === 'cheating' ? 'var(--accent)' : 'var(--ink-2)';
    const tx = mk('text', { x: LB - 10, y: ty, 'text-anchor': 'end', class: 'cs-mile', fill: col, 'data-call': m.end ? '' : m.k });
    if (two) {
      for (const [i, part] of [m.head, m.tail].entries()) {
        const ts = mk('tspan', { x: LB - 10, dy: i ? 11 : 0 });
        ts.textContent = part;
        tx.appendChild(ts);
      }
    } else tx.textContent = m.t;
    const tt = document.createElementNS(NS, 'title'); tt.textContent = m.tip || m.t; tx.appendChild(tt);
    svg.appendChild(tx);
    svg.appendChild(mk('line', { x1: LB - 7, y1: ty - 3, x2: LB - 2, y2: y0, stroke: col, 'stroke-width': 1 }));
  }
  if (last + 8 > H) svg.setAttribute('height', Math.ceil(last + 8));
  for (let k = 0; k < n; k += 10) {
    const y = TOP + k * rh + 3;
    if (placed.every(py => Math.abs(py - y) > 12)) {
      const tx = mk('text', { x: LB - 10, y, 'text-anchor': 'end', class: 'cs-tick', 'data-call': k });
      tx.textContent = `call ${k}`;
      svg.appendChild(tx);
    }
  }
  const mine = lv && lv.agents[ctx.agent];
  const ladderLine = mine ? h('div', { class: 'cs-ladders' }, order.map(L => {
    const c = mine[L];
    return [h('div', { class: 'cs-ladders-row', title: `${c.rule}${c.evidence ? '\nEvidence: ' + c.evidence : ''}` },
      h('i', { style: `background:${c.na || !c.index ? 'var(--line)' : RAMP[L][c.index]}` }), `${name(L)} `,
      c.na ? 'n/a' : h('b', null, c.level), c.na ? '' : ` ${c.index}/${c.of}`)];
  })) : null;
  const left = h('div', { class: 'left' },
    h('h2', null, 'The whole episode ', h('span', { class: 'sub' }, `${plural(n, 'call')}, ${nLabelled} with a label`)),
    ladderLine,
    svg,
    lv ? fullLegend(lv.ladders, order) : null,
    h('p', { class: 'note' }, 'One row per tool call, call 0 at the top. The lanes under "so far" show the level each ladder had reached by that call; the band below the last call is the level at the end of the run. The lanes under "this step" show the labels the call carries itself, and a red tick a call that met the hidden regulator on the board. The outlined row is the call at the top of the page. Click a row or a label to open that call.'),
    unplaced ? h('p', { class: 'note' }, `${plural(unplaced, 'label')} ${unplaced === 1 ? 'cites' : 'cite'} a call this transcript does not hold and ${unplaced === 1 ? 'is' : 'are'} not drawn.`) : null);

  // The outlined row: the call at the top of the page as it scrolls, and the call a click opened once the page
  // has come to rest there.
  let mark = null;
  let marked = -1;
  let opened = null;
  let rest = 0;
  function markRow(k) {
    if (k === marked) return;
    marked = k;
    if (mark) mark.remove();
    mark = mk('rect', { x: LB - 1, y: TOP + k * rh - 1, width: RX + RW - LB + 2, height: Math.max(3, rh + 2), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2 });
    svg.appendChild(mark);
    // The strip scrolls inside its own column when it is taller than the window: keep the outlined row in sight.
    const box = left.getBoundingClientRect();
    const row = mark.getBoundingClientRect();
    if (row.top < box.top + 8) left.scrollTop -= box.top + 8 - row.top;
    else if (row.bottom > box.bottom - 8) left.scrollTop += row.bottom - (box.bottom - 8);
  }
  function show(k) {
    const el = blocks[k];
    if (!el) return null;
    for (const b of right.querySelectorAll('.call.here')) b.classList.remove('here');
    el.classList.add('here');
    opened = k;
    el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    markRow(k);
    settle();
    return el;
  }
  // A call near the end of the page cannot reach the top, so the scroll alone would outline another row. Once the
  // page has stopped moving, the row of the call the click opened is outlined again.
  function settle() {
    clearTimeout(rest);
    rest = setTimeout(() => { if (opened != null) markRow(opened); opened = null; }, 250);
  }
  // The run page's bar wraps onto a second line in a narrow window. The strip and an opened call sit under its real
  // bottom edge (tv_style.css reads --under-bar).
  let under = '';
  function fit() {
    const bar = document.querySelector('.rp-bar');
    const now = bar ? `${Math.round(bar.getBoundingClientRect().bottom + 8)}px` : '';
    if (now && now !== under) el.style.setProperty('--under-bar', under = now);
  }
  function follow() {
    fit();
    if (!n) return;
    if (opened != null) settle();
    const bar = document.querySelector('.rp-bar');
    // The call at the top of the page: the first one with 60 px or more showing under the bar. Over the last
    // screen of the page that line slides to the bottom of the window, so the last calls get their turn too.
    const top = (bar ? bar.getBoundingClientRect().bottom : 0) + 60;
    const toGo = document.documentElement.scrollHeight - window.scrollY - window.innerHeight;
    const span = Math.max(1, window.innerHeight - top);
    const y = top + (toGo < span ? (1 - Math.max(0, toGo) / span) * span : 0);
    let k = -1;
    for (let i = 0; i < n; i++) {
      if (!blocks[i].offsetParent) continue;   // hidden by the Show filter
      k = i;
      if (blocks[i].getBoundingClientRect().bottom >= y) break;
    }
    if (k >= 0) markRow(k);
  }
  let queued = false;
  svg.addEventListener('click', e => {
    const tgt = e.target.closest('[data-call]');
    let k = tgt && tgt.getAttribute('data-call') !== '' ? Number(tgt.getAttribute('data-call')) : null;
    if (k == null) { const r = svg.getBoundingClientRect(); k = Math.floor((e.clientY - r.top - TOP) / rh); }
    if (k >= 0 && k < n) show(k);
  });
  const el = h('div', { class: 'case' }, h('div', { class: 'main' }, left, right));
  FOLLOW.set(el, () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; follow(); });
  });
  requestAnimationFrame(() => { if (el.isConnected) fit(); });   // the page shows the episode before the next frame
  return {
    el, nCalls: n, showAll() {},
    findCall: k => show(k),
    findToolCall: id => (idOf.has(id) ? show(idOf.get(id)) : null),
  };
}
