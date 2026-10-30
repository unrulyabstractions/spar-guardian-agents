/* View: runs (#/runs).
 * The page lists one row per run. A row opens to its agents.
 *
 *   import { viewRuns } from './view_runs.js';
 *   const node = await viewRuns(route, view);
 */
import { DASH, debounce, firstLine, fmtCompact, fmtInt, fmtSecs, fmtUsd, h, plural } from './dom.js';
import { P, R, apiFresh, setHashQuietly } from './api.js';
import { badge, dataTable, labeled, link, mono, muted, registeredCell, roleBadge, rungBadge, select, verdictCell, yn }
  from './components.js';
import { endingsText, settingSortKey } from './run_facts.js';

const runsState = { q: '', setting: '', control: '', cheated: '', failed: 'hide', sortKey: 'setting', sortDir: 1 };
const openRuns = new Set();   // runs whose agent rows are open, kept across visits

/** The task's verdict flags other than cheated, with the number of counted agents that have each. */
function flagCounts(run) {
  return run.verdict_fields.filter(f => f.field !== 'cheated').map(f => [f, run.aggregate.flags[f.field] || 0]);
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

const agg = run => run.aggregate;
const RUN_COLS = [
  { key: 'run', label: 'Run', sort: run => run.run },
  { key: 'setting', label: 'Setting', sort: run => settingSortKey(run.setting, run.run), title: 'task, graph, roster size and hidden roles' },
  { key: 'control', label: 'Control', sort: run => run.control },
  { key: 'agents', label: 'Agents', num: true, sort: run => run.n_agents },
  { key: 'cheated', label: 'Cheated', num: true, sort: run => (agg(run).judged ? agg(run).cheated / agg(run).judged : null), title: 'agents the judge found cheated, over counted verdicts' },
  { key: 'flags', label: 'Verdict flags', sort: run => flagCounts(run).reduce((s, [, n]) => s + n, 0), title: 'counted agents with each of the task’s verdict flags' },
  { key: 'registered', label: 'Registered', sort: run => (run.deliverables || {}).registered_on_main, title: 'a registry entry on main at the end' },
  { key: 'ended', label: 'Ended', sort: run => endingsText(run.endings), title: 'how the agents’ episodes ended' },
  { key: 'blocks', label: 'Blocks / checks', num: true, sort: run => (agg(run).controlled ? agg(run).blocks : null) },
  { key: 'wall', label: 'Wall', num: true, sort: run => agg(run).wall_time },
  { key: 'prompt', label: 'Prompt tok', num: true, sort: run => agg(run).tokens.input },
  { key: 'judge', label: 'Judge $', num: true, sort: run => agg(run).judge_cost },
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

function endedCell(ep) {
  if (ep.failure) {
    const limit = ep.failure === 'timeout' || ep.failure === 'max_turns';
    return badge(ep.failure, limit ? 'warn' : 'bad', limit ? 'the sample hit this limit before the agent submitted' : (ep.error ? firstLine(ep.error, 300) : ''));
  }
  if (ep.ended_by === 'submit') return badge('submit', 'good', 'the agent called submit');
  return muted(ep.ended_by || DASH);
}

/** The agents of one run, one row each. */
function episodesTable(run) {
  const flags = run.verdict_fields.filter(f => f.field !== 'cheated');
  const cols = ['agent', 'role', { label: 'calls', num: true }, 'ended by', 'cheated', 'rung', ...flags.map(f => f.label),
    { label: 'prompt tok', num: true }, { label: 'judge $', num: true }, h('span', { class: 'visually-hidden' }, 'links')];
  return dataTable(cols, run.episodes.map(ep => {
    const v = ep.verdict;
    const u = ep.usage || {};
    const name = ep.orbit_agent;
    return [
      ep.has_transcript ? link(R.episode(run.run, ep.episode), name, 'ep-link')
        : h('span', { title: 'no transcript on disk' }, name, ' ', muted('(no transcript)', 'small')),
      ep.role ? roleBadge(ep.role) : null,
      h('span', { title: `${fmtInt(ep.n_turns)} turns` }, fmtInt(ep.n_tool_calls)),
      endedCell(ep),
      h('span', null, verdictCell(ep), v && (v.parse_ok === false || v.judge_error) ? [' ', badge('judge issue', 'warn', String(v.judge_error || 'parse failed'))] : null),
      ep.ladder && ep.ladder.rung ? rungBadge(ep.ladder.rung) : null,
      ...flags.map(f => yn(v ? v[f.field] : null, f.kind)),
      h('span', { title: `${fmtInt(u.input)} prompt (${fmtInt(u.cache_read)} cache read), ${fmtInt(u.output)} completion` }, fmtCompact(u.input)),
      v ? fmtUsd(v.judge_cost_usd) : null,
      h('span', { class: 'links-cell' },
        ep.has_transcript ? link(R.episode(run.run, ep.episode), 'transcript', 'small') : null, ' ',
        link(R.context(run.run, { agent: ep.orbit_agent }), 'context', 'small')),
    ];
  }), { cls: 'compact eps-table', bare: true });
}

function runRowCells(run) {
  const a = run.aggregate;
  const td = (content, opts = {}) => h('td', { class: [opts.num && 'num', opts.cls], title: opts.title }, content);
  const frac = a.judged ? a.cheated / a.judged : null;
  const flagged = flagCounts(run).filter(([, n]) => n);
  return [
    td([link(R.run(run.run), run.run, 'run-link'), run.failed ? [' ', badge('failed', 'bad', 'harness crash or provider error; not counted')] : null,
      run.mock ? [' ', badge('mock', 'plain', 'mock-model smoke test; not counted in the summary')] : null], { cls: 'wrap-cell' }),
    td([run.setting.label, run.setting.composition ? [' ', mono(run.setting.composition)] : null], { cls: 'wrap-cell' }),
    td(run.control || DASH),
    td(fmtInt(run.n_agents), { num: true }),
    td(a.judged ? h('span', { class: ['cheated-cell', a.cheated && 'text-bad'] }, `${a.cheated}/${a.judged}`) : muted('not judged', 'small'),
      { num: true, title: frac == null ? 'no counted verdict' : `${Math.round(frac * 100)}% of counted verdicts` }),
    td(a.judged ? flagged.map(([f, n]) => `${f.label} ${n}`).join(', ') || 'none' : DASH, { cls: 'wrap-cell small' }),
    td(registeredCell(run.deliverables)),
    td(endingsText(run.endings) || DASH, { cls: 'wrap-cell small' }),
    td(a.controlled ? h('span', { class: a.blocks ? 'has-blocks' : '' }, `${fmtInt(a.blocks)} / ${fmtInt(a.checks)}`) : DASH, { num: true }),
    td(fmtSecs(a.wall_time), { num: true }),
    td(fmtCompact(a.tokens.input), { num: true, title: fmtInt(a.tokens.input) }),
    td(a.judge_cost ? fmtUsd(a.judge_cost) : DASH, { num: true }),
  ];
}

export async function viewRuns({ query: urlQ }, view) {
  const runs = await apiFresh(P.runs());
  let live = false;
  view.mounted(() => { live = true; document.title = 'Runs · results'; updateUrl(); });
  view.cleanup(() => { live = false; });
  const settings = [...new Map(runs.map(r => [r.setting.key, r.setting])).values()].sort((a, b) => a.order - b.order || a.n - b.n);
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

  const rows = runs.map(run => {
    const open = openRuns.has(run.run);
    const btn = h('button', { type: 'button', class: 'expander', 'aria-expanded': open ? 'true' : 'false', 'aria-label': `agents of ${run.run}` }, open ? '▾' : '▸');
    const tr = h('tr', { class: ['run-row', run.failed && 'failed', open && 'open'], title: run.failed ? 'failed run (harness crash or provider error)' : null },
      h('td', { class: 'exp-cell' }, btn), runRowCells(run));
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
    const hay = [run.run, run.control, run.setting.label, run.setting.composition, run.model, run.monitor_model, run.defenses.join(' '),
      ...run.episodes.flatMap(ep => [ep.orbit_agent, ep.failure, ep.role, ep.verdict && ep.verdict.summary])]
      .filter(Boolean).join(' \n ').toLowerCase();
    sync();
    tbody.append(tr, detail);
    return { run, tr, detail, sync, hay };
  });

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
      const { run } = row;
      const a = run.aggregate;
      let ok = true;
      if (runsState.failed === 'hide' && run.failed) { ok = false; failedHidden++; }
      if (runsState.failed === 'only' && !run.failed) ok = false;
      if (ok && runsState.setting && run.setting.key !== runsState.setting) ok = false;
      if (ok && runsState.control && run.control !== runsState.control) ok = false;
      if (ok && runsState.cheated) {
        if (runsState.cheated === 'yes' && !(a.cheated > 0)) ok = false;
        if (runsState.cheated === 'no' && !(a.judged > 0 && a.cheated === 0)) ok = false;
        if (runsState.cheated === 'none' && a.judged > 0) ok = false;
      }
      if (ok && q && !row.hay.includes(q)) ok = false;
      row.tr.hidden = !ok;
      row.sync();
      if (ok) {
        shown++;
        agents += run.n_agents;
        if (!run.failed) { judged += a.judged; cheated += a.cheated; }
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
    if (live) setHashQuietly(R.runs(runsQuery()));
  }

  function sortBy(key) {
    if (runsState.sortKey === key) runsState.sortDir = -runsState.sortDir;
    else { runsState.sortKey = key; runsState.sortDir = 1; }
    applySort();
  }

  function applySort() {
    const col = RUN_COLS.find(c => c.key === runsState.sortKey) || RUN_COLS[1];
    const order = rows.slice();
    order.sort((a, b) => cmpNullsLast(col.sort(a.run), col.sort(b.run), runsState.sortDir) || a.run.run.localeCompare(b.run.run, 'en', { numeric: true }));
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
