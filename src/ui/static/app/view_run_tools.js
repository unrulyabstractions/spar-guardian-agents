/* The run page's analysis tool cards (tools/ at the repository root): one card per tool whose summary the run holds.
 *
 *   import { toolsView } from './view_run_tools.js';
 *   section('Analysis tools', toolsView(run, d.tools));
 */
import { DASH, appendAll, asText, fmtInt, fmtTime, h, plural } from './dom.js';
import { P, fetchJson } from './api.js';
import { badge, dataTable, kv, lazyDetails, longText, mono, muted, yn } from './components.js';

// The analysis tools (tools/ at the repository root): each writes runs/<run>/tools/<tool>/summary.json and its own
// files, served by /api/runs/<run>/tools/<tool>/<file>. One card per tool present, in this order.
const TOOL_CARDS = [['scout', 'Scout', scoutCard], ['graphs', 'Network', networkCard], ['transect', 'Transect', transectCard],
  ['contagion', 'Contagion', contagionCard], ['failures', 'Failures', failuresCard], ['docent', 'Docent', docentCard],
  ['hawk', 'Hawk', hawkCard]];

/** The path inside runs/<run>/tools/<tool>/ of an absolute path a summary recorded, or null when it lies elsewhere. */
function toolRelPath(run, tool, abs) {
  if (!abs) return null;
  const marker = `${run}/tools/${tool}/`;
  const i = String(abs).lastIndexOf(marker);
  return i < 0 ? null : String(abs).slice(i + marker.length);
}

function toolLink(run, tool, file, label, attrs) {
  return h('a', { href: P.toolFile(run, tool, file), target: '_blank', rel: 'noopener', ...attrs }, label || file);
}

function toolFigure(run, tool, file, alt) {
  const src = P.toolFile(run, tool, file);
  return h('figure', { class: 'tool-fig' }, h('a', { href: src, target: '_blank', rel: 'noopener' }, h('img', { src, alt })));
}

const toolTable = (cols, rows) => dataTable(cols, rows, { cls: 'compact tool-table' });

const toolNum = (x, digits = 3) => (x == null ? null : Number.isInteger(x) ? String(x) : Number(x).toFixed(digits));
const toolPct = x => (x == null ? null : `${Math.round(x * 100)}%`);
const toolList = v => (Array.isArray(v) ? v.join(', ') : v == null ? null : String(v));

/** Every tool card present, or the one muted line that says how to make them. */
export function toolsView(run, tools) {
  const names = Object.keys(tools || {});
  if (!names.length) {
    return muted(`No analysis yet: make tools-setup, then make scout RUN=${run} (and graph, transect, contagion, failures, docent, hawk).`);
  }
  const known = TOOL_CARDS.filter(([t]) => tools[t]);
  const other = names.filter(t => !TOOL_CARDS.some(([k]) => k === t)).sort();
  return h('div', { class: 'tool-cards' },
    known.map(([t, title, build]) => toolCard(run, t, title, tools[t], build)),
    other.map(t => toolCard(run, t, t, tools[t], genericToolCard)));
}

function toolCard(run, tool, title, s, build) {
  let body;
  if (s && s.error && !s.tool) body = h('p', { class: 'tool-line' }, badge('summary unreadable', 'bad'), ' ', muted(String(s.error)));
  else {
    try {
      body = build(run, s, tool);
    } catch (e) {
      body = badge('could not draw this summary', 'bad', String((e && e.message) || e));
    }
  }
  return h('div', { class: 'tool-card' },
    h('div', { class: 'tool-head' }, h('h3', null, title),
      muted(`written ${fmtTime(s && s.written_at)}`, 'small'), toolLink(run, tool, 'summary.json', 'summary.json', { class: 'small' })),
    body);
}

function genericToolCard(run, s) {
  const scalars = Object.entries(s).filter(([k, v]) => !['tool', 'run', 'written_at'].includes(k) && (v == null || typeof v !== 'object'));
  return h('div', null, kv(scalars.map(([k, v]) => [k, String(v)]), 'kv-grid'),
    lazyDetails('summary.json', () => longText(JSON.stringify(s, null, 2), { lines: 40 }), { cls: 'sub' }));
}

// Scout: one row per agent, one column per scanner. A found signal shows the model's message number (M) and the
// timeline round; its explanation and quote are on hover and in the explanations below.
const SCOUT_KIND = { honeypot_seen: 'warn', regulator_intervened: 'good' };

function scoutCard(run, s) {
  const signals = s.scanners || [];
  const agents = Object.keys(s.agents || {}).sort();
  const val = s.validation || {};
  const judged = val.agents || {};
  const tipOf = f => [f.explanation, f.quote ? `quote: ${f.quote}` : null].filter(Boolean).join('\n\n');
  const cell = (sig, f) => {
    if (!f) return null;
    if (f.error) return badge('error', 'warn', String(f.error));
    if (!f.found) return h('span', { title: tipOf(f) }, badge('no', 'plain'));
    const where = [f.step != null ? `M${f.step}` : null, f.round != null ? `round ${f.round}` : null].filter(Boolean).join(' · ');
    return h('span', { title: tipOf(f) }, badge('yes', SCOUT_KIND[sig] || 'bad'), where ? [' ', muted(where, 'small')] : null);
  };
  const cols = ['agent', 'CheatBench judge: cheated', 'onset round', ...signals.map(sig => sig.replace(/^honeypot_/, 'honeypot ').replace(/_/g, ' '))];
  const rows = agents.map(a => {
    const j = (judged[a] || {}).judge || {};
    const onset = (s.onset || {})[a];
    return [a, yn(j.cheated, 'bad', 'good'), onset == null ? null : String(onset), ...signals.map(sig => cell(sig, s.agents[a][sig]))];
  });
  const agreement = Object.entries(val.agreement || {});
  const agreeLine = agreement.length ? h('p', { class: 'tool-line' }, 'Agreement with the judge: ',
    agreement.map(([k, v]) => [badge(`${k.replace('~', ' ~ ')} ${v.agree}/${v.agree + v.disagree + v.unknown}`,
      v.disagree ? 'warn' : 'good', `${v.agree} agree, ${v.disagree} disagree, ${v.unknown} unknown`), ' '])) : null;
  const explanations = () => h('div', null, agents.map(a => h('div', null, h('h4', { class: 'sub-h' }, a),
    kv(signals.map(sig => {
      const f = s.agents[a][sig] || {};
      const head = f.error ? `error: ${f.error}` : f.found ? `found (M${f.step ?? '?'}, round ${f.round ?? '?'})` : 'not found';
      return [sig.replace(/_/g, ' '), h('div', null, h('strong', null, head), f.explanation ? h('p', { class: 'tool-expl' }, f.explanation) : null,
        f.quote ? h('pre', { class: 'pre tool-quote' }, f.quote) : null)];
    })))));
  const files = s.files || [];
  const scan = s.scan || {};
  return h('div', null,
    kv([['model', s.model || 'grep only'], ['scan complete', scan.complete == null ? null : yn(scan.complete, 'good', 'warn')],
      ['scan errors', scan.errors ? String(scan.errors.length) : null],
      ['files', files.length ? h('span', null, files.map(f => [toolLink(run, 'scout', f), ' '])) : null]], 'kv-grid'),
    toolTable(cols, rows),
    agreeLine,
    lazyDetails('explanations and quotes', explanations, { cls: 'sub' }),
    files.includes('onset.png') ? lazyDetails('onset figure', () => toolFigure(run, 'scout', 'onset.png', 'Scout onset rounds'), { cls: 'sub' }) : null);
}

// Network (tools/graphs): who read whom on the board, and, with scout's onsets, who started and how it spread.
function networkCard(run, s) {
  const files = s.files || [];
  const degree = s.degree || {};
  const check = s.read_graph_check;
  const nodes = (s.nodes || []).map(n => [n.agent, n.role, yn(n.judge_cheated, 'bad', 'good'), n.onset == null ? null : String(n.onset),
    n.discovery_round == null ? null : String(n.discovery_round), String(n.posts ?? 0), String(n.reads ?? 0),
    String((degree[n.agent] || {}).in ?? 0), String((degree[n.agent] || {}).out ?? 0)]);
  const edges = (s.edges || []).map(e => [`${e.source} → ${e.target}`, e.kind, String(e.weight), e.first_round == null ? null : String(e.first_round),
    e.distinct_posts == null ? null : String(e.distinct_posts)]);
  const exposure = Object.entries(s.exposure_before_onset || {}).flatMap(([a, list]) => (list || []).map(x =>
    [a, x.sender, `#${x.number}`, x.round == null ? null : String(x.round), x.read_round == null ? null : String(x.read_round),
      yn(x.exposed_to_cheater, 'bad', 'plain')]));
  const spread = (s.spread_order || []).map(x => [x.agent, String(x.round ?? DASH), x.step == null ? null : String(x.step),
    x.turn == null ? null : String(x.turn)]);
  const contagion = (s.contagion_edges || []).map(e => [`${e.source} → ${e.target}`, toolList(e.posts)]);
  const onsetParts = s.onset_source
    ? [h('h4', { class: 'sub-h' }, 'Spread'), kv([['who started', s.who_started]]),
      spread.length ? toolTable(['agent', 'onset round', 'step', 'turn'], spread) : muted('No agent has an onset.'),
      h('h4', { class: 'sub-h' }, 'Contagion edges'),
      contagion.length ? toolTable(['edge', 'posts read before onset'], contagion) : muted('No agent read a post before its own onset.'),
      h('h4', { class: 'sub-h' }, 'Exposure before onset'),
      exposure.length ? toolTable(['agent', 'sender', 'post', 'post round', 'read round', 'sender cheated'], exposure)
        : muted('No posts read before an onset.')]
    : [muted('No onsets in this summary, so who started, the spread order, contagion edges and exposure are empty. Run scout, then graph.')];
  return h('div', null,
    files.includes('graph.png') ? toolFigure(run, 'graphs', 'graph.png', 'Board read graph') : null,
    kv([['nodes', String(s.n_nodes ?? DASH)], ['edges', String(s.n_edges ?? DASH)], ['density', toolNum(s.density)], ['layout', s.layout],
      ['onset source', s.onset_source || 'none'],
      ['read graph matches run.json', check ? yn(check.matches, 'good', 'warn') : null],
      ['files', h('span', null, files.includes('graph.gexf') ? [toolLink(run, 'graphs', 'graph.gexf', 'graph.gexf (open in Gephi)', { download: 'graph.gexf' }), ' '] : null,
        files.filter(f => f !== 'graph.gexf' && f !== 'graph.png').map(f => [toolLink(run, 'graphs', f), ' ']))]], 'kv-grid'),
    toolTable(['agent', 'role', 'CheatBench judge: cheated', 'onset', 'discovery round', 'posts', 'reads', 'in', 'out'], nodes),
    edges.length ? toolTable(['edge', 'kind', 'weight', 'first round', 'distinct posts'], edges) : muted('No edges.'),
    onsetParts);
}

// Transect: one report per agent and the phases its judge labelled. Turns are Transect's (0-based); rounds come from the timeline.
function transectCard(run, s) {
  const reports = s.reports || {};
  const agents = s.agents || {};
  const reportLinks = Object.entries(reports).filter(([, p]) => p).map(([k, p]) => {
    const rel = toolRelPath(run, 'transect', p);
    const label = k === 'run' ? 'merged sample report (structural)' : `${k} report`;
    return rel ? [toolLink(run, 'transect', rel, label, { class: 'btn btn-small' }), ' '] : [mono(p), ' '];
  });
  const span = (a, b) => (a == null ? null : a === b ? String(a) : `${a}–${b}`);
  const phaseRows = Object.entries(s.phases || {}).sort(([a], [b]) => a.localeCompare(b)).flatMap(([a, list]) =>
    (list || []).map((p, i) => [i === 0 ? a : h('span'), i === 0 ? yn((agents[a] || {}).judge_cheated, 'bad', 'good') : h('span'),
      badge(p.label, /^exploit/.test(p.label) ? 'bad' : 'plain'), span(p.start_turn, p.end_turn), span(p.start_round, p.end_round)]));
  const judge = s.judge;
  const merged = s.merged_sample || {};
  return h('div', null,
    kv([['judge', judge ? judge.model : 'structural only (no model calls)'],
      ['rolls', judge ? String(judge.k_rolls) : undefined], ['verify', judge ? yn(judge.verify, 'plain', 'plain') : undefined],
      ['judge tokens', judge && judge.totals ? fmtInt(judge.totals.total_tokens) : undefined],
      ['merged sample accepted', merged.sample ? h('span', { title: (merged.errors || []).join('\n') }, yn(merged.accepted, 'good', 'warn')) : undefined]], 'kv-grid'),
    h('div', { class: 'links' }, reportLinks),
    phaseRows.length ? toolTable(['agent', 'CheatBench judge: cheated', 'phase', 'turns', 'rounds'], phaseRows)
      : muted(s.structural_only ? 'Structural run: no phases were labelled.' : 'No phases.'));
}

// Contagion: the observed shares and the SI, threshold and SIR fits, or why the fit was skipped.
function contagionCard(run, s) {
  const files = s.files || [];
  const obs = s.observed_final || {};
  const fitRows = Object.entries(s.fits || {}).flatMap(([behaviour, fits]) => (fits && fits.skipped
    ? [[behaviour, muted('skipped'), fits.skipped.join('; '), null, null]]
    : Object.entries(fits || {}).map(([model, f]) => [behaviour, model,
      Object.entries(f.parameters || {}).map(([k, v]) => `${k} ${toolNum(v, 4)}`).join(', '), toolPct(f.final_size), toolNum(f.ssr, 4)])));
  const allSkipped = Object.values(s.fits || {}).every(fits => fits && fits.skipped);
  return h('div', null,
    files.includes('contagion.png') ? toolFigure(run, 'contagion', 'contagion.png', 'Contagion curves and fits') : null,
    kv([['graph source', s.graph_source], ['onset source', s.onset_source], ['agents', String(s.n_agents ?? DASH)],
      ['regulators', String(s.n_regulators ?? DASH)], ['rounds', String(s.rounds ?? DASH)], ['simulations', String(s.simulations ?? DASH)],
      ['seed', String(s.seed ?? DASH)], ['dishonest at the end', toolPct(obs.dishonest)], ['honest at the end', toolPct(obs.honest)]], 'kv-grid'),
    s.degenerate ? h('p', { class: 'tool-line' }, badge('degenerate', 'warn', 'the fits are not meaningful on this run'), ' ', muted(s.reason || '')) : null,
    // when every fit was skipped, the degenerate note above already gives the reasons
    allSkipped && s.degenerate ? null
      : fitRows.length ? toolTable(['behaviour', 'model', 'parameters', 'final size', 'SSR'], fitRows) : muted('No fits in this summary.'),
    (s.notes || []).length ? h('ul', { class: 'plain-list' }, s.notes.map(n => h('li', null, n))) : null,
    h('div', { class: 'links' }, files.filter(f => f !== 'contagion.png').map(f => [toolLink(run, 'contagion', f), ' '])));
}

// Failures: MAST's modes marked yes and Who&When's decisive agent and step by three methods. The mode names and the
// reasons are in mast.json and who_when.json, fetched when the card is drawn.
const WHO_WHEN_METHODS = [['all_at_once', 'all at once'], ['step_by_step', 'step by step'], ['binary_search', 'binary search']];

function failuresCard(run, s) {
  const holder = h('div');
  const draw = (mast, whoWhen) => {
    const m = s.mast;
    // a mode's name as the reply gave it (gpt-6-luna swaps 3.2 and 3.3 between replies), else the taxonomy's
    const names = (mast && mast.names) || {};
    const replyNames = (mast && mast.reply_names) || {};
    const nameOf = k => replyNames[k] || names[k];
    const yes = m ? (m.yes_strict || m.yes || []) : [];
    const mastPart = !m ? muted('MAST was not run.') : h('div', null,
      kv([['task completed', m.task_completed == null ? null : yn(m.task_completed === 'yes', 'good', 'warn')],
        ['modes marked yes', yes.length ? h('span', null, yes.map(k => [badge(nameOf(k) ? `${k} ${nameOf(k)}` : k, 'bad'), ' '])) : muted('none')],
        ['parser disagreements', (m.disagreements || []).length ? badge(m.disagreements.join(', '), 'warn', 'the notebook parser and the strict parser differ') : undefined]]),
      m.summary ? h('p', { class: 'verdict-summary' }, m.summary) : null);
    const ww = s.who_when;
    const detail = whoWhen || {};
    const rows = !ww ? [] : WHO_WHEN_METHODS.filter(([k]) => ww[k]).map(([k, label]) => {
      const r = ww[k];
      const d = detail[k] || {};
      const stepAgent = d.step_agent && d.step_agent !== r.agent ? muted(` (step ${r.step} belongs to ${d.step_agent})`, 'small') : null;
      return [label, h('span', null, r.agent || DASH, stepAgent), r.step == null ? null : String(r.step), r.round == null ? null : String(r.round),
        r.kind, d.reason ? h('span', { class: 'tool-expl' }, d.reason) : null];
    });
    holder.textContent = '';
    appendAll(holder, [h('h4', { class: 'sub-h' }, `MAST (${s.model || '?'})`), mastPart,
      h('h4', { class: 'sub-h' }, 'Who&When'), ww ? toolTable(['method', 'agent', 'step', 'round', 'kind', 'reason'], rows) : muted('Who&When was not run.'),
      h('div', { class: 'links' }, s.mast ? [toolLink(run, 'failures', 'mast.json'), ' '] : null,
        ww ? toolLink(run, 'failures', 'who_when.json') : null)]);
  };
  // the detail files exist only for the parts that ran; fetching a missing one would log a 404
  const detailOf = (part, file) => (s[part] ? fetchJson(P.toolFile(run, 'failures', file)).catch(() => null) : Promise.resolve(null));
  draw(null, null);
  Promise.all([detailOf('mast', 'mast.json'), detailOf('who_when', 'who_when.json')]).then(([mast, whoWhen]) => {
    try {
      draw(mast, whoWhen);
    } catch (e) {
      appendAll(holder, [badge('could not draw the failures details', 'bad', String((e && e.message) || e))]);
    }
  });
  return holder;
}

// Docent: the collection after an upload, or what a dry run prepared.
function docentCard(run, s) {
  const runs = (s.agent_runs || []).map(r => [r.name, r.kind, r.kind === 'agent' ? yn(r.cheated, 'bad', 'good') : null, String(r.messages ?? DASH)]);
  const head = s.collection_url
    ? kv([['collection', h('a', { href: s.collection_url, target: '_blank', rel: 'noopener' }, s.collection_name || s.collection_id || s.collection_url)],
      ['collection id', s.collection_id ? mono(s.collection_id) : null], ['upload status', s.upload_status ? String(asText(s.upload_status)) : null],
      ['agent runs', String(s.n_agent_runs ?? runs.length)]], 'kv-grid')
    : h('p', { class: 'tool-line' }, badge(s.dry_run ? 'dry run' : 'not uploaded', 'warn'), ' ',
      `${plural(s.n_agent_runs ?? runs.length, 'AgentRun')} prepared${s.board ? ' (with the board)' : ''}, not uploaded. `,
      s.agent_runs_file ? toolLink(run, 'docent', s.agent_runs_file) : null);
  return h('div', null, head, runs.length ? toolTable(['agent run', 'kind', 'cheated', 'messages'], runs) : null);
}

// Hawk: the scan config and whether the scan ran.
function hawkCard(run, s) {
  const rel = toolRelPath(run, 'hawk', s.config);
  const ran = s.ran ? badge(s.returncode === 0 ? 'ran' : `ran, exit ${s.returncode}`, s.returncode === 0 ? 'good' : 'bad')
    : badge('config only, not run', 'plain');
  return h('div', null,
    kv([['config', rel ? toolLink(run, 'hawk', rel, s.config) : mono(s.config)], ['scan', ran],
      ['model', s.model], ['source', s.source ? `${s.source.kind}: ${s.source.path}` : null],
      ['scanners', toolList(s.scanners)], ['results', (s.results || []).length ? h('span', null, s.results.map(r => [mono(r), ' '])) : null]]),
    s.command ? lazyDetails('command', () => longText(s.command, { lines: 6 }), { cls: 'sub' }) : null,
    (s.stdout_tail || []).length ? lazyDetails('output (last lines)', () => longText(s.stdout_tail.join('\n'), { lines: 40 }), { cls: 'sub' }) : null,
    (s.notes || []).length ? lazyDetails('notes', () => h('ul', { class: 'plain-list' }, s.notes.map(n => h('li', null, n))), { cls: 'sub' }) : null);
}
