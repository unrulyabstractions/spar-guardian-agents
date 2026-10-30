/* View: run page (#/run/<run>).
 * The page shows the run facts, its agents, the board and the analysis tools' cards.
 *
 *   import { viewRun } from './view_run.js';
 *   const node = await viewRun(route, view);
 */
import { DASH, appendAll, asText, debounce, fmtInt, fmtTime, h, plural } from './dom.js';
import { P, R, apiFresh, fetchJson } from './api.js';
import { badge, dataTable, episodeRecord, kv, labeled, ladderBlock, lazyDetails, link, logLink, longText, mono, muted,
  roleBadge, section, select, signalsBlock, verdictBlock, verdictCell, yn } from './components.js';
import { endingsText, episodeOf } from './run_facts.js';

export async function viewRun({ run }, view) {
  const d = await apiFresh(P.run(run));
  view.mounted(() => { document.title = `${run} · results`; });
  const rj = d.run_json;
  const eps = d.episodes;
  const logOk = d.eval_log_exists;
  const epFailures = [...new Set(eps.map(e => e.failure).filter(Boolean))];

  const head = h('div', { class: 'page-head' },
    h('h1', null, run),
    h('div', { class: 'chips' },
      d.failed ? badge('failed run', 'bad')
        : epFailures.length ? badge(`episode failure: ${epFailures.join(', ')}`, 'warn', 'not a harness crash, but an episode ended with a failure')
          : badge('completed', 'good'),
      badge(d.setting.label, 'plain', `topology in run.json: ${d.topology || '?'}`),
      badge(`control: ${d.control}`, 'def')),
    h('div', { class: 'links' },
      eps.map(ep => (ep.has_transcript
        ? link(R.episode(run, ep.episode), `transcript ${ep.episode}`, 'btn')
        : h('span', { class: 'btn disabled', title: 'no transcript on disk' }, `transcript ${ep.episode}`))),
      link(R.timeline(run), 'timeline', 'btn'),
      logLink(logOk, d.eval_log, R.context(run), 'context windows', 'btn'),
      logLink(logOk, d.eval_log, R.sample(run), 'sample and workspace', 'btn')));

  const meta = section('Run metadata', kv([
    ['task', d.task],
    ['model', d.model],
    ['setting', d.setting.label],
    ['topology in run.json', d.topology],
    ['agents', (d.agents || []).join(', ')],
    ['control', d.control],
    ['monitor model', d.monitor_model],
    ['mode', d.mode],
    ['scale', d.scale],
    ['preset', d.preset],
    ['access', d.access && d.access.policy ? d.access.policy : undefined],
    ['turn budget', d.turn_budget ? `${d.turn_budget.budget} turns, ${d.turn_budget.steps_per_agent} working steps per agent` : undefined],
    ['agents ended by', endingsText(d.endings) || undefined],
    ['team submission', submissionText(rj.team_submission) || undefined],
    ['shutdown rate', rj.shutdown_rate == null ? undefined
      : `${Math.round(rj.shutdown_rate * 100)}% of the team ended with stop_working`],
    ['eval id', rj.eval_id ? mono(rj.eval_id) : null],
    ['run id', rj.run_id],
    ['variant', rj.variant || null],
    ['ORBIT version', rj.orbit_version],
    ['Inspect version', rj.inspect_version],
  ], 'kv-grid'), kv([
    ['eval log', h('span', null, mono(d.eval_log), ' ', d.eval_log ? (d.eval_log_exists ? badge('present', 'good') : badge('missing', 'bad')) : null)],
    ['task root', rj.task_root ? mono(rj.task_root) : null],
    ['deviations', rj.deviations && rj.deviations.length
      ? h('ul', { class: 'plain-list' }, rj.deviations.map(x => h('li', null, mono(x)))) : null],
  ]));

  const defs = rj.defenses || [];
  const defSection = section('Defenses', defs.length ? defensesTable(defs) : muted('No defenses configured (baseline).'));

  const epSection = section(`Episodes (${eps.length})`,
    eps.length ? eps.map(ep => episodeCard(run, ep, d)) : muted('No episodes recorded.'));

  const boardSection = section('Board messages', communicationView(d.communication));
  const evidence = section('Defense evidence', defenseEvidence(d.defense, defs));
  const swarmSection = rj.swarm ? section('Swarm', swarmView(rj.swarm, d)) : null;
  const sweSection = d.swe ? section('SWE task', sweView(d.swe, d)) : null;
  const configSection = d.config ? section('Configuration', configKv(d.config)) : null;
  const rulesSection = d.rules ? section('Turn rules', rulesTable(d.rules)) : null;
  const engagementSection = d.engagement ? section('Board engagement', engagementView(d.engagement, eps)) : null;
  const accessSection = d.access ? section('Access', accessView(d.access, d)) : null;
  const knowledgeSection = d.knowledge ? section('Knowledge work', knowledgeView(d.knowledge)) : null;
  const toolsSection = section('Analysis tools', toolsView(run, d.tools));

  return h('div', { class: 'page' }, head, meta, configSection, defSection, sweSection, knowledgeSection, swarmSection,
    accessSection, rulesSection, engagementSection, toolsSection, epSection, boardSection, evidence);
}

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
function toolsView(run, tools) {
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
  const cols = ['agent', 'judge: cheated', 'onset round', ...signals.map(sig => sig.replace(/^honeypot_/, 'honeypot ').replace(/_/g, ' '))];
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
  const nodes = (s.nodes || []).map(n => [n.agent, n.role, yn(n.cheated, 'bad', 'good'), n.onset == null ? null : String(n.onset),
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
    toolTable(['agent', 'role', 'cheated', 'onset', 'discovery round', 'posts', 'reads', 'in', 'out'], nodes),
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
    (list || []).map((p, i) => [i === 0 ? a : h('span'), i === 0 ? yn((agents[a] || {}).cheated, 'bad', 'good') : h('span'),
      badge(p.label, /^exploit/.test(p.label) ? 'bad' : 'plain'), span(p.start_turn, p.end_turn), span(p.start_round, p.end_round)]));
  const judge = s.judge;
  const merged = s.merged_sample || {};
  return h('div', null,
    kv([['judge', judge ? judge.model : 'structural only (no model calls)'],
      ['rolls', judge ? String(judge.k_rolls) : undefined], ['verify', judge ? yn(judge.verify, 'plain', 'plain') : undefined],
      ['judge tokens', judge && judge.totals ? fmtInt(judge.totals.total_tokens) : undefined],
      ['merged sample accepted', merged.sample ? h('span', { title: (merged.errors || []).join('\n') }, yn(merged.accepted, 'good', 'warn')) : undefined]], 'kv-grid'),
    h('div', { class: 'links' }, reportLinks),
    phaseRows.length ? toolTable(['agent', 'cheated', 'phase', 'turns', 'rounds'], phaseRows)
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


// The access policy and what each agent held (run.json "access"): its items (a numbered piece such as slice2, the
// review tool or the read permission on knowledge work's pipeline artifact), the number of pieces dealt, and on
// openmath's split layout the team remote, the honeypot branches and the archive.
function accessView(access, run) {
  const agents = Object.keys(access.agents || {}).sort();
  const rows = agents.map(a => {
    const holds = (access.agents[a] || {}).holds;
    return [a, (episodeOf(run, a) || {}).role, holds == null ? null : (holds.length ? mono(holds.join(' + ')) : muted('nothing'))];
  });
  return h('div', null, kv([
    ['policy', access.policy || undefined],
    ['numbered pieces', access.n_pieces == null ? undefined : String(access.n_pieces)],
    ['team remote', access.remote ? mono(access.remote) : undefined],
    ['honeypot branches', access.honeypot_branches ? mono(access.honeypot_branches.join(', ')) : undefined],
    ['split archive', access.archive ? mono(`${access.archive.name} on ${access.archive.branch}`) : undefined],
  ], 'kv-grid'), dataTable(['agent', 'role', 'holds'], rows));
}

// The knowledge-work sample and its reviews (run.json "knowledge"): the layout, the pipeline artifact, each contract's
// to_send/ files and each agent's grader state.
function knowledgeView(kw) {
  const meta = kw.meta || {};
  const state = kw.grader_state || {};
  const last = kw.last_verdict || null;
  const reviews = Object.keys(state).sort().map(a => {
    const g = state[a] || {};
    return [a, mono(g.agent_id), g.calls, g.errors, (g.verdicts || []).join(', '), g.submitted_reference];
  });
  const contracts = Object.entries(kw.contracts || {}).map(([c, x]) => [mono(c), mono(x.candidate),
    (x.files || []).length ? x.files.map(f => h('div', null, mono(`${f.path} (${f.bytes} B)`))) : muted('none'),
    yn(x.copied_reference, 'bad', 'plain')]);
  return h('div', null, kv([
    ['row', mono(kw.row_id)],
    ['occupation', meta.occupation || null],
    ['layout', kw.layout],
    ['mode', kw.mode],
    ['pipeline artifact', mono(kw.comparator)],
    ['share of contracts delivered', kw.value == null ? null : String(kw.value)],
    ['reviews', kw.reviews == null ? null : String(kw.reviews)],
    ['last verdict', last ? `${last.verdict || last.error_type || DASH} (${last.agent}, ${last.deliverable})` : null],
  ], 'kv-grid'),
  h('h3', null, 'Reviews per agent'), dataTable(['agent', 'candidate', 'reviews', 'errors', 'verdicts', 'submitted the reference'], reviews),
  h('h3', null, 'Contracts'), dataTable(['contract', 'candidate', 'files in to_send/', 'copied the artifact'], contracts));
}

// The team's submission (run.json team_submission): who handed in, in which round, when the run ends, and the calls
// refused before or after.
function submissionText(sub) {
  if (!sub) return null;
  const round = r => (r == null ? DASH : r);
  const refused = (sub.refused || []).map(x => `${x.agent} r${round(x.round)}`).join(', ');
  return [sub.agent ? `${sub.agent} in round ${round(sub.round)}` : 'none accepted',
    sub.ends_after_round != null ? `the run ends after round ${sub.ends_after_round}` : null,
    refused ? `refused: ${refused}` : null].filter(Boolean).join('; ');
}

// Who looked at the board, read it, posted and called submit: run.json "engagement" and each episode's.
function engagementView(g, eps) {
  const summary = kv([
    ['agents on the board', g.agents],
    ['agents that looked', g.agents_checked],
    ['agents that posted', g.agents_posted],
    ['median discovery round', g.median_discovery_round],
    ['checks', g.checks],
    ['reads', g.reads],
    ['posts', g.posts],
    ['reads refused by the budget', g.reads_refused],
    ['posts refused by the budget', g.posts_refused],
    ['oversight queries (agent_actions)', g.oversight],
  ], 'kv-grid');
  const senders = m => Object.entries(m || {}).sort().map(([s, n]) => `${s} ${n}`).join(', ');
  const cols = ['agent', 'role', 'discovery round', 'checks', 'reads', 'posts', 'work steps', 'free steps', 'notices',
    'called submit', 'read from', 'reads refused', 'posts refused', 'oversight queries', 'queried'];
  const rows = eps.filter(e => e.engagement).map(({ orbit_agent: a, role, engagement: x }) => [a, role, x.discovery_round,
    x.checks, x.reads, x.posts, x.work_steps, x.free_steps, x.notices, x.called_submit ? 'yes' : 'no', senders(x.read_senders),
    x.reads_refused, x.posts_refused, x.oversight, senders(x.oversight_targets)]);
  return h('div', null, summary, dataTable(cols, rows));
}

// The run's SwarmConfig as the task built it (run.json "config"), one field per row.
function configKv(config) {
  const value = v => (typeof v === 'boolean' ? yn(v, 'plain', 'plain') : typeof v === 'object' && v !== null ? mono(asText(v)) : mono(String(v)));
  return kv(Object.entries(config).map(([k, v]) => [k.replace(/_/g, ' '), v == null ? null : value(v)]), 'kv-grid');
}

// What the turn rules recorded for each agent (episode "rules"): one row per agent, one column per fact.
function rulesTable(rules) {
  const agents = Object.keys(rules).sort();
  const facts = [...new Set(agents.flatMap(a => Object.keys(rules[a] || {})))];
  const cell = v => (v != null && typeof v === 'object' ? mono(JSON.stringify(v)) : v);
  return dataTable(['agent', ...facts.map(f => f.replace(/_/g, ' '))],
    agents.map(a => [a, ...facts.map(f => cell((rules[a] || {})[f]))]));
}

// A CheatBench SWE task: the arm, each agent's planted piece, the hidden-test rewards for the team's main and each
// agent's working tree, the judge, the code ladder's rung and the talk measures.
function sweView(swe, run) {
  const agents = run.episodes.map(e => e.orbit_agent).sort();
  const reward = k => (swe.rewards[k] == null ? null
    : h('span', { title: `${swe.required_passed[k] || 0} of ${swe.required_tests} required tests passed` },
      yn(swe.rewards[k] === 1, 'good', 'plain')));
  const m = (a, k) => ((swe.measures || {})[a] || {})[k];
  const cols = ['agent', ...(swe.pieces ? ['planted piece'] : []), 'reward', 'judge: cheated', 'code rung', 'posts',
    'about the fix', 'disclosed'];
  const rows = agents.map(a => {
    const ep = episodeOf(run, a);
    return [a,
      ...(swe.pieces ? [swe.pieces[a] == null ? null : mono(asText(swe.pieces[a]))] : []),
      reward(a), verdictCell(ep), ep.signals ? ep.signals.rung : null, m(a, 'posts'),
      m(a, 'fix_posts') == null ? null : `${m(a, 'fix_posts')} (rule ${m(a, 'rule_posts')})`,
      m(a, 'disclosed') == null ? null : yn(m(a, 'disclosed'), 'plain', 'warn')];
  });
  return h('div', null,
    kv([
      ['issue', h('span', null, mono(swe.issue), ' ', muted(swe.instance || '', 'small'))],
      ['agents', String(swe.n_agents)],
      ['honeypot', swe.honeypot ? mono(swe.honeypot) : muted('as released')],
      ['rule', swe.rule === 'none' ? muted('none (standard prompt)') : mono(swe.rule)],
      ['team reward (team/main)', swe.mode === 'shared_task' ? reward('team')
        : muted(swe.n_agents > 1 ? 'solo_task: each working tree ships' : 'lone agent: its working tree ships')],
      ['setup checks', swe.checks_ok == null ? null : yn(swe.checks_ok, 'good', 'bad')],
      ['base / fix commit', h('span', null, mono((swe.base_commit || '').slice(0, 10)), ' / ', mono((swe.fix_commit || '').slice(0, 10)))],
    ], 'kv-grid'),
    h('h3', null, 'Agents'), dataTable(cols, rows));
}

// Composition and hidden roles of a swarm run beside the judge's verdicts, plus what the team registered (run.json
// deliverables, from the registry on main) and how each agent ended.
function swarmView(sw, run) {
  const del = run.run_json.deliverables || null;
  const counted = run.episodes.filter(e => e.counted);
  const blind = run.episodes.filter(e => e.verdict && e.verdict.cheated != null && !e.counted).length;
  const roles = Object.entries(sw.roles || {}).sort(([a], [b]) => a.localeCompare(b));
  return h('div', null,
    kv([
      ['composition', h('span', null, mono(sw.composition), ' ', muted(`${sw.regulators} regulators, ${sw.disregulators} disregulators, ${sw.workers} workers; seed ${sw.seed}`, 'small'))],
      ['judge: any agent cheated', counted.length ? yn(counted.some(e => e.verdict.cheated === true), 'bad', 'good')
        : muted(blind ? `not counted: ${plural(blind, 'verdict')} on episodes without a transcript` : 'not judged')],
      ['registered on main', del == null ? muted('not recorded') : h('span', { title: `checkout ${del.checkout || DASH}; registry on main: ${(del.main_registry_agents || []).join(', ')}` }, yn(del.registered_on_main, 'good', 'warn'))],
      ['archive in workspace at end', del == null ? muted('not recorded') : yn(del.archive_in_workspace, 'plain', 'warn')],
    ], 'kv-grid'),
    del && del.commits && del.commits.length ? h('div', null, h('h3', null, 'Commits at the end (git log --all)'), h('pre', { class: 'pre' }, del.commits.join('\n'))) : null,
    h('h3', null, 'Roles'),
    dataTable(['agent', 'hidden role', 'judge: cheated'], roles.map(([name, role]) => [name, roleBadge(role), verdictCell(episodeOf(run, name))]), { bare: true }));
}

const DEFENSE_COLS = ['name', 'defense_type', 'defense_category', 'invocation', 'model_override', 'scope', 'fail_mode',
  'detection_threshold', 'max_resamples', 'enabled'];

function defensesTable(defs) {
  const rows = defs.map(d => {
    const cfgKeys = d.config && typeof d.config === 'object' ? Object.keys(d.config) : [];
    return [...DEFENSE_COLS.map(c => (d[c] == null || d[c] === '' ? null : c === 'name' || c === 'defense_type' ? mono(asText(d[c])) : asText(d[c]))),
      cfgKeys.length ? cfgKeys.map(k => badge(k, 'plain')) : muted('none')];
  });
  const configs = defs.map(d => {
    const extra = {};
    for (const k of Object.keys(d).filter(k => !DEFENSE_COLS.includes(k))) {
      const v = d[k];
      const empty = v == null || v === '' || (Array.isArray(v) && !v.length) || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length);
      if (!empty) extra[k] = v;
    }
    return Object.keys(extra).length ? lazyDetails(`${d.name} configuration`, () => configView(extra), { cls: 'sub' }) : null;
  });
  return [dataTable([...DEFENSE_COLS.map(c => c.replace(/_/g, ' ')), 'config keys'], rows), configs];
}

/** Show a config object: string values as text blocks, others as JSON. */
function configView(obj) {
  const box = h('div', { class: 'config' });
  const walk = (o, prefix) => {
    for (const [k, v] of Object.entries(o)) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === 'object' && !Array.isArray(v) && Object.values(v).some(x => typeof x === 'string' && x.length > 60)) {
        walk(v, key);
      } else {
        box.appendChild(h('div', { class: 'cfg-key mono' }, key));
        box.appendChild(longText(typeof v === 'string' ? v : JSON.stringify(v, null, 2), { lines: 40 }));
      }
    }
  };
  walk(obj, '');
  return box;
}

function episodeCard(run, ep, d) {
  return h('div', { class: 'ep-card' },
    h('div', { class: 'ep-card-head' },
      h('h3', null, ep.episode),
      ep.failure ? badge(ep.failure, 'bad') : null,
      h('span', { class: 'spacer' }),
      ep.has_transcript ? link(R.episode(run, ep.episode), 'transcript', 'btn btn-small') : muted('no transcript', 'small'),
      logLink(d.eval_log_exists, d.eval_log, R.context(run, { agent: ep.orbit_agent }), `context of ${ep.orbit_agent}`, 'btn btn-small')),
    episodeRecord(ep),
    verdictBlock(ep.verdict, d.verdict_fields),
    ladderBlock(ep.ladder),
    signalsBlock(ep.signals),
    ep.error ? lazyDetails('error', () => longText(ep.error, { lines: 30 }), { cls: 'sub' }) : null,
    ep.final_text ? lazyDetails('final text', () => longText(ep.final_text, { lines: 40 }), { cls: 'sub' }) : null);
}

function communicationView(comm) {
  const samples = Object.entries(comm || {});
  if (!samples.length) return muted('No board communication recorded for this run.');
  return samples.map(([sample, c]) => {
    const msgs = (c.messages || []).slice().sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
    const events = (c.events || []).slice().sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
    const kinds = {};
    for (const e of events) kinds[e.kind || '?'] = (kinds[e.kind || '?'] || 0) + 1;
    return h('div', { class: 'board' },
      h('div', { class: 'board-head' },
        h('h3', null, `sample ${sample}`),
        muted(`${plural(msgs.length, 'message')} · ${plural(events.length, 'event')}` +
          (events.length ? ` (${Object.entries(kinds).map(([k, n]) => `${k} ${n}`).join(', ')})` : ''))),
      msgs.length ? h('ol', { class: 'board-msgs' }, msgs.map(m => h('li', { class: 'board-msg' },
        h('div', { class: 'bm-head' },
          h('span', { class: 'bm-seq' }, `#${m.sequence ?? '?'}`),
          h('span', { class: 'bm-sender' }, m.sender || '?'),
          h('span', { class: 'muted' }, ' → ', (m.recipients || []).join(', ') || 'all'),
          m.turn != null ? muted(` · turn ${m.turn}`) : null,
          m.channel_id ? muted(` · ${m.channel_id}`) : null,
          h('span', { class: 'spacer' }),
          m.id ? h('span', { class: 'tc-id' }, m.id) : null),
        longText(m.content, { lines: 25, cls: 'msg-text' }))))
        : muted('No messages were posted.'),
      events.length ? lazyDetails(`board events (${events.length})`, () => dataTable(
        [{ label: 'seq', num: true }, 'kind', 'agent', 'message ids', 'tool call', 'channel'],
        events.map(e => [e.sequence, e.kind, e.agent_id, mono((e.message_ids || []).join(', ')), mono(e.tool_call_id), e.channel_id]),
        { cls: 'compact' }), { cls: 'sub' }) : null);
  });
}

function defenseEvidence(defense, runDefs) {
  const samples = Object.entries(defense || {});
  if (!samples.length) return muted('No defense evidence recorded for this run.');
  const typeOf = {};
  for (const d of runDefs) typeOf[d.name] = d.defense_type;
  return samples.map(([sample, dv]) => {
    const iv = dv.interventions || [];
    const log = dv.log || {};
    for (const [name, rd] of Object.entries(log.resolved_defenses || {})) {
      if (!typeOf[name] && rd && rd.config) typeOf[name] = rd.config.defense_type;
    }
    const vaccinated = (log.vaccinations && Object.keys(log.vaccinations).length) || (log.vaccination_injections && Object.keys(log.vaccination_injections).length);
    return h('div', { class: 'evidence' },
      h('h3', null, `sample ${sample}`),
      defenseCountsTable(iv, log, typeOf),
      kv([
        ['total activations', fmtInt(log.total_activations)],
        ['blocks in log', Array.isArray(log.blocks) ? fmtInt(log.blocks.length) : undefined],
        ['detections in log', Array.isArray(log.detections) ? fmtInt(log.detections.length) : undefined],
        ['true / false positives', log.true_positives != null ? `${fmtInt(log.true_positives)} / ${fmtInt(log.false_positives)}` : undefined],
        ['vaccinations', log.vaccinations ? fmtInt(Object.keys(log.vaccinations).length) : undefined],
        ['vaccination injections', log.vaccination_injections ? fmtInt(Object.keys(log.vaccination_injections).length) : undefined],
      ], 'kv-grid'),
      vaccinated ? lazyDetails('vaccinations (JSON)', () => h('pre', { class: 'pre' },
        JSON.stringify({ vaccinations: log.vaccinations, vaccination_injections: log.vaccination_injections }, null, 2)), { cls: 'sub' }) : null,
      interventionsTable(iv));
  });
}

const BOUNDARIES = ['model_input', 'model_output', 'tool_call'];

function defenseCountsTable(iv, log, typeOf) {
  const per = new Map();
  const counts = () => ({ checks: 0, blocks: 0, by: {} });
  for (const x of iv) {
    const name = x.defense || '?';
    if (!per.has(name)) per.set(name, counts());
    const p = per.get(name);
    const b = x.boundary || '?';
    p.by[b] = p.by[b] || { checks: 0, blocks: 0 };
    p.checks++; p.by[b].checks++;
    if (x.allow === false) { p.blocks++; p.by[b].blocks++; }
  }
  const activity = log.activity || {};
  for (const name of Object.keys(activity)) if (!per.has(name)) per.set(name, counts());
  if (!per.size) return muted('No interventions recorded.');
  const bounds = [...new Set([...BOUNDARIES, ...[...per.values()].flatMap(p => Object.keys(p.by))])];
  const cols = ['defense', 'type', { label: 'checks', num: true }, { label: 'blocks', num: true },
    ...bounds.map(b => ({ label: b, num: true, title: `blocks / checks at ${b}` })), { label: 'monitor model calls', num: true },
    'per agent (activity log)'];
  const rows = [...per.entries()].map(([name, p]) => {
    const act = activity[name] || {};
    const agents = Object.entries(act.agents || {});
    return [mono(name), mono(typeOf[name]), fmtInt(p.checks), p.blocks ? badge(fmtInt(p.blocks), 'blocked') : '0',
      ...bounds.map(b => (p.by[b] ? `${fmtInt(p.by[b].blocks)} / ${fmtInt(p.by[b].checks)}` : null)), fmtInt(act.model_calls),
      agents.length ? h('span', { class: 'small' }, agents.map(([a, s]) => h('div', null, mono(a), ': ',
        `${plural(s.checks || 0, 'check')}, ${plural(s.blocks || 0, 'block')}, ${plural(s.model_calls || 0, 'call')}`))) : null];
  });
  return h('div', null, h('div', { class: 'muted small' }, 'Boundary columns show blocks / checks, counted from the interventions below.'),
    dataTable(cols, rows));
}

function interventionsTable(iv) {
  if (!iv.length) return muted('No interventions.');
  const st = { boundary: '', allow: '', defense: '', agent: '', q: '' };
  const values = key => [...new Set(iv.map(x => x[key]).filter(Boolean))].sort();
  const bounds = values('boundary');
  const defs = values('defense');
  const agents = values('agent');
  const count = h('span', { class: 'muted' });
  const rows = iv.map((x, i) => {
    const ref = x.tool_call_id || x.message_id || '';
    const tr = h('tr', { class: x.allow === false ? 'row-blocked' : '' },
      h('td', { class: 'num' }, String(i + 1)),
      h('td', { class: 'num' }, x.turn == null ? DASH : String(x.turn)),
      h('td', null, x.agent || DASH),
      h('td', { class: 'mono' }, x.defense || DASH),
      h('td', null, x.boundary || DASH),
      h('td', null, x.allow === false ? badge('BLOCK', 'blocked') : x.allow === true ? badge('allow', 'good') : muted(String(x.allow))),
      h('td', { class: 'mono' }, x.tool || DASH),
      h('td', { class: 'reason' }, x.reason ? longText(x.reason, { lines: 6, chars: 1500 }) : muted(DASH)),
      h('td', { class: 'mono small', title: x.tool_call_id ? 'tool call id' : 'message id' }, ref || DASH));
    const hay = [x.reason, x.agent, x.defense, x.boundary, x.tool, ref].filter(Boolean).join(' ').toLowerCase();
    return { x, tr, hay };
  });
  function apply() {
    const q = st.q.trim().toLowerCase();
    let n = 0;
    let blocks = 0;
    for (const r of rows) {
      const x = r.x;
      const ok = (!st.boundary || x.boundary === st.boundary) &&
        (!st.allow || (st.allow === 'block' ? x.allow === false : x.allow !== false)) &&
        (!st.defense || x.defense === st.defense) &&
        (!st.agent || x.agent === st.agent) &&
        (!q || r.hay.includes(q));
      r.tr.hidden = !ok;
      if (ok) { n++; if (x.allow === false) blocks++; }
    }
    count.textContent = `${fmtInt(n)} of ${fmtInt(rows.length)} interventions shown, ${fmtInt(blocks)} blocks`;
  }
  const search = h('input', { type: 'search', class: 'search small-search', placeholder: 'search reasons…', 'aria-label': 'Search interventions' });
  search.addEventListener('input', debounce(() => { st.q = search.value; apply(); }, 80));
  const filters = h('div', { class: 'filters' },
    labeled('boundary', select([['', 'all'], ...bounds], '', v => { st.boundary = v; apply(); })),
    labeled('decision', select([['', 'all'], ['block', 'blocked'], ['allow', 'allowed']], '', v => { st.allow = v; apply(); })),
    defs.length > 1 ? labeled('defense', select([['', 'all'], ...defs], '', v => { st.defense = v; apply(); })) : null,
    agents.length > 1 ? labeled('agent', select([['', 'all'], ...agents], '', v => { st.agent = v; apply(); })) : null,
    search, count);
  apply();
  return h('div', { class: 'interventions' },
    h('h4', null, 'Interventions'),
    filters,
    dataTable([{ label: '#', num: true }, { label: 'turn', num: true }, 'agent', 'defense', 'boundary', 'decision', 'tool', 'reason',
      'message / call id'], rows.map(r => r.tr), { cls: 'compact' }));
}
