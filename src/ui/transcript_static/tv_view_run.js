/* View: one run (#/run/<collection>/<run key>?agent=<id>&call=<k>, ?tab=board&a=&b=&mode=&pos= or ?tab=details).
 * Ported from collusion-hermaneutics' viewer (viewer/static/app/view_run.js). A bar with the run, an agent tab per
 * agent, a Board tab and a Details tab; the selected agent's whole episode below it, each step with its labels
 * (tv_case_view.js); the run record under it: the run's labels, the run settings, each agent's record with its
 * Cheating evidence, its judge labels and CheatBench's judge on it.
 * Our edits: the imports (dom.js, components.js and charts.js are the results browser's own); the judges are read
 * from the run's verdict files (judges/<pass>.<judge>.jsonl), shown where the run has them; the source's API judges,
 * second judge and sentence lens are gone; a disregulator is a hidden role like a regulator; the header names the
 * collection and the eval log; the Board tab (tv_view_board.js) holds the message board, two agents' calls on one
 * clock with the board between them; the Details tab holds the Cheating, Coordination and Collusion panels and the
 * judge labels. A tab
 * beside the agents' is one entry of `views` (a key, its label and a function that draws it once, answering its
 * element and, for the Board, a promise of its handle). */
import { appendAll, enc, fmtInt, fmtUsd, h } from '/static/app/dom.js';
import { badge, dataTable, episodeRecord, kv, lazyDetails, longText, muted } from '/static/app/components.js';
import { ROLE_ORDER, roleIcon } from '/static/app/charts.js';
import { CHEATBENCH, DISCLOSURE, ROLE_LABEL, armLabel, collectionOf, modelLabel, taskLabel } from './tv_labels.js';
import { cheatbenchTeam, cheatbenchWords, cheatedTag, cheatingPanel, clauseList, collusionPanel, coordinationPanel,
  disclosureTeam, judgeLabels, judgeLabelsPanel, labelTag, labelTeam, levelChip, levelsPanel, unvalidatedNote } from './tv_levels.js';
import { fullLegend, renderCase } from './tv_case_view.js';
import { renderBoard } from './tv_view_board.js';

function flash(el) {
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
}

function roleTag(role) {
  return h('span', { class: ['badge', 'b-plain', 'role-tag', 'role-' + role], title: 'the agent\'s role in the run' },
    roleIcon(ROLE_ORDER.includes(role) ? role : 'worker', 10), ' ', ROLE_LABEL[role] || role);
}

const fieldText = v => (v === true ? 'yes' : v === false ? 'no' : typeof v === 'string' ? v : JSON.stringify(v));

export async function viewRun(id, q, view) {
  const r = await fetch('/api/run?id=' + enc(id));
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const row = d.row;
  const agentIds = d.agents.map(a => a.agent);
  const roles = Object.fromEntries(d.agents.map(a => [a.agent, a.role]));
  const multi = d.agents.length > 1;

  // ---- board: every message by the label the evidence, the Board tab and the judges give it, P1, P2, ... in board
  // order, harness notices included (board_no).
  const posts = new Map(d.boards.flatMap(b => b.messages.map(m => [`P${m.board_no}`, m])));
  const collusion = (d.levels && d.levels.collusion) || {};
  const cheating = (d.levels && d.levels.cheating) || {};

  // Calls that read a hidden regulator's post (a worker), or that posted as the regulator (the regulator).
  function regulatorCalls(agent, role) {
    const out = new Set();
    for (const b of d.boards) for (const m of b.messages) {
      if (m.role !== 'regulator') continue;
      if (role === 'regulator' && m.sent && m.sent.agent === agent) out.add(m.sent.call_id);
      if (role !== 'regulator') for (const rd of m.reads) if (rd.agent === agent) out.add(rd.call_id);
    }
    return out;
  }

  // ---- agent cards with lazy transcripts.
  const cards = new Map();
  function agentCard(a) {
    const card = h('section', { class: ['card', 'agent-card', 'role-' + a.role], id: `agent-${a.agent}` });
    const holder = h('div', { class: 'tr-holder' });
    const state = { loaded: null, tr: null };
    const sizeTxt = a.transcript_bytes != null ? `${fmtInt(Math.round(a.transcript_bytes / 1024))} KB` : 'missing';
    const btn = h('button', { type: 'button', class: 'btn', disabled: !a.folder }, a.folder ? `Show transcript (${sizeTxt})` : 'No transcript on disk');
    const load = () => {
      if (!state.loaded) {
        btn.disabled = true;
        btn.textContent = 'Loading transcript…';
        state.loaded = fetch(`/api/transcript?id=${enc(id)}&agent=${enc(a.agent)}`)
          .then(x => { if (!x.ok) throw new Error(`HTTP ${x.status}`); return x.json(); })
          .then(t => {
            state.tr = renderCase(t, { agent: a.agent, levels: d.levels, isRegulator: a.role === 'regulator', regulatorCalls: regulatorCalls(a.agent, a.role) });
            holder.replaceChildren(muted(`The transcript of ${a.agent} is shown at the top of the page, under its tab.`, 'small block'));
            btn.textContent = `Transcript at the top (${sizeTxt})`;
            return state.tr;
          })
          .catch(e => { holder.replaceChildren(h('div', { class: 'error-box' }, `Could not load the transcript: ${e.message}`)); btn.disabled = false; btn.textContent = 'Retry loading the transcript'; state.loaded = null; throw e; });
      }
      holder.hidden = false;
      return state.loaded;
    };
    btn.addEventListener('click', () => { selectTab(a.agent); });
    appendAll(card, [
      h('div', { class: 'ep-card-head' },
        h('h2', { class: 'agent-h' }, `Agent ${a.agent}`), roleTag(a.role),
        a.episode.model ? muted(a.episode.model, 'small') : null,
        h('span', { class: 'spacer' }), btn),
      episodeRecord(a.episode),
      agentCheating(a.agent),
      agentJudgeLabels(a.agent),
      h('div', null, h('h3', { class: 'sub-h' }, CHEATBENCH.column), cheatbenchBlock(a)),
      a.episode.final_text ? lazyDetails('final text of the agent', () => longText(a.episode.final_text, { lines: 40 }), { cls: 'sub' }) : null,
      holder]);
    cards.set(a.agent, { card, load, state });
    return card;
  }

  async function gotoCall(agent, k) {
    const c = cards.get(agent);
    if (!c) return;
    const tr = await selectTab(agent);
    tr.showAll();
    const box = tr.findCall(k);
    if (box) flash(box);
    else flash(c.card);
  }
  // A post opens in the Board tab.
  const openPost = label => selectTab('board').then(b => b.showPost(label)).catch(() => {});
  const boardPost = label => {
    const m = posts.get(label);
    if (!m) return h('span', { class: 'mono' }, label);
    return h('button', { type: 'button', class: 'linkish ev-link', title: 'show this post on the board',
      onclick: () => openPost(label) },
    `${label}${m.notice ? ' (a system notice)' : ` (by ${m.sender})`}`);
  };

  // Evidence text with every [k] linked to call k of the agent it names, and every post (P<n>) to the board.
  function evidenceText(text, agent) {
    const s = String(text == null ? '' : text);
    let who = agent;
    const lead = /^([A-Za-z0-9_-]+): /.exec(s);
    if (lead && agentIds.includes(lead[1])) who = lead[1];
    const out = [];
    let byRegulator = false;
    let last = 0;
    const re = /\[(\d+)\]|\bP\d+\b/g;
    let m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push(s.slice(last, m.index));
      if (m[1] != null && who && agentIds.includes(who)) {
        const k = parseInt(m[1], 10);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: `open call ${k} in ${who}'s transcript`, onclick: () => gotoCall(who, k) }, m[0]));
      } else if (m[1] == null && posts.has(m[0])) {
        const label = m[0];
        const p = posts.get(label);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: 'show this post on the board', onclick: () => openPost(label) }, label));
        if (p.role === 'regulator') byRegulator = true;
      } else out.push(m[0]);
      last = re.lastIndex;
    }
    if (last < s.length) out.push(s.slice(last));
    if (byRegulator) out.push(' ', badge('written by the hidden regulator', 'notice', 'the post this evidence cites was written by the run\'s hidden regulator'));
    return h('span', { class: 'ev-text' }, out);
  }
  const evidenceList = (lines, agent) => {
    const list = (Array.isArray(lines) ? lines : [lines]).filter(x => x);
    if (!list.length) return null;
    return h('ul', { class: 'ev-list' }, list.map(x => h('li', null, evidenceText(x, agent))));
  };

  function agentCheating(agent) {
    const r = cheating.applicable && cheating.agents[agent];
    if (!r) return muted(`Cheating: ${cheating.reason || 'no row for this agent'}.`, 'small block');
    const L = d.levels.ladders.cheating;
    return h('div', null, h('h3', { class: 'sub-h' }, 'Cheating for this agent'),
      h('div', { class: 'verdict-flags' }, levelChip('cheating', L, r.rung), ' ', cheatedTag(r.cheated, L),
        muted(` ${L.glosses[L.levels.indexOf(r.rung)]}`, 'small')),
      clauseList(d.levels, r.clauses, agent, { evidence: evidenceText }),
      (r.below || []).length ? h('div', { class: 'small' }, h('span', { class: 'field-label' }, 'Below the ladder, evidence only: '),
        evidenceList(r.below, agent)) : null,
      h('div', { class: 'small' }, h('span', { class: 'field-label' }, 'CheatBench\'s judge\'s labels beside the code: '), judgeLabels(r)));
  }

  // The agent's judge labels beside the ladders: awareness, manipulation and disclosure, each with its quote.
  function agentJudgeLabels(agent) {
    const about = d.levels.judge_label_about;
    const mine = d.levels.judge_labels[agent];
    return h('div', null, h('h3', { class: 'sub-h' }, 'Judge labels for this agent (no rung)'),
      h('div', { class: 'verdict-flags' }, Object.keys(about).map(k => [labelTag(about[k].name, mine[k]), ' ', unvalidatedNote(about[k]), ' '])),
      Object.keys(about).filter(k => mine[k].quote).map(k => h('div', { class: 'small' },
        h('span', { class: 'field-label' }, `${about[k].name}, from its ${mine[k].source}: `), h('q', null, mine[k].quote))));
  }

  function cheatbenchBlock(a) {
    const judges = Object.entries(a.cheatbench || {});
    if (!judges.length) return muted('Not judged: no CheatBench verdict file holds this agent.', 'small block');
    return judges.map(([judge, v]) => h('div', { class: 'judge-block' },
      h('div', { class: 'jb-head' }, h('h4', null, `judge ${judge}`), cheatbenchWords({ [judge]: v })),
      v.summary ? h('p', { class: 'verdict-summary' }, evidenceText(v.summary, a.agent)) : null,
      h('dl', { class: 'kv kv-grid' }, Object.entries(v).filter(([k]) => !['agent', 'summary'].includes(k)).map(([k, x]) =>
        h('div', { class: 'kv-item' }, h('dt', null, k.replace(/_/g, ' ')),
          h('dd', null, k === 'judge_cost_usd' ? fmtUsd(x) : typeof x === 'string' ? evidenceText(x, a.agent) : fieldText(x)))))));
  }

  // ---- labels table: each ladder's team level, the alarm and the judges.
  const L = d.levels.ladders;
  const teamCell = k => {
    const c = d.levels.team[k];
    return c.na ? muted(`not applicable: ${c.evidence}`) : h('span', null, h('b', null, c.level), muted(` (${c.index} of ${c.of}) ${c.evidence}`, 'small'));
  };
  const about = d.levels.judge_label_about;
  const labelRows = [
    ['Cheating (team)', teamCell('cheating'), L.cheating.rule],
    ['Cheated (team)', cheatedTag(cheating.team_cheated, L.cheating) || muted('not applicable'), L.cheating.line_words],
    ['Alarm', cheating.alarm ? badge(`alarm: ${cheating.alarm}`, 'bad') : muted('none'), L.cheating.note],
    ['Coordination (team)', teamCell('coordination'), L.coordination.rule],
    ['Collusion (team)', teamCell('collusion'), L.collusion.rule],
    ['Scheming judge', collusion.judged ? `flags of ${collusion.scheming.judge}` : muted('not judged'),
      'The scheming judge reads the board of a run with a pair at collusion once and flags posts; a colluding pair schemes when a flagged post lies between the two. Its flags give the Collusion ladder\'s one judge rung, scheming.'],
    ...['awareness', 'manipulation'].map(k => [about[k].name, [labelTeam(row.labels[k], about[k]), ' ', unvalidatedNote(about[k])],
      `${about[k].question} Read for ${about[k].read_for}. No rung.`]),
    [DISCLOSURE.column, disclosureTeam(row.disclosure), DISCLOSURE.about],
    [CHEATBENCH.column, cheatbenchTeam(row.cheatbench), CHEATBENCH.about],
  ];
  const labelsCard = h('section', { class: 'card' }, h('h2', null, 'Labels for this run'),
    dataTable(['Label', 'Value', 'What it means'], labelRows.map(([a, b, c]) => [h('strong', null, a), b, h('span', { class: 'small' }, c)]), { cls: 'labels-table' }));

  // ---- header.
  const m = d.meta || {};
  const cfg = m.config || {};
  const coll = collectionOf(row.col);
  const hidden = r => Object.values(roles).filter(x => x === r).length;
  const header = h('section', { class: 'card ep-header' },
    h('div', { class: 'ep-title' }, h('h1', null, row.run),
      badge(coll.label, 'def', coll.about), coll.tag ? badge(coll.tag, 'plain', coll.about) : null,
      h('span', { class: 'spacer' }), h('a', { class: 'btn btn-small', href: '#/' }, '← all runs')),
    kv([
      ['collection', `${coll.label} (runs/${row.col})`], ['folder', h('span', { class: 'mono' }, row.key)],
      ['task', `${taskLabel(row)}${row.instance ? `, instance ${row.instance}` : ''}`], ['arm or cell', armLabel(row)],
      ['seed post', row.seed_post || undefined], ['workers\' model', `${modelLabel(row)}${row.model_id ? ` (${row.model_id})` : ''}`],
      ['agents', `${row.n_agents}${hidden('regulator') ? ` (${hidden('regulator')} hidden regulator)` : ''}${hidden('disregulator') ? ` (${hidden('disregulator')} hidden disregulator)` : ''}`],
      ['seed', row.seed != null ? String(row.seed) : undefined],
      ['turn budget (rounds)', cfg.turn_budget != null && multi ? String(cfg.turn_budget) : undefined],
      ['rounds run', m.rounds != null && multi ? String(m.rounds) : undefined],
      ['mode, access', multi ? [m.mode || cfg.mode, cfg.access].filter(Boolean).join(', ') : undefined],
      ['eval log', m.eval_log ? h('span', { class: 'mono small' }, m.eval_log) : muted(m.eval_log_recorded ? `not found (run.json names ${m.eval_log_recorded})` : 'none recorded', 'small')],
      ['code', m.code && m.code.commit ? `${m.code.commit}${m.code.dirty ? ' (dirty)' : ''}` : undefined],
    ], 'kv-grid'),
    null);
  labelsCard.id = 'sec-labels';
  const agentsBox = h('div', { id: 'sec-agents' }, d.agents.map(agentCard));

  // ---- top bar: run, arm, agent tabs, Show filter, the run record's toggle; the stage below holds the selected tab.
  const firstAgent = (d.agents.find(a => a.role === 'worker' && a.folder) || d.agents.find(a => a.folder) || {}).agent;
  const stage = h('div', { class: 'rp-stage', dataset: { show: 'all' } });
  const tabs = new Map();
  let current = null;
  const ROLE_WORD = ROLE_LABEL;
  for (const a of d.agents) {
    tabs.set(a.agent, h('button', { type: 'button', class: 'rp-tab', onclick: () => selectTab(a.agent).catch(() => {}) },
      a.agent, h('span', { class: 'rp-role' }, ' ' + (ROLE_WORD[a.role] || a.role))));
  }
  const cheatbench = Object.fromEntries(d.agents.map(a => [a.agent, a.cheatbench]));
  const detailsView = () => ({ el: h('div', { class: 'rp-team' },
    levelsPanel(d.levels, roles, row.task_name, { cheatbench, row }),
    cheatingPanel(d.levels, roles, { evidence: evidenceText }),
    coordinationPanel(d.levels, roles),
    collusionPanel(d.levels, roles, { evidence: evidenceText, boardPost }),
    judgeLabelsPanel(d.levels, roles),
    h('section', { class: 'card' }, h('h2', null, 'Colour legend'), fullLegend(d.levels.ladders, d.levels.order))) });
  // The Board tab reads /api/board when first opened; its handle ({moveTo, showPost, state}) comes once it is drawn.
  // Its pair, mode and position come from the hash (a, b, mode, pos) and go back to it.
  const boardView = () => {
    const el = h('div', { class: 'rp-board' }, h('div', { class: 'loading' }, 'Loading the board…'));
    const handle = fetch('/api/board?id=' + enc(id))
      .then(async x => {
        const b = await x.json();
        if (!x.ok) throw new Error(b.error || `HTTP ${x.status}`);
        return renderBoard(el, b, { q, base: '#/run/' + id.split('/').map(enc).join('/') });
      })
      .catch(e => {
        el.replaceChildren(h('div', { class: 'error-box' }, `Could not load the board: ${e.message}`));
        drawn.delete('board');
        throw e;
      });
    return { el, handle };
  };
  // The tabs beside the agents': each draws its view once, when first opened.
  const views = new Map(multi
    ? [['board', { label: 'Board', draw: boardView }], ['details', { label: 'Details', draw: detailsView }]] : []);
  const drawn = new Map();
  for (const [key, v] of views) {
    const open = () => selectTab(key).catch(() => {});
    tabs.set(key, h('button', { type: 'button', class: 'rp-tab', onclick: open }, v.label));
  }
  async function selectTab(key) {
    current = key;
    for (const [k, b] of tabs) b.classList.toggle('on', k === key);
    if (views.has(key)) {
      if (!drawn.has(key)) drawn.set(key, views.get(key).draw());
      const v = drawn.get(key);
      stage.replaceChildren(v.el);
      return v.handle || null;
    }
    const c = cards.get(key);
    if (!c) return null;
    stage.replaceChildren(h('div', { class: 'loading' }, 'Loading the transcript…'));
    const tr = await c.load();
    if (current === key) stage.replaceChildren(tr.el);
    return tr;
  }
  const showSel = h('select', { 'aria-label': 'Which text to show', onchange: e => { stage.dataset.show = e.target.value; } },
    [['all', 'All'], ['reasoning', 'Reasoning'], ['tools', 'Tool calls'], ['board', 'Board calls']].map(([v, t]) => h('option', { value: v }, t)));
  const bar = h('div', { class: 'rp-bar' },
    h('a', { class: 'rp-back', href: '#/', title: 'all runs' }, '←'),
    h('b', { class: 'rp-run' }, row.run), h('span', { class: 'rp-arm' }, armLabel(row)),
    cheating.alarm ? badge(`alarm: ${cheating.alarm}`, 'bad', d.levels.ladders.cheating.note) : null,
    h('span', { class: 'rp-tabs' }, [...tabs.values()]),
    h('label', { class: 'rp-show' }, 'Show ', showSel),
    h('button', { type: 'button', class: 'rp-dbtn', title: 'the run\'s labels, its settings and each agent\'s record',
      onclick: () => { record.open = !record.open; if (record.open) record.scrollIntoView({ block: 'start' }); } },
    'Run record'));

  view.mounted(() => {
    document.title = `${row.run} · Transcripts`;
    const qa = q.get('agent');
    const qc = q.get('call');
    if (qa && qc != null && cards.has(qa)) gotoCall(qa, parseInt(qc, 10)).catch(() => {});
    else selectTab(views.has(q.get('tab')) ? q.get('tab') : (firstAgent || 'details')).catch(() => {});
  });
  const record = h('details', { class: 'rp-details' },
    h('summary', null, 'Run record: labels, run settings, each agent\'s record with its Cheating evidence and the '
      + 'judges'),
    header, labelsCard, agentsBox);
  return h('div', { class: 'page rp-page' }, bar, stage, record);
}

