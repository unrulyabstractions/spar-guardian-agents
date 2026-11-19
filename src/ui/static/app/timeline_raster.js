/* The agent by turn raster of one run's timeline: one cell per model call of each agent, coloured by the call's main
 * action. The timeline page draws it with agent labels (rasterPanel); the summary draws thumbnails (rasterCanvas).
 *
 *   import { rasterCanvas, rasterLegend, rasterPanel } from './timeline_raster.js';
 */
import { firstLine, fmtSecs, h, plural } from './dom.js';
import { cssVar, roleIcon, swatch, tip, vizLegend } from './charts.js';
import { ENDING_KINDS, isToolEvent, toolKind } from './run_facts.js';

const RASTER_OF_TOOL = { board_post: 'post', board_read: 'read' };

// Agent x turn raster: one cell per model call, coloured by the call's main action.
const RASTER_KINDS = [
  ['work', 'shell, run_python or another tool', 'sw-work'],
  ['post', 'board_post', 'sw-post'],
  ['read', 'board_read or board_status', 'sw-read'],
  ['exposed', 'exposed (code’s evidence)', 'sw-exposed'],
  ['opened', 'opened (code’s evidence)', 'sw-opened'],
  ['submit', 'submit (handed in)', 'sw-submit'],
  ['stop', 'stop_working', 'sw-stop'],
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
    else if (isToolEvent(e)) r.tools.push(e);
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
      const ending = mine.find(x => ENDING_KINDS.includes(x.kind));
      if (ending) kind = ending.kind;
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
    stop: cssVar('--warn', '#85560a'),
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
export function rasterPanel(tl, agents, maxW) {
  const roles = tl.roles;
  const { el, rows, cols, cellH } = rasterCanvas(tl, agents, { maxW });
  const present = new Set(rows.flatMap(r => r.cells.map(c => c.kind)));
  return h('div', null,
    h('div', { class: 'raster-wrap' },
      h('div', { class: 'raster-labels', style: `grid-auto-rows: ${cellH}px` },
        agents.map(a => h('div', { class: 'raster-label' }, roles[a] ? roleIcon(roles[a], 10) : null, a))),
      h('div', { class: 'raster-scroll' }, el)),
    h('div', { class: 'muted small' }, `Columns are each agent’s own model calls, 1 to ${cols}; a call takes the action of its first tool call, and the code’s exposed or opened evidence among the tool calls that follow it wins.`),
    rasterLegend(present));
}
