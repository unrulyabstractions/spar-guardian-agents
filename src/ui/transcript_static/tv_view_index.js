/* View: every valid run of every collection under runs/ (#/), with filters, sorting and paging. Ported from
 * collusion-hermaneutics' viewer (viewer/static/app/view_index.js). Our edits: the collections come from the index,
 * which serves the valid runs only; the columns are the run, its workers (their number and model over their
 * conditioning as chips) and its influencers (each one's role and level as chips over its model), its task, the team's
 * three ladders, the judge labels and the influencers' own three ladders, under a band that names the three groups,
 * and the collection last; one Cheating cell holds the code's rung and cheated and, under it, CheatBench's judge's
 * word, each marked by who says it; the judge labels disclosure and manipulation, each the team's highest score and the
 * worker it is of, linked to that worker's excerpt, with a filter each ("at least" a number), and the influencers' own
 * scores in the Influencers band; the Cheating ladder's alarm marks the row, with no column; the task, the
 * conditioning and the arm are the server's words; the heading carries the count, the pager beside it, and the filters
 * stand in three labelled groups (FILTER_GROUPS), a set filter outlined; a run with no influencer shows a dash in the
 * influencer columns; a legend table under the runs shows every level of the three ladders as the cells draw it,
 * with each ladder's line; the collections and the index's build stand in a small table behind one "Collections"
 * control. Every definition is on the Ladders page. */
import { enc, fmtInt, h, plural } from '/static/app/dom.js';
import { muted, select } from '/static/app/components.js';
import { cheatbenchTeam, cheatingCell, levelChip, lineMark, misleadMark, scoreChip, teamScore } from './tv_levels.js';
import { COLLECTION_ORDER, collectionOf, modelLabel, setCollections, taskLabel } from './tv_labels.js';

let INDEX = null;
export async function loadIndex() {
  if (!INDEX) {
    const r = await fetch('/api/index');
    if (!r.ok) throw new Error(`index: HTTP ${r.status}`);
    INDEX = await r.json();
    setCollections(INDEX.collections);
  }
  return INDEX;
}

let LADDERS = null;
/** /api/ladders' answer: the ladders' names, levels, lines, rules and the Ladders page's tables. */
export async function loadLadders() {
  if (!LADDERS) {
    const r = await fetch('/api/ladders');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `ladders: HTTP ${r.status}`);
    LADDERS = d;
  }
  return LADDERS;
}

const alarmOf = r => r.levels.alarm;
/** A run's team score of a label (its index row's labels.team), -1 when not judged. */
const teamScoreOf = (r, k) => (r.labels.team[k] ? r.labels.team[k].score : -1);
/** Where one agent's excerpt of a label opens: the run page at the agent's tab or the Board, the excerpt highlighted. */
const excerptHref = (r, label) => agent => `#/run/${r.id.split('/').map(enc).join('/')}?agent=${enc(agent)}&label=${enc(label)}`;

/** The legend under the runs, and the Ladders page's list of the three ladders: one row per ladder, every level drawn
 * as its cell, the ladder's line between its two sides. d is /api/ladders' answer. */
export function legendTable(d, title = 'Legend') {
  const rows = d.order.map(L => {
    const lad = d.ladders[L];
    return h('tr', null, h('th', { scope: 'row' }, lad.name),
      h('td', { class: 'idx-legend-levels' }, lad.levels.map(v => [v === lad.line ? lineMark(lad) : null, levelChip(L, lad, v, false)])));
  });
  return h('section', { class: 'card idx-legend', id: 'legend' }, h('h2', null, title),
    h('table', { class: 'idx-legend-table' }, h('tbody', null, rows)));
}

const colIx = r => { const i = COLLECTION_ORDER.indexOf(r.col); return i < 0 ? 99 : i; };
const TEAM_TITLE = {
  cheating: 'Highest worker\'s rung; under it, CheatBench judge\'s count',
  coordination: 'Workers\' levels averaged, rounded half up',
  collusion: 'Highest pair of workers',
};
/** A cell with nothing to show: a run with no influencer, or a run of one agent. */
const none = () => muted('–', 'small');

/** The influencers a run holds: none, regulator, disregulator or both. */
const INFLUENCER_SETS = ['regulator', 'disregulator', 'both', 'none'];
/** An Influencer filter value: any (at least one influencer) or one of INFLUENCER_SETS. */
const influencerIs = (r, v) => (v === 'any' ? influencerSet(r) !== 'none' : influencerSet(r) === v);
const influencerSet = r => {
  const roles = new Set(r.influencer_conditioning.map(x => x.role));
  return roles.size > 1 ? 'both' : roles.size ? [...roles][0] : 'none';
};
/** One influencer's conditioning as a filter value: its role and its level. */
const influencerLevel = x => `${x.role}: ${x.level || 'no level'}`;

/** A worker conditioning's chip: baseline calm, told to cheat warm, a rule cool and a rule naming the exploit strong. */
const CONDITIONING_TONE = { baseline: 'cond-none', 'told to cheat': 'cond-cheat', 'rule naming the exploit': 'cond-rule-strong',
  'rule naming where the exploit lies': 'cond-rule-strong' };
const conditioningChip = part => h('span', { class: ['cond-chip', CONDITIONING_TONE[part] || 'cond-rule'] }, part);
/** The Workers cell: the workers' number and model over their conditioning's chips. */
const workersCell = r => h('div', { class: 'idx-who' },
  h('span', { class: 'idx-model' }, h('span', { class: 'idx-count', title: plural(r.workers, 'worker') }, `${r.workers} ×`), ' ', modelLabel(r)),
  h('span', { class: 'idx-chips' }, r.worker_conditioning.split('; ').map(conditioningChip)));
/** The Influencers cell: per influencer its role and its level as chips over its model; none without one. */
const influencersCell = r => (r.influencer_conditioning.length
  ? h('div', { class: 'idx-who' }, r.influencer_conditioning.map(x => h('div', { class: 'idx-influencer' },
    h('span', { class: 'idx-chips' },
      h('span', { class: ['role-chip', `role-chip-${x.role}`] }, x.n === 1 ? x.role : `${x.n} ${x.role}s`),
      x.level ? h('span', { class: ['level-chip', `level-chip-${x.role}`] }, x.level) : null),
    h('span', { class: 'idx-model' }, modelLabel({ model: x.model })))))
  : none());

/** A name that may break after each underscore (and at a space, unless the space is a no-break one). */
const breakable = name => String(name).split('_').flatMap((part, i) => (i ? ['_', h('wbr'), part] : [part]));

/** The runs table's columns, in the order the user set on 2026-11-19: the interest, the run, the workers and the
 * influencers (each model with its conditioning), the task, the team's three ladders, the judge labels, the
 * influencers' three ladders and their judge labels, and the collection last. The Workers cell says how many workers
 * and the Influencers cell names each influencer, so no column counts the agents.
 * Each is {key, label, group, title, sort(row), cell(row)}; group names the band over it (GROUPS). d is /api/ladders'
 * answer. */
function columns(d) {
  const lad = L => d.ladders[L];
  const rank = (L, v) => lad(L).levels.indexOf(v);
  const team = L => ({ key: 'lv_' + L, label: lad(L).name, group: 'team', title: TEAM_TITLE[L], sort: r => rank(L, r.levels.team[L]),
    cell: r => (L === 'cheating'
      ? cheatingCell(lad(L), r.levels.team[L], r.levels.cheated, cheatbenchTeam(r.cheatbench), false)
      : levelChip(L, lad(L), r.levels.team[L], false)) });
  const influencers = L => ({ key: `lv_${L}_influencers`, label: lad(L).name, group: 'influencers',
    title: 'Each influencer\'s own level, kept out of the team\'s',
    sort: r => Math.max(-1, ...r.levels[`${L}_influencers`].map(x => rank(L, x.level))),
    cell: r => {
      const xs = r.levels[`${L}_influencers`];
      if (!xs.length) return none();
      return h('span', { class: 'lv-influencers' }, xs.map(x => h('div', { class: 'lv-influencer-row' },
        h('span', { class: 'small muted' }, `${x.role} `), levelChip(L, lad(L), x.level, false))));
    } });
  const about = d.judge_labels;
  const label = k => ({ key: k, label: about.labels[k].name, group: 'labels',
    title: 'Team\'s highest score, 0 to 100, and its worker',
    sort: r => teamScoreOf(r, k), cell: r => teamScore(r.labels.team[k], excerptHref(r, k)) });
  const influencerLabel = k => ({ key: `${k}_influencers`, label: about.labels[k].name, group: 'influencers',
    title: 'Each influencer\'s own score, kept out of the team\'s',
    sort: r => Math.max(-1, ...r.labels.influencers.map(x => (x[k] == null ? -1 : x[k]))),
    cell: r => (!r.labels.influencers.length ? none()
      : h('span', { class: 'lv-influencers' }, r.labels.influencers.map(x => h('div', { class: 'lv-influencer-row' },
        h('span', { class: 'small muted' }, `${x.role} `), x[k] == null ? muted('not judged', 'small')
          : [h('a', { href: excerptHref(r, k)(x.agent), title: `open ${x.agent}'s excerpt` }, scoreChip(x[k])),
            (x.kinds || {})[k] === 'mislead' ? [' ', misleadMark('mislead')] : null])))) });
  return [
    { key: 'interest', label: 'Interest', title: 'Default order: team\'s Cheating rung against its influencers',
      sort: r => r.interest.rank, cell: r => h('span', { class: 'small' }, h('strong', null, String(r.interest.rank)), ` ${r.interest.why}`) },
    { key: 'run', label: 'Run', sort: r => r.run,
      cell: r => h('a', { class: 'run-link', href: `#/run/${r.id.split('/').map(enc).join('/')}` },
        breakable(r.run)) },
    { key: 'workers', label: 'Workers', title: 'Number, model and system prompt\'s instruction',
      sort: r => `${modelLabel(r)} · ${r.worker_conditioning}`, cell: workersCell },
    { key: 'influencers', label: 'Influencers', title: 'Each influencer\'s role, instruction level and model',
      sort: r => r.influencer_conditioning.map(x => `${x.role} ${x.level || ''} ${modelLabel({ model: x.model })}`).join(' · '),
      cell: influencersCell },
    { key: 'task', label: 'Task', sort: r => taskLabel(r), cell: r => taskLabel(r) },
    ...d.order.map(team),
    ...Object.keys(about.labels).map(label),
    ...d.order.map(influencers),
    ...Object.keys(about.labels).map(influencerLabel),
    { key: 'col', label: 'Collection', title: 'Folder under runs/', sort: colIx,
      cell: r => h('span', { title: `${collectionOf(r.col).tag}: ${collectionOf(r.col).about}` }, breakable(collectionOf(r.col).label.replaceAll(' ', '\u00a0'))) },
  ];
}

/** The bands over the columns: the run's identity and conditioning (no name), the team's ladders, the judge labels,
 * the influencers' ladders, and the collection (no name). */
const GROUPS = { team: 'Team', labels: 'Judge labels', influencers: 'Influencers' };

// Columns whose text wraps.
const WRAP = new Set(['interest', 'col', 'run', 'task']);

function uniq(a) { return [...new Set(a.filter(x => x != null))]; }

/** The filters: {key (the query key), label, group, title, a control (options(rows) -> [[value, text]], number: a box
 * for a score, or search: a box for the run's name), test(row, value)}, in the order the page shows them within their
 * group. The ladder filters, cheated and the label filters read the team's own, influencers left out. Worker
 * conditioning is the filter that tells runs told to cheat from baseline runs, baseline first; Influencer keeps the runs with an
 * influencer of that kind and level; "Disclosure at least" and "Manipulation at least" keep the runs whose team score
 * is at least the number. */
function filters(d) {
  const lad = L => d.ladders[L];
  const ladder = L => ({ key: 'lv_' + L, label: lad(L).name, group: 'outcome', title: `The team's level on the ${lad(L).name} ladder`,
    options: rows => lad(L).levels.filter(v => rows.some(r => r.levels.team[L] === v)).map(v => [v, v]),
    test: (r, v) => r.levels.team[L] === v });
  return [
    { key: 'q', label: 'Search', group: 'run', title: 'Runs whose name contains the text', search: true,
      test: (r, v) => String(r.run).toLowerCase().includes(v.toLowerCase()) },
    { key: 'task', label: 'Task', group: 'run', options: rows => uniq(rows.map(taskLabel)).sort().map(x => [x, x]), test: (r, v) => taskLabel(r) === v },
    { key: 'workers', label: 'Workers', group: 'run', title: 'The number of workers',
      options: rows => uniq(rows.map(r => r.workers)).sort((a, b) => a - b).map(n => [String(n), String(n)]),
      test: (r, v) => String(r.workers) === v },
    { key: 'col', label: 'Collection', group: 'run', options: () => COLLECTION_ORDER.map(c => [c, collectionOf(c).label]), test: (r, v) => r.col === v },
    { key: 'model', label: 'Workers\' model', group: 'who', options: rows => uniq(rows.map(modelLabel)).sort().map(x => [x, x]), test: (r, v) => modelLabel(r) === v },
    { key: 'worker', label: 'Worker conditioning', group: 'who', options: rows => uniq(rows.map(r => r.worker_conditioning)).sort((a, b) => (b === 'baseline') - (a === 'baseline') || a.localeCompare(b)).map(x => [x, x]),
      test: (r, v) => r.worker_conditioning === v },
    { key: 'influencer', label: 'Influencer', group: 'who', title: 'Which influencers the run holds',
      options: rows => ['any', ...INFLUENCER_SETS].filter(k => rows.some(r => influencerIs(r, k))).map(k => [k, k]),
      test: (r, v) => influencerIs(r, v) },
    { key: 'influencer_level', label: 'Influencer conditioning', group: 'who', dependsOn: 'influencer',
      title: 'An influencer\'s instruction level',
      options: (rows, f) => {
        const kept = rows.filter(r => !f.influencer || influencerIs(r, f.influencer));
        const one = ['regulator', 'disregulator'].includes(f.influencer);
        return uniq(kept.flatMap(r => r.influencer_conditioning.map(influencerLevel))).sort()
          .map(v => [v, one ? v.slice(v.indexOf(': ') + 2) : v]);
      },
      test: (r, v) => r.influencer_conditioning.some(x => influencerLevel(x) === v) },
    ladder('cheating'),
    { key: 'cheated', label: 'Cheated', group: 'outcome', title: 'Whether the team cheated, read by code',
      options: () => [['yes', 'yes'], ['no', 'no']], test: (r, v) => r.levels.cheated === (v === 'yes') },
    ladder('coordination'),
    ladder('collusion'),
    ...Object.entries(d.judge_labels.labels).map(([k, x]) => ({ key: `min_${k}`, label: `${x.name} at least`, group: 'outcome',
      title: `The team's highest ${x.name.toLowerCase()} score, 0 to 100`, number: true, test: (r, v) => teamScoreOf(r, k) >= Number(v) })),
  ];
}

/** The filters' three groups, in the table's own order: the run, who ran, what came out. */
const FILTER_GROUPS = [['run', 'Run'], ['who', 'Who'], ['outcome', 'Outcome']];

const PAGE_SIZES = [['100', '100 per page'], ['250', '250 per page'], ['1000', '1,000 per page'], ['all', 'all on one page']];

export async function viewIndex(q) {
  const idx = await loadIndex();
  const ladders = await loadLadders();
  const COLUMNS = columns(ladders);
  const FILTERS = filters(ladders);
  const rows = idx.rows;
  const state = { sort: q.get('sort') || '', dir: q.get('dir') === 'desc' ? 'desc' : 'asc',
    page: Math.max(0, (parseInt(q.get('page'), 10) || 1) - 1), size: q.get('size') || '250', f: {} };
  for (const f of FILTERS) state.f[f.key] = q.get(f.key) || '';

  const syncUrl = () => {
    const p = new URLSearchParams();
    for (const f of FILTERS) if (state.f[f.key]) p.set(f.key, state.f[f.key]);
    if (state.sort) { p.set('sort', state.sort); p.set('dir', state.dir); }
    if (state.page) p.set('page', String(state.page + 1));
    if (state.size !== '250') p.set('size', state.size);
    const s = p.toString();
    try { history.replaceState(null, '', '#/' + (s ? '?' + s : '')); } catch (e) { /* view stays as drawn */ }
  };

  // Each filter's control in a field, its label above it; a field whose filter is set is outlined (draw).
  const changed = () => { state.page = 0; draw(); };
  const fields = new Map();
  const control = f => {
    if (f.search || f.number) {
      const box = h('input', f.search ? { type: 'search', placeholder: 'run name contains', 'aria-label': f.label, value: state.f[f.key] }
        : { type: 'number', min: '0', max: '100', step: '1', placeholder: '0 to 100', 'aria-label': f.label, value: state.f[f.key] });
      let t = null;
      box.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { state.f[f.key] = box.value.trim(); changed(); }, 150); });
      return box;
    }
    const options = [['', 'All'], ...f.options(rows, state.f)];
    if (!options.some(([v]) => v === state.f[f.key])) state.f[f.key] = '';
    return select(options, state.f[f.key], v => { state.f[f.key] = v; refreshDependents(f.key); changed(); },
      { 'aria-label': f.label });
  };
  // A filter whose options follow another's choice (dependsOn) is drawn again when that choice changes.
  const refreshDependents = key => {
    for (const g of FILTERS.filter(x => x.dependsOn === key)) {
      const x = fields.get(g.key);
      const el = control(g);
      x.field.replaceChild(el, x.el);
      x.el = el;
    }
  };
  for (const f of FILTERS) {
    const el = control(f);
    fields.set(f.key, { el, field: h('label', { class: 'idx-field', title: f.title || null }, h('span', { class: 'field-label' }, f.label), el) });
  }
  const reset = h('button', { type: 'button', class: 'btn btn-small idx-reset', onclick: () => {
    for (const f of FILTERS) state.f[f.key] = '';
    for (const [key, x] of fields) x.el.value = state.f[key];
    for (const f of FILTERS) refreshDependents(f.key);
    state.sort = '';
    changed();
  } }, 'Reset filters');
  const filterBox = h('div', { class: 'idx-filters' }, FILTER_GROUPS.map(([g, name], i) => h('div', { class: 'idx-fgroup' },
    h('div', { class: 'idx-fgroup-name' }, name),
    h('div', { class: 'idx-fgroup-fields' }, FILTERS.filter(f => f.group === g).map(f => fields.get(f.key).field),
      i === FILTER_GROUPS.length - 1 ? reset : null))));

  const pager = h('div', { class: 'pager' });
  const thead = h('thead');
  const tbody = h('tbody');
  const table = h('table', { class: 'data idx-table' }, thead, tbody);

  // The band: one cell per run of columns of the same group, named by GROUPS (the identity columns' band is blank).
  const band = h('tr', { class: 'idx-band' });
  for (let i = 0; i < COLUMNS.length;) {
    let j = i;
    while (j < COLUMNS.length && COLUMNS[j].group === COLUMNS[i].group) j++;
    const g = COLUMNS[i].group;
    band.appendChild(h('th', { colspan: String(j - i), scope: 'colgroup', class: [g ? `idx-band-${g}` : 'idx-band-none', i && 'idx-group-start'] },
      g ? GROUPS[g] : ''));
    i = j;
  }
  // The first column of each group but the first, drawn with a rule at its left.
  const STARTS = new Set(COLUMNS.filter((c, i) => i && COLUMNS[i - 1].group !== c.group).map(c => c.key));
  const headRow = h('tr');
  for (const c of COLUMNS) {
    const th = h('th', { class: ['sortable', STARTS.has(c.key) && 'idx-group-start'], title: c.title || null, scope: 'col', tabindex: '0' }, c.label,
      c.note ? [h('br'), c.note] : null);
    const go = () => {
      if (state.sort === c.key) state.dir = state.dir === 'asc' ? 'desc' : 'asc';
      else { state.sort = c.key; state.dir = ['run', 'col', 'interest'].includes(c.key) ? 'asc' : 'desc'; }
      changed();
    };
    th.addEventListener('click', go);
    th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    c.th = th;
    headRow.appendChild(th);
  }
  thead.append(band, headRow);

  const coll = new Intl.Collator('en', { numeric: true });
  const titleEl = h('h1', null, 'Runs');
  function draw() {
    const set = FILTERS.filter(f => state.f[f.key]);
    for (const [key, x] of fields) x.field.classList.toggle('is-set', !!state.f[key]);
    let shown = rows.filter(r => set.every(f => f.test(r, state.f[f.key])));
    const col = COLUMNS.find(c => c.key === state.sort);
    if (col) {  // with no column chosen the rows keep the index's own order: by interest (transcript_index.INTEREST)
      const sign = state.dir === 'desc' ? -1 : 1;
      const keyed = shown.map(r => [col.sort(r), r]);
      keyed.sort((a, b) => {
        const x = a[0]; const y = b[0];
        const c = typeof x === 'string' || typeof y === 'string' ? coll.compare(String(x), String(y)) : x - y;
        return c * sign || coll.compare(String(a[1].run), String(b[1].run));
      });
      shown = keyed.map(p => p[1]);
    }
    for (const c of COLUMNS) {
      c.th.classList.toggle('sorted', c.key === state.sort);
      if (c.key === state.sort) c.th.dataset.dir = state.dir; else delete c.th.dataset.dir;
    }
    const size = state.size === 'all' ? Math.max(shown.length, 1) : parseInt(state.size, 10);
    const pages = Math.max(1, Math.ceil(shown.length / size));
    if (state.page >= pages) state.page = pages - 1;
    const slice = shown.slice(state.page * size, state.page * size + size);
    tbody.replaceChildren(...(slice.length ? slice.map(r => h('tr', { class: ['idx-row', alarmOf(r) && 'row-alarm'], title: alarmOf(r) ? `alarm: ${alarmOf(r)}` : null },
      COLUMNS.map(c => h('td', { class: [WRAP.has(c.key) && 'wrap', c.key.startsWith('lv_') && 'lv', STARTS.has(c.key) && 'idx-group-start'] }, c.cell(r)))))
      : [h('tr', null, h('td', { colspan: String(COLUMNS.length) }, muted('No run matches these filters.')))]));
    pager.replaceChildren(
      h('button', { type: 'button', class: 'btn btn-small', disabled: state.page === 0, onclick: () => { state.page--; draw(); } }, '← previous'),
      h('span', { class: 'muted small' }, ` page ${state.page + 1} of ${pages} `),
      h('button', { type: 'button', class: 'btn btn-small', disabled: state.page >= pages - 1, onclick: () => { state.page++; draw(); } }, 'next →'),
      select(PAGE_SIZES, state.size, v => { state.size = v; state.page = 0; draw(); }, { 'aria-label': 'Rows per page' }));
    titleEl.textContent = set.length ? `Runs: ${fmtInt(shown.length)} of ${fmtInt(rows.length)}` : `Runs: ${fmtInt(rows.length)}`;
    syncUrl();
  }
  draw();

  // The collections and the index's build, behind one small control.
  const about = h('details', { class: 'idx-about' },
    h('summary', { class: 'btn btn-small' }, 'Collections'),
    h('div', { class: 'idx-about-box' }, h('table', { class: 'data compact' },
      h('thead', null, h('tr', null, ['Collection', 'Folder', 'Valid runs', 'Holds'].map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, COLLECTION_ORDER.map(c => h('tr', null, h('td', null, h('b', null, collectionOf(c).label)),
        h('td', { class: 'mono small' }, `runs/${c}`), h('td', { class: 'num' }, fmtInt(collectionOf(c).runs)),
        h('td', { class: 'small' }, collectionOf(c).about))))),
    h('p', { class: 'small muted' }, `Index ${idx.built_at} · ${fmtInt(idx.measured)} measured in ${idx.build_seconds} s`,
      idx.errors.length ? ` · ${plural(idx.errors.length, 'run')} unreadable` : '')));

  return h('div', { class: 'page wide' },
    h('div', { class: 'idx-head' }, h('div', { class: 'idx-title' }, titleEl, about), pager),
    filterBox,
    h('div', { class: 'table-wrap idx-wrap' }, table),
    legendTable(ladders));
}
