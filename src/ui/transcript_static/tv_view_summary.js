/* View: one collection's runs by condition (#/summary?col=<collection>), from /api/summary
 * (src/ui/transcript_summary.py). A row per workers' model and task (and number of workers, only in a collection that
 * mixes team sizes); a column per condition: the baseline, one column per influencer combination with baseline workers
 * (role, instruction level and model, stacked on three lines in the header), the positive control (told to cheat), the
 * two negative controls (a rule), and a column per other combination the collection holds. A cell: its runs (a link
 * to the runs page filtered to exactly them, when the server found such filters), cheated, accessed and passed as k of
 * n with a bar each, and the teams' level counts on the three ladders as chips in the ladders' colours. A last row
 * totals each column. Labels and values only: every definition is on the Ladders page. */
import { fmtInt, h, plural } from '/static/app/dom.js';
import { badge, muted, select } from '/static/app/components.js';
import { RAMP, inkOn } from './tv_case_view.js';
import { loadLadders } from './tv_view_index.js';
import { COLLECTION_ORDER, collectionOf, modelLabel } from './tv_labels.js';

const SUMMARIES = new Map();
/** /api/summary's answer for one collection. */
async function loadSummary(col) {
  if (!SUMMARIES.has(col)) {
    const r = await fetch(`/api/summary?col=${encodeURIComponent(col)}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `summary: HTTP ${r.status}`);
    SUMMARIES.set(col, d);
  }
  return SUMMARIES.get(col);
}

/** The runs page's address that keeps exactly a cell's runs: its filters, the model as the runs page names it. */
function runsHref(filters) {
  const p = new URLSearchParams();
  for (const k of ['col', 'task', 'model', 'workers', 'worker', 'influencer', 'influencer_level', 'q']) {
    const v = k === 'model' && filters.model ? modelLabel({ model: filters.model }) : filters[k];
    if (v != null && v !== '') p.set(k, v);
  }
  return `#/?${p.toString()}`;
}

/** k of n with a thin bar, filled in `colour`. */
const share = (label, k, n, colour) => h('div', { class: 'sum-share', title: `${label}: ${k} of ${n}` },
  h('span', { class: 'sum-share-label' }, label),
  h('span', { class: ['sum-share-k', k > 0 && 'is-some'] }, `${n ? Math.round((100 * k) / n) : 0}%`),
  h('span', { class: 'sum-bar' }, h('span', { style: `width:${n ? Math.round((100 * k) / n) : 0}%;background:${colour}` })));

const SHORT = { cheating: 'H', coordination: 'C', collusion: 'K' };
/** One ladder's level counts as chips in the ladder's colours; "n/a" where the ladder does not apply. */
function ladderChips(key, lad, counts) {
  const chips = counts.map(([level, n]) => {
    const i = lad.levels.indexOf(level);
    if (i < 0) return h('span', { class: 'sum-chip sum-chip-na', title: `${lad.name}: ${level}` }, `n/a ${n}`);
    const bg = RAMP[key][i];
    return h('span', { class: 'sum-chip', style: `background:${bg};color:${inkOn(bg)}`, title: `${lad.name}: ${level}` },
      `${level} ${n}`);
  });
  return h('div', { class: 'sum-lad' }, h('span', { class: 'sum-lad-name', title: lad.name }, SHORT[key]), h('span', { class: 'sum-chips' }, chips));
}

/** One ladder's share of teams at or above its line, as a row: the ladder's letter, the rate, a bar in the colour of
 * the line's level. */
function lineShare(key, lad, counts, n) {
  const at = lad.levels.indexOf(lad.line);
  const k = counts.reduce((t, [v, c]) => t + (lad.levels.indexOf(v) >= at ? c : 0), 0);
  const pct = n ? Math.round((100 * k) / n) : 0;
  return h('div', { class: 'sum-share', title: `${lad.name}, ${lad.line} or above: ${k} of ${n}` },
    h('span', { class: `sum-lad-name sum-lad-${key}` }, SHORT[key]),
    h('span', { class: ['sum-share-k', k > 0 && 'is-some'] }, `${pct}%`),
    h('span', { class: 'sum-bar' }, h('span', { style: `width:${pct}%;background:${RAMP[key][at]}` })));
}

/** A cell, centred: each ladder's share at or above its line, or with `detailed` each ladder's level chips; then its
 * runs (linked when the server found the filters that keep exactly them). */
function cellView(c, d, detailed) {
  if (!c) return h('td', { class: 'sum-cell sum-empty' }, muted('–'));
  const n = plural(c.n, 'run');
  return h('td', { class: 'sum-cell' }, h('div', { class: 'sum-stack' },
    detailed ? d.order.map(L => ladderChips(L, d.ladders[L], c.levels[L]))
      : d.order.map(L => lineShare(L, d.ladders[L], c.levels[L], c.n)),
    c.filters ? h('a', { class: 'sum-n', href: runsHref(c.filters), title: 'Open these runs' }, n)
      : h('span', { class: 'sum-n', title: 'No runs-page filter keeps exactly these' }, n)));
}

/** A column's header: its label, or for influencers three short lines: role chips, level chips, models. */
function colLabel(c) {
  const xs = c.influencers;
  if (!xs.length) return c.label;
  const role = x => (x.n === 1 ? x.role : `${x.n} ${x.role}s`);
  const set = c.influencer === 'both' ? 'both' : role(xs[0]);
  return [
    c.group === 'other' ? h('div', { class: 'sum-col-line' }, c.worker[0].toUpperCase() + c.worker.slice(1)) : null,
    h('div', { class: 'sum-col-line' }, '+ ', c.influencer === 'both'
      ? xs.map((x, i) => [i ? ' ' : null, h('span', { class: ['role-chip', `role-chip-${x.role}`] }, role(x))])
      : h('span', { class: ['role-chip', `role-chip-${xs[0].role}`] }, set)),
    h('div', { class: 'sum-col-line' }, xs.map((x, i) => [i ? ' ' : null,
      h('span', { class: ['level-chip', `level-chip-${x.role}`] }, x.level || 'no level')])),
    h('div', { class: 'sum-col-line sum-col-model' }, xs.map(x => modelLabel({ model: x.model })).join(', ')),
  ];
}

/** A column's tooltip: its workers' conditioning and each influencer's role, level and model. */
const colTitle = c => [c.worker, ...(c.influencers.length
  ? c.influencers.map(x => `${x.role} ${x.level || 'no level'} ${modelLabel({ model: x.model })}`) : ['no influencer'])].join('; ');

/** The All row over some rows: each column's cells summed, the ladders' level counts in each ladder's order; no
 * link, since the runs page has no filter for a set of rows. */
function summed(rows, columns, d) {
  const out = {};
  for (const c of columns) {
    const cs = rows.map(r => r.cells[c.key]).filter(Boolean);
    if (!cs.length) continue;
    const add = k => cs.reduce((t, x) => t + x[k], 0);
    const levels = {};
    for (const L of d.order) {
      const by = new Map();
      for (const x of cs) for (const [v, n] of x.levels[L] || []) by.set(v, (by.get(v) || 0) + n);
      const order = d.ladders[L].levels;
      levels[L] = [...by.entries()].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
    }
    out[c.key] = { n: add('n'), cheated: add('cheated'), accessed: add('accessed'), passed: add('passed'), levels, filters: null };
  }
  return out;
}

export async function viewSummary(q) {
  const ladders = await loadLadders();
  const cols = COLLECTION_ORDER;
  const col = cols.includes(q.get('col')) ? q.get('col') : (cols.includes('study_exploration_1') ? 'study_exploration_1' : cols[0]);
  const s = await loadSummary(col);
  // The influencer bands the page hides (hide=regulator,disregulator,both), each toggled by its button.
  const BANDS = [['regulator', 'Regulators'], ['disregulator', 'Disregulators'], ['both', 'Both']]
    .filter(([g]) => s.columns.some(c => c.group === g));
  const hidden = new Set((q.get('hide') || '').split(',').filter(g => BANDS.some(([b]) => b === g)));
  // The two model filters: the workers' model keeps its rows; an influencer's model keeps the influencer columns
  // holding it (Baseline and the controls stay).
  const wmodel = q.get('wmodel') || '';
  const detailed = q.get('detail') === '1';
  const task = q.get('task') || '';
  const imodel = q.get('imodel') || '';
  const rows = s.rows.filter(r => (!wmodel || r.model === wmodel) && (!task || r.task === task));
  const C = s.columns.filter(c => !hidden.has(c.group)
    && (!imodel || !c.influencers.length || c.influencers.some(x => x.model === imodel)));
  const counted = s.rows.reduce((t, r) => t + s.columns.reduce((u, c) => u + (r.cells[c.key] ? r.cells[c.key].n : 0), 0), 0);
  const nav = (over = {}) => {
    const p = new URLSearchParams({ col, hide: [...hidden].join(','), task, wmodel, imodel, detail: detailed ? '1' : '', ...over });
    for (const [k, v] of [...p.entries()]) if (!v) p.delete(k);
    location.hash = `#/summary?${p}`;
  };
  const toggles = h('span', { class: 'rp-togs' }, h('button', {
    type: 'button', class: 'rp-tog', 'aria-pressed': String(detailed), title: 'each ladder\'s levels instead of its rate at the line',
    onclick: () => nav({ detail: detailed ? '' : '1' }) }, 'Detailed'), BANDS.map(([g, label]) => h('button', {
    type: 'button', class: 'rp-tog', 'aria-pressed': String(!hidden.has(g)), title: `show or hide the ${label.toLowerCase()} columns`,
    onclick: () => { const next = new Set(hidden); if (next.has(g)) next.delete(g); else next.add(g); nav({ hide: [...next].join(',') }); } },
    label)));
  const uniqueModels = list => [...new Set(list)].sort((a, b) => modelLabel({ model: a }).localeCompare(modelLabel({ model: b })));
  const tPick = select([['', 'All tasks'], ...[...new Set(s.rows.map(r => r.task))].sort().map(t => [t, t])],
    task, v => nav({ task: v }), { 'aria-label': 'Task' });
  const wPick = select([['', 'All workers\' models'], ...uniqueModels(s.rows.map(r => r.model)).map(m => [m, modelLabel({ model: m })])],
    wmodel, v => nav({ wmodel: v }), { 'aria-label': 'Workers\' model' });
  const iPick = select([['', 'All influencer models'], ...uniqueModels(s.columns.flatMap(c => c.influencers.map(x => x.model)).filter(Boolean))
    .map(m => [m, modelLabel({ model: m })])], imodel, v => nav({ imodel: v }), { 'aria-label': 'Influencer model' });
  const lead = s.by_workers ? 3 : 2;

  // The band over the condition columns: one cell per run of columns of the same group. A group of one column named
  // as its column is that column's header, over both header rows.
  const starts = new Set(C.filter((c, i) => i === 0 || C[i - 1].group !== c.group).map(c => c.key));
  const colHead = (c, more = {}) => h('th', { scope: 'col', class: ['sum-col', starts.has(c.key) && 'sum-group-start'],
    title: colTitle(c), ...more }, colLabel(c));
  const band = [h('th', { colspan: String(lead), class: 'sum-band-none' }, '')];
  const spanned = new Set();
  for (let i = 0; i < C.length;) {
    let j = i;
    while (j < C.length && C[j].group === C[i].group) j++;
    if (j - i === 1 && s.groups[C[i].group] === C[i].label) {
      band.push(colHead(C[i], { rowspan: '2' }));
      spanned.add(C[i].key);
    } else {
      band.push(h('th', { colspan: String(j - i), scope: 'colgroup', class: ['sum-band', `sum-band-${C[i].group}`, 'sum-group-start'] },
        s.groups[C[i].group]));
    }
    i = j;
  }
  const head = h('tr', null, h('th', { scope: 'col' }, 'Workers\' model'), h('th', { scope: 'col' }, 'Task'),
    s.by_workers ? h('th', { scope: 'col', title: 'Workers per team' }, 'Workers') : null,
    C.filter(c => !spanned.has(c.key)).map(c => colHead(c)));
  const cells = cs => C.map(c => {
    const td = cellView(cs[c.key], ladders, detailed);
    if (starts.has(c.key)) td.classList.add('sum-group-start');
    return td;
  });
  const body = rows.map(r => h('tr', null,
    h('th', { scope: 'row', class: 'sum-model' }, modelLabel({ model: r.model })),
    h('td', { class: 'sum-task' }, r.task), s.by_workers ? h('td', { class: 'num' }, String(r.workers)) : null,
    cells(r.cells)));
  const total = h('tr', { class: 'sum-total' }, h('th', { scope: 'row', colspan: String(lead) }, 'All'),
    cells(wmodel || task ? summed(rows, s.columns, ladders) : s.totals));

  const picker = select(cols.map(c => [c, collectionOf(c).label]), col, v => nav({ col: v }), { 'aria-label': 'Collection' });
  return h('div', { class: 'page wide' },
    h('div', { class: 'idx-head' }, h('div', { class: 'idx-title' }, h('h1', null, `Summary: ${fmtInt(s.total)} runs`), picker, tPick, wPick, iPick,
      counted !== s.total ? badge(`cells hold ${counted}`, 'bad', 'Cells and the collection disagree') : null), toggles),
    h('div', { class: 'table-wrap sum-wrap' }, h('table', { class: 'data sum-table' },
      h('thead', null, h('tr', { class: 'sum-band-row' }, band), head), h('tbody', null, body, total))));
}
