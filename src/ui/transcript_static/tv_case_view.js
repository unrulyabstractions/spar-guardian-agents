/* One agent's whole episode in the case-study layout: a sticky left column "The whole episode" (the three ladders, one
 * lane each, one row per tool call, and beside them the labels each call carries itself) and the transcript on the
 * right, one block per step, a tool call's labels under its head line. Copied from collusion-hermaneutics' viewer
 * (viewer/static/app/case_view.js, after arena-capstone script/visualize_case.html). Our edits: readableReasoning,
 * which the source imported from its transcript.js, sits here verbatim; the imports point at this viewer's files; the
 * source's sentence lens is gone and each call shows its labels (src/ui/transcript_levels.py _agent_labels) in its
 * place; the run page shows the strip and the steps as two panes, the steps scrolling in theirs, and the strip follows
 * them, with a slider beside it that brings any call to the top; a step holds the messages that fed its model turn,
 * then its calls; every box folds to its header; each judge label whose excerpt code located in a model call's
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
const SHORT = { cheating: 'H', coordination: 'C', collusion: 'K' };
// The strip pane's height besides the strip's rows: its padding, heading, chips and legend, the strip's own head and
// end band, and the pane's bottom gap (tv_style.css .rp-stage .case .left).
const STRIP_CHROME = 260;
/** A ladder's own colour: its first level above the line. lad: its /api/ladders entry. */
const inkOf = (L, lad) => RAMP[L][lad.levels.indexOf(lad.line)];
const clip = (s, n) => (s && s.length > n ? `${s.slice(0, n - 1)}…` : s);
/** A tooltip of at most n words. */
const clipWords = (s, n = 12) => { const w = String(s || '').split(/\s+/).filter(Boolean); return w.length > n ? `${w.slice(0, n).join(' ')}…` : w.join(' '); };
// Milestone wording: what the strip writes beside the call that first reached a level.
const WORDS = {
  cheating: { looked: 'looked', tempted: 'tempted', attempted: 'attempted', hacked: 'hacked' },
  coordination: { read: 'read a teammate', write: 'posted', low: 'low back-and-forth', medium: 'medium back-and-forth', high: 'high back-and-forth', extreme: 'extreme back-and-forth' },
  collusion: { cooperation: 'cooperation', collaboration: 'collaboration', collusion: 'collusion' },
};

// The page has one episode on screen at a time. One scroll listener for the page (a narrow window's one column) asks that
// episode to move its strip, and a WeakMap holds the episodes, so an episode the page dropped is freed; the steps' own
// pane has its own listener.
const FOLLOW = new WeakMap();
function followPage() {
  const shown = document.querySelector('.case');
  const follow = shown && FOLLOW.get(shown);
  if (follow) follow();
}
window.addEventListener('scroll', followPage, { passive: true });
window.addEventListener('resize', followPage, { passive: true });

/** Bring el to the middle of the nearest box that scrolls up and down (or the window), moving only that one, up or
 * down: scrollIntoView would also shift every clipped ancestor, sideways too, where no reader can scroll it back. */
export function centreVertically(el) {
  if (!el) return;
  let box = el.parentElement;
  while (box && !/(auto|scroll)/.test(getComputedStyle(box).overflowY)) box = box.parentElement;
  const r = el.getBoundingClientRect();
  if (!box || box === document.body || box === document.documentElement) {
    window.scrollTo({ top: window.scrollY + r.top + r.height / 2 - window.innerHeight / 2 });
    return;
  }
  const b = box.getBoundingClientRect();
  box.scrollTop += r.top + r.height / 2 - (b.top + box.clientHeight / 2);
}

/** Reasoning as text, or a note when the provider hid it. */
export function readableReasoning(raw) {
  if (raw == null || raw === '') return null;
  const s = typeof raw === 'string' ? raw : JSON.stringify(raw);
  const t = s.trim();
  if (t === '[redacted reasoning]') return { hidden: 'redacted' };
  // An encrypted reasoning token (gAAAAA..., URL-safe base64 parts joined by dots): unreadable, so not shown.
  if (/^gAAAAA[\w.=-]{80,}$/.test(t.replace(/\s+/g, ''))) return { hidden: 'encrypted' };
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

/** The strip's legend: one row per ladder, its lane code and name, then every level above none as a swatch in a grid of
 * equal columns, a rule at the ladder's line; the grid holds every ladder's count of levels a whole number of times
 * (their least common multiple, while that stays small), so each ladder's levels share the row equally and a long name
 * gets the room of a ladder with fewer levels; a row for the regulator's tick when the strip draws one. */
export function stripLegend(ladders, order, regulator = false) {
  const counts = order.map(L => ladders[L].levels.length - 1);
  const gcd = (a, b) => (b ? gcd(b, a % b) : a);
  const lcm = counts.reduce((a, b) => (a * b) / gcd(a, b), 1);
  const cols = lcm <= 24 ? lcm : Math.max(...counts);
  const row = L => {
    const lad = ladders[L];
    const line = lad.levels.indexOf(lad.line);
    const span = cols % (lad.levels.length - 1) === 0 ? cols / (lad.levels.length - 1) : 1;
    const cells = lad.levels.slice(1).map((v, j) => h('td', { class: ['csl-level', j + 1 === line && 'csl-line'],
      colspan: span > 1 ? String(span) : null, title: clipWords(`${v}: ${lad.meanings[j + 1]}`) },
    h('i', { style: `background:${RAMP[L][j + 1]}` }), v));
    return h('tr', null, h('th', { scope: 'row' }, h('span', { class: 'csl-mark', style: `color:${inkOf(L, lad)}` }, SHORT[L]), lad.name),
      cells);
  };
  return h('table', { class: 'cs-legend' },
    h('colgroup', null, h('col', { class: 'csl-name-col' }), Array.from({ length: cols }, () => h('col'))),
    h('tbody', null, order.map(row),
      regulator ? h('tr', null, h('th', { scope: 'row' }, h('span', { class: 'csl-mark csl-reg' }), 'Regulator'),
        h('td', { class: 'csl-level csl-reg-cell', colspan: String(cols) }, h('i', { class: 'csl-reg-swatch' }), 'met on the board')) : null));
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
// The parts of a step, each a section with a header strip that folds it: the run page's bar folds a part everywhere.
export const PARTS = [['step', 'Steps'], ['reasoning', 'Reasoning'], ['call', 'Tool calls'], ['result', 'Tool results']];
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
/** A prompt the agent was given (System, User): a section headed by its length, long ones clipped. */
const promptSection = text => section('prompt', 'Text', h('span', { class: 'sec-size' },
  plural(String(text || '').split('\n').length, 'line')), text, null);
/** A chevron that folds its step box or call box to its header. */
function foldButton(kind) {
  return h('button', { type: 'button', class: 'box-fold', 'aria-expanded': 'true', title: `fold or unfold this ${kind}`,
    onclick: e => {
      const box = e.currentTarget.closest(kind === 'step' ? '.step-box' : '.call');
      const on = !box.classList.contains('folded');
      box.classList.toggle('folded', on);
      e.currentTarget.setAttribute('aria-expanded', String(!on));
    } }, h('span', { class: 'sec-chev', 'aria-hidden': 'true' }));
}
/** A box and the step around it unfolded. */
function unfold(box) {
  for (const b of [box, box.closest('.step-box')]) {
    if (!b || !b.classList.contains('folded')) continue;
    b.classList.remove('folded');
    const btn = b.querySelector(':scope > .ch > .box-fold, :scope > .step-h > .box-fold');
    if (btn) btn.setAttribute('aria-expanded', 'true');
  }
}
/** Every section of a step box unfolded and unclipped. */
function openSections(box) {
  for (const s of box.querySelectorAll('.sec')) { foldSection(s, false); s.classList.remove('clipped'); }
}

/**
 * ctx: {agent, levels (the run's three-ladder payload, with its labels by call and its judge labels), isRegulator,
 * regulatorCalls: Set of tool_call_ids that read a regulator's post, or that posted as the regulator}.
 * Returns {el, findCall, findToolCall, showReasoning, setFolds (a Set of PARTS keys folded everywhere), refresh, nCalls}. A model call is numbered from 1 over the
 * agent's steps, as the judge and code number it (src/judging/judge_records.py reasoning_calls); its reasoning sits on
 * its first tool call, or on its own block when it made none.
 */
export function renderCase(t, ctx) {
  const lv = ctx.levels;
  const steps = (t && t.trajectory && t.trajectory.steps) || [];
  // 1. tool calls in order, with the step's reasoning and text on its first call; the episode in order as model turns
  // ({turn: true, step, calls, msg: its message when it made no call}) and the messages that feed the next turn ({msg})
  const calls = [];
  const loose = [];
  const seq = [];
  let model = 0;
  for (const s of steps) {
    const src = s.source || 'unknown';
    const rs = (s.observation && s.observation.results) || [];
    const reasoning = readableReasoning(s.reasoning_content);
    const msg = asStr(s.message);
    const m = src === 'agent' ? ++model : null;
    if (src === 'agent' && (s.tool_calls || []).length) {
      const mine = s.tool_calls.map((tc, j) => {
        let res = rs.filter(r => r.source_call_id === tc.tool_call_id);
        if (!res.length && rs[j] && !rs[j].source_call_id) res = [rs[j]];
        const c = { k: calls.length, tc, res, reasoning: j === 0 && reasoning && reasoning.text, text: j === 0 ? msg : '', step: s.step_id, model: j === 0 ? m : null };
        calls.push(c);
        return c;
      });
      seq.push({ turn: true, step: s.step_id, calls: mine });
    } else {
      const x = { before: calls.length, src, text: msg, reasoning: reasoning && reasoning.text, step: s.step_id, model: m };
      loose.push(x);
      seq.push(src === 'agent' ? { turn: true, step: s.step_id, calls: [], msg: x } : { msg: x });
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
    if (w) miles.push({ k, t: w, head: w, tail: '', ladder: e.ladder, tip: `call ${k}: ${e.evidence || w}` });
  }
  const regAt = new Set([...(ctx.regulatorCalls || [])].map(id => idOf.get(id)).filter(k => k != null));
  if (regAt.size) {
    const k0 = Math.min(...regAt);
    miles.push({ k: k0, t: 'regulator warned', head: 'regulator', tail: 'warned', ladder: 'reg', tip: `call ${k0}: ${ctx.isRegulator ? 'this agent\'s first post as the regulator' : 'the first call that read a regulator\'s post'}` });
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
  const looseEls = new Map();
  const looseBlock = x => {
    const box = looseCard(x);
    if (x.model != null) looseEls.set(x.model, box);
    return box;
  };
  // A message box folds to its header as a call does; its text is a section of its own.
  const looseCard = x => h('div', { class: 'call cs-msg' },
    h('div', { class: 'ch' }, foldButton('call'), h('span', { class: 'cn', title: x.step != null ? `step ${x.step}` : null },
      x.src === 'agent' ? 'Message' : x.src === 'user' ? 'User' : 'System'), lv ? headChips(x.before - 1, false) : null),
    h('div', { class: 'cb' }, judgedLines(x.model), x.reasoning ? reasoningSection(x.reasoning, markOf(x.model)) : null,
      x.text ? (x.src === 'agent' ? textSection(x.text) : promptSection(x.text)) : null));
  const callCard = c => {
    const k = c.k;
    const labs = own.get(k) || [];
    const el = h('div', { class: ['call', labelled(k) && 'has-label', labs.some(l => l.ladder === 'coordination') && 'has-board'], id: `a-${ctx.agent}-call-${k}`,
      dataset: { call: String(k) }, style: labs.length ? `--step:${RAMP[labs[0].ladder][labs[0].index]}` : null },
      h('div', { class: 'ch' }, foldButton('call'), h('span', { class: 'cn' }, `Call ${k}`),
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
    return el;
  };
  // A step encloses one model turn: the messages that fed it (the System and User prompts in the first), then its calls
  // or its message. A step and each box in it fold to their header.
  const stepBody = (step, label) => {
    const box = h('div', { class: 'step-box', dataset: { step: String(step != null ? step : '') } },
      h('div', { class: 'step-h' }, foldButton('step'), h('span', { class: 'step-n' }, label)),
      h('div', { class: 'step-b' }));
    right.appendChild(box);
    return box.lastChild;
  };
  let fed = [];
  for (const it of seq) {
    if (!it.turn) { fed.push(it.msg); continue; }
    const body = stepBody(it.step, it.step != null ? `Step ${it.step}` : it.calls.length ? `Call ${it.calls[0].k}` : 'Message');
    for (const x of fed) body.appendChild(looseBlock(x));
    fed = [];
    if (it.msg) body.appendChild(looseBlock(it.msg));
    for (const c of it.calls) body.appendChild(callCard(c));
  }
  // Messages after the last turn: a step of their own.
  if (fed.length) {
    const body = stepBody(fed[0].step, fed[0].step != null ? `Step ${fed[0].step}` : 'Message');
    for (const x of fed) body.appendChild(looseBlock(x));
  }

  // 5. left: the whole episode, one row per call. A lane per ladder for the level so far, one per ladder for the
  // call's own labels, the two groups apart, each lane headed by its code in the ladder's colour; milestones in the
  // margin.
  const NS = 'http://www.w3.org/2000/svg';
  const LB = 160, LW = 14, GAP = 5, SEP = 16, TOP = 30;
  const lanesW = order.length * (LW + GAP) - GAP;
  // RX + RW + 3 to + 7: the regulator's ticks; from + 11: the call numbers, clear of them.
  const RX = LB + lanesW + SEP, RW = lanesW, W = RX + RW + 38;
  // A milestone label wider than the margin breaks before its call or "end" onto a second line.
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = '600 10px "IBM Plex Sans", system-ui, sans-serif';
  const fits = t => measure.measureText(t).width <= LB - 14;
  // The strip fits the pane under the run page's bar with its heading, its chips and its legend (STRIP_CHROME).
  const bar = document.querySelector('.rp-bar');
  const underBar = bar ? bar.getBoundingClientRect().bottom + 8 : 120;
  const maxH = Math.max(200, Math.min(window.innerHeight - underBar - STRIP_CHROME, 900));
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
  let lastAt = null;
  const placed = [];
  for (const m of miles) {
    const y0 = m.end ? TOP + n * rh + 8 : TOP + m.k * rh + rh / 2;
    const ty = Math.max(y0 + 3, last + 12);
    const two = !fits(m.t);
    last = two ? ty + 11 : ty;
    placed.push(ty);
    if (two) placed.push(ty + 11);
    // Each milestone in its ladder's colour, as the lanes' heads; the regulator's in the regulator's.
    const col = m.ladder === 'reg' ? 'var(--bad)' : lv && lv.ladders[m.ladder] ? inkOf(m.ladder, lv.ladders[m.ladder]) : 'var(--ink-2)';
    const tx = mk('text', { x: LB - 10, y: ty, 'text-anchor': 'end', class: 'cs-mile', fill: col, 'data-call': m.end ? '' : m.k });
    if (two) {
      for (const [i, part] of [m.head, m.tail].entries()) {
        const ts = mk('tspan', { x: LB - 10, dy: i ? 11 : 0 });
        ts.textContent = part;
        tx.appendChild(ts);
      }
    } else tx.textContent = m.t;
    const tt = document.createElementNS(NS, 'title'); tt.textContent = clipWords(m.tip) || m.t; tx.appendChild(tt);
    svg.appendChild(tx);
    // One leader per call: milestones stacked on one call share the first one's.
    const at = m.end ? 'end' : m.k;
    if (at !== lastAt) svg.appendChild(mk('line', { x1: LB - 7, y1: ty - 3, x2: LB - 2, y2: y0, stroke: col, 'stroke-width': 1 }));
    lastAt = at;
  }
  if (last + 8 > H) svg.setAttribute('height', Math.ceil(last + 8));
  // A faint grid across the lanes, a line every `step` calls, its call number at the right.
  const step = [5, 10, 20, 25, 50, 100, 200, 500].find(s => s * rh >= 28) || 1000;
  for (let k = 0; k <= n; k += step) {
    const y = TOP + k * rh;
    svg.appendChild(mk('line', { x1: LB - 3, y1: y, x2: RX + RW + 9, y2: y, class: 'cs-grid' }));
    const tx = mk('text', { x: RX + RW + 11, y: y + 3, class: 'cs-tick', 'data-call': Math.min(k, n - 1) });
    tx.textContent = String(k);
    svg.appendChild(tx);
  }
  // Where one step (model turn) ends and the next begins: a fainter line across the lanes, with no number.
  for (let k = 1; k < n; k++) {
    if (calls[k].step === calls[k - 1].step) continue;
    const y = TOP + k * rh;
    svg.appendChild(mk('line', { x1: LB, y1: y, x2: LB + lanesW, y2: y, class: 'cs-stepline' }));
    svg.appendChild(mk('line', { x1: RX, y1: y, x2: RX + RW, y2: y, class: 'cs-stepline' }));
  }
  // The agent's level on each ladder at the end of the run, as chips with their evidence on hover.
  const mine = lv && lv.agents[ctx.agent];
  const ladderLine = mine ? h('div', { class: 'cs-ladders' }, order.filter(L => !mine[L].na).map(L =>
    h('span', { class: 'cs-ladder', title: lv.ladders[L].name },
      h('span', { class: 'cs-ladder-mark', style: `color:${inkOf(L, lv.ladders[L])}` }, SHORT[L]),
      levelChip(L, lv.ladders[L], mine[L].level, false, { facts: mine[L].facts })))) : null;
  // A slider beside the strip, a row per call as the strip's: its handle at the call at the top of the steps, and a drag,
  // a click on its track or the arrow keys bring a call there.
  const thumb = h('div', { class: 'cs-thumb' });
  const slider = h('div', { class: 'cs-slider', role: 'slider', tabindex: n ? '0' : null, 'aria-label': 'call',
    'aria-orientation': 'vertical', 'aria-valuemin': '0', 'aria-valuemax': String(Math.max(0, n - 1)),
    style: `left:${W + 12}px;top:${TOP}px;height:${Math.max(1, n * rh)}px`, hidden: !n },
  h('div', { class: 'cs-track' }), thumb);
  // One step back or forward, beside the slider at its middle: the previous step's first call, or the next step's.
  const stepJump = d => () => {
    if (!n) return;
    const at = Math.max(0, marked);
    const sid = calls[at].step;
    let k = at;
    if (d > 0) {
      while (k < n - 1 && calls[k].step === sid) k++;
      if (calls[k].step === sid) return;
    } else {
      while (k > 0 && calls[k].step === sid) k--;
      const prev = calls[k].step;
      while (k > 0 && calls[k - 1].step === prev) k--;
    }
    seek(k);
  };
  const stepBtns = h('div', { class: 'cs-stepbtns', style: `left:${W + 34}px;top:${TOP + (n * rh) / 2 - 22}px`, hidden: !n },
    h('button', { type: 'button', class: 'cs-stepbtn', title: 'previous step', 'aria-label': 'previous step', onclick: stepJump(-1) }, '▲'),
    h('button', { type: 'button', class: 'cs-stepbtn', title: 'next step', 'aria-label': 'next step', onclick: stepJump(1) }, '▼'));
  const left = h('div', { class: 'left' },
    ladderLine,
    h('div', { class: 'cs-strip', style: `width:${W + 58}px` }, svg, slider, stepBtns),
    lv ? stripLegend(lv.ladders, single ? ['cheating'] : order, regAt.size > 0) : null,
    unplaced ? h('p', { class: 'note' }, `${plural(unplaced, 'label')} not drawn: call not in this transcript`) : null);

  // The outlined row: the call at the top of the page as it scrolls, and the call a click opened once the page
  // has come to rest there.
  let mark = null;
  let marked = -1;
  let opened = null;
  let rest = 0;
  let dragging = false;
  let seeking = false;
  function markRow(k) {
    if (k === marked) return;
    marked = k;
    if (mark) mark.remove();
    mark = mk('rect', { x: LB - 1, y: TOP + k * rh - 1, width: RX + RW - LB + 2, height: Math.max(3, rh + 2), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2 });
    svg.appendChild(mark);
    thumb.style.top = `${k * rh + rh / 2}px`;
    slider.setAttribute('aria-valuenow', String(k));
    slider.title = `call ${k}`;
    // The strip scrolls inside its own column when it is taller than the window: keep the outlined row in sight.
    const box = left.getBoundingClientRect();
    const row = mark.getBoundingClientRect();
    if (row.top < box.top + 8) left.scrollTop -= box.top + 8 - row.top;
    else if (row.bottom > box.bottom - 8) left.scrollTop += row.bottom - (box.bottom - 8);
  }
  // The steps scroll in their own pane (tv_style.css), or with the page in a narrow window's one column.
  const paned = () => getComputedStyle(right).overflowY !== 'visible';
  // A wheel over the strip's pane scrolls the steps, unless the strip itself has more to scroll that way: the steps
  // move wherever the pointer is.
  left.addEventListener('wheel', e => {
    if (!paned() || e.ctrlKey) return;
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? right.clientHeight : 1);
    const leftCan = dy > 0 ? left.scrollTop + left.clientHeight < left.scrollHeight - 1 : left.scrollTop > 0;
    if (leftCan || !dy) return;
    e.preventDefault();
    right.scrollTop += dy;
  }, { passive: false });
  // box at the top of the steps' pane, or of the page under the run page's bar.
  function toTop(box, behavior) {
    if (!paned()) {
      const bar = document.querySelector('.rp-bar');
      const under = bar ? bar.offsetHeight + (parseFloat(getComputedStyle(bar).top) || 0) + 8 : 8;
      window.scrollTo({ top: Math.max(0, window.scrollY + box.getBoundingClientRect().top - under), behavior });
      return;
    }
    const off = () => box.getBoundingClientRect().top - right.getBoundingClientRect().top - 8;
    right.scrollTo({ top: Math.max(0, right.scrollTop + off()), behavior });
    // A smooth scroll can end short when the steps above it change height on the way: once it ends, the box is put
    // at the top, unless the reader has scrolled since (a second and a half later).
    if (behavior !== 'smooth') return;
    const asked = Date.now();
    right.addEventListener('scrollend', () => {
      if (Date.now() - asked < 1500 && Math.abs(off()) > 2) right.scrollTop += off();
    }, { once: true });
  }
  // The box that shows call k: its own, its step's when the step is folded, or none when Board calls only hides it.
  function shownBox(k) {
    const b = blocks[k];
    if (b.offsetParent) return b;
    const step = b.closest('.step-box.folded');
    return step && step.offsetParent ? step : null;
  }
  function show(k, behavior = 'smooth') {
    const el = blocks[k];
    if (!el) return null;
    for (const b of right.querySelectorAll('.call.here')) b.classList.remove('here');
    el.classList.add('here');
    unfold(el);
    openSections(el);
    opened = k;
    toTop(el, behavior);
    markRow(k);
    settle();
    return el;
  }
  // The slider's call brought to the top of the steps at once, or the nearest call shown, which the handle then marks.
  function seek(k) {
    if (!n) return;
    k = Math.max(0, Math.min(n - 1, k));
    let box = null;
    for (let i = k; i < n && !box; i++) if ((box = shownBox(i))) k = i;
    for (let i = k - 1; i >= 0 && !box; i--) if ((box = shownBox(i))) k = i;
    opened = k;
    seeking = true;   // the strip keeps the slider's call until the pane comes to rest
    markRow(k);
    if (box) toTop(box, 'instant');
    settle();
  }
  // A call near the end of the page cannot reach the top, so the scroll alone would outline another row. Once the
  // page has stopped moving, the row of the call the click opened is outlined again.
  function settle() {
    clearTimeout(rest);
    rest = setTimeout(() => { if (opened != null) markRow(opened); opened = null; seeking = false; }, 250);
  }
  // The run page's bar wraps onto a second line in a narrow window. The strip and an opened call sit under its real
  // bottom edge (tv_style.css reads --under-bar).
  let under = '';
  function fit() {
    const bar = document.querySelector('.rp-bar');
    const now = bar ? `${Math.round(bar.getBoundingClientRect().bottom + 8)}px` : '';
    if (now && now !== under) el.style.setProperty('--under-bar', under = now);
  }
  // The steps' window: the pane's top, height and what is left below it, or the page's under the bar.
  function frame() {
    if (paned()) {
      const r = right.getBoundingClientRect();
      return { top: r.top, height: right.clientHeight, toGo: right.scrollHeight - right.scrollTop - right.clientHeight };
    }
    const bar = document.querySelector('.rp-bar');
    const top = bar ? bar.getBoundingClientRect().bottom : 0;
    return { top, height: window.innerHeight - top, toGo: document.documentElement.scrollHeight - window.scrollY - window.innerHeight };
  }
  function follow() {
    fit();
    if (!n || dragging || seeking) return;
    if (opened != null) settle();
    // The call at the top of the steps: the first one with 40 px or more showing. Over the last screen that line
    // slides to the bottom, so the last calls get their turn too.
    const f = frame();
    const top = f.top + 40;
    const span = Math.max(1, f.top + f.height - top);
    const y = top + (f.toGo < span ? (1 - Math.max(0, f.toGo) / span) * span : 0);
    let k = -1;
    for (let i = 0; i < n; i++) {
      const box = shownBox(i);
      if (!box) continue;   // hidden by Board calls only
      k = i;
      if (box.getBoundingClientRect().bottom >= y) break;
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
  const callAt = y => { const r = slider.getBoundingClientRect(); return Math.floor((y - r.top) / rh); };
  slider.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    e.preventDefault();
    try { slider.setPointerCapture(e.pointerId); } catch (x) { /* a synthetic pointer: the moves still reach the slider */ }
    dragging = true;
    slider.classList.add('dragging');
    slider.focus({ preventScroll: true });
    seek(callAt(e.clientY));
  });
  slider.addEventListener('pointermove', e => { if (dragging) seek(callAt(e.clientY)); });
  const release = () => { if (!dragging) return; dragging = false; slider.classList.remove('dragging'); settle(); };
  slider.addEventListener('pointerup', release);
  slider.addEventListener('pointercancel', release);
  slider.addEventListener('lostpointercapture', release);
  const KEYS = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1, PageDown: 10, PageUp: -10 };
  slider.addEventListener('keydown', e => {
    const k = e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : e.key in KEYS ? Math.max(0, marked) + KEYS[e.key] : null;
    if (k == null) return;
    e.preventDefault();
    seek(k);
  });
  const el = h('div', { class: 'case' }, h('div', { class: 'main' }, left, right));
  const schedule = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; follow(); });
  };
  FOLLOW.set(el, schedule);
  right.addEventListener('scroll', schedule, { passive: true });
  requestAnimationFrame(() => { if (el.isConnected) fit(); });   // the page shows the episode before the next frame
  // The parts the run page's bar folds everywhere: applied when the page's choice changed since this episode last
  // took it, so a section folded or opened by hand stays as it is across tab switches.
  let foldedParts = null;
  return {
    el, nCalls: n,
    refresh: schedule,   // the strip and its slider brought back to the steps, after the page showed or changed them
    setFolds(off) {
      const key = [...off].sort().join(',');
      if (key === foldedParts) return;
      foldedParts = key;
      for (const s of right.querySelectorAll('.sec')) foldSection(s, off.has(s.dataset.part));
      for (const b of right.querySelectorAll('.step-box')) b.classList.toggle('folded', off.has('step'));
    },
    findCall: k => show(k),
    findToolCall: id => (idOf.has(id) ? show(idOf.get(id)) : null),
    // Model call mm's reasoning: its block opened and the judge's excerpt, marked there, brought to the middle at
    // once (a smooth scroll to the block would end elsewhere) and flashed.
    showReasoning(mm) {
      const c = calls.find(x => x.model === mm);
      const box = c ? show(c.k, 'instant') : looseEls.get(mm);
      if (!box) return null;
      unfold(box);
      openSections(box);
      const mark = box.querySelector('mark.xl-mark');
      const centre = () => centreVertically(mark || box);
      centre();
      requestAnimationFrame(centre);
      if (mark) { mark.classList.remove('flash'); void mark.offsetWidth; mark.classList.add('flash'); }
      return box;
    },
  };
}
