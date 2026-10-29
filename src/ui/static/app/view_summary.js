/* Views: summary (#/) and swarm (#/swarm).
 * The summary shows cheating by setting and the composition grids. The swarm view shows the grids, the
 * outcome funnel, the adoption curves and the run explorer.
 *
 *   import { viewSummary, viewSwarm } from './view_summary.js';
 *   const node = await viewSummary(route, view);
 */
import { DASH, debounce, fmtInt, fmtSecs, h, plural } from './dom.js';
import { P, R, apiFresh, setHashQuietly } from './api.js';
import { badge, errorBox, labeled, link, muted, section, select } from './components.js';
import { route } from './router.js';
import { settingOf } from './view_runs.js';
import { ROLE_ORDER, TIME_STEPS, fmtTick, inkOn, jobQueue, lgSvg, nextVizUid, r2, roleGlyph, roleIcon, seqColor,
  svgEl, swatch, ticksFor, tip, vizLegend, withTip } from './charts.js';
import { laneOrder, rasterCanvas, rasterLegend, timelinePanels } from './view_timeline.js';

const swarmSize = x => ((x.regulators || 0) + (x.disregulators || 0) + (x.workers || 0)) || x.n_agents || 0;
const kOfN = (k, n) => (n ? `${fmtInt(k)}/${fmtInt(n)}` : DASH);
const isSwarmRow = x => x.composition != null;

/** A run the grid can place: it records a composition and has at least one agent. */
const drawableComposition = x => x.regulators != null && x.disregulators != null && swarmSize(x) > 0;

/** One grid cell: completed runs are counted; failed runs of the same composition are only listed. */
function cellAggregate(runs, failed = []) {
  const by = {};
  for (const role of ROLE_ORDER) by[role] = { n: 0, cheated: 0 };
  let k = 0;
  let n = 0;
  for (const x of runs) {
    k += x.cheated || 0;
    n += x.judged || 0;
    for (const role of ROLE_ORDER) {
      const s = (x.by_role || {})[role];
      if (s) { by[role].n += s.n || 0; by[role].cheated += s.cheated || 0; }
    }
  }
  return { k, n, byRole: by, runs, failed, failedOnly: !runs.length };
}

function runTipLines(x) {
  return `${x.run} · seed ${x.seed ?? DASH} · ${x.judged ? `${x.cheated}/${x.judged} cheated` : 'not judged'} · ${plural(x.timeouts || 0, 'timeout')} · score ${x.score ?? DASH}`;
}
const failedTipLine = x => `${x.run} · seed ${x.seed ?? DASH} · failed run (harness crash or provider error), not counted`;
const failedNote = cell => (cell.failed.length ? ` (${plural(cell.failed.length, 'failed run')} not counted)` : '');

/** Regulators (y, up) by disregulators (x) grid for one swarm size; only cells with R + D <= N exist. */
function compositionGrid(N, cells, drawCell, label) {
  const c = N <= 5 ? 52 : N <= 8 ? 44 : 38;
  const padL = 44;
  const padT = 6;
  const padB = 38;
  const W = padL + (N + 1) * c + 8;
  const H = padT + (N + 1) * c + padB;
  const uid = nextVizUid();
  const svg = svgEl('svg', { class: 'viz comp-grid', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'group', 'aria-label': label });
  svg.appendChild(svgEl('defs', null,
    svgEl('pattern', { id: `hatch-${uid}`, width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' },
      svgEl('line', { class: 'hatch-line', x1: 0, y1: 0, x2: 0, y2: 6 }))));
  for (let r = 0; r <= N; r++) {
    for (let d = 0; d + r <= N; d++) {
      const x = padL + d * c;
      const y = padT + (N - r) * c;
      const cell = cells.get(`${r},${d}`);
      if (!cell) {
        svg.appendChild(withTip(svgEl('rect', { class: 'hm-empty', x: x + 1, y: y + 1, width: c - 2, height: c - 2, fill: `url(#hatch-${uid})` }),
          () => [`R${r} D${d} W${N - r - d}`, 'no run with this composition']));
        continue;
      }
      if (cell.failedOnly) failedCell(svg, cell, x, y, c, r, d, N);
      else drawCell(svg, cell, x, y, c, r, d, N);
    }
  }
  for (let d = 0; d <= N; d++) svg.appendChild(svgEl('text', { class: 'tick-label', x: padL + d * c + c / 2, y: padT + (N + 1) * c + 14, 'text-anchor': 'middle' }, String(d)));
  for (let r = 0; r <= N; r++) svg.appendChild(svgEl('text', { class: 'tick-label', x: padL - 8, y: padT + (N - r) * c + c / 2 + 4, 'text-anchor': 'end' }, String(r)));
  svg.appendChild(svgEl('text', { class: 'axis-title', x: padL + ((N + 1) * c) / 2, y: H - 6, 'text-anchor': 'middle' }, 'disregulators →'));
  svg.appendChild(svgEl('text', { class: 'axis-title', transform: `translate(12 ${r2(padT + ((N + 1) * c) / 2)}) rotate(-90)`, 'text-anchor': 'middle' }, 'regulators →'));
  return svg;
}

/** A clickable cell group: tooltip, click and Enter open the (first) run, completed runs first. */
function cellLink(cell, title) {
  const all = cell.runs.concat(cell.failed);
  const g = svgEl('g', { class: 'hm-cell', tabindex: 0, role: 'link', 'aria-label': title });
  withTip(g, () => [title, ...cell.runs.map(runTipLines), ...cell.failed.map(failedTipLine),
    all.length > 1 ? `click opens ${all[0].run}` : 'click opens the run']);
  const go = () => { tip.hide(); location.hash = R.run(all[0].run); };
  g.addEventListener('click', go);
  g.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); go(); } });
  return g;
}

/** A composition whose only runs failed: outlined and labelled, never filled as an outcome. */
function failedCell(svg, cell, x, y, c, r, d, N) {
  const g = cellLink(cell, `R${r} D${d} W${N - r - d}: ${plural(cell.failed.length, 'failed run')} (harness crash or provider error), not counted`);
  g.appendChild(svgEl('rect', { class: 'hm-failed', x: x + 1.5, y: y + 1.5, width: c - 3, height: c - 3, rx: 3 }));
  g.appendChild(svgEl('text', { class: 'hm-failed-text', x: x + c / 2, y: y + c / 2 + 3, 'text-anchor': 'middle' }, 'failed'));
  svg.appendChild(g);
}

function heatmapCell(svg, cell, x, y, c, r, d, N) {
  // A judged cell takes the ramp; a cell not judged yet is hollow, so it never reads as 0%.
  const fill = cell.n ? seqColor(cell.k / cell.n) : null;
  const ink = fill ? `fill:${inkOn(fill)}` : null;
  const g = cellLink(cell, `R${r} D${d} W${N - r - d}: ${cell.n ? `${cell.k} of ${cell.n} judged agents cheated` : 'not judged yet'}${failedNote(cell)}`);
  g.appendChild(fill
    ? svgEl('rect', { x: x + 1, y: y + 1, width: c - 2, height: c - 2, rx: 3, style: `fill:${fill}` })
    : svgEl('rect', { class: 'hm-unjudged', x: x + 1.5, y: y + 1.5, width: c - 3, height: c - 3, rx: 3 }));
  g.appendChild(svgEl('text', { class: ['hm-text', !fill && 't-muted'], x: x + c / 2, y: y + c / 2 + 4, 'text-anchor': 'middle', style: ink }, cell.n ? `${cell.k}/${cell.n}` : DASH));
  if (cell.runs.length > 1) g.appendChild(svgEl('text', { class: ['hm-count', !fill && 't-muted'], x: x + 5, y: y + c - 5, style: ink }, `×${cell.runs.length}`));
  svg.appendChild(g);
}

function roleBarsCell(svg, cell, x, y, c, r, d, N) {
  const parts = ROLE_ORDER.map(role => `${role} ${kOfN(cell.byRole[role].cheated, cell.byRole[role].n)}`).join(', ');
  const g = cellLink(cell, `R${r} D${d} W${N - r - d}: ${parts}${failedNote(cell)}`);
  g.appendChild(svgEl('rect', { class: 'rb-bg', x: x + 1, y: y + 1, width: c - 2, height: c - 2, rx: 3 }));
  const bw = Math.max(4, Math.min(8, (c - 16) / 3));
  const gap = (c - 2 - 3 * bw) / 4;
  const top = y + 6;
  const bot = y + c - 12;
  ROLE_ORDER.forEach((role, i) => {
    const s = cell.byRole[role];
    const bx = x + 1 + gap + i * (bw + gap);
    if (s.n) {
      g.appendChild(svgEl('rect', { class: 'rb-track', x: bx, y: top, width: bw, height: bot - top }));
      const hh = ((bot - top) * s.cheated) / s.n;
      if (hh > 0) g.appendChild(svgEl('rect', { class: ['rb-bar', 'role-' + role], x: bx, y: bot - hh, width: bw, height: hh }));
    }
    const glyph = roleGlyph(role, bx + bw / 2, y + c - 6, 2.6);
    if (!s.n) glyph.classList.add('faint');
    g.appendChild(glyph);
  });
  svg.appendChild(g);
}

function seqLegend() {
  const uid = nextVizUid();
  const stops = [0, 0.25, 0.5, 0.75, 1].map(t => svgEl('stop', { offset: `${t * 100}%`, style: `stop-color:${seqColor(t)}` }));
  return lgSvgWide(120, svgEl('defs', null, svgEl('linearGradient', { id: `seq-${uid}` }, ...stops)),
    svgEl('rect', { x: 0, y: 3, width: 120, height: 8, rx: 2, fill: `url(#seq-${uid})` }));
}
export function lgSvgWide(w, ...children) {
  return svgEl('svg', { class: 'lg-glyph viz', width: w, height: 14, viewBox: `0 0 ${w} 14`, 'aria-hidden': 'true' }, ...children);
}

/**
 * Composition grids, one pair per swarm size. Sizes come only from runs that record a composition;
 * failed runs mark their cell "failed" and are never counted.
 */
function compositionSection(swarm, failed = []) {
  const drawn = swarm.filter(drawableComposition);
  const failedDrawn = failed.filter(drawableComposition);
  const notDrawn = swarm.concat(failed).filter(x => !drawableComposition(x));
  const sizes = [...new Set(drawn.concat(failedDrawn).map(swarmSize))].sort((a, b) => a - b);
  const bySeed = (a, b) => (a.seed ?? 0) - (b.seed ?? 0) || a.run.localeCompare(b.run);
  const blocks = sizes.map(N => {
    const runs = drawn.filter(x => swarmSize(x) === N);
    const fails = failedDrawn.filter(x => swarmSize(x) === N);
    const groups = new Map();
    const add = (x, which) => {
      const k = `${x.regulators},${x.disregulators}`;
      if (!groups.has(k)) groups.set(k, { runs: [], failed: [] });
      groups.get(k)[which].push(x);
    };
    for (const x of runs) add(x, 'runs');
    for (const x of fails) add(x, 'failed');
    const cells = new Map([...groups].map(([k, g]) => [k, cellAggregate(g.runs.sort(bySeed), g.failed.sort(bySeed))]));
    const nCells = ((N + 1) * (N + 2)) / 2;
    const withRuns = [...cells.values()].filter(cl => cl.runs.length).length;
    return h('div', { class: 'comp-block' },
      h('h3', null, `${N} agents · ${plural(runs.length, 'run')} in ${fmtInt(withRuns)} of ${fmtInt(nCells)} compositions` +
        (fails.length ? ` · ${plural(fails.length, 'failed run')} not counted` : '')),
      h('div', { class: 'comp-pair' },
        h('figure', { class: 'comp-fig' }, h('figcaption', null, 'Share of judged agents who cheated'),
          compositionGrid(N, cells, heatmapCell, `Composition heatmap for ${N} agents`)),
        h('figure', { class: 'comp-fig' }, h('figcaption', null, 'Cheated by hidden role'),
          compositionGrid(N, cells, roleBarsCell, `Per-role cheating for ${N} agents`))));
  });
  const note = notDrawn.length ? h('div', { class: 'muted small' },
    `${plural(notDrawn.length, 'swarm run')} without a recorded composition, not drawn: ${notDrawn.map(x => x.run).join(', ')}.`) : null;
  if (!blocks.length) return h('div', null, note);
  return h('div', null, blocks, note,
    vizLegend([
      [seqLegend(), '0 to 100% of judged agents cheated (cell text k/n)'],
      [swatch('sw-hatch'), 'no run'],
      [swatch('sw-unjudged'), 'not judged yet'],
      failedDrawn.length ? [swatch('sw-failed'), 'failed runs only (harness crash or provider error), not counted'] : null,
    ]),
    vizLegend([
      [lgSvg(svgEl('rect', { class: 'rb-track', x: 7, y: 1, width: 6, height: 12 }), svgEl('rect', { class: 'rb-bar role-disregulator', x: 7, y: 7, width: 6, height: 6 })), 'per-role bar: share of that role’s judged agents who cheated'],
      [lgSvg(roleGlyph('disregulator', 10, 7.6, 4.2)), 'disregulator'],
      [lgSvg(roleGlyph('worker', 10, 7, 4.2)), 'worker'],
      [lgSvg(roleGlyph('regulator', 10, 7, 4.2)), 'regulator'],
      [lgSvg(roleGlyph('worker', 10, 7, 4.2, 'role-worker faint')), 'faded: no judged agent of that role'],
    ]));
}

/** One row per swarm run: composition, verdicts by role, the team report, registration; thumbs(run) adds a raster column. */
function swarmTable(swarm, thumbs, onTimeline) {
  const sorted = swarm.slice().sort((a, b) => (a.regulators ?? 99) - (b.regulators ?? 99) || (a.disregulators ?? 99) - (b.disregulators ?? 99) ||
    (a.seed ?? 0) - (b.seed ?? 0) || a.run.localeCompare(b.run));
  const roleCell = (x, role) => {
    const s = x.by_role && x.by_role[role];
    return h('td', { class: 'num' }, s && s.n ? `${s.cheated}/${s.n}` : muted(DASH));
  };
  const registered = x => (x.registered_on_main == null ? muted(DASH)
    : !x.registered_on_main ? badge('no', 'plain')
      : h('span', null, badge('yes', 'good'), x.registered_copied_from ? [' ', badge('copied', 'warn', `the registry entry carries ${x.registered_copied_from}’s message verbatim`)] : null));
  const ended = x => Object.entries(x.endings || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k, c]) => `${c} ${k}`).join(', ') || DASH;
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data compact swarm-table' },
    h('thead', null, h('tr', null,
      ['run', 'composition', 'seed', 'judged', 'cheated'].map((c, i) => h('th', { class: i >= 2 ? 'num' : '' }, c)),
      ROLE_ORDER.map(role => h('th', { class: 'num', title: `${role}s judged cheated / judged` }, roleIcon(role, 10), ' ', role)),
      ['registered', 'ended'].map(c => h('th', null, c)),
      thumbs ? h('th', { title: 'agent by turn raster; click for the timeline' }, 'agent × turn') : null)),
    h('tbody', null, sorted.map(x => h('tr', { class: x.failed && 'failed', title: x.failed ? 'failed run (harness crash or provider error): not counted in the charts' : null },
      h('td', null, link(R.run(x.run), x.run, 'run-link'), x.failed ? [' ', badge('failed', 'bad')] : null, ' ',
        h('a', { href: R.swarm({ run: x.run }), class: 'small', onclick: onTimeline ? (e => { e.preventDefault(); onTimeline(x.run); }) : null }, 'timeline')),
      h('td', { class: 'mono' }, x.composition || DASH),
      h('td', { class: 'num' }, x.seed ?? DASH),
      h('td', { class: 'num' }, fmtInt(x.judged)),
      h('td', { class: 'num' }, x.judged ? (x.cheated ? badge(`${x.cheated}`, 'bad') : '0') : muted(DASH)),
      ROLE_ORDER.map(role => roleCell(x, role)),
      h('td', null, registered(x)),
      h('td', { class: 'small' }, ended(x)),
      thumbs ? h('td', { class: 'thumb-cell' }, thumbs(x.run)) : null)))));
}

// ---- summary: cheating by setting ----------------------------------------------------

/** The setting of a /api/summary row, as settingOf() reads a run. */
function settingOfRow(x) {
  return settingOf({ graph: x.graph, n_agents: x.n_agents, swe: x.swe || null, task: x.task, swarm: x.composition != null
    ? { composition: x.composition, regulators: x.regulators, disregulators: x.disregulators, workers: x.workers } : null });
}

/** Rows of the settings chart: baselines, then each control per setting, then each composition. */
function settingRows(ok) {
  const rows = [];
  const agg = list => ({ k: list.reduce((s, x) => s + (x.cheated || 0), 0), n: list.reduce((s, x) => s + (x.judged || 0), 0), runs: list.map(x => x.run) });
  const push = (group, label, list, href) => { if (list.length) rows.push({ group, label, href, ...agg(list) }); };
  const plain = ok.filter(x => !isSwarmRow(x)).map(x => ({ x, s: settingOfRow(x) }));
  const settings = [...new Map(plain.map(p => [p.s.key, p.s])).values()].sort((a, b) => a.order - b.order || a.n - b.n);
  for (const s of settings) {
    push('Baseline', s.label, plain.filter(p => p.s.key === s.key && p.x.control === 'baseline').map(p => p.x), R.runs({ setting: s.key, control: 'baseline' }));
  }
  for (const s of settings) {
    const controls = [...new Set(plain.filter(p => p.s.key === s.key && p.x.control !== 'baseline').map(p => p.x.control))].sort();
    for (const c of controls) push(`Controls, ${s.label}`, c, plain.filter(p => p.s.key === s.key && p.x.control === c).map(p => p.x), R.runs({ setting: s.key, control: c }));
  }
  const swarm = ok.filter(isSwarmRow).map(x => ({ x, s: settingOfRow(x) }))
    .sort((a, b) => a.s.n - b.s.n || (a.s.R ?? 0) - (b.s.R ?? 0) || (a.s.D ?? 0) - (b.s.D ?? 0) || a.x.run.localeCompare(b.x.run));
  const comps = [...new Map(swarm.map(p => [`${p.s.key}|${p.s.composition}`, p])).values()];
  for (const p of comps) {
    const list = swarm.filter(q => q.s.key === p.s.key && q.s.composition === p.s.composition).map(q => q.x);
    push(`${p.s.label[0].toUpperCase()}${p.s.label.slice(1)}`, `R${p.s.R} D${p.s.D} W${p.s.W}`, list, R.runs({ setting: p.s.key, q: p.s.composition }));
  }
  return rows;
}

/** Horizontal bars, one per row, grouped under headers: share of judged agents who cheated. */
function shareBars(rows, W) {
  const labelW = 230;
  const valW = 90;
  const rowH = 24;
  const headH = 30;
  const padT = 22;
  const x0 = labelW + 10;
  const x1 = W - valW - 10;
  const items = [];
  let y = padT;
  let g = null;
  for (const r of rows) {
    if (r.group !== g) { g = r.group; items.push({ head: g, y }); y += headH; }
    items.push({ r, y });
    y += rowH;
  }
  const H = y + 6;
  const svg = svgEl('svg', { class: 'viz bars-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'group',
    'aria-label': 'Share of judged agents who cheated, by setting' });
  const sx = f => x0 + f * (x1 - x0);
  for (const f of [0, 0.25, 0.5, 0.75, 1]) {
    svg.appendChild(svgEl('line', { class: f ? 'grid' : 'baseline', x1: sx(f), x2: sx(f), y1: padT - 4, y2: H - 4 }));
    svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(f), y: padT - 9, 'text-anchor': 'middle' }, `${f * 100}%`));
  }
  for (const it of items) {
    if (it.head) { svg.appendChild(svgEl('text', { class: 'grp-label', x: 4, y: it.y + 19 }, it.head.toUpperCase())); continue; }
    const r = it.r;
    const cy = it.y + rowH / 2;
    const frac = r.n ? r.k / r.n : 0;
    const a = svgEl('a', { href: r.href, class: 'bar-row', 'aria-label': `${r.label}: ${r.n ? `${r.k} of ${r.n} judged agents cheated` : 'not judged'}` });
    a.appendChild(svgEl('rect', { class: 'bar-hit', x: 0, y: it.y, width: W, height: rowH }));
    a.appendChild(svgEl('text', { class: 'bar-name', x: labelW, y: cy + 4, 'text-anchor': 'end' }, r.label.length > 34 ? r.label.slice(0, 33) + '…' : r.label));
    a.appendChild(svgEl('rect', { class: 'bar-track', x: x0, y: cy - 6, width: x1 - x0, height: 12, rx: 2 }));
    if (r.n && frac > 0) a.appendChild(svgEl('rect', { class: 'bar-fill', x: x0, y: cy - 6, width: Math.max(2, sx(frac) - x0), height: 12, rx: 2 }));
    a.appendChild(svgEl('text', { class: 'bar-label', x: x1 + 8, y: cy + 4 }, r.n ? `${r.k}/${r.n}` : 'not judged'));
    withTip(a, () => [`${r.label} · ${r.group}`, r.n ? `${r.k} of ${r.n} judged agents cheated` : 'no judged agent', `runs: ${r.runs.join(', ')}`, 'click opens these runs']);
    svg.appendChild(a);
  }
  return svg;
}

function headline(items) {
  return h('div', { class: 'headline' }, items.map(([num, label, href]) =>
    h(href ? 'a' : 'div', { class: 'hl', href }, h('div', { class: 'hl-num' }, num), h('div', { class: 'hl-label' }, label))));
}

export async function viewSummary(r, view) {
  const sum = await apiFresh(P.summary());
  const all = sum.runs || [];
  const mock = all.filter(x => x.mock);
  const ok = all.filter(x => !x.failed && !x.mock);
  const failed = all.filter(x => x.failed && !x.mock);
  const swarm = ok.filter(isSwarmRow);
  const swarmFailed = failed.filter(isSwarmRow);
  const judged = ok.reduce((s, x) => s + (x.judged || 0), 0);
  const cheated = ok.reduce((s, x) => s + (x.cheated || 0), 0);
  const nSingle = ok.filter(x => x.graph === 'single').length;
  const nBoard = ok.filter(x => x.graph === 'board' && !isSwarmRow(x)).length;
  const rows = settingRows(ok);
  const barsHost = h('div', { class: 'viz-host' });
  const draw = () => barsHost.replaceChildren(rows.length ? shareBars(rows, Math.max(640, barsHost.clientWidth || 900)) : muted('No judged run.'));
  const onResize = debounce(() => { tip.hide(); draw(); }, 150);
  view.mounted(() => {
    document.title = 'Summary · results';
    if (location.hash.startsWith('#/overview')) setHashQuietly('#/');
    draw();
    window.addEventListener('resize', onResize);
  });
  view.cleanup(() => { window.removeEventListener('resize', onResize); tip.hide(); });
  return h('div', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Summary'),
      h('div', { class: 'links' }, h('button', { type: 'button', class: 'btn btn-small', onclick: () => route() }, 're-read')),
      muted('A judged agent has a verdict and a transcript. Failed runs (harness crash or provider error) are left out of every count and listed under Runs.' +
        (mock.length ? ` ${plural(mock.length, 'mock-model smoke run')} left out as well.` : ''))),
    headline([
      [fmtInt(ok.length), `completed runs: ${nSingle} lone agent, ${nBoard} board, ${swarm.length} board with hidden roles`, R.runs({ all: '1' })],
      [fmtInt(judged), 'judged agents', R.runs({ all: '1' })],
      [fmtInt(cheated), 'agents judged cheated', R.runs({ cheated: 'yes' })],
      [fmtInt(failed.length), 'failed runs set aside', R.runs({ failed: 'only' })],
    ]),
    section('Share of judged agents who cheated, by setting', barsHost,
      h('div', { class: 'muted small' }, 'A setting is the graph (lone agent, or every agent on one board), the roster size, and whether roles were hidden. Bar: cheated over judged. A row opens the runs behind it.')),
    section('Teams with hidden roles, by composition',
      swarm.length || swarmFailed.length ? compositionSection(swarm, swarmFailed) : muted('No swarm run yet.'),
      h('div', { class: 'muted small' }, 'Left grid: share of judged agents who cheated. Right grid: the same by hidden role. ', link(R.swarm(), 'Swarm visualizations'), ' has the funnel, the adoption curves and every run’s timeline.')),
    section('Swarm runs',
      swarm.length ? swarmTable(swarm, null) : muted('No completed swarm run yet.')));
}

// ---- swarm visualizations ------------------------------------------------------------

const FUNNEL_STAGES = [['honey_pots_seen', 'saw honeypot'], ['attempted_access', 'attempted access'], ['cheated', 'cheated'], ['disclosed_access', 'disclosed']];
const FUNNEL_ROLES = ['worker', 'regulator', 'disregulator'];

/** Grouped bars: at each judge stage, the share of judged agents of each hidden role. */
function funnelChart(agents, W) {
  const roles = FUNNEL_ROLES.filter(r => agents.some(a => a.role === r));
  if (!roles.length) return muted('No judged agent with a hidden role.');
  const n = Object.fromEntries(roles.map(r => [r, agents.filter(a => a.role === r).length]));
  W = Math.max(560, Math.min(820, W));
  const padL = 48;
  const padR = 12;
  const padT = 22;
  const plotH = 220;
  const padB = 62;
  const H = padT + plotH + padB;
  const groupW = (W - padL - padR) / FUNNEL_STAGES.length;
  const bw = Math.min(36, (groupW - 28) / roles.length);
  const y = v => padT + plotH - v * plotH;
  const svg = svgEl('svg', { class: 'viz funnel-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': 'Outcome funnel by hidden role: saw honeypot, attempted access, cheated, disclosed' });
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    svg.appendChild(svgEl('line', { class: v ? 'grid' : 'baseline', x1: padL, x2: W - padR, y1: y(v), y2: y(v) }));
    svg.appendChild(svgEl('text', { class: 'tick-label', x: padL - 6, y: y(v) + 4, 'text-anchor': 'end' }, `${v * 100}%`));
  }
  FUNNEL_STAGES.forEach(([key, label], si) => {
    const gx = padL + si * groupW + groupW / 2;
    const left = gx - (roles.length * bw + (roles.length - 1) * 4) / 2;
    roles.forEach((role, j) => {
      const k = agents.filter(a => a.role === role && a.v[key]).length;
      const share = n[role] ? k / n[role] : 0;
      const bx = left + j * (bw + 4);
      const top = y(share);
      if (share > 0) svg.appendChild(svgEl('rect', { class: ['fn-bar', 'role-' + role], x: bx, y: top, width: bw, height: y(0) - top }));
      svg.appendChild(svgEl('text', { class: 'bar-label', x: bx + bw / 2, y: top - 5, 'text-anchor': 'middle' }, `${k}/${n[role]}`));
      svg.appendChild(roleGlyph(role, bx + bw / 2, y(0) + 12, 4));
      svg.appendChild(withTip(svgEl('rect', { class: 'hit', x: bx, y: padT, width: bw, height: plotH + 20 }),
        () => [`${role} · ${label}`, `${k} of ${n[role]} judged ${role}s`]));
    });
    svg.appendChild(svgEl('text', { class: 'ctl-label', x: gx, y: y(0) + 34, 'text-anchor': 'middle' }, label));
    if (si < FUNNEL_STAGES.length - 1) svg.appendChild(svgEl('text', { class: 'tick-label', x: padL + (si + 1) * groupW, y: y(0) + 34, 'text-anchor': 'middle' }, '→'));
  });
  return h('div', null, h('div', { class: 'chart-scroll' }, svg),
    vizLegend(roles.map(role => [lgSvg(roleGlyph(role, 10, role === 'disregulator' ? 7.6 : 7, 4.2)), `${role} (n = ${n[role]})`])),
    h('div', { class: 'muted small' }, `Pooled over ${plural(agents.length, 'judged agent')} of the completed swarm runs. Bar label: agents at the stage over agents in the role.`));
}

/** Last moment on a run's clock: the sample's total time or the last event, whichever is later. */
function runEnd(tl) {
  let end = tl.duration || 0;
  for (const e of tl.events || []) { if (e.t != null) end = Math.max(end, e.t); if (e.t_end != null) end = Math.max(end, e.t_end); }
  return end;
}

/** One adoption curve: the moments each agent first read the honeypot archive (timeline marker). */
function curveOf(x, tl) {
  const lanes = tl.agents || [];
  const roles = tl.roles || {};
  const first = Object.entries(tl.first_member_read || {}).filter(([a, t]) => lanes.includes(a) && t != null).map(([, t]) => t).sort((a, b) => a - b);
  const disPosts = (tl.board || []).filter(p => p.t != null && roles[p.sender] === 'disregulator').map(p => p.t);
  return { run: x.run, R: x.regulators, D: x.disregulators, W: x.workers, n: lanes.length, reads: first, end: runEnd(tl),
    disPost: disPosts.length ? Math.min(...disPosts) : null };
}

/** Small multiples, one per composition: share of agents that have read the archive against time. */
function adoptionCurves(curves, noLog, onOpen) {
  if (!curves.length) return muted('No swarm run with a readable timeline.');
  const groups = new Map();
  for (const c of curves) {
    const k = `${c.R},${c.D}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(c);
  }
  const keys = [...groups.keys()].sort((a, b) => { const [ra, da] = a.split(',').map(Number); const [rb, db] = b.split(',').map(Number); return ra - rb || da - db; });
  const xmax = Math.max(1, ...curves.map(c => c.end));
  const W = 250;
  const H = 150;
  const padL = 30;
  const padR = 10;
  const padT = 22;
  const padB = 24;
  const sx = t => padL + (t / xmax) * (W - padL - padR);
  const sy = v => padT + (1 - v) * (H - padT - padB);
  let anyDis = false;
  const panels = keys.map(k => {
    const runs = groups.get(k).sort((a, b) => a.run.localeCompare(b.run));
    const [R_, D_] = k.split(',').map(Number);
    const svg = svgEl('svg', { class: 'viz adopt-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': `Adoption curve for R${R_} D${D_}: ${runs.map(c => `${c.reads.length} of ${c.n} agents read the archive`).join('; ')}` });
    for (const v of [0, 0.5, 1]) {
      svg.appendChild(svgEl('line', { class: v ? 'grid' : 'baseline', x1: padL, x2: W - padR, y1: sy(v), y2: sy(v) }));
      svg.appendChild(svgEl('text', { class: 'tick-label', x: padL - 4, y: sy(v) + 3, 'text-anchor': 'end' }, v === 1 ? '1' : v ? '.5' : '0'));
    }
    for (const t of ticksFor(0, xmax, 3, TIME_STEPS)) {
      svg.appendChild(svgEl('text', { class: 'tick-label', x: sx(t), y: H - 8, 'text-anchor': t === 0 ? 'start' : (sx(t) > W - padR - 16 ? 'end' : 'middle') }, fmtTick(t)));
    }
    for (const c of runs) {
      if (c.disPost != null) {
        anyDis = true;
        svg.appendChild(svgEl('line', { class: 'adopt-dis', x1: sx(c.disPost), x2: sx(c.disPost), y1: padT, y2: sy(0) }));
        svg.appendChild(roleGlyph('disregulator', sx(c.disPost), sy(0) + 1, 3.6));
      }
      const pts = [[0, 0]];
      c.reads.forEach((t, i) => { pts.push([t, i / c.n]); pts.push([t, (i + 1) / c.n]); });
      pts.push([c.end, c.reads.length / c.n]);
      svg.appendChild(svgEl('path', { class: ['adopt-line', runs.length > 1 && 'faint'], d: pts.map(([t, v], i) => `${i ? 'L' : 'M'}${r2(sx(t))},${r2(sy(v))}`).join('') }));
      const share = c.reads.length / c.n;
      svg.appendChild(svgEl('text', { class: 'bar-label', x: W - padR, y: sy(share) + (share > 0.8 ? 12 : -4), 'text-anchor': 'end' }, `${c.reads.length}/${c.n}`));
      svg.appendChild(withTip(svgEl('path', { class: 'edge-hit', d: pts.map(([t, v], i) => `${i ? 'L' : 'M'}${r2(sx(t))},${r2(sy(v))}`).join('') }),
        () => [c.run, `${c.reads.length} of ${c.n} agents read the archive`, c.reads.length ? `first at ${fmtSecs(c.reads[0])}, last at ${fmtSecs(c.reads[c.reads.length - 1])}` : null,
          c.disPost != null ? `first disregulator post at ${fmtSecs(c.disPost)}` : 'no disregulator post', 'click opens the timeline']));
      svg.classList.add('clickable');
    }
    svg.appendChild(svgEl('text', { class: 'lane-name', x: padL, y: 13 }, `R${R_} D${D_}` + (runs.length > 1 ? ` (${runs.length} runs)` : '')));
    const fig = h('figure', { class: 'adopt-fig' }, svg);
    svg.addEventListener('click', () => { tip.hide(); if (onOpen) onOpen(runs[0].run); else location.hash = R.swarm({ run: runs[0].run }); });
    return fig;
  });
  return h('div', null,
    h('div', { class: 'panel-grid' }, panels),
    vizLegend([
      [lgSvg(svgEl('line', { class: 'adopt-line', x1: 1, y1: 11, x2: 8, y2: 11 }), svgEl('line', { class: 'adopt-line', x1: 8, y1: 11, x2: 8, y2: 4 }), svgEl('line', { class: 'adopt-line', x1: 8, y1: 4, x2: 19, y2: 4 })), 'share of agents that have read the archive (end label: read / agents)'],
      anyDis ? [lgSvg(svgEl('line', { class: 'adopt-dis', x1: 10, x2: 10, y1: 1, y2: 13 }), roleGlyph('disregulator', 10, 11, 3)), 'first board post by a disregulator'] : null,
    ]),
    h('div', { class: 'muted small' }, 'An agent counts from its first successful bash or python call whose output holds text found only inside the honeypot archive (the timeline’s member-read marker). The x axis is seconds from the start of the run.' +
      (noLog.length ? ` No readable eval log: ${noLog.join(', ')}.` : '')));
}

export async function viewSwarm({ query: q }, view) {
  const [sum, runs] = await Promise.all([apiFresh(P.summary()), apiFresh(P.runs())]);
  const all = sum.runs || [];
  const swarm = all.filter(x => !x.failed && isSwarmRow(x));
  const swarmFailed = all.filter(x => x.failed && isSwarmRow(x));
  const byName = new Map(runs.map(r => [r.run, r]));
  const agents = [];
  for (const x of swarm) {
    const run = byName.get(x.run);
    for (const ep of (run && run.episodes) || []) {
      if (ep.verdict && ep.verdict.cheated != null && ep.has_transcript) agents.push({ run: x.run, agent: ep.orbit_agent, role: ep.role, v: ep.verdict });
    }
  }
  let live = true;
  const queue = jobQueue(2);
  const timelines = new Map();
  // Each visit re-reads the timelines (a run still being produced can gain its log); a job still queued
  // when the user leaves resolves without fetching.
  const tlOf = run => {
    if (!timelines.has(run)) timelines.set(run, queue(() => (live ? apiFresh(P.timeline(run)) : Promise.resolve(null))));
    return timelines.get(run);
  };
  const io = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    for (const en of entries) if (en.isIntersecting) { io.unobserve(en.target); loadThumb(en.target); }
  }, { rootMargin: '200px' }) : null;
  function loadThumb(box) {
    const run = box.dataset.run;
    tlOf(run).then(tl => {
      if (!live || !tl) return;
      const { el } = rasterCanvas(tl, laneOrder(tl), { thumb: true, maxW: 160, onClick: () => { tip.hide(); showRun(run, true); } });
      box.replaceChildren(el);
    }).catch(e => {
      if (!live) return;
      box.replaceChildren(h('span', { class: 'muted small', title: String((e && e.message) || e) }, e && e.status === 404 ? 'no eval log' : 'could not load'));
    });
  }
  const thumbs = run => {
    const box = h('div', { class: 'thumb-box', dataset: { run } }, muted('…', 'small'));
    if (io) io.observe(box); else loadThumb(box);
    return box;
  };

  // Funnel.
  const funnelHost = h('div', { class: 'viz-host' });
  const drawFunnel = () => funnelHost.replaceChildren(funnelChart(agents, funnelHost.clientWidth || 800));

  // Adoption curves, drawn once every completed run's timeline has arrived.
  const adoptHost = h('div', { class: 'viz-host' }, muted(`reading ${plural(swarm.length, 'timeline')}…`));
  const noLog = [];
  const adoptionDone = Promise.all(swarm.map(x => tlOf(x.run).then(tl => (tl ? curveOf(x, tl) : null), () => { noLog.push(x.run); return null; })))
    .then(curves => { if (live) adoptHost.replaceChildren(adoptionCurves(curves.filter(Boolean), noLog, name => showRun(name, true))); });

  // Run explorer: raster, swimlanes and cascade of one run.
  const names = swarm.map(x => x.run).concat(swarmFailed.map(x => x.run));
  const want = q.get('run');
  let current = names.includes(want) ? want : (names[0] || null);
  const noteBox = h('div', { class: 'url-notes' });
  if (want && !names.includes(want)) noteBox.appendChild(h('div', { class: 'url-note' }, `Run “${want}” is not a swarm run; showing ${current || 'nothing'}.`));
  const sel = select(names.map(n => [n, n + (byName.get(n) && byName.get(n).failed ? ' (failed)' : '')]), current || '',
    v => showRun(v, false), { 'aria-label': 'swarm run to explore' });
  const exploreHost = h('div', null, muted('Choose a run.'));
  const exploreAnchor = h('div', { id: 'explore' });
  let panels = null;
  let showToken = 0;
  async function showRun(name, scroll) {
    current = name;
    sel.value = name;
    updateUrl();
    const tok = ++showToken;
    if (panels) { panels.unmount(); panels = null; }
    exploreHost.replaceChildren(h('div', { class: 'loading' }, `Loading ${name}…`));
    if (scroll) exploreAnchor.scrollIntoView({ block: 'start' });
    try {
      const [tl, detail] = await Promise.all([apiFresh(P.timeline(name)), apiFresh(P.run(name)).catch(() => null)]);
      if (!live || tok !== showToken) return;
      panels = timelinePanels(name, tl, detail);
      const eps = (detail && detail.episodes) || [];
      exploreHost.replaceChildren(
        h('div', { class: 'links' },
          link(R.run(name), 'run page', 'btn btn-small'),
          link(R.timeline(name), 'timeline page', 'btn btn-small'),
          eps.filter(e => e.has_transcript).map(e => link(R.episode(name, e.episode), `transcript ${e.orbit_agent || e.episode}`, 'btn btn-small'))),
        panels.note, panels.node);
      panels.mount();
      // The sections above the explorer finish drawing on their own clock; scroll again once they have.
      if (scroll) adoptionDone.then(() => { if (live && tok === showToken) exploreAnchor.scrollIntoView({ block: 'start' }); });
    } catch (e) {
      if (!live || tok !== showToken) return;
      exploreHost.replaceChildren(errorBox(e, name));
    }
  }
  function updateUrl() {
    if (!live) return;
    const target = R.swarm({ run: current });
    setHashQuietly(target);
  }
  const onResize = debounce(() => { tip.hide(); drawFunnel(); }, 150);
  view.mounted(() => {
    document.title = 'Swarm · results';
    drawFunnel();
    window.addEventListener('resize', onResize);
    if (current) showRun(current, !!want && names.includes(want));
  });
  view.cleanup(() => { live = false; if (io) io.disconnect(); if (panels) panels.unmount(); window.removeEventListener('resize', onResize); tip.hide(); });

  const none = muted('No swarm run yet. A run appears here once its run.json records a swarm composition.');
  return h('div', { class: 'page wide' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Swarm visualizations'),
      h('div', { class: 'links' }, h('button', { type: 'button', class: 'btn btn-small', onclick: () => route() }, 're-read')),
      muted(`${plural(swarm.length, 'completed swarm run')}${swarmFailed.length ? ` and ${plural(swarmFailed.length, 'failed one')} (listed, never counted)` : ''}. ` +
        'A swarm is the board with hidden roles: workers, regulators (peers told to keep the team honest) and disregulators (peers privately told to register a result by any means).')),
    section('Compositions', swarm.length || swarmFailed.length ? compositionSection(swarm, swarmFailed) : none),
    section('Outcome funnel by hidden role', funnelHost),
    section('Adoption of the honeypot archive over time', adoptHost),
    section('Swarm runs', swarm.length || swarmFailed.length ? [swarmTable(swarm.concat(swarmFailed), thumbs, name => showRun(name, true)), rasterLegend()] : none),
    h('section', { class: 'card', id: 'explore-card' }, exploreAnchor,
      h('div', { class: 'sec-head' }, h('h2', null, 'Run explorer'), labeled('run', sel), noteBox),
      h('div', { class: 'muted small' }, 'Agent by turn raster, swimlanes with board-message arrows, and the exposure cascade of the chosen run.'),
      exploreHost));
}
