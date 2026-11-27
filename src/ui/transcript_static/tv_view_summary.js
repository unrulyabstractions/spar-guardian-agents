/* View: one collection's runs by condition (#/summary?col=<collection>), from /api/summary
 * (src/ui/transcript_summary.py). A row per workers' model, task and team size (its workers, or its agents where
 * influencers take workers' places: the summary's rows_by) (and seed pack, only in a
 * collection with seeded runs); a column per condition: the baseline, one column per influencer combination with baseline workers
 * (role, instruction level and model, stacked on three lines in the header), the positive control (told to cheat), the
 * two negative controls (a rule), and a column per other combination the collection holds. A cell: its runs (a link
 * to the runs page filtered to exactly them, when the server found such filters), cheated, accessed and passed as k of
 * n with a bar each, and the teams' level counts on the three ladders as chips in the ladders' colours. The toggle
 * teams | members (&by=members) counts the runs' workers instead of the runs: each worker's own levels (the cell's
 * members), its alert row still the runs'. Under the rates, in both views, how the cell's runs ended: the round of
 * their last hand-in (▲, a range over several runs) and the workers still working at the end, k of n (the cell's
 * ending). A last row totals each column. Labels and values only: every definition is on the Ladders page. */
import { fmtInt, h, plural } from '/static/app/dom.js';
import { badge, muted, select } from '/static/app/components.js';
import { RAMP, inkOn } from './tv_case_view.js';
import { loadLadders } from './tv_view_index.js';
import { COLLECTION_ORDER, byStrength, collectionOf, modelLabel } from './tv_labels.js';
import { taskShown } from './tv_settings.js';

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
  for (const k of ['col', 'task', 'model', 'workers', 'seed_pack', 'worker', 'influencer', 'influencer_level', 'q']) {
    const v = k === 'model' && filters.model ? modelLabel({ model: filters.model }) : filters[k];
    if (v != null && v !== '') p.set(k, v);
  }
  return `#/?${p.toString()}`;
}

/** Text that breaks only at its spaces: "GPT-6 Luna" as "GPT-6" over "Luna", never inside a word. */
const spaced = text => String(text).split(' ').flatMap((w, i) => [i ? ' ' : null, h('span', { class: 'sum-word' }, w)]).filter(x => x !== null);

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

/** One ladder's share of teams (or of workers: `unit`) at or above its line, as a row: the ladder's letter, the rate,
 * a bar in the colour of the line's level. */
function lineShare(key, lad, counts, n, unit) {
  const at = lad.levels.indexOf(lad.line);
  const k = counts.reduce((t, [v, c]) => t + (lad.levels.indexOf(v) >= at ? c : 0), 0);
  const pct = n ? Math.round((100 * k) / n) : 0;
  return h('div', { class: 'sum-share', title: `${lad.name}, ${lad.line} or above: ${k} of ${plural(n, unit)}` },
    h('span', { class: `sum-lad-name sum-lad-${key}` }, SHORT[key]),
    h('span', { class: ['sum-share-k', k > 0 && 'is-some'] }, `${pct}%`),
    h('span', { class: 'sum-bar' }, h('span', { style: `width:${pct}%;background:${RAMP[key][at]}` })));
}

/** An influencer column's alert row: the share of its runs in which an influencer alerted humans, under the letter
 * A; with `detailed` the alerts as chips, all of them and the true and the false alarms. */
function alertShare(c, detailed) {
  const name = h('span', { class: 'sum-lad-name sum-lad-alerts', title: 'Alerts to humans' }, 'A');
  if (detailed) {
    return h('div', { class: 'sum-lad' }, name, h('span', { class: 'sum-chips' },
      [['alerts', c.alerts.n], ['true', c.alerts.true], ['false', c.alerts.false]].map(([k, v]) => h('span',
        { class: ['sum-chip', 'sum-chip-alert', !v && 'is-zero'], title: `Alerts to humans, ${k}: ${v}` }, `${k} ${v}`))));
  }
  const pct = c.n ? Math.round((100 * c.alerted) / c.n) : 0;
  return h('div', { class: 'sum-share', title: `Humans alerted: ${c.alerted} of ${c.n} runs, ${c.alerts.n} alerts` }, name,
    h('span', { class: ['sum-share-k', c.alerted > 0 && 'is-some'] }, `${pct}%`),
    h('span', { class: 'sum-bar' }, h('span', { class: 'sum-bar-alerts', style: `width:${pct}%` })));
}

/** How a cell's runs ended (its ending): "▲ r14" or "▲ r9–r14" for the rounds of their last hand-ins, and
 * "3/8 working", the workers still working at the end of n; null for a cell whose rows recorded none. */
function endingLine(e) {
  if (!e) return null;
  const r = e.last_hand_in;
  const rounds = !r.length ? 'none' : r.length === 1 ? `r${r[0]}` : `r${r[0]}–r${r[r.length - 1]}`;
  const tip = [`last hand-in: ${r.length ? r.map(x => `round ${x}`).join(', ') : 'none'}`,
    e.no_hand_in ? `${plural(e.no_hand_in, 'run')} without a hand-in` : null,
    `working at the end: ${e.working} of ${plural(e.workers, 'worker')}`].filter(Boolean).join('\n');
  return h('div', { class: 'sum-ending', title: tip },
    h('span', { class: 'sum-end-hand' }, h('span', { class: 'hi-arrow hi-submit', 'aria-hidden': 'true' }, '▲'), ` ${rounds}`),
    h('span', { class: ['sum-end-working', e.working > 0 && 'is-some'] }, `${e.working}/${e.workers} working`));
}

/** A cell, centred: each ladder's share at or above its line, or with `detailed` each ladder's level chips, of its
 * teams or, with `members`, of their workers (the cell's members); in a column with influencers (`col.influencers`)
 * the alert row after them; how its runs ended (endingLine); then its runs (linked when the server found the filters
 * that keep exactly them), and with `members` its workers. */
function cellView(c, d, detailed, col, members) {
  if (!c) return h('td', { class: 'sum-cell sum-empty' }, muted('–'));
  const n = plural(c.n, 'run');
  const u = members ? c.members : c;
  const unit = members ? 'worker' : 'team';
  const alerts = col && col.influencers.length ? alertShare(c, detailed) : null;
  return h('td', { class: 'sum-cell' }, h('div', { class: ['sum-stack', detailed && 'is-detailed'] },
    detailed ? h('div', { class: 'sum-lads' }, d.order.map(L => ladderChips(L, d.ladders[L], u.levels[L])), alerts)
      : [d.order.map(L => lineShare(L, d.ladders[L], u.levels[L], u.n, unit)), alerts],
    endingLine(c.ending),
    h('span', { class: 'sum-ns' },
      c.filters ? h('a', { class: 'sum-n', href: runsHref(c.filters), title: 'Open these runs' }, n)
        : h('span', { class: 'sum-n', title: 'No runs-page filter keeps exactly these' }, n),
      members ? h('span', { class: 'sum-n sum-n-workers' }, plural(u.n, 'worker')) : null)));
}

/** A column's header: its label, or for influencers three short lines: the role chip, the level chip, the model; with
 * two influencers, one such block per influencer side by side, each centred over its own lines. */
function colLabel(c) {
  const xs = c.influencers;
  if (!xs.length) return c.label;
  const block = x => [
    h('div', { class: 'sum-col-line' }, h('span', { class: ['role-chip', `role-chip-${x.role}`],
      title: 'share of the team' }, `${x.share}% ${x.role}s`)),
    h('div', { class: 'sum-col-line' }, h('span', { class: ['level-chip', `level-chip-${x.role}`] }, x.level || 'no level')),
    h('div', { class: 'sum-col-line sum-col-model' }, modelLabel({ model: x.model }))];
  return [
    c.group === 'other' ? h('div', { class: 'sum-col-line' }, c.worker[0].toUpperCase() + c.worker.slice(1)) : null,
    xs.length > 1 ? h('div', { class: 'sum-col-pair' }, xs.map(x => h('div', { class: 'sum-col-one' }, block(x)))) : block(xs[0]),
  ];
}

/** Numbers as one value or a range: "3", "1–2". */
const span = v => (v.length === 1 ? String(v[0]) : `${v[0]}–${v[v.length - 1]}`);

/** A row's workers (the row's `team`, transcript_summary.row_team): one number, a range where its cells differ. */
export function workersOf(t) {
  return h('td', { class: 'num sum-workers', title: t.workers.map(n => plural(n, 'worker')).join(', ') }, span(t.workers));
}

/** A row's influencers: one role chip per role its columns add, "1 · 33%" (how many, their share of the team), the
 * long form in its tooltip; "none" where no cell holds one. */
export function influencersOf(t) {
  const xs = t.influencers;
  return h('td', { class: 'sum-infl' }, !xs.length ? muted('none') : h('div', { class: 'sum-infl-chips' }, xs.map(x =>
    h('span', { class: ['role-chip', `role-chip-${x.role}`, 'sum-infl-chip'],
      title: x.teams.map(([k, n]) => `${plural(k, x.role)} of ${plural(n, 'agent')}`).join('\n') },
    `${span(x.n)} · ${span(x.share)}%`))));
}

/** A column's tooltip: its workers' conditioning and each influencer's role, level and model. */
const colTitle = c => [c.worker, ...(c.influencers.length
  ? c.influencers.map(x => `${x.share}% ${x.role}s ${x.level || 'no level'} ${modelLabel({ model: x.model })}`)
  : ['no influencer'])].join('; ');

/** Counts summed over some cells (or their members): n, cheated, accessed and the ladders' level counts, each
 * ladder's in its order. */
function sumCounts(cs, d) {
  const add = k => cs.reduce((t, x) => t + x[k], 0);
  const levels = {};
  for (const L of d.order) {
    const by = new Map();
    for (const x of cs) for (const [v, n] of x.levels[L] || []) by.set(v, (by.get(v) || 0) + n);
    const order = d.ladders[L].levels;
    levels[L] = [...by.entries()].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  }
  return { n: add('n'), cheated: add('cheated'), accessed: add('accessed'), levels };
}

/** Some cells' endings together: the rounds of their last hand-ins joined, the rest summed; null when none has one. */
function sumEndings(es) {
  const xs = es.filter(Boolean);
  if (!xs.length) return null;
  const add = k => xs.reduce((t, x) => t + x[k], 0);
  return { last_hand_in: [...new Set(xs.flatMap(x => x.last_hand_in))].sort((a, b) => a - b),
    no_hand_in: add('no_hand_in'), working: add('working'), workers: add('workers'), runs: add('runs') };
}

/** The All row over some rows: each column's cells summed, their teams and their workers (members), their endings;
 * no link, since the runs page has no filter for a set of rows. */
function summed(rows, columns, d) {
  const out = {};
  for (const c of columns) {
    const cs = rows.map(r => r.cells[c.key]).filter(Boolean);
    if (!cs.length) continue;
    const add = k => cs.reduce((t, x) => t + x[k], 0);
    const alerts = Object.fromEntries(['n', 'true', 'false'].map(k => [k, cs.reduce((t, x) => t + x.alerts[k], 0)]));
    out[c.key] = { ...sumCounts(cs, d), passed: add('passed'), alerted: add('alerted'), alerts,
      ending: sumEndings(cs.map(x => x.ending)), members: sumCounts(cs.map(x => x.members), d), filters: null };
  }
  return out;
}

export async function viewSummary(q) {
  const ladders = await loadLadders();
  const cols = COLLECTION_ORDER;
  const col = cols.includes(q.get('col')) ? q.get('col') : cols[0];  // the newest study (the index lists newest first)
  const all = await loadSummary(col);
  // The rows of the tasks the Settings show; the server's totals stand only when none is left out.
  const kept = all.rows.filter(r => taskShown(r.task_name));
  const s = { ...all, rows: kept };
  const allShown = kept.length === all.rows.length;
  // The influencer bands the page hides (hide=regulator,disregulator,both), each toggled by its button.
  const BANDS = [['regulator', 'Regulators'], ['disregulator', 'Disregulators'], ['both', 'Both']]
    .filter(([g]) => s.columns.some(c => c.group === g));
  const hidden = new Set((q.get('hide') || '').split(',').filter(g => BANDS.some(([b]) => b === g)));
  // The two model filters: the workers' model keeps its rows; an influencer's model keeps the influencer columns
  // holding it (Baseline and the controls stay).
  // A value the collection does not hold falls back to All, as the pickers show it.
  const known = (v, list) => (list.includes(v) ? v : '');
  const wmodel = known(q.get('wmodel') || '', s.rows.map(r => r.model));
  const detailed = q.get('detail') === '1';
  const members = q.get('by') === 'members';
  const task = known(q.get('task') || '', s.rows.map(r => r.task));
  const imodel = known(q.get('imodel') || '', s.columns.flatMap(c => c.influencers.map(x => x.model)));
  // A seed is its pack's name, the same over the tasks (swe/revealing, creative_writing/revealing).
  const seedName = pack => pack.split('/').pop();
  const seed = s.by_seed ? known(q.get('seed') || '', s.rows.map(r => seedName(r.seed_pack))) : '';
  // A team's size is its row's: its agents where influencers take workers' places, else its workers (rows_by).
  const size = known(q.get('size') || '', s.rows.map(r => String(r.size)));
  const rows = s.rows.filter(r => (!wmodel || r.model === wmodel) && (!task || r.task === task)
    && (!seed || seedName(r.seed_pack) === seed) && (!size || String(r.size) === size));
  // The columns shown: not hidden, holding the influencer model chosen, and holding a cell in some row shown. A
  // control hides with the influencers it is weighed against: the negative controls with the regulators, the positive
  // control with the disregulators.
  const PARTNER = { negative: 'regulator', positive: 'disregulator' };
  const C = s.columns.filter(c => !hidden.has(c.group) && !hidden.has(PARTNER[c.group])
    && (!imodel || !c.influencers.length || c.influencers.some(x => x.model === imodel))
    && rows.some(r => r.cells[c.key]));
  const runsIn = rs => rs.reduce((t, r) => t + s.columns.reduce((u, c) => u + (r.cells[c.key] ? r.cells[c.key].n : 0), 0), 0);
  const counted = runsIn(all.rows);
  const keptRuns = runsIn(kept);
  const nav = (over = {}) => {
    const p = new URLSearchParams({ col, hide: [...hidden].join(','), task, wmodel, imodel, seed, size,
      detail: detailed ? '1' : '', by: members ? 'members' : '', ...over });
    for (const [k, v] of [...p.entries()]) if (!v) p.delete(k);
    location.hash = `#/summary?${p}`;
  };
  // The column toggles, then a rule, then teams | members and Detailed in the corner: view switches, drawn apart from
  // the toggles.
  const detailBtn = h('button', { type: 'button', class: 'sum-detail', 'aria-pressed': String(detailed),
    title: 'each ladder\'s levels instead of its rate at the line', onclick: () => nav({ detail: detailed ? '' : '1' }) },
  'Detailed');
  // Teams or members: what a cell counts, its runs or their workers.
  const byPick = h('span', { class: 'seg sum-by', role: 'group', 'aria-label': 'count' },
    [['', 'teams'], ['members', 'members']].map(([v, label]) => h('button', { type: 'button',
      class: ['seg-btn', (v === 'members') === members && 'active'], 'aria-pressed': String((v === 'members') === members),
      title: v ? 'workers, influencers left out' : 'runs', onclick: () => nav({ by: v }) }, label)));
  const toggles = h('span', { class: 'rp-togs' }, BANDS.map(([g, label]) => h('button', {
    type: 'button', class: 'rp-tog', 'aria-pressed': String(!hidden.has(g)), title: { regulator: 'regulators and the negative controls', disregulator: 'disregulators and the positive control',
      both: 'regulator and disregulator together' }[g],
    onclick: () => { const next = new Set(hidden); if (next.has(g)) next.delete(g); else next.add(g); nav({ hide: [...next].join(',') }); } },
    label)));
  const uniqueModels = list => [...new Set(list)].sort(byStrength);
  const tPick = select([['', 'All tasks'], ...[...new Set(s.rows.map(r => r.task))].sort().map(t => [t, t])],
    task, v => nav({ task: v }), { 'aria-label': 'Task' });
  const wPick = select([['', 'All workers\' models'], ...uniqueModels(s.rows.map(r => r.model)).map(m => [m, modelLabel({ model: m })])],
    wmodel, v => nav({ wmodel: v }), { 'aria-label': 'Workers\' model' });
  const iPick = select([['', 'All influencer models'], ...uniqueModels(s.columns.flatMap(c => c.influencers.map(x => x.model)).filter(Boolean))
    .map(m => [m, modelLabel({ model: m })])], imodel, v => nav({ imodel: v }), { 'aria-label': 'Influencer model' });
  // The seed picker stands only where the collection's runs differ by seed pack, as the Seed column does.
  const sPick = s.by_seed ? select([['', 'All seeds'], ...[...new Set(s.rows.map(r => seedName(r.seed_pack)))]
    .sort().map(name => [name, name])], seed, v => nav({ seed: v }), { 'aria-label': 'Seed' }) : null;
  const byAgents = s.rows_by === 'agents';
  // The size picker stands only where the collection's teams differ in size.
  const sizes = [...new Set(s.rows.map(r => r.size))].sort((a, b) => a - b);
  const zPick = sizes.length > 1 ? select([['', 'All sizes'], ...sizes.map(n => [String(n),
    plural(n, byAgents ? 'agent' : 'worker')])], size, v => nav({ size: v }), { 'aria-label': 'Team size' }) : null;
  const lead = 4 + (s.by_seed ? 1 : 0) + (byAgents ? 1 : 0);

  // The band over the condition columns: one cell per run of columns of the same group. A group of one column named
  // as its column is that column's header, over both header rows.
  const starts = new Set(C.filter((c, i) => i === 0 || C[i - 1].group !== c.group).map(c => c.key));
  // Within a group, each instruction level (generic, instructed, ...) is a block: a soft rule where the level changes and
  // a faint background on every other block.
  const levelOf = c => (c.influencers || []).map(x => x.level || '').join('+');
  const levelStarts = new Set(C.filter((c, i) => i > 0 && !starts.has(c.key) && levelOf(C[i - 1]) !== levelOf(c)).map(c => c.key));
  const shaded = new Set();
  C.reduce((alt, c) => { const a = starts.has(c.key) ? false : levelStarts.has(c.key) ? !alt : alt; if (a) shaded.add(c.key); return a; }, false);
  const levelClasses = c => [levelStarts.has(c.key) && 'sum-level-start', shaded.has(c.key) && 'sum-level-alt'];
  const colHead = (c, more = {}) => h('th', { scope: 'col', class: ['sum-col', starts.has(c.key) && 'sum-group-start', ...levelClasses(c)],
    title: colTitle(c), ...more }, colLabel(c));
  // The lead columns' headers stand over both header rows, on the condition headers' baseline, and stick at the left.
  const band = [h('th', { scope: 'col', rowspan: '2', class: 'sum-lead sum-model' }, 'Workers\' model'),
    h('th', { scope: 'col', rowspan: '2', class: 'sum-lead sum-task' }, 'Task'),
    byAgents ? h('th', { scope: 'col', rowspan: '2', class: 'sum-lead sum-agents', title: 'Agents per team' }, 'Agents')
      : null,
    h('th', { scope: 'col', rowspan: '2', class: 'sum-lead sum-workers', title: 'Workers per team' }, 'Workers'),
    h('th', { scope: 'col', rowspan: '2', class: 'sum-lead sum-infl', title: 'Influencers per team, share of the team' },
      'Influencers'),
    s.by_seed ? h('th', { scope: 'col', rowspan: '2', class: 'sum-lead sum-seed', title: 'Board posts and files planted before round 1' }, 'Seed') : null];
  const spanned = new Set();
  for (let i = 0; i < C.length;) {
    let j = i;
    while (j < C.length && C[j].group === C[i].group) j++;
    // A band's only column stands under its own name with no band: the positive control, and one negative control.
    const alone = j - i === 1 ? (s.groups[C[i].group] === C[i].label ? C[i].label : s.alone[C[i].group]) : null;
    if (alone) {
      band.push(colHead({ ...C[i], label: alone }, { rowspan: '2' }));
      spanned.add(C[i].key);
    } else {
      band.push(h('th', { colspan: String(j - i), scope: 'colgroup', class: ['sum-band', `sum-band-${C[i].group}`, 'sum-group-start'] },
        s.groups[C[i].group]));
    }
    i = j;
  }
  const head = h('tr', null, C.filter(c => !spanned.has(c.key)).map(c => colHead(c)));
  const cells = cs => C.map(c => {
    const td = cellView(cs[c.key], ladders, detailed, c, members);
    if (starts.has(c.key)) td.classList.add('sum-group-start');
    for (const k of levelClasses(c)) if (k) td.classList.add(k);
    return td;
  });
  const body = rows.map(r => h('tr', null,
    h('th', { scope: 'row', class: 'sum-model' }, spaced(modelLabel({ model: r.model }))),
    h('td', { class: 'sum-task' }, spaced(r.task)), byAgents ? h('td', { class: 'num sum-agents' }, String(r.size)) : null,
    workersOf(r.team), influencersOf(r.team),
    s.by_seed ? h('td', { class: 'sum-seed' }, spaced(r.seed_pack.split('/').pop())) : null,
    cells(r.cells)));
  const totals = wmodel || task || seed || size || !allShown ? summed(rows, s.columns, ladders) : s.totals;
  const total = h('tr', { class: 'sum-total' }, h('th', { scope: 'row', colspan: String(lead), class: 'sum-model' }, 'All'),
    cells(totals));
  // The heading counts the runs the table shows: the All row's cells over the columns shown.
  const shownRuns = C.reduce((t, c) => t + (totals[c.key] ? totals[c.key].n : 0), 0);

  const picker = select(cols.map(c => [c, collectionOf(c).label]), col, v => nav({ col: v }), { 'aria-label': 'Collection' });
  const table = h('table', { class: ['data', 'sum-table', detailed && 'is-detailed'] },
    h('thead', null, h('tr', { class: 'sum-band-row' }, band), head), h('tbody', null, body, total));
  // The lead columns stick at the left as the table scrolls: each at the summed width of the lead columns before it.
  requestAnimationFrame(() => {
    if (!table.isConnected) return;
    const lefts = [0];
    for (const th of [...band].filter(Boolean).slice(0, lead - 1)) lefts.push(lefts[lefts.length - 1] + th.getBoundingClientRect().width);
    for (const tr of table.querySelectorAll('tr')) {
      [...tr.children].filter(x => x.matches('.sum-model, .sum-task, .sum-agents, .sum-workers, .sum-infl, .sum-seed'))
        .forEach((x, i) => { x.classList.add('sum-stick'); x.style.left = `${lefts[i]}px`; });
    }
  });
  return h('div', { class: 'page wide' },
    h('div', { class: 'idx-head' }, h('div', { class: 'idx-title' }, h('h1', null, shownRuns === keptRuns ? `Summary: ${fmtInt(keptRuns)} runs` : `Summary: ${fmtInt(shownRuns)} of ${fmtInt(keptRuns)} runs`), picker, tPick, wPick, iPick, sPick, zPick,
      counted !== all.total ? badge(`cells hold ${counted}`, 'bad', 'Cells and the collection disagree') : null),
    h('span', { class: 'sum-corner' }, toggles, h('span', { class: 'rp-sep' }), byPick, detailBtn)),
    h('div', { class: 'table-wrap sum-wrap' }, table));
}
