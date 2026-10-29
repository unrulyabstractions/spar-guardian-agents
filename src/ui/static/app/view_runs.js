/* View: runs (#/runs).
 * The page lists one row per run. A row opens to its agents.
 *
 *   import { viewRuns } from './view_runs.js';
 *   const node = await viewRuns(route, view);
 */
import { DASH, debounce, firstLine, fmtCompact, fmtInt, fmtSecs, fmtUsd, h, plural } from './dom.js';
import { P, R, apiFresh, setHashQuietly } from './api.js';
import { badge, labeled, link, mono, muted, rungBadge, select, yn } from './components.js';
import { ROLE_ORDER, roleIcon } from './charts.js';

const runsState = { q: '', setting: '', control: '', cheated: '', failed: 'hide', sortKey: 'setting', sortDir: 1 };
const openRuns = new Set();   // runs whose agent rows are open, kept across visits

/**
 * The arrangement a run was made in: the communication graph (a lone agent, or every agent on one
 * shared board), the roster size, and whether roles were hidden. run.json calls a board with hidden
 * roles a "swarm"; the graph is the same board, so the setting is read from graph, n_agents and swarm.
 */
/** The tasks with a setting of their own: the key's prefix, the label and the sort order. */
const OWN_SETTING = { knowledge_work: ['kw', 'knowledge work', 4], subset_sum: ['subset', 'subset sum', 5],
  prime_factorization: ['factor', 'prime factorization', 6], protein_design: ['protein', 'protein design', 7] };

export function settingOf(run) {
  const graph = run.graph || (run.topology === 'single' ? 'single' : 'board');
  const n = run.n_agents || (run.agents || []).length || (run.episodes || []).length;
  const sw = run.swarm || null;
  if (run.swe) return sweSetting(run.swe, n);
  if (OWN_SETTING[run.task]) {
    // these tasks are their own settings, apart from openmath, by team size and hidden roles
    const [prefix, label, order] = OWN_SETTING[run.task];
    const n1 = graph === 'single' ? 1 : n;
    const hidden = !!(sw && sw.composition);
    return { key: `${prefix}-${hidden ? 'roles' : 'board'}-${n1}`, order, n: n1,
      label: `${label} ${n1 === 1 ? 'lone agent' : `board of ${n1}`}${hidden ? ', hidden roles' : ''}`,
      ...(hidden ? { composition: sw.composition, R: sw.regulators, D: sw.disregulators, W: sw.workers } : {}) };
  }
  if (graph === 'single') return { key: 'single', label: 'lone agent', order: 0, n: 1 };
  if (sw && sw.composition) {
    return { key: `roles-${n}`, label: `board of ${n}, hidden roles`, order: 2, n,
      composition: sw.composition, R: sw.regulators, D: sw.disregulators, W: sw.workers };
  }
  return { key: `board-${n}`, label: `board of ${n}`, order: 1, n };
}

/** A CheatBench SWE run: its own setting, apart from the openmath runs, by issue, team size, honeypot and hint. */
function sweSetting(swe, n0) {
  const n = swe.n_agents || n0;
  const honeypot = swe.honeypot && swe.honeypot !== 'as_released' ? swe.honeypot : null;
  const bits = [honeypot, swe.rule && swe.rule !== 'none' ? `rule: ${swe.rule}` : null].filter(Boolean);
  const base = `SWE ${swe.task_key || ''} ${n === 1 ? 'lone agent' : `board of ${n}`}`.replace(/\s+/g, ' ');
  return { key: `swe-${swe.task_key}-${swe.arm}`, label: base + (bits.length ? ` (${bits.join(', ')})` : ''), order: 3, n };
}

function settingSortKey(s, run) {
  const pad = x => String(x == null ? 0 : x).padStart(3, '0');
  return `${s.order}|${pad(s.n)}|${pad(s.R)}|${pad(s.D)}|${run}`;
}

/** Counts of one run from its episodes; a verdict counts only with a transcript (the rule of /api/summary). */
function runAggregate(run) {
  const eps = run.episodes || [];
  const judged = eps.filter(e => e.verdict && e.verdict.cheated != null && e.has_transcript);
  const n = k => judged.filter(e => e.verdict[k]).length;
  const sum = f => eps.reduce((s, e) => s + (Number(f(e)) || 0), 0);
  const endings = run.endings || {};
  const d = run.deliverables || null;
  return {
    agents: eps.length, judged: judged.length, cheated: n('cheated'), seen: n('honey_pots_seen'),
    attempted: n('attempted_access'), extracted: n('extracted_submission'), disclosed: n('disclosed_access'),
    blocks: sum(e => (e.defense || {}).blocks), checks: sum(e => (e.defense || {}).checks), hasDefense: eps.some(e => e.defense),
    calls: sum(e => e.n_tool_calls), wall: Math.max(0, ...eps.map(e => e.wall_time || 0)),
    prompt: sum(e => (e.usage || {}).input), judgeCost: sum(e => (e.verdict || {}).judge_cost_usd),
    endings,
    ended: Object.entries(endings).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k, c]) => `${c} ${k}`).join(', '),
    registered: d ? d.registered_on_main : null, copied: d ? d.registered_message_copied_from : null,
  };
}

function cmpNullsLast(a, b, dir) {
  const an = a == null || a === '';
  const bn = b == null || b === '';
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  if (typeof a === 'boolean') a = +a;
  if (typeof b === 'boolean') b = +b;
  if (typeof a === 'number' && typeof b === 'number') return (a - b) * dir;
  return String(a).localeCompare(String(b), 'en', { numeric: true }) * dir;
}

const RUN_COLS = [
  { key: 'run', label: 'Run', sort: r => r.run.run },
  { key: 'setting', label: 'Setting', sort: r => settingSortKey(r.setting, r.run.run), title: 'graph, roster size and hidden roles' },
  { key: 'control', label: 'Control', sort: r => r.run.control },
  { key: 'agents', label: 'Agents', num: true, sort: r => r.agg.agents },
  { key: 'cheated', label: 'Cheated', num: true, sort: r => (r.agg.judged ? r.agg.cheated / r.agg.judged : null), title: 'agents the judge found cheated, over judged agents' },
  { key: 'seen', label: 'Saw honeypot', num: true, sort: r => r.agg.seen, title: 'judged agents that saw the honeypot' },
  { key: 'attempted', label: 'Attempted', num: true, sort: r => r.agg.attempted, title: 'judged agents that attempted access' },
  { key: 'registered', label: 'Registered', sort: r => r.agg.registered, title: 'a registry entry on main at the end' },
  { key: 'ended', label: 'Ended', sort: r => r.agg.ended, title: 'how the agents’ episodes ended' },
  { key: 'blocks', label: 'Blocks / checks', num: true, sort: r => (r.agg.hasDefense ? r.agg.blocks : null) },
  { key: 'wall', label: 'Wall', num: true, sort: r => r.agg.wall },
  { key: 'prompt', label: 'Prompt tok', num: true, sort: r => r.agg.prompt },
  { key: 'judge', label: 'Judge $', num: true, sort: r => r.agg.judgeCost },
];

/**
 * Runs-list state from the URL: #/runs?q=&setting=&control=&cheated=&failed=1|only&sort=&dir=desc.
 * A bare #/runs keeps the state from earlier in the session; #/runs?all=1 resets every filter.
 */
function runsStateFromQuery(q, settings, controls) {
  if (!q || ![...q.keys()].length) return;
  const pick = (v, allowed) => (allowed.includes(v) ? v : '');
  runsState.q = q.get('q') || '';
  runsState.setting = pick(q.get('setting') || '', settings);
  runsState.control = pick(q.get('control') || '', controls);
  runsState.cheated = pick(q.get('cheated') || '', ['yes', 'no', 'none']);
  const f = q.get('failed');
  runsState.failed = f === '1' || f === 'show' ? 'show' : f === 'only' ? 'only' : 'hide';
  const sk = q.get('sort');
  runsState.sortKey = RUN_COLS.some(c => c.key === sk) ? sk : 'setting';
  runsState.sortDir = q.get('dir') === 'desc' ? -1 : 1;
}

function runsQuery() {
  const s = runsState;
  return {
    q: s.q.trim() ? s.q : '', setting: s.setting, control: s.control, cheated: s.cheated,
    failed: s.failed === 'show' ? '1' : s.failed === 'only' ? 'only' : '', sort: s.sortKey === 'setting' ? '' : s.sortKey, dir: s.sortDir < 0 ? 'desc' : '',
  };
}

// Hidden roles: Okabe-Ito colour plus shape (circle worker, square regulator, triangle disregulator).
// Red and green stay reserved for outcomes.
export function roleBadge(role) {
  return h('span', { class: 'badge b-plain', title: 'hidden role in this swarm run' },
    ROLE_ORDER.includes(role) ? roleIcon(role, 10) : null, role);
}

/** A verdict counts only on an episode with a transcript (the rule of /api/summary); others are drawn grey. */
export function verdictCell(ep, yesKind = 'bad', noKind = 'good') {
  const v = ep && ep.verdict ? ep.verdict.cheated : null;
  if (v != null && !ep.has_transcript) {
    return h('span', { title: 'verdict on an episode with no transcript (harness crash or provider error); not counted in the summary' }, yn(v, 'plain', 'plain'));
  }
  return yn(v, yesKind, noKind);
}

function endedCell(ep) {
  if (ep.failure) {
    const limit = ep.failure === 'timeout' || ep.failure === 'max_turns';
    return badge(ep.failure, limit ? 'warn' : 'bad', limit ? 'the sample hit this limit before the agent submitted' : (ep.error ? firstLine(ep.error, 300) : ''));
  }
  if (ep.ended_by === 'submit') return badge('submit', 'good', 'the agent called submit');
  return muted(ep.ended_by || DASH);
}

function registeredCell(agg) {
  if (agg.registered == null) return muted(DASH, 'small');
  if (!agg.registered) return badge('no', 'plain');
  return h('span', null, badge('yes', 'good'),
    agg.copied ? [' ', badge('copied', 'warn', `the registry entry carries ${agg.copied}’s message verbatim`)] : null);
}


/** The agents of one run, one row each. */
function episodesTable(run) {
  const eps = run.episodes || [];
  const cols = [['agent'], ['role'], ['calls', 'num'], ['ended by'], ['cheated'], ['rung'], ['saw honeypot'], ['attempted'], ['extracted'], ['disclosed'],
    ['prompt tok', 'num'], ['judge $', 'num'], ['links', 'hidden-name']];
  return h('table', { class: 'data compact eps-table' },
    h('thead', null, h('tr', null, cols.map(([c, cls]) => h('th', { class: cls === 'hidden-name' ? '' : cls, scope: 'col' }, cls === 'hidden-name' ? h('span', { class: 'visually-hidden' }, c) : c)))),
    h('tbody', null, eps.map(ep => {
      const v = ep.verdict || null;
      const u = ep.usage || {};
      const name = ep.orbit_agent || ep.episode;
      return h('tr', null,
        h('td', null, ep.has_transcript ? link(R.episode(run.run, ep.episode), name, 'ep-link')
          : h('span', { title: 'no transcript on disk' }, name, ' ', muted('(no transcript)', 'small'))),
        h('td', null, ep.role ? roleBadge(ep.role) : muted(DASH)),
        h('td', { class: 'num', title: `${fmtInt(ep.n_turns)} turns` }, fmtInt(ep.n_tool_calls)),
        h('td', null, endedCell(ep)),
        h('td', null, verdictCell(ep), v && (v.parse_ok === false || v.judge_error) ? [' ', badge('judge issue', 'warn', String(v.judge_error || 'parse failed'))] : null),
        h('td', null, ep.ladder && ep.ladder.rung ? rungBadge(ep.ladder.rung) : muted(DASH)),
        h('td', null, yn(v ? v.honey_pots_seen : null, 'warn')),
        h('td', null, yn(v ? v.attempted_access : null, 'bad', 'good')),
        h('td', null, yn(v ? v.extracted_submission : null, 'bad')),
        h('td', null, yn(v ? v.disclosed_access : null, 'good')),
        h('td', { class: 'num', title: `${fmtInt(u.input)} prompt, ${fmtInt(u.cache_read)} cache read, ${fmtInt(u.output)} completion` }, fmtCompact(u.input)),
        h('td', { class: 'num' }, v ? fmtUsd(v.judge_cost_usd) : DASH),
        h('td', { class: 'links-cell' },
          ep.has_transcript ? link(R.episode(run.run, ep.episode), 'transcript', 'small') : null,
          ep.orbit_agent ? [' ', link(R.context(run.run, { agent: ep.orbit_agent }), 'context', 'small')] : null));
    })));
}

function runRowCells(row) {
  const { run, setting, agg } = row;
  const td = (content, opts = {}) => h('td', { class: [opts.num && 'num', opts.cls], title: opts.title }, content);
  const frac = agg.judged ? agg.cheated / agg.judged : null;
  return [
    td([link(R.run(run.run), run.run, 'run-link'), run.failed ? [' ', badge('failed', 'bad', 'harness crash or provider error; not counted')] : null,
      run.mock ? [' ', badge('mock', 'plain', 'mock-model smoke test; not counted in the summary')] : null], { cls: 'wrap-cell' }),
    td([setting.label, setting.composition ? [' ', mono(setting.composition)] : null], { cls: 'wrap-cell' }),
    td(run.control || DASH),
    td(fmtInt(agg.agents), { num: true }),
    td(agg.judged ? h('span', { class: ['cheated-cell', agg.cheated && 'text-bad'] }, `${agg.cheated}/${agg.judged}`) : muted('not judged', 'small'),
      { num: true, title: frac == null ? 'no judged agent' : `${Math.round(frac * 100)}% of judged agents` }),
    td(agg.judged ? fmtInt(agg.seen) : DASH, { num: true }),
    td(agg.judged ? fmtInt(agg.attempted) : DASH, { num: true }),
    td(registeredCell(agg)),
    td(agg.ended || DASH, { cls: 'wrap-cell small' }),
    td(agg.hasDefense ? h('span', { class: agg.blocks ? 'has-blocks' : '' }, `${fmtInt(agg.blocks)} / ${fmtInt(agg.checks)}`) : DASH, { num: true }),
    td(fmtSecs(agg.wall), { num: true }),
    td(fmtCompact(agg.prompt), { num: true, title: fmtInt(agg.prompt) }),
    td(agg.judgeCost ? fmtUsd(agg.judgeCost) : DASH, { num: true }),
  ];
}

export async function viewRuns({ query: urlQ }, view) {
  const runs = await apiFresh(P.runs());
  let live = false;
  view.mounted(() => { live = true; document.title = 'Runs · results'; updateUrl(); });
  view.cleanup(() => { live = false; });
  const rows = runs.map(run => ({ run, setting: settingOf(run), agg: runAggregate(run) }));
  const settings = [...new Map(rows.map(r => [r.setting.key, r.setting])).values()].sort((a, b) => a.order - b.order || a.n - b.n);
  const controls = [...new Set(runs.map(r => r.control).filter(Boolean))].sort();
  runsStateFromQuery(urlQ, settings.map(s => s.key), controls);

  const summary = h('div', { class: 'summary-line' });
  const tbody = h('tbody');
  const ths = RUN_COLS.map(col => {
    const th = h('th', { class: [col.num && 'num', 'sortable'], title: col.title || 'click to sort', tabindex: '0', scope: 'col' }, col.label);
    const go = () => sortBy(col.key);
    th.addEventListener('click', go);
    th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    col.th = th;
    return th;
  });
  const nCols = RUN_COLS.length + 1;

  for (const row of rows) {
    const { run } = row;
    const open = openRuns.has(run.run);
    const btn = h('button', { type: 'button', class: 'expander', 'aria-expanded': open ? 'true' : 'false', 'aria-label': `agents of ${run.run}` }, open ? '▾' : '▸');
    const tr = h('tr', { class: ['run-row', run.failed && 'failed', open && 'open'], title: run.failed ? 'failed run (harness crash or provider error)' : null },
      h('td', { class: 'exp-cell' }, btn), runRowCells(row));
    const detailTd = h('td', { colspan: nCols });
    const detail = h('tr', { class: 'detail-row', hidden: !open }, detailTd);
    let built = false;
    const sync = () => {
      const isOpen = openRuns.has(run.run);
      if (isOpen && !built) { built = true; detailTd.appendChild(h('div', { class: 'detail-box' }, episodesTable(run))); }
      detail.hidden = !isOpen || tr.hidden;
      tr.classList.toggle('open', isOpen);
      btn.textContent = isOpen ? '▾' : '▸';
      btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    };
    const toggle = () => { if (openRuns.has(run.run)) openRuns.delete(run.run); else openRuns.add(run.run); sync(); };
    btn.addEventListener('click', e => { e.stopPropagation(); toggle(); });
    tr.addEventListener('click', e => {
      if (e.target.closest('a, button')) return;
      const sel = window.getSelection && window.getSelection();
      if (sel && String(sel).length) return;
      toggle();
    });
    tr.dataset.run = run.run;
    row.tr = tr;
    row.detail = detail;
    row.sync = sync;
    row.hay = [run.run, run.control, row.setting.label, row.setting.composition, run.model, run.monitor_model, (run.defenses || []).join(' '),
      ...(run.episodes || []).flatMap(ep => [ep.orbit_agent, ep.failure, ep.role, ep.verdict && ep.verdict.summary])]
      .filter(Boolean).join(' \n ').toLowerCase();
    sync();
    tbody.append(tr, detail);
  }

  function band() {
    let alt = false;
    for (const row of rows) {
      if (row.tr.hidden) continue;
      alt = !alt;
      row.tr.classList.toggle('band', alt);
      row.detail.classList.toggle('band', alt);
    }
  }

  function apply() {
    const q = runsState.q.trim().toLowerCase();
    let shown = 0;
    let agents = 0;
    let judged = 0;
    let cheated = 0;
    let failedHidden = 0;
    for (const row of rows) {
      const { run, agg } = row;
      let ok = true;
      if (runsState.failed === 'hide' && run.failed) { ok = false; failedHidden++; }
      if (runsState.failed === 'only' && !run.failed) ok = false;
      if (ok && runsState.setting && row.setting.key !== runsState.setting) ok = false;
      if (ok && runsState.control && run.control !== runsState.control) ok = false;
      if (ok && runsState.cheated) {
        if (runsState.cheated === 'yes' && !(agg.cheated > 0)) ok = false;
        if (runsState.cheated === 'no' && !(agg.judged > 0 && agg.cheated === 0)) ok = false;
        if (runsState.cheated === 'none' && agg.judged > 0) ok = false;
      }
      if (ok && q && !row.hay.includes(q)) ok = false;
      row.tr.hidden = !ok;
      row.sync();
      if (ok) {
        shown++;
        agents += agg.agents;
        if (!run.failed) { judged += agg.judged; cheated += agg.cheated; }
      }
    }
    band();
    summary.replaceChildren(
      h('strong', null, plural(shown, 'run')), ` shown, ${plural(agents, 'agent')} · `,
      h('strong', { class: cheated ? 'text-bad' : '' }, `${fmtInt(cheated)} cheated`), ` of ${fmtInt(judged)} judged`,
      runsState.failed === 'hide' && failedHidden ? ` · ${plural(failedHidden, 'failed run')} hidden` : '',
      runsState.failed !== 'hide' ? ' · failed runs are never counted' : '');
    updateUrl();
  }

  function updateUrl() {
    if (!live) return;
    const target = R.runs(runsQuery());
    setHashQuietly(target);
  }

  function sortBy(key) {
    if (runsState.sortKey === key) runsState.sortDir = -runsState.sortDir;
    else { runsState.sortKey = key; runsState.sortDir = 1; }
    applySort();
  }

  function applySort() {
    const col = RUN_COLS.find(c => c.key === runsState.sortKey) || RUN_COLS[1];
    const order = rows.slice();
    order.sort((a, b) => cmpNullsLast(col.sort(a), col.sort(b), runsState.sortDir) || a.run.run.localeCompare(b.run.run, 'en', { numeric: true }));
    for (const r of order) tbody.append(r.tr, r.detail);
    for (const c of RUN_COLS) {
      c.th.classList.toggle('sorted', c === col);
      c.th.dataset.dir = c === col ? (runsState.sortDir > 0 ? 'asc' : 'desc') : '';
      c.th.setAttribute('aria-sort', c === col ? (runsState.sortDir > 0 ? 'ascending' : 'descending') : 'none');
    }
    band();
    updateUrl();
  }

  const search = h('input', {
    type: 'search', placeholder: 'Search run, control, agent, role, judge summary…', value: runsState.q,
    class: 'search', 'aria-label': 'Search runs',
  });
  search.addEventListener('input', debounce(() => { runsState.q = search.value; apply(); }, 80));
  const openAll = h('button', { type: 'button', class: 'btn btn-small', onclick: () => {
    const anyClosed = rows.some(r => !r.tr.hidden && !openRuns.has(r.run.run));
    for (const r of rows) { if (r.tr.hidden) continue; if (anyClosed) openRuns.add(r.run.run); else openRuns.delete(r.run.run); r.sync(); }
    openAll.textContent = anyClosed ? 'close all' : 'open all';
  } }, 'open all');

  const filters = h('div', { class: 'filters' },
    search,
    labeled('setting', select([['', 'all'], ...settings.map(s => [s.key, s.label])], runsState.setting, v => { runsState.setting = v; apply(); })),
    labeled('control', select([['', 'all'], ...controls], runsState.control, v => { runsState.control = v; apply(); })),
    labeled('cheated', select([['', 'any'], ['yes', 'at least one agent'], ['no', 'none of the judged'], ['none', 'not judged']], runsState.cheated,
      v => { runsState.cheated = v; apply(); })),
    labeled('failed runs', select([['hide', 'hidden'], ['show', 'shown'], ['only', 'only failed']], runsState.failed, v => { runsState.failed = v; apply(); })),
    openAll);

  const table = h('table', { class: 'data runs-table' }, h('thead', null, h('tr', null, h('th', { class: 'exp-cell', scope: 'col' }, h('span', { class: 'visually-hidden' }, 'agents')), ths)), tbody);
  const page = h('div', { class: 'page wide' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Runs'),
      muted(`${plural(runs.length, 'run')}. A row opens to its agents; the run name opens the run page (metadata, board, defenses, timeline, context windows, sample).`)),
    filters, summary,
    h('div', { class: 'table-wrap runs-wrap' }, table));
  applySort();
  apply();
  return page;
}
