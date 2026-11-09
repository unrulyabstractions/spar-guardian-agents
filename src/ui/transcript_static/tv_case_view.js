/* One agent's whole episode in the case-study layout: a sticky left column "The whole episode" (the six ladders, one
 * lane each, one row per tool call, and the lens's sentence raster) and the transcript on the right, one block per tool
 * call, every sentence a span the lens colours. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/case_view.js, after arena-capstone script/visualize_case.html). Our edits: readableReasoning,
 * which the source imported from its transcript.js, sits here verbatim; the imports point at this viewer's files.
 * Every string goes in through h() text nodes. */
import { h, plural } from '/static/app/dom.js';
import { colour, lensLabel, scoreZ } from './tv_lens.js';

// Colour ramps per ladder, level 0 first. Cheating and collaboration are viz_common.js RUNG and COLLAB fills.
export const RAMP = {
  cheat: ['#eeede8', '#fde4c8', '#f9c396', '#f29a6a', '#e8714a', '#dc5b3c', '#d0452e', '#a8201c'],
  coord: ['#eeede8', '#f1eefa', '#e4dff7', '#d1c8f3', '#bdb2ee', '#8a76dc', '#4f3cb0'],
  coop: ['#eeede8', '#cfe6f0', '#7fb8d4', '#2b7d96'],
  collab: ['#eeede8', '#d3ecd9', '#7cc192', '#1f6b47'],
  collude: ['#eeede8', '#f4d6d0', '#eaa79a', '#d0452e', '#a8201c', '#6e0f0c'],
  scheme: ['#eeede8', '#c9a0d8', '#7a4fb0'],
};
const SHORT = { cheat: 'Ch', coord: 'Cd', coop: 'Cp', collab: 'Cb', collude: 'Cl', scheme: 'Sc' };
// Milestone wording, after group_labels.js RUNG_W ("saw it", "tried to reach it", ...).
const WORDS = {
  cheat: { exposed: 'saw it', tried: 'tried to reach it', opened: 'opened it', used: 'used it', tampered: 'tampered with the game', checked: 'checked it with the grader', shipped: 'shipped it' },
  coord: { 'read the board': 'read the board', posted: 'posted', discussed: 'discussed', solicited: 'solicited', crossed: 'piece crossed', assembled: 'assembled' },
};
const STAGE = ['not seen yet', 'saw it', 'tried to reach it', 'opened it', 'used it', 'tampered with the game', 'checked it with the grader', 'shipped it'];

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

/** Sentences of a text, each in a span.sn (so a later reader pass can colour them one by one). */
function sentences(text, call) {
  const box = h('div', { class: 'txt' });
  // the lens file's sentence_split_regex, (?<=[.!?])\s+|\n+, with the separators kept as plain text
  const parts = String(text || '').split(/((?<=[.!?])\s+|\n+)/);
  parts.forEach((p, i) => { if (!p) return; box.appendChild(i % 2 ? document.createTextNode(p) : h('span', { class: 'sn', dataset: { call: String(call) } }, p)); });
  return box;
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
function blk(label, text, call, input, kind) {
  const el = h('div', { class: ['blk', input && 'inp', input && 'fold', kind && 'k-' + kind] }, h('div', { class: 'lab' }, label), sentences(text, call));
  if (input) el.addEventListener('click', () => el.classList.remove('fold'));
  return el;
}

/**
 * ctx: {agent, levels (the run's six-ladder payload), boardCalls Map<tool_call_id, [mark]>, regulatorReads: Set of
 * tool_call_ids that read a regulator post, isRegulator, regulatorPostCalls: Set of tool_call_ids}.
 * Returns the same handle as renderTranscript: {el, findCall, findToolCall, showAll, nCalls}.
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
  const order = lv ? lv.order : ['cheat'];
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
  if (ctx.regulatorCalls) {
    const ks = [...ctx.regulatorCalls].map(id => idOf.get(id)).filter(k => k != null).sort((a, b) => a - b);
    if (ks.length) miles.push({ k: ks[0], t: `regulator warned, call ${ks[0]}`, head: 'regulator warned,', tail: `call ${ks[0]}`, ladder: 'reg', tip: ctx.isRegulator ? 'this agent\'s first post as the hidden regulator' : 'the first call that read a hidden regulator\'s post' });
  }
  for (const e of endEv) {
    const w = (WORDS[e.ladder] || {})[e.level];
    if (w) miles.push({ k: n, t: `${w} (end of run)`, head: w, tail: '(end of run)', ladder: e.ladder, tip: e.evidence, end: true });
  }
  miles.sort((a, b) => a.k - b.k);

  // 3. right: one block per tool call
  const blocks = [];
  const right = h('div', { class: 'right' });
  const stripOf = k => h('span', { class: 'cs-strip' }, order.map((L, i) => {
    const lad = lv ? lv.ladders[L] : { name: 'Cheating', levels: STAGE };
    const na = single && L !== 'cheat';
    const v = level[i][k];
    const isNew = (fresh.get(k) || []).some(e => e.ladder === L);
    return h('span', { class: ['cs-chip', isNew && 'cs-new'], title: na ? `${lad.name}: n/a (one agent, no board)` : `${lad.name}: ${lad.levels[v]} (${v} of ${lad.levels.length - 1})${isNew ? '\nnew at this call: ' + (fresh.get(k) || []).filter(e => e.ladder === L).map(e => e.evidence).join('; ') : ''}` },
      h('i', { style: `background:${na ? 'transparent' : RAMP[L][v]}` }), na ? `${lad.name}: n/a` : [`${lad.name}: `, h('b', null, lad.levels[v]), ` (${v} of ${lad.levels.length - 1})`]);
  }));
  const miniLanes = k => h('span', { class: 'cs-mini' }, order.map((L, i) => {
    const lad = lv ? lv.ladders[L] : { name: 'Cheating', levels: STAGE };
    const na = single && L !== 'cheat';
    const v = level[i][k];
    return h('i', { style: `background:${na || !v ? 'var(--line)' : RAMP[L][v]}`,
      title: na ? `${lad.name}: n/a (one agent, no board)` : `${lad.name}: ${lad.levels[v]} (${v} of ${lad.levels.length - 1})` });
  }));
  let li = 0;
  const looseBlock = x => h('div', { class: 'call msg' }, h('div', { class: 'ch' }, h('span', { class: 'cn' }, x.src === 'agent' ? 'Message' : x.src === 'user' ? 'User' : 'System'), x.step != null ? h('span', null, `step ${x.step}`) : null),
    x.reasoning ? blk('Reasoning', x.reasoning, -1, false, 'reasoning') : null, x.text ? blk(x.src === 'agent' ? 'Text' : 'Prompt', x.text, -1, x.src !== 'agent', x.src === 'agent' ? 'reasoning' : 'input') : null);
  for (const c of calls) {
    while (li < loose.length && loose[li].before <= c.k) right.appendChild(looseBlock(loose[li++]));
    const k = c.k;
    const marks = ctx.boardCalls.get(c.tc.tool_call_id) || [];
    const evNew = fresh.get(k) || [];
    const el = h('div', { class: 'call', id: `a-${ctx.agent}-call-${k}`, dataset: { call: String(k) } },
      h('div', { class: 'ch' }, h('span', { class: 'cn' }, `Call ${k}`), ' · ', h('span', { title: 'how far the agent had got toward the honeypot by this call' }, STAGE[level[0][k]] || ''),
        evNew.filter(e => e.ladder !== 'cheat').map(e => [' · ', h('span', { class: 'ev-w', title: e.evidence }, `${lv.ladders[e.ladder].name} ${e.level}`)]),
        evNew.filter(e => e.ladder === 'cheat' && WORDS.cheat[e.level] !== STAGE[level[0][k]]).map(e => [' · ', h('span', { class: 'ev-w', title: e.evidence }, WORDS.cheat[e.level] || e.level)]),
        h('span', { class: 'mz' }, h('span', { class: 'cs-lz' }), ' ', miniLanes(k), ' ', c.tc.function_name || '')),
      marks.length ? h('div', { class: 'cs-board' }, 'Board: ', marks.map((m, i) => [i ? ' · ' : '', h('span', { title: m.title || '' }, m.badge), m.text ? ` (${m.text})` : ''])) : null,
      c.reasoning ? blk('Reasoning', c.reasoning, k, false, 'reasoning') : null,
      c.text && c.text.trim() ? blk('Text', c.text, k, false, 'reasoning') : null,
      blk(`Tool call: ${c.tc.function_name || '?'}`, argText(c.tc.arguments), k, false, 'tool'),
      c.res.length ? c.res.map(r => blk('Tool result', resultText(r), k, true, 'tool')) : null);
    blocks.push(el);
    right.appendChild(el);
  }
  while (li < loose.length) right.appendChild(looseBlock(loose[li++]));

  // 4. left: the whole episode, one row per call, six lanes, milestones in the margin
  const NS = 'http://www.w3.org/2000/svg';
  const LB = 160, LW = 10, GAP = 1, RW = 66, RX = LB + order.length * (LW + GAP) + 4, W = RX + RW + 2;
  // A milestone label wider than the margin breaks before its call or "(end of run)" onto a second line.
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = '600 10px "IBM Plex Sans", system-ui, sans-serif';
  const fits = t => measure.measureText(t).width <= LB - 14;
  const maxH = Math.max(320, Math.min(window.innerHeight - 220, 900));
  const rh = Math.max(2, Math.min(9, maxH / Math.max(n, 1)));
  const H = Math.ceil(n * rh) + 20 + 14;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('class', 'cs-svg');
  const mk = (tag, a) => { const e = document.createElementNS(NS, tag); for (const [x, y] of Object.entries(a)) e.setAttribute(x, y); return e; };
  order.forEach((L, i) => {
    const x = LB + i * (LW + GAP);
    const lab = mk('text', { x: x + LW / 2, y: 10, 'text-anchor': 'middle', class: 'cs-lane-h' });
    lab.textContent = SHORT[L];
    const ttl = document.createElementNS(NS, 'title'); ttl.textContent = lv ? lv.ladders[L].name : L; lab.appendChild(ttl);
    svg.appendChild(lab);
    for (let k = 0; k < n; k++) {
      const na = single && L !== 'cheat';
      const r = mk('rect', { x, y: 14 + k * rh, width: LW, height: rh + 0.3, fill: na || !level[i][k] ? 'var(--line)' : RAMP[L][level[i][k]], class: 'cs-cell', 'data-call': k });
      svg.appendChild(r);
    }
    // end-of-run level (no call recorded) as a final band
    const v = finalIx[L];
    svg.appendChild(mk('rect', { x, y: 14 + n * rh + 4, width: LW, height: 8, fill: (single && L !== 'cheat') || !v ? 'var(--line)' : RAMP[L][v] }));
  });
  let last = -99;
  const placed = [];
  for (const m of miles) {
    const y0 = m.end ? 14 + n * rh + 8 : 14 + m.k * rh + rh / 2;
    const ty = Math.max(y0 + 3, last + 12);
    const two = !fits(m.t);
    last = two ? ty + 11 : ty;
    placed.push(ty);
    if (two) placed.push(ty + 11);
    const col = m.ladder === 'reg' ? 'var(--bad)' : m.ladder === 'cheat' ? 'var(--accent)' : 'var(--ink-2)';
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
    const y = 14 + k * rh + 3;
    if (placed.every(py => Math.abs(py - y) > 12)) {
      const tx = mk('text', { x: LB - 10, y, 'text-anchor': 'end', class: 'cs-tick', 'data-call': k });
      tx.textContent = `call ${k}`;
      svg.appendChild(tx);
    }
  }
  let mark = null;
  function show(k) {
    const el = blocks[k];
    if (!el) return null;
    for (const b of right.querySelectorAll('.call.here')) b.classList.remove('here');
    el.classList.add('here');
    el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    if (mark) mark.remove();
    mark = mk('rect', { x: LB - 1, y: 14 + k * rh - 1, width: RX + RW - LB + 2, height: Math.max(3, rh + 2), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2 });
    svg.appendChild(mark);
    return el;
  }
  svg.addEventListener('click', e => {
    const tgt = e.target.closest('[data-call]');
    let k = tgt && tgt.getAttribute('data-call') !== '' ? Number(tgt.getAttribute('data-call')) : null;
    if (k == null) { const r = svg.getBoundingClientRect(); k = Math.floor((e.clientY - r.top - 14) / rh); }
    if (k >= 0 && k < n) show(k);
  });
  const nSent = right.querySelectorAll('.sn').length;
  const mine = lv && lv.agents[ctx.agent];
  const sixLine = mine ? h('div', { class: 'cs-six' }, order.map((L, i) => {
    const c = mine[L];
    return [h('div', { class: 'cs-six-row', title: `${c.kind === 'proxy' ? 'Proxy' : 'Direct'} rule. ${c.rule}${c.evidence ? '\nEvidence: ' + c.evidence : ''}` },
      h('i', { style: `background:${c.na || !c.index ? 'var(--line)' : RAMP[L][c.index]}` }), `${lv.ladders[L].name} `,
      c.na ? 'n/a' : h('b', null, c.level), c.na ? '' : ` ${c.index}/${c.of}`)];
  })) : null;
  // ---- lens: tint every sentence span, draw the per-call sentence raster beside the lanes, put the mean in headers
  const rasterG = document.createElementNS(NS, 'g');
  svg.appendChild(rasterG);
  const scale = h('div', { class: 'scale', hidden: true }, h('span', null, '−2 sd'), h('span', { class: 'grad' }), h('span', null, '+2 sd'));
  const lensNote = h('p', { class: 'note', hidden: true });
  const LENS_ABOUT = { 'lexical_cheat': 'Lexical cheat lens: word weights fitted on the controls; its strongest terms are names of the planted files, so it marks contact with the exploit more than cheating language (held-out AUROC 0.92).' };
  function setLens(L) {
    rasterG.replaceChildren();
    const spans = right.querySelectorAll('.sn');
    if (!L) {
      for (const sp of spans) { sp.style.background = ''; sp.style.color = ''; sp.removeAttribute('title'); }
      for (const b of blocks) b.querySelector('.cs-lz').textContent = '';
      scale.hidden = true; lensNote.hidden = true;
      return;
    }
    const name = /lens/i.test(L.name || '') ? L.name : lensLabel(L.name || 'lens');
    const idx = new Map();
    const perCall = new Map();
    for (const sp of spans) {
      const call = Number(sp.dataset.call);
      const i = idx.get(call) || 0; idx.set(call, i + 1);
      const z = scoreZ(L, sp.textContent, ctx.runId, ctx.agent, call, i);
      const own = !sp.closest('.inp');
      const al = own ? 0.85 : 0.6;
      const t = Math.abs(Math.max(-1, Math.min(1, (z || 0) / 2)));
      sp.style.background = colour(z, al);
      sp.style.color = t * al > 0.55 ? '#fff' : '';
      sp.title = `${name} ${Number.isFinite(z) ? (z >= 0 ? '+' : '−') + Math.abs(z).toFixed(2) + ' sd' : 'not scored'}`;
      if (call >= 0) { if (!perCall.has(call)) perCall.set(call, []); perCall.get(call).push([sp.textContent.length, z, own]); }
    }
    for (const b of blocks) {
      const k = Number(b.dataset.call);
      const own = (perCall.get(k) || []).filter(x => x[2] && Number.isFinite(x[1]));
      const mz = own.length ? own.reduce((a, x) => a + x[1], 0) / own.length : NaN;
      b.querySelector('.cs-lz').textContent = Number.isFinite(mz) ? `${name} ${mz >= 0 ? '+' : '−'}${Math.abs(mz).toFixed(2)} sd` : '';
    }
    for (let k = 0; k < n; k++) {
      const ss = perCall.get(k) || [];
      const tot = ss.reduce((a, x) => a + Math.max(1, x[0]), 0) || 1;
      let x = RX;
      for (const [len, z, own] of ss) {
        const w = RW * Math.max(1, len) / tot;
        rasterG.appendChild(mk('rect', { x, y: 14 + k * rh, width: w + 0.3, height: rh + 0.2, fill: Number.isFinite(z) ? colour(z, own ? 1 : 0.55) : 'transparent', 'data-call': k }));
        x += w;
      }
    }
    lensNote.textContent = `${LENS_ABOUT[L._key] || name + '.'} Read left to right, top to bottom; each sentence is as wide as its length. One row per call; inputs (prompts, tool results) are drawn fainter. Red is above the lens's usual level, blue below.`;
    scale.hidden = false; lensNote.hidden = false;
  }
  const rl = mk('text', { x: RX + RW / 2, y: 10, 'text-anchor': 'middle', class: 'cs-lane-h' });
  rl.textContent = 'sentences';
  svg.appendChild(rl);
  svg.appendChild(mk('rect', { x: RX, y: 14, width: RW, height: n * rh, fill: 'none', stroke: 'var(--line)', 'stroke-width': 0.5 }));

  const left = h('div', { class: 'left' },
    h('h2', null, 'The whole episode ', h('span', { class: 'sub' }, `${n} calls, ${nSent} sentences`)),
    sixLine,
    svg, scale, lensNote,
    lv ? fullLegend(lv.ladders, order) : null,
    h('p', { class: 'note' }, 'Lanes show the six ladders; the sentence column shows the selected lens (none: blank). One row per tool call, call 0 at the top; the band below the last call is the level at the end of the run. Click a row or a label to open that call.'));
  const el = h('div', { class: 'case' }, h('div', { class: 'main' }, left, right));
  return {
    el, nCalls: n, showAll() {}, setLens,
    findCall: k => show(k),
    findToolCall: id => (idOf.has(id) ? show(idOf.get(id)) : null),
  };
}
