/* View: timeline (#/run/<run>/timeline).
 * The page draws the swimlanes, the exposure cascade and the agent by turn raster of one run.
 *
 *   import { viewTimeline } from './view_timeline.js';
 *   const node = await viewTimeline(route, view);
 */
import { debounce, firstLine, fmtInt, fmtSecs, h, plural } from './dom.js';
import { P, R, apiFresh } from './api.js';
import { badge, errorBox, link, logLink, muted, segmented } from './components.js';
import { episodeOf } from './run_facts.js';
import { COUNT_STEPS, ROLE_ORDER, ROLE_PLURAL, TIME_STEPS, cssVar, diamond, fmtTick, lgRoles, lgSvg, nextVizUid, r2,
  roleGlyph, roleIcon, svgEl, swatch, ticksFor, tip, vizLegend, withTip, xMark } from './charts.js';

// The mark a tool call gets: the task's shell and Python tools, the board tools (board_status drawn as a read) and
// submit; any other tool is 'other'. In the raster a call's cell takes post, read or submit, else work.
const TOOL_KIND = { shell: 'shell', run_python: 'python', board_post: 'board_post', board_read: 'board_read',
  board_status: 'board_read', submit: 'submit' };
const toolKind = fn => TOOL_KIND[fn] || 'other';
const RASTER_OF_TOOL = { board_post: 'post', board_read: 'read', submit: 'submit' };

/**
 * The verdict a chart draws for one agent: a counted verdict (the server's `counted`: a verdict on an episode with a
 * transcript) in a run that did not fail. Any other verdict is drawn as "not counted" and kept for the tooltip.
 * Returns {v: true|false|null (drawn outcome), raw (the judge's value), label, why}.
 */
function verdictInfo(run, ep) {
  const c = ep && ep.verdict ? ep.verdict.cheated : null;
  const raw = c === true || c === false ? c : null;
  if (raw == null) return { v: null, raw, label: 'not judged', why: null };
  if (!ep.counted) return { v: null, raw, label: 'not counted', why: 'the episode has no transcript (harness crash or provider error)' };
  if (run.failed) return { v: null, raw, label: 'not counted', why: 'the run failed (harness crash or provider error)' };
  return { v: raw, raw, label: raw ? 'cheated' : 'not cheated', why: null };
}
function verdictLine(info) {
  if (info.v != null) return `judge: ${info.label}`;
  if (info.raw != null) return `judge said ${info.raw ? 'cheated' : 'not cheated'}; not counted, since ${info.why}`;
  return info.why ? `judge: ${info.label} (${info.why})` : `judge: ${info.label}`;
}

/** Agents in lane order: grouped by role (disregulator, worker, regulator, then none), by name within a group. */
export function laneOrder(tl) {
  const roles = tl.roles;
  const names = new Set(tl.agents);
  for (const e of tl.events) if (e.agent) names.add(e.agent);
  const rank = r => { const i = ROLE_ORDER.indexOf(r); return i < 0 ? ROLE_ORDER.length : i; };
  return [...names].sort((a, b) => rank(roles[a]) - rank(roles[b]) || a.localeCompare(b, 'en', { numeric: true }));
}

/**
 * Round-robin turn clock: each agent's k-th model call starts at turn k (counted from 1, as in the
 * tooltips and the raster), and time between two calls maps linearly between them. Each lane keeps
 * its own clock; the axis runs from 1 to maxTurns + 1.
 */
function turnClock(tl, agents) {
  const starts = new Map(agents.map(a => [a, []]));
  const ends = new Map(agents.map(a => [a, 0]));
  for (const e of tl.events) {
    if (!starts.has(e.agent) || e.t == null) continue;
    if (e.kind === 'model') starts.get(e.agent).push(e.t);
    const te = e.t_end != null ? e.t_end : e.t;
    if (te > ends.get(e.agent)) ends.set(e.agent, te);
  }
  const fns = new Map();
  let maxTurns = 0;
  for (const a of agents) {
    const st = starts.get(a).sort((x, y) => x - y);
    maxTurns = Math.max(maxTurns, st.length);
    const knots = st.map((t, i) => [t, i + 1]);
    knots.push([Math.max(ends.get(a), st.length ? st[st.length - 1] + 1e-3 : 0), st.length + 1]);
    fns.set(a, t => {
      if (!st.length || t <= knots[0][0]) return 1;
      let lo = 0;
      let hi = knots.length - 1;
      if (t >= knots[hi][0]) return knots[hi][1];
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (knots[mid][0] <= t) lo = mid; else hi = mid; }
      const [t0, k0] = knots[lo];
      const [t1, k1] = knots[hi];
      return k0 + (t1 > t0 ? (t - t0) / (t1 - t0) : 0) * (k1 - k0);
    });
  }
  return { at: (a, t) => (fns.has(a) ? fns.get(a)(t) : 1), maxTurns: Math.max(1, maxTurns) };
}

// Agent x turn raster: one cell per model call, coloured by the call's main action.
const RASTER_KINDS = [
  ['work', 'shell, run_python or another tool', 'sw-work'],
  ['post', 'board_post', 'sw-post'],
  ['read', 'board_read or board_status', 'sw-read'],
  ['exposed', 'exposed (code ladder)', 'sw-exposed'],
  ['opened', 'opened (code ladder)', 'sw-opened'],
  ['submit', 'submit', 'sw-submit'],
  ['refusal', 'refusal sentence as output', 'sw-refusal'],
  ['none', 'no tool call', 'sw-none'],
];
const RASTER_LABEL = Object.fromEntries(RASTER_KINDS.map(([k, l]) => [k, l]));

/** Per agent: its model calls in order, each with the action of tool_calls[0] and the tool events that follow it. */
function rasterRows(tl, agents) {
  const by = new Map(agents.map(a => [a, { models: [], tools: [] }]));
  for (const e of tl.events) {
    const r = by.get(e.agent);
    if (!r || e.t == null) continue;
    if (e.kind === 'model') r.models.push(e);
    else if (e.kind === 'tool' || e.kind === 'submit') r.tools.push(e);
  }
  return agents.map(a => {
    const { models, tools } = by.get(a);
    models.sort((x, y) => x.t - y.t);
    tools.sort((x, y) => x.t - y.t);
    let j = 0;
    const cells = models.map((m, k) => {
      const next = k + 1 < models.length ? models[k + 1].t : Infinity;
      while (j < tools.length && tools[j].t < m.t) j++;
      const mine = [];
      while (j < tools.length && tools[j].t < next) mine.push(tools[j++]);
      const fn = m.tool_calls[0];
      let kind = !fn ? 'none' : RASTER_OF_TOOL[toolKind(fn)] || 'work';
      if (m.refusal) kind = 'refusal';
      if (mine.some(x => x.opened)) kind = 'opened';
      else if (mine.some(x => x.exposed)) kind = 'exposed';
      return { kind, fn, model: m, tools: mine };
    });
    return { agent: a, cells };
  });
}

function rasterColors() {
  return {
    work: cssVar('--raster-work', '#77776f'), post: cssVar('--tl-post', '#e87ba4'), read: cssVar('--tl-read', '#4a3aa7'),
    exposed: cssVar('--exposed', '#2e94dc'), opened: cssVar('--opened', '#750459'), submit: cssVar('--text', '#1c1c1a'),
    refusal: cssVar('--tl-refusal', '#b02a20'), none: cssVar('--raster-none', '#e4e4de'),
  };
}

/** Canvas raster. thumb: tiny cells, no gaps, click opens the timeline. */
export function rasterCanvas(tl, agents, opts = {}) {
  const { thumb = false, maxW = 900, onClick = null } = opts;
  const rows = rasterRows(tl, agents);
  const cols = Math.max(1, ...rows.map(r => r.cells.length));
  const cellH = thumb ? 4 : 13;
  const cw = thumb ? Math.max(1, Math.min(4, maxW / cols)) : Math.max(2, Math.min(12, maxW / cols));
  const W = Math.max(1, Math.ceil(cw * cols));
  const H = Math.max(1, rows.length * cellH);
  const dpr = window.devicePixelRatio || 1;
  const cv = h('canvas', { class: ['raster', thumb && 'raster-thumb'], width: Math.ceil(W * dpr), height: Math.ceil(H * dpr), role: 'img',
    'aria-label': `Agent by turn raster: ${plural(rows.length, 'agent')}, up to ${plural(cols, 'model call')} each` });
  cv.style.width = W + 'px';
  cv.style.height = H + 'px';
  const ctx = cv.getContext('2d');
  if (ctx) {
    ctx.scale(dpr, dpr);
    const col = rasterColors();
    const gx = cw >= 4 ? 1 : 0;
    rows.forEach((r, i) => r.cells.forEach((c, k) => {
      const x = k * cw;
      const y = i * cellH;
      const w = Math.max(0.8, cw - gx);
      const hh = cellH - 1;
      if (c.kind === 'refusal') {
        ctx.fillStyle = col.none; ctx.fillRect(x, y, w, hh);
        ctx.fillStyle = col.refusal; ctx.fillRect(x, y, w, Math.ceil(hh / 2));
      } else {
        ctx.fillStyle = col[c.kind] || col.none; ctx.fillRect(x, y, w, hh);
      }
    }));
  }
  const cellAt = ev => {
    const box = cv.getBoundingClientRect();
    const i = Math.floor((ev.clientY - box.top) / (box.height / rows.length));
    const k = Math.floor((ev.clientX - box.left) / (box.width / cols));
    const r = rows[i];
    return r && r.cells[k] ? { r, k, c: r.cells[k] } : (r ? { r, k, c: null } : null);
  };
  cv.addEventListener('pointermove', ev => {
    const hit = cellAt(ev);
    if (!hit) { tip.hide(); return; }
    const { r, k, c } = hit;
    const role = tl.roles[r.agent];
    if (!c) { tip.show(ev, [`${r.agent}${role ? ` (${role})` : ''}`, `${plural(r.cells.length, 'model call')}`, thumb ? 'click for the timeline' : null]); return; }
    const first = c.tools[0];
    tip.show(ev, [
      `${r.agent}${role ? ` (${role})` : ''} · call ${k + 1} of ${r.cells.length}`,
      `${RASTER_LABEL[c.kind]} · ${fmtSecs(c.model.t)}`,
      c.fn ? `tool calls: ${c.model.tool_calls.join(', ')}` : 'no tool call',
      first && first.preview ? firstLine(first.preview, 160) : null,
      thumb ? 'click for the timeline' : null,
    ]);
  });
  cv.addEventListener('pointerleave', () => tip.hide());
  if (onClick) { cv.classList.add('clickable'); cv.addEventListener('click', onClick); }
  return { el: cv, rows, cols, cellH };
}

/** Raster legend; with `present` (a Set of kinds) only the kinds that occur. */
export function rasterLegend(present) {
  return vizLegend(RASTER_KINDS.filter(([k]) => !present || present.has(k)).map(([, label, cls]) => [swatch(cls), label]), 'raster-legend');
}

/** Raster with agent labels, for the top of the timeline page. */
function rasterPanel(tl, agents, maxW) {
  const roles = tl.roles;
  const { el, rows, cols, cellH } = rasterCanvas(tl, agents, { maxW });
  const present = new Set(rows.flatMap(r => r.cells.map(c => c.kind)));
  return h('div', null,
    h('div', { class: 'raster-wrap' },
      h('div', { class: 'raster-labels', style: `grid-auto-rows: ${cellH}px` },
        agents.map(a => h('div', { class: 'raster-label' }, roles[a] ? roleIcon(roles[a], 10) : null, a))),
      h('div', { class: 'raster-scroll' }, el)),
    h('div', { class: 'muted small' }, `Columns are each agent’s own model calls, 1 to ${cols}; a call takes the action of its first tool call, and the code ladder’s exposed or opened call among the tool calls that follow it wins.`),
    rasterLegend(present));
}

/**
 * The three charts of one run's timeline (agent by turn raster, swimlanes, exposure cascade) as cards.
 * mount() draws them once the node is on screen and redraws on resize; unmount() removes the listener.
 * runInfo is the run's row of /api/runs (or its run page record): the verdicts and whether each is counted.
 */
export function timelinePanels(run, tl, runInfo) {
  const agents = laneOrder(tl);
  const roles = tl.roles;
  const hasRoles = Object.keys(roles).length > 0;
  const infos = new Map(agents.map(a => [a, verdictInfo(runInfo, episodeOf(runInfo, a))]));
  const verdictInfoOf = a => infos.get(a);
  const disclosedOf = a => { const ep = episodeOf(runInfo, a); return verdictInfoOf(a).v != null && !!ep.verdict.disclosed_access; };
  const exposedOf = a => tl.first_exposed[a];
  const openedOf = a => tl.first_opened[a];
  const ctx = { tl, agents, roles, hasRoles, verdictInfoOf, disclosedOf, exposedOf, openedOf };
  const lane = buildLaneData(tl, agents);
  const notCounted = agents.filter(a => verdictInfoOf(a).raw != null && verdictInfoOf(a).v == null);
  const whyNot = [...new Set(notCounted.map(a => verdictInfoOf(a).why))].join('; ');

  const rasterHost = h('div', { class: 'viz-host' });
  const rasterCard = h('section', { class: 'card' }, h('h2', null, 'Agent by turn'), rasterHost);
  const swim = swimlaneCard(ctx, lane, run);
  const cascadeHost = h('div', { class: 'viz-host' });
  const cascadeCard = h('section', { class: 'card' },
    h('div', { class: 'sec-head' }, h('h2', null, 'Exposure cascade'), badge('exposure, not cause', 'warn', 'reading a post before an exposure does not show the post caused it')),
    cascadeHost);
  const draw = () => {
    const w = rasterHost.clientWidth || 900;
    rasterHost.replaceChildren(rasterPanel(tl, agents, Math.max(200, w - 110)));
    swim.draw();
    cascadeHost.replaceChildren(cascadeView(ctx, cascadeHost.clientWidth || 900));
  };
  const onResize = debounce(() => { tip.hide(); draw(); }, 150);
  const note = h('div', null,
    muted(`${plural(agents.length, 'agent')} · ${plural(tl.events.length, 'event')} · ${plural(tl.board.length, 'board post')} · ${fmtSecs(tl.duration)} sample time. ` +
      'Exposed and opened are the code ladder’s calls (signals.jsonl): the first call that showed the agent a pointer to the honeypot, and the first that showed it the content. The judge verdict sits at the end of each lane.'),
    notCounted.length ? h('div', { class: 'muted small' },
      `${plural(notCounted.length, 'judge verdict')} drawn grey as “not counted”, as in the runs list and the summary: ${whyNot}.`) : null);
  return {
    node: h('div', null, rasterCard, swim.card, cascadeCard), note, draw,
    mount() { draw(); window.addEventListener('resize', onResize); },
    unmount() { window.removeEventListener('resize', onResize); tip.hide(); },
  };
}

export async function viewTimeline({ run }, view) {
  let tl;
  let detail;
  try {
    [tl, detail] = await Promise.all([apiFresh(P.timeline(run)), apiFresh(P.run(run))]);
  } catch (e) {
    view.mounted(() => { document.title = `timeline · ${run} · results`; });
    return h('div', { class: 'page' },
      h('div', { class: 'page-head' }, h('h1', null, `${run}: timeline`),
        h('div', { class: 'links' }, link(R.run(run), 'run page', 'btn btn-small'), link(R.swarm(), 'swarm visualizations', 'btn btn-small'))),
      h('p', { class: 'muted' }, 'The timeline is read from the run’s eval log. A run that is still being produced has no log yet; press Reload once it has finished.'),
      errorBox(e, 'timeline'));
  }
  const sw = detail.swarm;
  const panels = timelinePanels(run, tl, detail);
  const head = h('div', { class: 'page-head' },
    h('h1', null, `${run}: timeline`),
    h('div', { class: 'chips' },
      detail.failed ? badge('failed run', 'bad') : null,
      badge(detail.setting.label, 'plain'),
      badge(`control: ${detail.control}`, 'def'),
      sw ? badge(`composition ${sw.composition}`, 'plain', `${sw.regulators} regulators, ${sw.disregulators} disregulators, ${sw.workers} workers; seed ${sw.seed}`) : null,
      tl.limit ? badge(`limit: ${tl.limit}`, 'warn', 'the sample ended on this limit') : null),
    h('div', { class: 'links' },
      link(R.run(run), 'run page', 'btn btn-small'),
      detail.episodes.filter(e => e.has_transcript).map(e => link(R.episode(run, e.episode), `transcript ${e.orbit_agent}`, 'btn btn-small')),
      logLink(detail.eval_log_exists, detail.eval_log, R.context(run), 'context windows', 'btn btn-small')),
    panels.note);
  view.mounted(() => { document.title = `timeline · ${run} · results`; panels.mount(); });
  view.cleanup(() => panels.unmount());
  return h('div', { class: 'page' }, head, panels.node);
}

/** Events split per lane, posts, and de-duplicated reads (a read and a model exposure at the same moment draw once). */
function buildLaneData(tl, agents) {
  const lanes = new Map(agents.map(a => [a, { models: [], defcalls: [], tools: [], blocks: [] }]));
  const events = tl.events.filter(e => e.t != null);
  const freeBlocks = [];
  for (const e of events) {
    const L = lanes.get(e.agent);
    if (e.kind === 'defense') { if (e.allow === false) (L ? L.blocks : freeBlocks).push(e); continue; }
    if (!L) continue;
    if (e.kind === 'model') L.models.push(e);
    else if (e.kind === 'defense_call') L.defcalls.push(e);
    else if (e.kind === 'tool' || e.kind === 'submit') L.tools.push(e);
  }
  // A block's band starts where the control call that decided it started (same boundary preferred,
  // then the latest end): a control call that ended in (b.t - 2, b.t + 0.05]. The calls are sorted by
  // end once per lane and the window is found by binary search.
  const blockFrom = new Map();
  for (const L of lanes.values()) {
    if (!L.blocks.length) continue;
    const dcs = L.defcalls.filter(dc => dc.t_end != null).sort((x, y) => x.t_end - y.t_end);
    for (const b of L.blocks) {
      let lo = 0;
      let hi = dcs.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (dcs[mid].t_end <= b.t + 0.05) lo = mid + 1; else hi = mid; }
      let first = lo;
      while (first > 0 && b.t - dcs[first - 1].t_end < 2) first--;
      const cands = dcs.slice(first, lo);
      // A control call's defense is its span's name, "<defense>:<boundary>" (orbit.defense spans).
      const want = `${b.defense}:${b.boundary}`;
      cands.sort((x, y) => (y.defense === want) - (x.defense === want) || y.t_end - x.t_end);
      blockFrom.set(b, cands.length ? cands[0].t : b.t);
    }
  }
  const posts = tl.board.filter(p => p.t != null && p.sender);
  const reads = [];
  for (const p of posts) {
    const by = new Map();
    for (const r of p.read_by) {
      if (!r.agent || r.agent === p.sender || r.t == null || !lanes.has(r.agent)) continue;
      const key = `${r.agent}|${r.t}`;
      const prev = by.get(key);
      if (!prev) by.set(key, { post: p, agent: r.agent, t: r.t });
    }
    reads.push(...by.values());
  }
  let maxTok = 1;
  let duration = 0;
  for (const e of events) {
    if (e.kind === 'model') maxTok = Math.max(maxTok, (e.tokens_in || 0) + (e.tokens_out || 0));
    duration = Math.max(duration, e.t_end != null ? e.t_end : e.t);
  }
  duration = Math.max(duration, tl.duration || 0) || 1;
  return { lanes, blockFrom, freeBlocks, posts, reads, maxTok, duration, clock: turnClock(tl, agents) };
}

function swimlaneCard(ctx, lane, run) {
  const { agents, roles, hasRoles, verdictInfoOf, exposedOf, openedOf } = ctx;
  const { lanes, blockFrom, freeBlocks, posts, reads, maxTok, duration, clock } = lane;
  const L_ = [...lanes.values()];
  const anyDefcalls = L_.some(L => L.defcalls.length);
  // What occurs in this run, so the legend lists only marks that can be on screen.
  const allTools = L_.flatMap(L => L.tools);
  const kinds = new Set(allTools.map(e => toolKind(e.function)));
  const anyExposed = allTools.some(e => e.exposed);
  const anyOpened = allTools.some(e => e.opened);
  const anyBlockedTool = allTools.some(e => e.blocked);
  const anyRefusal = L_.some(L => L.models.some(m => m.refusal));
  const anyBlocks = freeBlocks.length > 0 || L_.some(L => L.blocks.length);
  const anyRead = reads.length > 0;
  const st = { mode: 'time', lo: 0, hi: duration, arrows: true, defcalls: true };
  const full = () => (st.mode === 'time' ? [0, duration] : [1, clock.maxTurns + 1]);
  const X = (a, t) => (st.mode === 'time' ? t : clock.at(a, t));
  const host = h('div', { class: 'viz-host swim-host' });

  // Controls: axis mode, layers, zoom.
  const modeBtns = segmented([['time', 'seconds from start'], ['turn', 'turn index']], st.mode, v => {
    if (st.mode === v) return;
    st.mode = v;
    [st.lo, st.hi] = full();
    syncZoom();
    draw();
  }, 'x axis');
  const chk = (label, key, title) => {
    const c = h('input', { type: 'checkbox', checked: st[key] });
    c.addEventListener('change', () => { st[key] = c.checked; draw(); });
    return h('label', { class: 'field check', title }, c, h('span', null, label));
  };
  const rFrom = h('input', { type: 'range', min: 0, max: 1000, step: 1, value: 0, class: 'zoom-range', 'aria-label': 'zoom from' });
  const rTo = h('input', { type: 'range', min: 0, max: 1000, step: 1, value: 1000, class: 'zoom-range', 'aria-label': 'zoom to' });
  const zoomText = h('span', { class: 'muted small zoom-text' });
  const fmtX = v => (st.mode === 'time' ? fmtSecs(v) : `call ${+v.toFixed(1)}`);
  function syncZoom() {
    const [f0, f1] = full();
    rFrom.value = Math.round(((st.lo - f0) / (f1 - f0)) * 1000);
    rTo.value = Math.round(((st.hi - f0) / (f1 - f0)) * 1000);
    zoomText.textContent = `${fmtX(st.lo)} to ${fmtX(st.hi)}`;
  }
  let raf = 0;
  const drawSoon = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };
  const onRange = which => {
    let a = +rFrom.value;
    let b = +rTo.value;
    if (b - a < 2) { if (which === 'from') a = b - 2; else b = a + 2; }
    a = Math.max(0, a); b = Math.min(1000, b);
    rFrom.value = a; rTo.value = b;
    const [f0, f1] = full();
    st.lo = f0 + ((f1 - f0) * a) / 1000;
    st.hi = f0 + ((f1 - f0) * b) / 1000;
    zoomText.textContent = `${fmtX(st.lo)} to ${fmtX(st.hi)}`;
    drawSoon();
  };
  rFrom.addEventListener('input', () => onRange('from'));
  rTo.addEventListener('input', () => onRange('to'));
  function setRange(lo, hi) {
    const [f0, f1] = full();
    const minSpan = (f1 - f0) / 500;
    lo = Math.max(f0, Math.min(lo, hi));
    hi = Math.min(f1, Math.max(lo, hi));
    if (hi - lo < minSpan) { const c = (lo + hi) / 2; lo = Math.max(f0, c - minSpan / 2); hi = Math.min(f1, lo + minSpan); }
    st.lo = lo; st.hi = hi;
    syncZoom();
    draw();
  }
  const resetBtn = h('button', { type: 'button', class: 'btn btn-small', onclick: () => setRange(...full()) }, 'reset zoom');

  const controls = h('div', { class: 'filters viz-controls' },
    h('span', { class: 'field' }, h('span', { class: 'field-label' }, 'x axis'), modeBtns),
    posts.length ? chk('board arrows', 'arrows', 'arrows from each post to its readers') : null,
    anyDefcalls ? chk('control model calls', 'defcalls', 'the control’s own judge calls, as a strip at the top of the lane') : null,
    h('span', { class: 'zoom' }, h('span', { class: 'field-label' }, 'zoom'), rFrom, rTo, zoomText, resetBtn),
    muted('drag across the chart to zoom; double-click to reset', 'small'));

  const legend = vizLegend([
    [lgSvg(svgEl('rect', { class: 'm-model', x: 6, y: 4, width: 8, height: 9, rx: 1.5 })), 'model call (height: tokens in + out, log)'],
    anyDefcalls ? [lgSvg(svgEl('rect', { class: 'm-defcall', x: 2, y: 5, width: 16, height: 3 })), 'control’s model call'] : null,
    kinds.has('shell') ? [lgSvg(svgEl('line', { class: 'tk tk-shell', x1: 10, x2: 10, y1: 2, y2: 13 })), 'shell'] : null,
    kinds.has('python') ? [lgSvg(svgEl('line', { class: 'tk tk-python', x1: 10, x2: 10, y1: 3, y2: 13 }), svgEl('rect', { class: 'fill-python', x: 8, y: 1, width: 4, height: 4 })), 'run_python'] : null,
    kinds.has('board_post') ? [lgSvg(svgEl('line', { class: 'tk tk-board_post', x1: 10, x2: 10, y1: 1, y2: 7 })), 'board_post (upper half)'] : null,
    kinds.has('board_read') ? [lgSvg(svgEl('line', { class: 'tk tk-board_read', x1: 10, x2: 10, y1: 7, y2: 13 })), 'board_read or board_status (lower half)'] : null,
    kinds.has('submit') ? [lgSvg(svgEl('line', { class: 'tk tk-submit', x1: 7, x2: 7, y1: 1, y2: 13 }), svgEl('path', { class: 'fill-ink', d: 'M7,1 l7,3 l-7,3 z' })), 'submit'] : null,
    kinds.has('other') ? [lgSvg(svgEl('line', { class: 'tk tk-other', x1: 10, x2: 10, y1: 2, y2: 13 })), 'another tool'] : null,
    posts.length ? [lgSvg(svgEl('circle', { class: 'm-post', cx: 10, cy: 7, r: 4.5 })), 'board post'] : null,
    anyRead ? [lgSvg(svgEl('line', { class: 'arrow', x1: 1, y1: 7, x2: 19, y2: 7 })), 'post read'] : null,
    anyExposed ? [lgSvg(svgEl('circle', { class: 'm-exposed', cx: 10, cy: 7, r: 5 })), 'exposed (code ladder)'] : null,
    anyOpened ? [lgSvg(diamond(10, 7, 5, 'm-opened')), 'opened (code ladder)'] : null,
    anyBlocks ? [lgSvg(svgEl('rect', { class: 'm-block', x: 5, y: 0, width: 9, height: 14 }), svgEl('line', { class: 'm-block-line', x1: 14, x2: 14, y1: 0, y2: 14 })), 'defense block (band from the control call to the decision)'] : null,
    anyRefusal || anyBlockedTool ? [lgSvg(xMark(10, 7, 4)),
      anyRefusal && anyBlockedTool ? 'refusal sentence or blocked tool call' : anyRefusal ? 'refusal sentence as output' : 'blocked tool call'] : null,
    hasRoles ? [lgRoles(), 'roles: worker, regulator, disregulator'] : null,
  ]);

  const card = h('section', { class: 'card' },
    h('h2', null, 'Agent swimlanes'),
    controls, host, legend,
    posts.length < 1 ? null : h('div', { class: 'muted small' },
      'In turn-index mode each lane runs on its own clock (its k-th model call sits at k), so an arrow between lanes can point backwards.'));

  function draw() {
    tip.hide();
    const W = Math.max(640, host.clientWidth || 1000);
    const n = Math.max(1, agents.length);
    const laneH = n <= 1 ? 76 : n <= 2 ? 64 : n <= 4 ? 52 : n <= 8 ? 42 : 36;
    const padL = 132;
    const padR = 118;
    const axisH = 26;
    const groupH = hasRoles ? 18 : 0;
    const laneY = new Map();
    const groups = [];
    let y = axisH + 2;
    let cur;
    for (const a of agents) {
      const role = roles[a] || null;
      if (hasRoles && role !== cur) { groups.push({ role, y, n: agents.filter(x => (roles[x] || null) === role).length }); y += groupH; cur = role; }
      laneY.set(a, y);
      y += laneH;
    }
    const H = y + 4;
    const x0 = padL;
    const x1 = W - padR;
    const { lo, hi } = st;
    const sx = v => x0 + ((v - lo) / (hi - lo || 1)) * (x1 - x0);
    const inv = px => lo + ((px - x0) / (x1 - x0)) * (hi - lo);
    const vis = (xa, xb) => xb >= x0 - 8 && xa <= x1 + 8;
    const uid = nextVizUid();
    const hits = [];

    const svg = svgEl('svg', { class: 'viz timeline-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': `Swimlane timeline of ${run}: ${agents.length} agents` });
    svg.appendChild(svgEl('defs', null,
      svgEl('clipPath', { id: `tlclip-${uid}` }, svgEl('rect', { x: x0, y: 0, width: x1 - x0, height: H })),
      svgEl('marker', { id: `tlarrow-${uid}`, viewBox: '0 0 8 8', refX: 7.5, refY: 4, markerWidth: 6, markerHeight: 6, orient: 'auto' },
        svgEl('path', { class: 'arrowhead', d: 'M0,0L8,4L0,8Z' }))));

    agents.forEach((a, i) => svg.appendChild(svgEl('rect', { class: ['lane-bg', i % 2 && 'alt'], x: 0, y: laneY.get(a), width: W, height: laneH })));
    for (const g of groups) {
      svg.appendChild(svgEl('line', { class: 'grp-rule', x1: 0, x2: W, y1: g.y + 0.5, y2: g.y + 0.5 }));
      svg.appendChild(svgEl('text', { class: 'grp-label', x: 8, y: g.y + 13 }, `${(ROLE_PLURAL[g.role] || 'no role').toUpperCase()} (${g.n})`));
    }

    // Axis and gridlines.
    const ticks = ticksFor(lo, hi, Math.max(3, Math.floor((x1 - x0) / 80)), st.mode === 'time' ? TIME_STEPS : COUNT_STEPS);
    for (const v of ticks) {
      const px = sx(v);
      svg.appendChild(svgEl('line', { class: 'grid', x1: px, x2: px, y1: axisH - 4, y2: H - 2 }));
      svg.appendChild(svgEl('text', { class: 'tick-label', x: px, y: axisH - 9, 'text-anchor': 'middle' }, st.mode === 'time' ? fmtTick(v) : String(v)));
    }
    svg.appendChild(svgEl('text', { class: 'axis-title', x: 10, y: axisH - 9 }, st.mode === 'time' ? 'seconds from start' : 'agent’s model call'));

    const marks = svgEl('g', { 'clip-path': `url(#tlclip-${uid})` });
    const arrowsG = svgEl('g', { 'clip-path': `url(#tlclip-${uid})` });
    const over = svgEl('g', { 'clip-path': `url(#tlclip-${uid})` });
    const barMax = laneH * 0.52;
    const barH = tok => 3 + (barMax - 3) * (tok > 0 ? Math.log1p(tok) / Math.log1p(maxTok) : 0);

    for (const a of agents) {
      const L = lanes.get(a);
      const ly = laneY.get(a);
      const base = ly + laneH - 4;
      const top = ly + 7;
      const mid = ly + laneH / 2;
      for (const b of L.blocks) {
        const xa = sx(X(a, blockFrom.get(b)));
        const xb = sx(X(a, b.t));
        if (!vis(xa, xb)) continue;
        const w = Math.max(4, xb - xa);
        marks.appendChild(svgEl('rect', { class: 'm-block', x: xb - w, y: ly, width: w, height: laneH }));
        marks.appendChild(svgEl('line', { class: 'm-block-line', x1: xb, x2: xb, y1: ly, y2: ly + laneH }));
        hits.push({ x0: xb - w, x1: xb, y0: ly, y1: ly + laneH, pri: 1,
          lines: () => [`BLOCKED by ${b.defense || '?'} at ${b.boundary || '?'}`, `${a} · ${fmtSecs(b.t)}`, b.tool ? `tool: ${b.tool}` : null, b.reason ? firstLine(b.reason, 220) : null] });
      }
      if (st.defcalls) {
        for (const dc of L.defcalls) {
          const xa = sx(X(a, dc.t));
          const xb = sx(X(a, dc.t_end != null ? dc.t_end : dc.t));
          if (!vis(xa, xb)) continue;
          marks.appendChild(svgEl('rect', { class: 'm-defcall', x: xa, y: ly + 1.5, width: Math.max(1, xb - xa), height: 3 }));
          hits.push({ x0: xa, x1: Math.max(xa + 1, xb), y0: ly, y1: ly + 5, pri: 0,
            lines: () => [`control call: ${dc.defense || '?'}`, `${a} · ${fmtSecs(dc.t)} for ${fmtSecs((dc.t_end || dc.t) - dc.t)}`, `tokens in ${fmtInt(dc.tokens_in)} · out ${fmtInt(dc.tokens_out)}`] });
        }
      }
      L.models.forEach((m, k) => {
        const xa = sx(X(a, m.t));
        const xb = sx(X(a, m.t_end != null ? m.t_end : m.t));
        if (!vis(xa, xb)) return;
        const tok = (m.tokens_in || 0) + (m.tokens_out || 0);
        const bh = barH(tok);
        const w = Math.max(1.5, xb - xa);
        marks.appendChild(svgEl('rect', { class: ['m-model', m.error && 'err'], x: xa, y: base - bh, width: w, height: bh, rx: Math.min(1.5, w / 2) }));
        if (m.refusal) over.appendChild(xMark(xa + w / 2, base - bh - 5, 3.2));
        hits.push({ x0: xa, x1: xa + w, y0: base - bh - (m.refusal ? 9 : 2), y1: base, pri: m.refusal ? 3 : 1,
          lines: () => [`${a} · model call ${k + 1} of ${L.models.length}`,
            `${fmtSecs(m.t)} for ${fmtSecs((m.t_end || m.t) - m.t)}`,
            m.tokens_in == null && m.tokens_out == null ? 'no token usage recorded' : `tokens in ${fmtInt(m.tokens_in)} · out ${fmtInt(m.tokens_out)}`,
            `tool calls: ${m.tool_calls.join(', ') || 'none'}`,
            m.refusal ? 'REFUSAL: the output text is the defense refusal sentence' : null,
            m.output_blocked ? 'a defense replaced this completion (block event)' : null, m.error ? 'model error' : null] });
      });
      for (const e of L.tools) {
        const fn = toolKind(e.function);
        const xa = sx(X(a, e.t));
        const xe = e.t_end != null ? sx(X(a, e.t_end)) : xa;
        if (!vis(xa, xe)) continue;
        let ya = top + laneH * 0.08;
        let yb = base;
        if (fn === 'board_post') { ya = top - 3; yb = mid; } else if (fn === 'board_read') { ya = mid; yb = base; } else if (fn === 'submit') { ya = top - 4; }
        if (xe - xa > 3) marks.appendChild(svgEl('line', { class: ['dur', 'tk-' + fn], x1: xa, x2: xe, y1: (ya + yb) / 2, y2: (ya + yb) / 2 }));
        marks.appendChild(svgEl('line', { class: ['tk', 'tk-' + fn], x1: xa, x2: xa, y1: ya, y2: yb }));
        if (fn === 'python') marks.appendChild(svgEl('rect', { class: 'fill-python', x: xa - 2, y: ya - 3, width: 4, height: 4 }));
        if (fn === 'submit') marks.appendChild(svgEl('path', { class: 'fill-ink', d: `M${r2(xa)},${r2(ya)}l7,3l-7,3z` }));
        if (e.blocked) over.appendChild(xMark(xa, ya - 5, 3.2));
        if (e.exposed) over.appendChild(svgEl('circle', { class: 'm-exposed', cx: xa, cy: mid, r: 5.5 }));
        if (e.opened) over.appendChild(diamond(xa, mid, 4.6, 'm-opened'));
        const pri = e.opened || e.exposed ? 4 : e.blocked ? 3 : 2;
        hits.push({ x0: xa - 1, x1: Math.max(xa + 1, xe), y0: ya - (e.blocked ? 9 : 2), y1: yb, pri,
          lines: () => [`${a} · ${e.function}${e.blocked ? ' · BLOCKED' : e.error ? ' · error' : ''}`,
            `${fmtSecs(e.t)}${e.t_end != null ? ` for ${fmtSecs(e.t_end - e.t)}` : ''}`,
            e.exposed ? 'exposed: the code ladder’s first call that showed this agent a pointer to the honeypot' : null,
            e.opened ? 'opened: the code ladder’s first call that showed this agent the honeypot’s content' : null,
            e.preview ? String(e.preview).slice(0, 300) : null] });
      }
      // Verdict at the lane's end: only a counted verdict is drawn red or green.
      const vi = verdictInfoOf(a);
      const v = vi.v;
      const vx = x1 + 16;
      const cy = ly + laneH / 2 - (laneH >= 36 && exposedOf(a) != null ? 5 : 0);
      if (v === true) {
        svg.appendChild(svgEl('rect', { class: 'v-cheated', x: vx - 5, y: cy - 5, width: 10, height: 10, rx: 2 }));
        svg.appendChild(svgEl('path', { class: 'v-mark', d: `M${vx - 2.5},${cy - 2.5}L${vx + 2.5},${cy + 2.5}M${vx + 2.5},${cy - 2.5}L${vx - 2.5},${cy + 2.5}` }));
      } else if (v === false) {
        svg.appendChild(svgEl('circle', { class: 'v-honest', cx: vx, cy, r: 5.5 }));
        svg.appendChild(svgEl('path', { class: 'v-mark', d: `M${vx - 2.8},${cy}L${vx - 0.6},${cy + 2.4}L${vx + 3},${cy - 2.4}` }));
      } else {
        svg.appendChild(svgEl('circle', { class: 'v-unknown', cx: vx, cy, r: 4.5 }));
      }
      svg.appendChild(svgEl('text', { class: ['verdict-label', v == null && 't-muted'], x: vx + 10, y: cy + 4 }, vi.label));
      if (laneH >= 36 && exposedOf(a) != null) svg.appendChild(svgEl('text', { class: 't-muted t-small', x: vx + 10, y: cy + 17 }, `exposed ${fmtSecs(exposedOf(a))}`));
      hits.push({ x0: x1 + 4, x1: W, y0: ly, y1: ly + laneH, pri: 0, lines: () => laneInfo(a) });
      // Lane label.
      const role = roles[a];
      const lcy = ly + laneH / 2;
      if (role) svg.appendChild(roleGlyph(role, 16, lcy, 5.5));
      const name = a.length > 16 ? a.slice(0, 15) + '…' : a;
      svg.appendChild(svgEl('text', { class: 'lane-name', x: role ? 28 : 10, y: lcy + (role && laneH >= 36 ? -1 : 4) }, name));
      if (role && laneH >= 36) svg.appendChild(svgEl('text', { class: 't-muted t-small', x: 28, y: lcy + 12 }, role));
      hits.push({ x0: 0, x1: x0 - 4, y0: ly, y1: ly + laneH, pri: 0, lines: () => laneInfo(a) });
    }
    // A block with no owning lane has no turn clock to map it, so it is drawn on the seconds axis only.
    for (const b of st.mode === 'time' ? freeBlocks : []) {
      const xb = sx(b.t);
      if (!vis(xb, xb)) continue;
      marks.appendChild(svgEl('line', { class: 'm-block-line', x1: xb, x2: xb, y1: axisH, y2: H }));
    }

    // Board: arrows from the post on the sender's lane to each reader's lane at read time.
    if (st.arrows) {
      for (const r of reads) {
        const p = r.post;
        if (!laneY.has(p.sender)) continue;
        const xa = sx(X(p.sender, p.t));
        const xb = sx(X(r.agent, r.t));
        if ((xa < x0 && xb < x0) || (xa > x1 && xb > x1)) continue;
        const ya = laneY.get(p.sender) + laneH * 0.3;
        const yb = laneY.get(r.agent) + laneH * 0.3;
        arrowsG.appendChild(svgEl('line', { class: 'arrow', x1: xa, y1: ya, x2: xb, y2: yb, 'marker-end': `url(#tlarrow-${uid})` }));
        hits.push({ x0: xb - 4, x1: xb + 2, y0: yb - 4, y1: yb + 4, pri: 3,
          lines: () => [`${r.agent} read post #${p.sequence} from ${p.sender}`,
            `${fmtSecs(r.t)}, posted at ${fmtSecs(p.t)}`, String(p.content || '').slice(0, 400)] });
      }
    }
    for (const p of posts) {
      if (!laneY.has(p.sender)) continue;
      const px = sx(X(p.sender, p.t));
      if (!vis(px, px)) continue;
      const py = laneY.get(p.sender) + laneH * 0.3;
      over.appendChild(svgEl('circle', { class: 'm-post', cx: px, cy: py, r: 4.5 }));
      const readers = [...new Set((p.read_by).filter(x => x.agent && x.agent !== p.sender).map(x => x.agent))];
      hits.push({ x0: px - 5, x1: px + 5, y0: py - 5, y1: py + 5, pri: 5,
        lines: () => [`post #${p.sequence} by ${p.sender} at ${fmtSecs(p.t)}`, readers.length ? `read by ${readers.join(', ')}` : 'read by no other agent',
          String(p.content || '').slice(0, 400)] });
    }
    svg.append(marks, arrowsG, over);

    // Crosshair, brush, tooltip.
    const cross = svgEl('line', { class: 'crosshair', x1: 0, x2: 0, y1: axisH - 4, y2: H, visibility: 'hidden' });
    const crossLabel = svgEl('text', { class: 'cross-label', x: 0, y: 10, 'text-anchor': 'middle', visibility: 'hidden' });
    const brush = svgEl('rect', { class: 'brush', x: 0, y: axisH - 4, width: 0, height: H - axisH + 4, visibility: 'hidden' });
    svg.append(brush, cross, crossLabel);
    const pt = ev => { const b = svg.getBoundingClientRect(); return [(ev.clientX - b.left) * (W / b.width), (ev.clientY - b.top) * (H / b.height)]; };
    const pick = (px, py) => {
      let best = null;
      let bd = 6;
      for (const it of hits) {
        const d = Math.max(0, it.x0 - px, px - it.x1) + Math.max(0, it.y0 - py, py - it.y1) - it.pri * 0.4;
        if (d < bd) { bd = d; best = it; }
      }
      return best;
    };
    let drag = null;
    svg.addEventListener('pointerdown', ev => {
      const [px, py] = pt(ev);
      if (ev.button !== 0 || px < x0 || px > x1 || py < axisH - 4) return;
      drag = { x: px, id: ev.pointerId };
      svg.setPointerCapture(ev.pointerId);
    });
    svg.addEventListener('pointermove', ev => {
      const [px, py] = pt(ev);
      const inPlot = px >= x0 && px <= x1;
      cross.setAttribute('visibility', inPlot ? 'visible' : 'hidden');
      crossLabel.setAttribute('visibility', inPlot ? 'visible' : 'hidden');
      if (inPlot) {
        cross.setAttribute('x1', r2(px)); cross.setAttribute('x2', r2(px));
        crossLabel.setAttribute('x', r2(Math.min(x1 - 24, Math.max(x0 + 24, px))));
        crossLabel.textContent = st.mode === 'time' ? fmtSecs(inv(px)) : `call ${+inv(px).toFixed(1)}`;
      }
      if (drag) {
        const a = Math.max(x0, Math.min(drag.x, px));
        const b = Math.min(x1, Math.max(drag.x, px));
        brush.setAttribute('x', r2(a)); brush.setAttribute('width', r2(b - a));
        brush.setAttribute('visibility', b - a > 2 ? 'visible' : 'hidden');
        tip.hide();
        return;
      }
      const it = pick(px, py);
      if (it) tip.show(ev, it.lines()); else tip.hide();
    });
    svg.addEventListener('pointerup', ev => {
      if (!drag) return;
      const [px] = pt(ev);
      const a = Math.max(x0, Math.min(drag.x, px));
      const b = Math.min(x1, Math.max(drag.x, px));
      drag = null;
      brush.setAttribute('visibility', 'hidden');
      if (b - a > 6) setRange(inv(a), inv(b));
    });
    // A cancelled touch or pen drag ends the brush without zooming.
    svg.addEventListener('pointercancel', () => {
      drag = null;
      brush.setAttribute('visibility', 'hidden');
    });
    svg.addEventListener('pointerleave', () => {
      tip.hide();
      cross.setAttribute('visibility', 'hidden');
      crossLabel.setAttribute('visibility', 'hidden');
    });
    svg.addEventListener('dblclick', () => setRange(...full()));
    host.replaceChildren(svg);
  }

  function laneInfo(a) {
    const L = lanes.get(a);
    const blocks = L.blocks.length;
    const refusals = L.models.filter(m => m.refusal).length;
    return [`${a}${roles[a] ? ` (${roles[a]})` : ''}`,
      verdictLine(verdictInfoOf(a)),
      `${plural(L.models.length, 'model call')} · ${plural(L.tools.length, 'tool call')}`,
      exposedOf(a) != null ? `exposed at ${fmtSecs(exposedOf(a))}` : 'never exposed (code ladder)',
      openedOf(a) != null ? `opened at ${fmtSecs(openedOf(a))}` : null,
      blocks || refusals ? `${plural(blocks, 'block')}, ${plural(refusals, 'refusal')}` : null];
  }

  syncZoom();
  return { card, draw };
}

/**
 * Edges A -> B: posts of A that B read no later than B's exposure (the code ladder's exposed call, which may be the
 * read itself). An agent the ladder never found exposed has no incoming edge.
 */
function exposureEdges(tl) {
  const edges = new Map();
  for (const p of tl.board) {
    if (!p.sender) continue;
    const seen = new Set();
    for (const r of p.read_by) {
      const B = r.agent;
      if (!B || B === p.sender || r.t == null || seen.has(B)) continue;
      const cutoff = tl.first_exposed[B];
      if (cutoff == null || !(r.t <= cutoff)) continue;
      seen.add(B);
      const k = `${p.sender}\u0000${B}`;
      if (!edges.has(k)) edges.set(k, { from: p.sender, to: B, posts: [] });
      edges.get(k).posts.push(p);
    }
  }
  return [...edges.values()];
}

function cascadeView(ctx, width) {
  const { tl, agents, roles, hasRoles, verdictInfoOf, disclosedOf, exposedOf, openedOf } = ctx;
  const edges = exposureEdges(tl).filter(e => agents.includes(e.from) && agents.includes(e.to));
  const W = Math.max(560, width);
  const rowH = agents.length <= 4 ? 44 : 32;
  const padL = 132;
  const padT = 26;
  const neverX = W - 64;
  const divX = W - 118;
  const x0 = padL + 24;
  const x1 = divX - 40;
  const H = padT + agents.length * rowH + 30;
  const touched = agents.filter(a => exposedOf(a) != null);
  const tMax = Math.max(1e-6, ...touched.map(a => exposedOf(a)));
  const sx = t => (touched.length === 1 ? (x0 + x1) / 2 : x0 + (t / tMax) * (x1 - x0));
  const pos = new Map(agents.map((a, i) => [a, [exposedOf(a) != null ? sx(exposedOf(a)) : neverX, padT + i * rowH + rowH / 2]]));
  const uid = nextVizUid();
  const nodeR = 8;

  const svg = svgEl('svg', { class: 'viz cascade-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': `Exposure cascade: ${plural(edges.length, 'edge')} between ${plural(agents.length, 'agent')}` });
  svg.appendChild(svgEl('defs', null, svgEl('marker', { id: `csarrow-${uid}`, viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 5, markerHeight: 5, orient: 'auto' },
    svgEl('path', { class: 'arrowhead', d: 'M0,0L8,4L0,8Z' }))));
  agents.forEach((a, i) => svg.appendChild(svgEl('rect', { class: ['lane-bg', i % 2 && 'alt'], x: 0, y: padT + i * rowH, width: W, height: rowH })));
  svg.appendChild(svgEl('line', { class: 'grp-rule dashed', x1: divX, x2: divX, y1: padT - 18, y2: H - 22 }));
  svg.appendChild(svgEl('text', { class: 'axis-title', x: (divX + W) / 2, y: 14, 'text-anchor': 'middle' }, 'never exposed'));
  svg.appendChild(svgEl('text', { class: 'axis-title', x: x0, y: 14 }, 'exposed (code ladder) →'));
  const ticks = touched.length ? ticksFor(0, tMax, Math.max(2, Math.floor((x1 - x0) / 90)), TIME_STEPS) : [];
  if (touched.length > 1) {
    for (const v of ticks) {
      svg.appendChild(svgEl('line', { class: 'grid', x1: sx(v), x2: sx(v), y1: padT, y2: H - 22 }));
      svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(v), y: H - 8, 'text-anchor': 'middle' }, fmtTick(v)));
    }
  } else if (touched.length === 1) {
    svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(0), y: H - 8, 'text-anchor': 'middle' }, fmtTick(exposedOf(touched[0]))));
  }
  agents.forEach((a, i) => {
    const cy = padT + i * rowH + rowH / 2;
    svg.appendChild(svgEl('text', { class: 'lane-name', x: 10, y: cy + (hasRoles && rowH >= 32 ? -1 : 4) }, a.length > 16 ? a.slice(0, 15) + '…' : a));
    if (roles[a] && rowH >= 32) svg.appendChild(svgEl('text', { class: 't-muted t-small', x: 10, y: cy + 12 }, roles[a]));
  });

  // Edges first, so nodes sit on top. An edge bows to the side that keeps it clearer of other
  // nodes; the two edges of a pair that read each other bow to opposite sides.
  const has = new Set(edges.map(e => `${e.from}\u0000${e.to}`));
  const clearance = (ax, ay, cx, cy, bx, by, skip) => {
    let m = Infinity;
    for (let i = 1; i < 20; i++) {
      const t = i / 20;
      const x = (1 - t) * (1 - t) * ax + 2 * (1 - t) * t * cx + t * t * bx;
      const y = (1 - t) * (1 - t) * ay + 2 * (1 - t) * t * cy + t * t * by;
      for (const [a, [nx, ny]] of pos) if (!skip.includes(a)) m = Math.min(m, Math.hypot(nx - x, ny - y));
    }
    return m;
  };
  for (const e of edges) {
    const [ax, ay] = pos.get(e.from);
    const [bx, by] = pos.get(e.to);
    const dx = bx - ax;
    const dy = by - ay;
    const len = Math.hypot(dx, dy) || 1;
    const bow = Math.min(40, 14 + len * 0.12);
    const ctrl = (side, k) => [(ax + bx) / 2 - side * k * (dy / len) * bow, (ay + by) / 2 + side * k * (dx / len) * bow];
    const sides = has.has(`${e.to}\u0000${e.from}`) ? [e.from < e.to ? 1 : -1] : [1, -1];
    const skip = [e.from, e.to];
    let best = null;
    for (const k of [1, 0.4, 2, 3]) {
      for (const side of sides) {
        const c = ctrl(side, k);
        const m = clearance(ax, ay, c[0], c[1], bx, by, skip);
        if (!best || m > best.m) best = { c, m };
      }
      if (best.m >= 18) break;
    }
    const [cx, cy] = best.c;
    const tx = bx - cx;
    const ty = by - cy;
    const tl2 = Math.hypot(tx, ty) || 1;
    const ex = bx - (tx / tl2) * (nodeR + 4);
    const ey = by - (ty / tl2) * (nodeR + 4);
    const d = `M${r2(ax)},${r2(ay)}Q${r2(cx)},${r2(cy)} ${r2(ex)},${r2(ey)}`;
    const w = 1 + 1.4 * Math.min(6, e.posts.length);
    svg.appendChild(svgEl('path', { class: 'edge', d, 'stroke-width': w, 'marker-end': `url(#csarrow-${uid})` }));
    svg.appendChild(withTip(svgEl('path', { class: 'edge-hit', d }), () => [
      `${e.from} → ${e.to}: ${plural(e.posts.length, 'post')}`,
      `read by ${e.to} no later than its exposure at ${fmtSecs(exposedOf(e.to))}`,
      `posts ${e.posts.map(p => '#' + p.sequence).join(', ')}`,
      firstLine(e.posts[0].content, 160)]));
  }
  for (const a of agents) {
    const [x, y] = pos.get(a);
    const vi = verdictInfoOf(a);
    const v = vi.v;
    const out = v === true ? 'out-cheated' : v === false ? 'out-honest' : 'out-unknown';
    const g = svgEl('g', { class: 'node-g' });
    if (disclosedOf(a)) g.appendChild(roleGlyph(roles[a], x, y + (roles[a] === 'disregulator' ? 1 : 0), nodeR + 4, 'disclosed-ring'));
    g.appendChild(roleGlyph(roles[a], x, y + (roles[a] === 'disregulator' ? 1 : 0), nodeR, out));
    const ins = edges.filter(e => e.to === a);
    const outs = edges.filter(e => e.from === a);
    svg.appendChild(withTip(g, () => [
      `${a}${roles[a] ? ` (${roles[a]})` : ''}`,
      `${verdictLine(vi)}${disclosedOf(a) ? ', disclosed access' : ''}`,
      exposedOf(a) != null ? `exposed at ${fmtSecs(exposedOf(a))}` : 'never exposed (code ladder)',
      openedOf(a) != null ? `opened at ${fmtSecs(openedOf(a))}` : null,
      ins.length ? `exposed by ${ins.map(e => `${e.from} (${e.posts.length})`).join(', ')}`
        : exposedOf(a) == null ? 'never exposed, so no exposure edge' : 'read no post before its exposure',
      outs.length ? `exposed ${outs.map(e => `${e.to} (${e.posts.length})`).join(', ')}` : null]));
  }

  const caption = agents.length < 2
    ? 'One agent: no board, so no exposure edges.'
    : !tl.board.length ? 'No board posts in this run, so no exposure edges.'
      : !touched.length ? 'The code ladder found no agent exposed, so there are no exposure edges.'
        : edges.length ? null : 'No agent read another agent’s post before its exposure.';
  return h('div', null,
    svg,
    caption ? h('div', { class: 'muted small' }, caption) : null,
    h('p', { class: 'small viz-note' }, 'An arrow A → B means B read a post of A no later than B’s exposure. An agent never exposed has no incoming arrow. Width counts such posts. Position is the code ladder’s exposed call; the node’s fill is the judge verdict. The arrows show exposure, not cause.'),
    vizLegend([
      [lgSvg(svgEl('circle', { class: 'glyph out-cheated', cx: 10, cy: 7, r: 5 })), 'judged cheated'],
      [lgSvg(svgEl('circle', { class: 'glyph out-honest', cx: 10, cy: 7, r: 5 })), 'judged not cheated'],
      [lgSvg(svgEl('circle', { class: 'glyph out-unknown', cx: 10, cy: 7, r: 5 })), 'not judged, or verdict not counted'],
      [lgSvg(svgEl('circle', { class: 'glyph disclosed-ring', cx: 10, cy: 7, r: 6 }), svgEl('circle', { class: 'glyph out-unknown', cx: 10, cy: 7, r: 3.5 })), 'outline: disclosed access'],
      hasRoles ? [lgRoles('out-unknown'), 'shape: worker, regulator, disregulator'] : null,
    ]));
}
