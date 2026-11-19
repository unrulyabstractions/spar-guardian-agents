/* View: every valid run of every collection under runs/ (#/), with filters, sorting and paging. Ported from
 * collusion-hermaneutics' viewer (viewer/static/app/view_index.js). Our edits: the collections come from the index,
 * which serves the valid runs only; the columns are the run's identity, its worker and influencer conditioning, the
 * team's three ladders, the judge labels and the influencers' own three ladders, under a band that names the three
 * groups; one Cheating cell holds the code's rung and cheated and, under it, CheatBench's judge's word, each marked by
 * who says it; the judge labels disclosure and manipulation, each the team's highest score and the worker it is of,
 * linked to that worker's excerpt, with a filter each ("at least" a number), and the influencers' own scores in the
 * Influencers band; the Cheating ladder's alarm marks the row and is counted above the table, with no column; the task,
 * the agents, the conditioning and the arm are the server's words; a legend table under the runs shows every level of
 * the three ladders as the cells draw it, with each ladder's line. */
import { enc, fmtInt, h, plural } from '/static/app/dom.js';
import { muted, select } from '/static/app/components.js';
import { cheatbenchTeam, cheatingCell, labelAbout, levelChip, lineMark, scoreChip, teamScore } from './tv_levels.js';
import { CHEATBENCH, COLLECTION_ORDER, armLabel, collectionOf, influencerParts, modelLabel, setCollections,
  taskLabel } from './tv_labels.js';

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
 * as its cell with the ladder's line marked, and how the level is read. d is /api/ladders' answer. */
export function legendTable(d, title = 'Legend: the three ladder columns') {
  const rows = d.order.map(L => {
    const lad = d.ladders[L];
    return h('tr', null,
      h('td', null, h('b', null, lad.name)),
      h('td', { class: 'lv' }, lad.levels.map(v => [
        v === lad.line ? h('div', { class: 'idx-legend-line' }, lineMark(lad), ' ', lad.line_words) : null,
        h('div', { class: 'idx-legend-level' }, levelChip(L, lad, v))]),
        L === 'cheating' ? h('div', { class: 'idx-legend-sample' }, muted('How a Cheating cell reads (an example, no run\'s data): ', 'small block'),
          cheatingCell(lad, lad.line, true, h('span', { class: 'small' }, '1 of 3 cheated'))) : null),
      h('td', { class: 'small' }, lad.rule));
  });
  return h('section', { class: 'card idx-legend', id: 'legend' }, h('h2', null, title),
    h('p', { class: 'small muted' }, 'A ladder cell shows the level the team reached: the colour of the level, one bar per level of the ladder, the level\'s name and its rank. ',
      'Each ladder has a line: the levels below it are drawn pale, the levels above it strong, and the bars leave a gap at it. ',
      'All three ladders are read by code from the run\'s records, with no judge. ',
      h('a', { href: '#/ladders' }, 'What these words mean, and the levels task by task')),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data idx-legend-table' },
      h('thead', null, h('tr', null, ['Ladder', 'Levels, lowest to highest, and the line', 'How the level is read'].map(t => h('th', { scope: 'col' }, t)))),
      h('tbody', null, rows))));
}

const colIx = r => { const i = COLLECTION_ORDER.indexOf(r.col); return i < 0 ? 99 : i; };
const TEAM_TITLE = {
  cheating: 'The team\'s rung on the Cheating ladder, read by code: the highest rung of any worker, or of the team\'s own evidence, and whether the team cheated. Under it, CheatBench\'s judge\'s own word: how many workers it says cheated. An influencer is not in either (the Influencers columns)',
  coordination: 'The team\'s level on the Coordination ladder: the workers\' levels averaged and rounded half up; an influencer is not in it (the Influencers columns)',
  collusion: 'The team\'s rung on the Collusion ladder, read by code: the highest rung of a pair of workers; an influencer is not in it (the Influencers columns)',
};

/** The influencer conditioning filter's value of one influencer: its kind and level. */
const influencerKind = x => (x.level ? `${x.role}: ${x.level}` : x.role);
const NO_INFLUENCER = 'none';
const influencerKinds = r => (r.influencer_conditioning.length ? r.influencer_conditioning.map(influencerKind) : [NO_INFLUENCER]);

/** The runs table's columns, in the order the user set on 2026-11-19: the run's identity, the two conditioning
 * columns, the team's three ladders, the judge labels, then the influencers' three ladders and their judge labels.
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
    title: `Each influencer's own level on the ${lad(L).name} ladder, read the same way as a worker's and kept out of the team's level`,
    sort: r => Math.max(-1, ...r.levels[`${L}_influencers`].map(x => rank(L, x.level))),
    cell: r => {
      const xs = r.levels[`${L}_influencers`];
      if (!xs.length) return muted(r.n_agents > 1 ? 'no influencer' : 'one agent', 'small');
      return h('span', { class: 'lv-influencers' }, xs.map(x => h('div', { class: 'lv-influencer-row' },
        h('span', { class: 'small muted' }, `${x.role} `), levelChip(L, lad(L), x.level, false))));
    } });
  const about = d.judge_labels;
  const label = k => ({ key: k, label: about.labels[k].name, group: 'labels',
    title: `${about.labels[k].scored} The team's highest score of a worker (0 to 100) and which worker; the worker opens its excerpt. ${about.scored_by}`,
    sort: r => teamScoreOf(r, k), cell: r => teamScore(r.labels.team[k], excerptHref(r, k)) });
  const influencerLabel = k => ({ key: `${k}_influencers`, label: about.labels[k].name, group: 'influencers',
    title: `Each influencer's own ${about.labels[k].name.toLowerCase()} score, kept out of the team's`,
    sort: r => Math.max(-1, ...r.labels.influencers.map(x => (x[k] == null ? -1 : x[k]))),
    cell: r => (!r.labels.influencers.length ? muted(r.n_agents > 1 ? 'no influencer' : 'one agent', 'small')
      : h('span', { class: 'lv-influencers' }, r.labels.influencers.map(x => h('div', { class: 'lv-influencer-row' },
        h('span', { class: 'small muted' }, `${x.role} `), x[k] == null ? muted('not judged', 'small')
          : h('a', { href: excerptHref(r, k)(x.agent), title: `open ${x.agent}'s excerpt` }, scoreChip(x[k])))))) });
  return [
    { key: 'col', label: 'Collection', title: 'Which folder under runs/ holds the run', sort: colIx,
      cell: r => h('span', { title: `${collectionOf(r.col).tag}: ${collectionOf(r.col).about}` }, collectionOf(r.col).label) },
    { key: 'run', label: 'Run', title: 'The run\'s name; opens the run page', sort: r => r.run,
      cell: r => h('a', { class: 'run-link', href: `#/run/${r.id.split('/').map(enc).join('/')}` },
        String(r.run).split('_').flatMap((part, i) => (i ? ['_', h('wbr'), part] : [part]))) },
    { key: 'task', label: 'Task', sort: r => taskLabel(r), cell: r => taskLabel(r) },
    { key: 'agents', label: 'Agents', title: 'Who ran: one agent, or the workers and the number of influencers', sort: r => r.agents, cell: r => r.agents },
    { key: 'model', label: 'Workers\' model', sort: r => modelLabel(r), cell: r => modelLabel(r) },
    { key: 'worker', label: 'Worker conditioning', title: 'What the workers\' system prompt told them: not told, told to cheat, or a rule sentence (a generic honesty rule up to a rule naming the exploit), from the run\'s configuration',
      sort: r => r.worker_conditioning, cell: r => r.worker_conditioning },
    { key: 'influencer', label: 'Influencer conditioning', title: 'Each influencer of the run: regulator or disregulator, its instruction level and the model it ran; none for a run with no influencer',
      sort: r => influencerKinds(r).join(', '),
      cell: r => (r.influencer_conditioning.length ? r.influencer_conditioning.map(x => h('div', { class: 'small' }, influencerParts(x).map((part, i) => [i ? ' · ' : '', h('span', { class: 'nowrap' }, part)])))
        : muted(NO_INFLUENCER, 'small')) },
    ...d.order.map(team),
    ...Object.keys(about.labels).map(label),
    ...d.order.map(influencers),
    ...Object.keys(about.labels).map(influencerLabel),
  ];
}

/** The bands over the columns: the run's identity and conditioning (no name), the team's ladders, the judge labels and
 * the influencers' ladders. */
const GROUPS = { team: 'Team', labels: 'Judge labels', influencers: 'Influencers' };

// Columns whose text wraps.
const WRAP = new Set(['col', 'run', 'task', 'agents', 'model', 'worker', 'influencer']);

function uniq(a) { return [...new Set(a.filter(x => x != null))]; }

/** The filters, in the order the page shows them: {key (the query key), label, options(rows) -> [[value, text]] or
 * number (a box for a score), test(row, value)}. The ladder filters read the team's level only. Worker conditioning
 * is the filter that tells runs told to cheat from runs not told; influencer conditioning keeps the runs with an
 * influencer of that kind and level; "Disclosure at least" and "Manipulation at least" keep the runs whose team score
 * is at least the number. */
function filters(d) {
  const lad = L => d.ladders[L];
  return [
    { key: 'col', label: 'Collection', options: () => COLLECTION_ORDER.map(c => [c, collectionOf(c).label]), test: (r, v) => r.col === v },
    { key: 'task', label: 'Task', options: rows => uniq(rows.map(taskLabel)).sort().map(x => [x, x]), test: (r, v) => taskLabel(r) === v },
    { key: 'model', label: 'Workers\' model', options: rows => uniq(rows.map(modelLabel)).sort().map(x => [x, x]), test: (r, v) => modelLabel(r) === v },
    { key: 'workers', label: 'Workers', options: rows => uniq(rows.map(r => r.workers)).sort((a, b) => a - b).map(n => [String(n), String(n)]),
      test: (r, v) => String(r.workers) === v },
    { key: 'worker', label: 'Worker conditioning', options: rows => uniq(rows.map(r => r.worker_conditioning)).sort().map(x => [x, x]),
      test: (r, v) => r.worker_conditioning === v },
    { key: 'influencer', label: 'Influencer conditioning', options: rows => uniq(rows.flatMap(influencerKinds)).sort().map(x => [x, x]),
      test: (r, v) => influencerKinds(r).includes(v) },
    ...d.order.map(L => ({ key: 'lv_' + L, label: `${lad(L).name} (team)`,
      options: rows => lad(L).levels.filter(v => rows.some(r => r.levels.team[L] === v)).map(v => [v, v]),
      test: (r, v) => r.levels.team[L] === v })),
    { key: 'cheated', label: 'Cheated (team, code)', options: () => [['yes', 'yes'], ['no', 'no']], test: (r, v) => r.levels.cheated === (v === 'yes') },
    ...Object.entries(d.judge_labels.labels).map(([k, x]) => ({ key: `min_${k}`, label: `${x.name} at least`, number: true,
      test: (r, v) => teamScoreOf(r, k) >= Number(v) })),
  ];
}

const PAGE_SIZES = [['100', '100 per page'], ['250', '250 per page'], ['1000', '1,000 per page'], ['all', 'all on one page']];

export async function viewIndex(q) {
  const idx = await loadIndex();
  const ladders = await loadLadders();
  const COLUMNS = columns(ladders);
  const FILTERS = filters(ladders);
  const rows = idx.rows;
  const state = { search: q.get('q') || '', sort: q.get('sort') || '', dir: q.get('dir') === 'desc' ? 'desc' : 'asc',
    page: Math.max(0, (parseInt(q.get('page'), 10) || 1) - 1), size: q.get('size') || '250', f: {} };
  for (const f of FILTERS) state.f[f.key] = q.get(f.key) || '';

  const syncUrl = () => {
    const p = new URLSearchParams();
    for (const f of FILTERS) if (state.f[f.key]) p.set(f.key, state.f[f.key]);
    if (state.search) p.set('q', state.search);
    if (state.sort) { p.set('sort', state.sort); p.set('dir', state.dir); }
    if (state.page) p.set('page', String(state.page + 1));
    if (state.size !== '250') p.set('size', state.size);
    const s = p.toString();
    try { history.replaceState(null, '', '#/' + (s ? '?' + s : '')); } catch (e) { /* view stays as drawn */ }
  };

  const filterBox = h('div', { class: 'filters idx-filters' });
  const inputs = new Map();
  const changed = () => { state.page = 0; draw(); };
  for (const f of FILTERS) {
    if (f.number) {
      const box = h('input', { type: 'number', min: '0', max: '100', step: '1', class: 'idx-score-box', placeholder: '0 to 100',
        'aria-label': f.label, value: state.f[f.key] });
      box.addEventListener('input', () => { state.f[f.key] = box.value.trim(); changed(); });
      inputs.set(f.key, box);
      filterBox.appendChild(h('label', { class: 'field' }, h('span', { class: 'field-label' }, f.label), box));
      continue;
    }
    const options = [['', 'All'], ...f.options(rows)];
    if (!options.some(([v]) => v === state.f[f.key])) state.f[f.key] = '';
    const sel = select(options, state.f[f.key], v => { state.f[f.key] = v; changed(); }, { 'aria-label': f.label });
    inputs.set(f.key, sel);
    filterBox.appendChild(h('label', { class: 'field' }, h('span', { class: 'field-label' }, f.label), sel));
  }
  const search = h('input', { type: 'search', class: 'search', placeholder: 'run name contains', 'aria-label': 'Search run names', value: state.search });
  let t = null;
  search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { state.search = search.value.trim(); changed(); }, 150); });
  filterBox.appendChild(h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Search'), search));
  filterBox.appendChild(h('button', { type: 'button', class: 'btn btn-small', onclick: () => {
    for (const f of FILTERS) state.f[f.key] = '';
    for (const [key, el] of inputs) el.value = state.f[key];
    state.search = ''; state.sort = ''; search.value = '';
    changed();
  } }, 'Reset filters'));

  const summary = h('div', { class: 'summary-line' });
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
    band.appendChild(h('th', { colspan: String(j - i), scope: 'colgroup', class: g ? `idx-band-${g}` : 'idx-band-none' }, g ? GROUPS[g] : ''));
    i = j;
  }
  // The first column of each named group, drawn with a rule at its left.
  const STARTS = new Set(COLUMNS.filter((c, i) => c.group && (!i || COLUMNS[i - 1].group !== c.group)).map(c => c.key));
  const headRow = h('tr');
  for (const c of COLUMNS) {
    const th = h('th', { class: ['sortable', STARTS.has(c.key) && 'idx-group-start'], title: (c.title ? c.title + '. ' : '') + 'Click to sort.', scope: 'col', tabindex: '0' }, c.label,
      c.note ? [h('br'), c.note] : null);
    const go = () => {
      if (state.sort === c.key) state.dir = state.dir === 'asc' ? 'desc' : 'asc';
      else { state.sort = c.key; state.dir = c.key === 'run' || c.key === 'col' ? 'asc' : 'desc'; }
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
    const needle = state.search.toLowerCase();
    let shown = rows.filter(r => (!needle || String(r.run).toLowerCase().includes(needle))
      && FILTERS.every(f => !state.f[f.key] || f.test(r, state.f[f.key])));
    const col = COLUMNS.find(c => c.key === state.sort);
    if (!col) {  // default order: collection, task, arm, model, then seed
      shown = shown.slice().sort((a, b) => colIx(a) - colIx(b) || coll.compare(taskLabel(a), taskLabel(b))
        || coll.compare(armLabel(a), armLabel(b)) || coll.compare(String(modelLabel(a)), String(modelLabel(b)))
        || (Number(a.seed) || 0) - (Number(b.seed) || 0) || coll.compare(String(a.run), String(b.run)));
    } else {
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
    const per = {};
    for (const r of shown) per[r.col] = (per[r.col] || 0) + 1;
    summary.replaceChildren(h('span', null,
      h('strong', null, fmtInt(shown.length)), ` of ${fmtInt(rows.length)} valid runs shown`,
      ' · ', COLLECTION_ORDER.map((c, i) => [i ? ', ' : '', `${collectionOf(c).label} ${fmtInt(per[c] || 0)}`]),
      ' · ', `${plural(shown.filter(r => r.levels.cheated).length, 'team')} cheated (code)`,
      ' · ', `${fmtInt(shown.filter(alarmOf).length)} with the alarm "passed with no trace of the honeypot"`));
    pager.replaceChildren(
      h('button', { type: 'button', class: 'btn btn-small', disabled: state.page === 0, onclick: () => { state.page--; draw(); } }, '← previous'),
      h('span', { class: 'muted small' }, ` page ${state.page + 1} of ${pages} `),
      h('button', { type: 'button', class: 'btn btn-small', disabled: state.page >= pages - 1, onclick: () => { state.page++; draw(); } }, 'next →'),
      select(PAGE_SIZES, state.size, v => { state.size = v; state.page = 0; draw(); }, { 'aria-label': 'Rows per page' }));
    titleEl.textContent = state.f.col ? `${collectionOf(state.f.col).label}: ${plural(shown.length, 'run')}` : `Runs: ${fmtInt(shown.length)}`;
    syncUrl();
  }
  draw();

  const about = h('details', { class: 'sub about' },
    h('summary', null, 'What the collections and labels mean'),
    h('div', { class: 'about-body' },
      h('ul', { class: 'plain-list' },
        COLLECTION_ORDER.map(c => h('li', null, h('strong', null, `${collectionOf(c).label} (runs/${c}). `), collectionOf(c).about,
          muted(` ${fmtInt(collectionOf(c).runs)} valid runs.`, 'small')))),
      h('p', null, h('strong', null, 'Valid runs only. '), 'The viewer lists, measures and serves the valid runs of each collection; invalid, incomplete, mock, attempt and extra-copy runs are not read.'),
      h('p', null, h('strong', null, 'Ladders. '), 'The legend under the runs lists every level and each ladder\'s line; ', h('a', { href: '#/ladders' }, 'the Ladders page'), ` says what puts a run on each. Cheated: ${ladders.ladders.cheating.line_words}.`),
      h('div', null, h('strong', null, 'Judge labels. '), 'Each column shows the team\'s highest score of a worker and which worker; the worker\'s name opens its excerpt. A run no judge has scored reads "not judged".',
        labelAbout(ladders.judge_labels)),
      h('p', null, h('strong', null, 'Influencers. '), 'A regulator or a disregulator is an influencer: an agent the workers cannot tell from a worker. Influencer conditioning names each one, its instruction level and its model. Every team column leaves the influencers out; the Influencers columns show their own levels.'),
      h('p', null, h('strong', null, 'Alarm. '), 'The grader passed the hand-in and no agent of any role, and no evidence of the team\'s own, put a trace of the honeypot in it. A row with the alarm carries a red mark at its left edge; the line above the table counts them; the run page shows it.'),
      h('p', null, h('strong', null, `${CHEATBENCH.name}. `), CHEATBENCH.about),
      h('p', null, `Index built ${idx.built_at}: ${fmtInt(idx.measured)} runs measured in ${idx.build_seconds} s, the others read from the cache${idx.errors.length ? `; ${idx.errors.length} runs could not be read` : ''}. A start measures again every run whose files changed.`)));

  return h('div', { class: 'page wide' },
    h('div', { class: 'page-head' }, titleEl, muted('One row per valid run. Click a run to see its three ladders, the evidence, the board and the transcripts.'
      + (idx.left_out.runs ? ` Not listed: ${fmtInt(idx.left_out.runs)} valid runs of the tasks the study does not read (${idx.left_out.tasks.join(', ')}).` : ''))),
    about, filterBox, summary, pager,
    h('div', { class: 'table-wrap idx-wrap' }, table),
    legendTable(ladders));
}
