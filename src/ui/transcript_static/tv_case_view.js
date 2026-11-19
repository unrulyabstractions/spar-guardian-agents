/* One agent's whole episode in the case-study layout: a sticky left column "The whole episode" (the three ladders, one
 * lane each, one row per tool call, and beside them the labels each call carries itself) and the transcript on the
 * right, one block per step, a tool call's labels under its head line. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/case_view.js, after arena-capstone script/visualize_case.html). Our edits: readableReasoning,
 * which the source imported from its transcript.js, sits here verbatim; the imports point at this viewer's files; the
 * source's sentence lens is gone and each call shows its labels (src/ui/transcript_levels.py _agent_labels) in its
 * place; the strip follows the page as it scrolls; each judge label whose excerpt code located in a model call's
 * reasoning is a label of that call and its excerpt is marked in the reasoning (showReasoning opens it); a step's
 * reasoning, tool call and tool result are three sections, each folded by its header strip; a step's head line is its
 * call number and its levels as chips, an outline for a level reached before and a marked chip for a label of its own;
 * the strip's legend is one small table, a row per ladder. Every string goes in through h() text nodes. */
import { h, plural } from '/static/app/dom.js';
import { excerptSpan, markedNodes } from './tv_excerpt.js';
import { levelChip, scoreChip } from './tv_levels.js';

// Colour ramps per ladder, level 0 first. Each ladder's line (/api/ladders' "line", the first level above it) parts two
// colour families: pale and calm below it, strong above it, so a reader sees the line at a glance.
export const RAMP = {
  // none, looked, tempted (calm blue-grey) | attempted, hacked (strong red): src/measures/rungs.py RUNGS
  cheating: ['#eeede8', '#dbe5ee', '#a9c1d9', '#cf3a27', '#7d140f'],
  // none, read, write, low (pale sand) | medium, high, extreme (strong violet): src/measures/coordination.py LEVELS
  coordination: ['#eeede8', '#efe8d3', '#e2d4ab', '#cfbb85', '#6f4fc0', '#4e2ca3', '#2e1773'],
  // none, cooperation, collaboration (calm green) | collusion (strong crimson): src/measures/collusion.py RUNGS
  collusion: ['#eeede8', '#d5ebe0', '#93c9ae', '#c2185b'],
};
// A judge label's score from pale to strong, one calm ramp: 0, 1 to 24, 25 to 49, 50 to 74, 75 to 99, 100.
export const SCORE_RAMP = ['#eeede8', '#e3e7ef', '#c5d0e4', '#97a9cf', '#5b73ad', '#2c3f78'];
export const scoreColour = s => SCORE_RAMP[s <= 0 ? 0 : s >= 100 ? 5 : 1 + Math.floor(s / 25)];

// WCAG relative luminance of a #rrggbb colour.
const luminance = hex => [1, 3, 5].map(j => parseInt(hex.slice(j, j + 2), 16) / 255)
  .map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  .reduce((sum, c, i) => sum + [0.2126, 0.7152, 0.0722][i] * c, 0);
const INKS = ['#ffffff', '#16191a'];
/** The text colour that reads on a swatch: white or near-black, whichever has the higher WCAG contrast. */
export function inkOn(hex) {
  const contrast = ink => { const [a, b] = [luminance(hex), luminance(ink)].sort((x, y) => y - x); return (a + 0.05) / (b + 0.05); };
  return contrast(INKS[0]) >= contrast(INKS[1]) ? INKS[0] : INKS[1];
}
const SHORT = { cheating: 'Ch', coordination: 'Cd', collusion: 'Cl' };
/** A ladder's own colour: its first level above the line. lad: its /api/ladders entry. */
const inkOf = (L, lad) => RAMP[L][lad.levels.indexOf(lad.line)];
const clip = (s, n) => (s && s.length > n ? `${s.slice(0, n - 1)}…` : s);
// Milestone wording: what the strip writes beside the call that first reached a level.
const WORDS = {
  cheating: { looked: 'looked', tempted: 'tempted', attempted: 'attempted', hacked: 'hacked' },
  coordination: { read: 'read a teammate', write: 'posted', low: 'low back-and-forth', medium: 'medium back-and-forth', high: 'high back-and-forth', extreme: 'extreme back-and-forth' },
  collusion: { cooperation: 'cooperation', collaboration: 'collaboration', collusion: 'collusion' },
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

/** The strip's legend: one row per ladder, its lane code and name, then every level above none as a swatch in aligned
 * cells, a rule at the ladder's line; a row for the regulator's tick when the strip draws one. */
export function stripLegend(ladders, order, regulator = false) {
  const cols = Math.max(...order.map(L => ladders[L].levels.length)) - 1;
  const row = L => {
    const lad = ladders[L];
    const line = lad.levels.indexOf(lad.line);
    const cells = lad.levels.slice(1).map((v, j) => h('td', { class: ['csl-level', j + 1 === line && 'csl-line'], title: lad.meanings[j + 1] },
      h('i', { style: `background:${RAMP[L][j + 1]}` }), v));
    return h('tr', null, h('th', { scope: 'row' }, h('span', { class: 'csl-mark', style: `color:${inkOf(L, lad)}` }, SHORT[L]), lad.name),
      cells, cells.length < cols ? h('td', { colspan: String(cols - cells.length) }) : null);
  };
  return h('table', { class: 'cs-legend' }, h('tbody', null, order.map(row),
    regulator ? h('tr', null, h('th', { scope: 'row' }, h('span', { class: 'csl-mark csl-reg' }), 'Regulator'),
      h('td', { class: 'csl-level', colspan: String(cols) }, 'met on the board')) : null));
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
/** A prompt the agent was given (System, User): its first lines, the whole text on a click. */
function promptBlock(label, text) {
  const el = h('div', { class: 'blk inp fold' }, h('div', { class: 'lab' }, label), h('div', { class: 'txt' }, String(text || '')));
  el.addEventListener('click', () => el.classList.remove('fold'));
  return el;
}

// The parts of a step, each a section with a header strip that folds it: the run page's bar folds a part everywhere.
export const PARTS = [['reasoning', 'Reasoning'], ['call', 'Tool calls'], ['result', 'Tool results']];
// A section longer than this starts clipped to its first lines.
const CLIP_LINES = 12;
/** Fold (on) or unfold one section; its header strip stays. */
function foldSection(el, on) {
  el.classList.toggle('folded', on);
  el.querySelector('.sec-h').setAttribute('aria-expanded', String(!on));
}
/** A step's part: part is reasoning, call or result; name and tag head its strip; mark: the span of the text to mark
 * (a judge's excerpt), or null. */
function section(part, name, tag, text, mark) {
  const s = String(text || '');
  const lines = s.split('\n').length;
  const long = lines > CLIP_LINES;
  const el = h('div', { class: ['sec', `sec-${part}`, long && 'clipped'], dataset: { part } },
    h('button', { type: 'button', class: 'sec-h', 'aria-expanded': 'true', title: `fold or unfold this ${name.toLowerCase()}`,
      onclick: () => foldSection(el, !el.classList.contains('folded')) },
    h('span', { class: 'sec-chev', 'aria-hidden': 'true' }), h('span', { class: 'sec-name' }, name), tag),
    h('div', { class: 'sec-b' }, h('div', { class: 'txt' }, mark ? markedNodes(s, mark) : s),
      long ? h('button', { type: 'button', class: 'sec-more', title: 'show the whole text', onclick: () => el.classList.remove('clipped') },
        `show all ${lines} lines`) : null));
  return el;
}
const reasoningSection = (text, mark) => section('reasoning', 'Reasoning', null, text, mark);
const textSection = text => section('reasoning', 'Text', null, text, null);
/** Every section of a step box unfolded and unclipped. */
function openSections(box) {
  for (const s of box.querySelectorAll('.sec')) { foldSection(s, false); s.classList.remove('clipped'); }
}

/**
 * ctx: {agent, levels (the run's three-ladder payload, with its labels by call and its judge labels), isRegulator,
 * regulatorCalls: Set of tool_call_ids that read a regulator's post, or that posted as the regulator}.
 * Returns {el, findCall, findToolCall, showReasoning, setFolds (a Set of PARTS keys folded everywhere), nCalls}. A model call is numbered from 1 over the
 * agent's steps, as the judge and code number it (src/judging/judge_records.py reasoning_calls); its reasoning sits on
 * its first tool call, or on its own block when it made none.
 */
export function renderCase(t, ctx) {
  const lv = ctx.levels;
  const steps = (t && t.trajectory && t.trajectory.steps) || [];
  // 1. tool calls in order, with the step's reasoning and text on its first call
  const calls = [];
  const loose = [];
  let model = 0;
  for (const s of steps) {
    const src = s.source || 'unknown';
    const rs = (s.observation && s.observation.results) || [];
    const reasoning = readableReasoning(s.reasoning_content);
    const msg = asStr(s.message);
    const m = src === 'agent' ? ++model : null;
    if (src === 'agent' && (s.tool_calls || []).length) {
      s.tool_calls.forEach((tc, j) => {
        let res = rs.filter(r => r.source_call_id === tc.tool_call_id);
        if (!res.length && rs[j] && !rs[j].source_call_id) res = [rs[j]];
        calls.push({ k: calls.length, tc, res, reasoning: j === 0 && reasoning && reasoning.text, text: j === 0 ? msg : '', step: s.step_id, model: j === 0 ? m : null });
      });
    } else {
      loose.push({ before: calls.length, src, text: msg, reasoning: reasoning && reasoning.text, step: s.step_id, model: m });
    }
  }
  // The judge labels whose excerpt code located in a model call's reasoning, by model call: [{key, name, score,
  // reason, span (null when the reasoning shown does not hold the excerpt)}].
  const about = lv ? lv.judge_label_about.labels : {};
  const scored = (lv && lv.judge_labels.agents[ctx.agent]) || {};
  const reasoningOf = new Map([...calls, ...loose].filter(x => x.model != null).map(x => [x.model, x.reasoning]));
  const judgedAt = new Map();
  for (const [key, e] of Object.entries(scored)) {
    const at = e && e.located;
    if (!at || at.where !== 'reasoning' || !about[key]) continue;
    const span = excerptSpan(reasoningOf.get(at.call), e.excerpt, at.start, at.end);
    if (!judgedAt.has(at.call)) judgedAt.set(at.call, []);
    judgedAt.get(at.call).push({ key, name: about[key].name, score: e.score, reason: e.reason, span });
  }
  // The first excerpt marked in a model call's reasoning (one mark per block).
  const markOf = mm => ((judgedAt.get(mm) || []).find(x => x.span) || {}).span || null;
  const n = calls.length;
  const idOf = new Map(calls.map(c => [c.tc.tool_call_id, c.k]));
  // 2. level per ladder per call, and milestones: the highest reached so far, or for a held event (Coordination) the
  // level the event sets, which may be lower than the one before
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
  // The event that reached each ladder's level so far, at each call: {k, facts}.
  const reach = order.map(() => new Array(n).fill(null));
  const cur = Object.fromEntries(order.map(L => [L, 0]));
  const curEv = Object.fromEntries(order.map(L => [L, null]));
  const fresh = new Map();
  for (let k = 0; k < n; k++) {
    for (const e of at.get(k) || []) {
      if (e.held ? e.index === cur[e.ladder] : e.index <= cur[e.ladder]) continue;
      cur[e.ladder] = e.index;
      curEv[e.ladder] = { k, facts: e.facts || [] };
      if (!fresh.has(k)) fresh.set(k, []);
      fresh.get(k).push(e);
    }
    order.forEach((L, i) => { level[i][k] = cur[L]; reach[i][k] = curEv[L]; });
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
    miles.push({ k: k0, t: `regulator warned, call ${k0}`, head: 'regulator warned,', tail: `call ${k0}`, ladder: 'reg', tip: ctx.isRegulator ? 'this agent\'s first post as the regulator' : 'the first call that read a regulator\'s post' });
  }
  for (const e of endEv) {
    const w = (WORDS[e.ladder] || {})[e.level];
    if (w) miles.push({ k: n, t: `${w}, end`, head: `${w},`, tail: 'end', ladder: e.ladder, tip: e.evidence, end: true });
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

  // 4. right: one block per step. Its head line holds the call's levels as chips, in the ladders' order: the level
  // reached before as an outline, a label the call carries itself as a marked chip.
  const name = L => lv.ladders[L].name;
  const valsAt = k => order.map((L, i) => (k < 0 ? 0 : level[i][Math.min(k, n - 1)] || 0));
  const reachFacts = (i, k) => {
    const e = k < 0 || !n ? null : reach[i][Math.min(k, n - 1)];
    return e ? [['reached', `call ${e.k}`], ...e.facts] : [];
  };
  const regChip = () => h('span', { class: 'cs-reg-chip', title: ctx.isRegulator ? 'Posted as the regulator' : 'Read a regulator\'s post' },
    h('span', { class: 'lv-own-mark', 'aria-hidden': 'true' }), 'regulator');
  // k: the call; mine: whether the call's own labels show (a message between calls has none).
  function headChips(k, mine) {
    const vals = valsAt(k);
    const labels = mine ? own.get(k) || [] : [];
    return h('span', { class: 'cs-chips' }, order.map((L, i) => {
      if (single && L !== 'cheating') return null;
      const lad = lv.ladders[L];
      const here = labels.filter(l => l.ladder === L);
      const carried = vals[i] && !here.some(l => l.index === vals[i])
        ? levelChip(L, lad, lad.levels[vals[i]], false, { look: 'carried', facts: reachFacts(i, k) }) : null;
      return [carried, here.map(l => levelChip(L, lad, l.level, false, { look: 'own', facts: l.facts || [] }))];
    }), mine && regAt.has(k) ? regChip() : null);
  }
  // The judge labels whose excerpt sits in this model call's reasoning: the score, the label and the judge's reason.
  function judgedLines(mm) {
    const here = judgedAt.get(mm) || [];
    return here.length ? h('div', { class: 'cs-labels cs-judged' }, here.map(x => h('div', { class: 'cs-label' },
      scoreChip(x.score, `${x.name}: ${x.score} of 100`), h('b', null, x.name),
      h('span', { class: 'cs-why' }, x.reason || '', x.span ? null : h('i', null, ' (excerpt not in the reasoning shown)'))))) : null;
  }
  const blocks = [];
  const right = h('div', { class: 'right' });
  let li = 0;
  const looseEls = new Map();
  const looseBlock = x => {
    const box = looseCard(x);
    if (x.model != null) looseEls.set(x.model, box);
    return box;
  };
  const looseCard = x => h('div', { class: 'call msg' },
    h('div', { class: 'ch' }, h('span', { class: 'cn', title: x.step != null ? `step ${x.step}` : null },
      x.src === 'agent' ? 'Message' : x.src === 'user' ? 'User' : 'System'), lv ? headChips(x.before - 1, false) : null),
    h('div', { class: 'cb' }, judgedLines(x.model), x.reasoning ? reasoningSection(x.reasoning, markOf(x.model)) : null,
      x.text ? (x.src === 'agent' ? textSection(x.text) : promptBlock('Prompt', x.text)) : null));
  for (const c of calls) {
    while (li < loose.length && loose[li].before <= c.k) right.appendChild(looseBlock(loose[li++]));
    const k = c.k;
    const labs = own.get(k) || [];
    const el = h('div', { class: ['call', labelled(k) && 'has-label', labs.some(l => l.ladder === 'coordination') && 'has-board'], id: `a-${ctx.agent}-call-${k}`,
      dataset: { call: String(k) }, style: labs.length ? `--step:${RAMP[labs[0].ladder][labs[0].index]}` : null },
      h('div', { class: 'ch' }, h('span', { class: 'cn', title: c.step != null ? `step ${c.step}` : null }, `Call ${k}`),
        lv ? headChips(k, true) : null, h('span', { class: 'mz' }, c.tc.function_name || '')),
      h('div', { class: 'cb' }, c.model != null ? judgedLines(c.model) : null,
        c.reasoning ? reasoningSection(c.reasoning, c.model != null ? markOf(c.model) : null) : null,
        c.text && c.text.trim() ? textSection(c.text) : null,
        section('call', 'Tool call', h('span', { class: 'sec-tool' }, c.tc.function_name || '?'), argText(c.tc.arguments), null),
        c.res.map(r => {
          const out = resultText(r);
          return section('result', 'Tool result', h('span', { class: 'sec-size' }, plural(out.split('\n').length, 'line')), out, null);
        })));
    blocks.push(el);
    right.appendChild(el);
  }
  while (li < loose.length) right.appendChild(looseBlock(loose[li++]));

  // 5. left: the whole episode, one row per call. A lane per ladder for the level so far, one per ladder for the
  // call's own labels, the two groups apart, each lane headed by its code in the ladder's colour; milestones in the
  // margin.
  const NS = 'http://www.w3.org/2000/svg';
  const LB = 160, LW = 14, GAP = 5, SEP = 16, TOP = 30;
  const lanesW = order.length * (LW + GAP) - GAP;
  const RX = LB + lanesW + SEP, RW = lanesW, W = RX + RW + 8;
  // A milestone label wider than the margin breaks before its call or "end" onto a second line.
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = '600 10px "IBM Plex Sans", system-ui, sans-serif';
  const fits = t => measure.measureText(t).width <= LB - 14;
  const maxH = Math.max(320, Math.min(window.innerHeight - 220, 900));
  const rh = Math.max(2, Math.min(9, maxH / Math.max(n, 1)));
  const H = Math.ceil(n * rh) + TOP + 6 + 14;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('class', 'cs-svg');
  // An SVG element with its attributes and, when tip is given, its tooltip.
  const mk = (tag, a, tip) => {
    const e = document.createElementNS(NS, tag);
    for (const [x, y] of Object.entries(a)) e.setAttribute(x, y);
    if (tip) { const ttl = document.createElementNS(NS, 'title'); ttl.textContent = tip; e.appendChild(ttl); }
    return e;
  };
  const text = (x, y, cls, str, tip, fill) => {
    const e = mk('text', { x, y, 'text-anchor': 'middle', class: cls, ...(fill ? { fill } : {}) }, tip);
    e.insertBefore(document.createTextNode(str), e.firstChild);
    svg.appendChild(e);
  };
  text(LB + lanesW / 2, 9, 'cs-lane-g', 'so far', 'Level each ladder had reached by this call');
  text(RX + RW / 2, 9, 'cs-lane-g', 'this step', 'Labels this call carries itself');
  svg.appendChild(mk('line', { x1: RX - SEP / 2, y1: 2, x2: RX - SEP / 2, y2: TOP + n * rh + 12, stroke: 'var(--line)', 'stroke-width': 1 }));
  const levelName = (L, v) => (lv ? lv.ladders[L].levels[v] : String(v));
  order.forEach((L, i) => {
    const x = LB + i * (LW + GAP);
    const na = single && L !== 'cheating';
    const ink = lv ? inkOf(L, lv.ladders[L]) : 'var(--ink-2)';
    text(x + LW / 2, 24, 'cs-lane-h', SHORT[L], lv ? name(L) : L, ink);
    text(RX + i * (LW + GAP) + LW / 2, 24, 'cs-lane-h', SHORT[L], lv ? name(L) : L, ink);
    for (let k = 0; k < n; k++) {
      svg.appendChild(mk('rect', { x, y: TOP + k * rh, width: LW, height: rh + 0.3, fill: na || !level[i][k] ? 'var(--line)' : RAMP[L][level[i][k]], class: 'cs-cell', 'data-call': k },
        na ? null : `call ${k}: ${lv ? name(L) : L} ${levelName(L, level[i][k])}`));
    }
    // end-of-run level (no call recorded) as a final band
    const v = finalIx[L];
    svg.appendChild(mk('rect', { x, y: TOP + n * rh + 4, width: LW, height: 8, fill: na || !v ? 'var(--line)' : RAMP[L][v] },
      na ? null : `end of run: ${lv ? name(L) : L} ${levelName(L, v)}`));
  });
  svg.appendChild(mk('rect', { x: RX, y: TOP, width: RW, height: n * rh, fill: 'none', stroke: 'var(--line)', 'stroke-width': 0.5 }));
  for (let k = 0; k < n; k++) {
    for (const l of own.get(k) || []) {
      const i = order.indexOf(l.ladder);
      // sorted highest level first, so the first label of a ladder at this call is the one drawn
      if (i < 0 || (own.get(k).find(o => o.ladder === l.ladder) !== l)) continue;
      svg.appendChild(mk('rect', { x: RX + i * (LW + GAP), y: TOP + k * rh, width: LW, height: Math.max(rh, 3), fill: RAMP[l.ladder][l.index], stroke: 'var(--line-2)', 'stroke-width': 0.5, 'data-call': k },
        `call ${k}: ${name(l.ladder)} ${l.level}`));
    }
    if (regAt.has(k)) {
      svg.appendChild(mk('rect', { x: RX + RW + 3, y: TOP + k * rh, width: 4, height: Math.max(rh, 3), fill: 'var(--bad)', 'data-call': k },
        ctx.isRegulator ? `call ${k}: posted as the regulator` : `call ${k}: met the regulator`));
    }
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
    const tt = document.createElementNS(NS, 'title'); tt.textContent = clip(m.tip, 80) || m.t; tx.appendChild(tt);
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
  // The agent's level on each ladder at the end of the run, as chips with their evidence on hover.
  const mine = lv && lv.agents[ctx.agent];
  const ladderLine = mine ? h('div', { class: 'cs-ladders' }, order.filter(L => !mine[L].na).map(L =>
    levelChip(L, lv.ladders[L], mine[L].level, false, { facts: mine[L].facts }))) : null;
  const left = h('div', { class: 'left' },
    h('h2', null, 'Episode ', h('span', { class: 'sub' }, `${plural(n, 'call')} · ${nLabelled} labelled`)),
    ladderLine,
    svg,
    lv ? stripLegend(lv.ladders, single ? ['cheating'] : order, regAt.size > 0) : null,
    unplaced ? h('p', { class: 'note' }, `${plural(unplaced, 'label')} not drawn: call not in this transcript`) : null);

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
  function show(k, behavior = 'smooth') {
    const el = blocks[k];
    if (!el) return null;
    for (const b of right.querySelectorAll('.call.here')) b.classList.remove('here');
    el.classList.add('here');
    openSections(el);
    opened = k;
    el.scrollIntoView({ block: 'start', behavior });
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
      if (!blocks[i].offsetParent) continue;   // hidden by Board calls only
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
  // The parts the run page's bar folds everywhere: applied when the page's choice changed since this episode last
  // took it, so a section folded or opened by hand stays as it is across tab switches.
  let foldedParts = null;
  return {
    el, nCalls: n,
    setFolds(off) {
      const key = [...off].sort().join(',');
      if (key === foldedParts) return;
      foldedParts = key;
      for (const s of right.querySelectorAll('.sec')) foldSection(s, off.has(s.dataset.part));
    },
    findCall: k => show(k),
    findToolCall: id => (idOf.has(id) ? show(idOf.get(id)) : null),
    // Model call mm's reasoning: its block opened and the judge's excerpt, marked there, brought to the middle at
    // once (a smooth scroll to the block would end elsewhere) and flashed.
    showReasoning(mm) {
      const c = calls.find(x => x.model === mm);
      const box = c ? show(c.k, 'instant') : looseEls.get(mm);
      if (!box) return null;
      openSections(box);
      const mark = box.querySelector('mark.xl-mark');
      const centre = () => (mark || box).scrollIntoView({ block: 'center', behavior: 'instant' });
      centre();
      requestAnimationFrame(centre);
      if (mark) { mark.classList.remove('flash'); void mark.offsetWidth; mark.classList.add('flash'); }
      return box;
    },
  };
}
