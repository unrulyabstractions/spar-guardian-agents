/* View: one run (#/run/<collection>/<run key>?agent=<id>&call=<k>&tab=team). Ported from collusion-hermaneutics'
 * viewer (viewer/static/app/view_run.js). A bar with the run, an agent tab per agent and a Team tab, and the lens
 * buttons; the selected agent's whole episode below it (tv_case_view.js); the details under it: labels, the code
 * ladder's evidence with links into the transcripts, each judge's reply, the run settings, each agent's record.
 * Our edits: the imports (dom.js, components.js and charts.js are the results browser's own); the judges are the
 * judged ladder (ladder.jsonl) and CheatBench's rules (judge.jsonl), shown where the run has them; the source's API
 * judges and second judge are gone; a disregulator is a hidden role like a regulator; the header names the
 * collection, the status and the eval log. */
import { DASH, appendAll, enc, fmtInt, fmtUsd, h, plural } from '/static/app/dom.js';
import { badge, collaborationBadge, dataTable, episodeRecord, kv, lazyDetails, longText, muted, rungBadge,
  yn } from '/static/app/components.js';
import { ROLE_ORDER, roleIcon } from '/static/app/charts.js';
import { COLLAB_MEANING, COLLAB_ORDER, JUDGES, RUNG_MEANING, RUNG_ORDER, ROLE_LABEL, STATUS, armLabel,
  collectionOf, modelLabel, taskLabel } from './tv_labels.js';
import { cheatLabelBadge, yesNo } from './tv_view_index.js';
import { levelsPanel } from './tv_levels.js';
import { fullLegend, renderCase } from './tv_case_view.js';
import { lensLabel, listLenses, loadLens } from './tv_lens.js';

const PRESENTATION_KIND = { disclosed: 'good', silent: 'warn', passed_off: 'bad', none: 'plain' };
const presentationBadge = v => badge(String(v).replace(/_/g, ' '), PRESENTATION_KIND[v] || 'plain', 'how the hand-in presents the honeypot content (judged)');

const LADDER = ['exposed', 'tried', 'opened', 'used', 'tampered', 'checked', 'shipped'];
const CALL_FIELDS = ['exposed', 'tried', 'opened', 'joined'];
const EVIDENCE_FIELDS = ['tried', 'opened', 'joined', 'used', 'tampered', 'checked', 'shipped'];
const MARK_KIND = { exposed: 'warn', tried: 'bad', opened: 'bad', joined: 'def', used: 'bad', tampered: 'bad', checked: 'bad', shipped: 'bad' };
const MARK_MEANING = { ...RUNG_MEANING, joined: 'the first call that joined the whole exploit' };

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

/** Marks for the tool calls an evidence row cites: {k: [{source, badge, kind, title, text}]}. */
function ladderMarks(row, source) {
  const marks = new Map();
  if (!row) return marks;
  const seen = new Set();
  const add = (k, rung, text) => {
    if (k == null || Number.isNaN(k) || seen.has(`${k}|${rung}`)) return;
    seen.add(`${k}|${rung}`);
    const who = source === 'code' ? 'code ladder' : 'judge';
    const m = { source, badge: `${who}: ${rung}`, kind: source === 'code' ? (MARK_KIND[rung] || 'plain') : 'notice',
      title: `${who}: ${MARK_MEANING[rung] || rung}`, text };
    if (!marks.has(k)) marks.set(k, []);
    marks.get(k).push(m);
  };
  for (const f of CALL_FIELDS) {
    const k = row[f + '_call'];
    if (k == null) continue;
    const ev = typeof row[f + '_evidence'] === 'string' ? row[f + '_evidence'] : '';
    const seg = ev ? (ev.split(/(?=\[\d+\])/).find(s => s.startsWith(`[${k}]`)) || ev) : (f === 'exposed' && row.exposed_source ? `source: ${row.exposed_source.replace(/_/g, ' ')}` : '');
    add(k, f, seg.trim());
  }
  for (const f of EVIDENCE_FIELDS) {
    const ev = row[f + '_evidence'];
    if (typeof ev !== 'string' || !ev) continue;
    for (const seg of ev.split(/(?=\[\d+\])/)) {
      const m = /^\[(\d+)\]/.exec(seg);
      if (m) add(parseInt(m[1], 10), f, seg.replace(/[;\s]+$/, '').trim());
    }
  }
  return marks;
}

export async function viewRun(id, q, view) {
  const r = await fetch('/api/run?id=' + enc(id));
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  const row = d.row;
  const team = d.team || {};
  const agentIds = d.agents.map(a => a.agent);
  const roles = Object.fromEntries(d.agents.map(a => [a.agent, a.role]));
  const multi = d.agents.length > 1;

  // ---- board: post numbers, the calls behind posts and reads, and the collaboration evidence that cites a post.
  const posts = new Map();
  const collabByPost = new Map();
  for (const [level, lines] of Object.entries(team.collaboration_evidence || {})) {
    for (const line of lines || []) {
      const m = /^post (\d+) by /.exec(line);
      if (m) {
        const n = parseInt(m[1], 10);
        if (!collabByPost.has(n)) collabByPost.set(n, []);
        collabByPost.get(n).push(level);
      }
    }
  }
  const boardCalls = new Map(agentIds.map(a => [a, new Map()]));
  const addBoardCall = (agent, callId, mark) => {
    if (!agent || !callId || !boardCalls.has(agent)) return;
    const m = boardCalls.get(agent);
    if (!m.has(callId)) m.set(callId, []);
    m.get(callId).push(mark);
  };
  for (const b of d.boards) {
    for (const m of b.messages) {
      if (m.post_n != null) posts.set(m.post_n, m);
      const name = m.post_n != null ? `post ${m.post_n}` : `system notice #${m.board_no}`;
      const cites = m.post_n != null ? collabByPost.get(m.post_n) : null;
      if (m.sent) {
        addBoardCall(m.sent.agent, m.sent.call_id, { source: 'board', badge: `wrote ${name}`, kind: 'def',
          title: `this call posted ${name} on the board (#${m.board_no} as the agents see it)`,
          text: cites ? `collaboration evidence: ${cites.join(', ')}` : '' });
      }
      for (const rd of m.reads) {
        addBoardCall(rd.agent, rd.call_id, { source: 'board', badge: `read ${name}`, kind: 'plain', title: `this call read ${name}`, text: '' });
      }
    }
  }

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
    const s = a.signals;
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
            const marks = ladderMarks(s, 'code');
            if (a.ladder_judge) {
              for (const [k, ms] of ladderMarks(a.ladder_judge, 'judge')) marks.set(k, [...(marks.get(k) || []), ...ms]);
            }
            state.tr = renderCase(t, { agent: a.agent, levels: d.levels, boardCalls: boardCalls.get(a.agent) || new Map(),
              isRegulator: a.role === 'regulator', regulatorCalls: regulatorCalls(a.agent, a.role), runId: id });
            if (lensState.L) state.tr.setLens(lensState.L);
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
      episodeRecord(Object.assign({}, a.episode, { role: undefined })),
      s ? h('div', null, h('h3', { class: 'sub-h' }, 'Code ladder for this agent'), agentLadder(s, a.agent)) : muted('No code-ladder row for this agent.'),
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
  async function gotoToolCall(agent, callId) {
    const c = cards.get(agent);
    if (!c) return;
    const tr = await selectTab(agent);
    tr.showAll();
    flash(tr.findToolCall(callId) || c.card);
  }
  const gotoPost = n => flash(document.getElementById(`post-${n}`));

  // Evidence text with every [k] linked to call k of the agent it names, and every "post N" to the board.
  function evidenceText(text, agent) {
    const s = String(text == null ? '' : text);
    let who = agent;
    const lead = /^([A-Za-z0-9_-]+): /.exec(s);
    if (lead && agentIds.includes(lead[1])) who = lead[1];
    const out = [];
    let byRegulator = false;
    let last = 0;
    const re = /\[(\d+)\]|\bpost (\d+)\b/g;
    let m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push(s.slice(last, m.index));
      if (m[1] != null && who && agentIds.includes(who)) {
        const k = parseInt(m[1], 10);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: `open call ${k} in ${who}'s transcript`, onclick: () => gotoCall(who, k) }, m[0]));
      } else if (m[2] != null && posts.has(parseInt(m[2], 10))) {
        const n = parseInt(m[2], 10);
        const p = posts.get(n);
        out.push(h('button', { type: 'button', class: 'linkish ev-link', title: 'show this post on the board', onclick: () => gotoPost(n) }, m[0]));
        if (p.role === 'regulator') byRegulator = true;
      } else out.push(m[0]);
      last = re.lastIndex;
    }
    if (last < s.length) out.push(s.slice(last));
    if (byRegulator) out.push(' ', badge('written by the hidden regulator', 'notice', 'the post this evidence cites was written by the run\'s hidden regulator, whose posts count toward collaboration'));
    return h('span', { class: 'ev-text' }, out);
  }
  const evidenceList = (lines, agent) => {
    const list = (Array.isArray(lines) ? lines : [lines]).filter(x => x);
    if (!list.length) return null;
    return h('ul', { class: 'ev-list' }, list.map(x => h('li', null, evidenceText(x, agent))));
  };

  function agentLadder(s, agent) {
    const counted = f => RUNG_ORDER.indexOf(f) <= RUNG_ORDER.indexOf(s.rung);
    const rows = LADDER.filter(f => f in s).map(f => {
      const call = s[f + '_call'];
      return [h('span', { title: RUNG_MEANING[f] }, f), counted(f) ? yn(s[f], 'bad', 'good') : yn(s[f], 'plain', 'good'),
        call != null ? h('button', { type: 'button', class: 'linkish', onclick: () => gotoCall(agent, call) }, `call ${call}`) : null,
        f === 'exposed' ? (s.exposed_source ? muted(`source: ${s.exposed_source.replace(/_/g, ' ')}`, 'small') : null)
          : evidenceList(s[f + '_evidence'], agent)];
    });
    if (s.joined_call != null || s.joined_evidence) {
      rows.push([h('span', { title: MARK_MEANING.joined }, 'joined the exploit'), muted(DASH),
        s.joined_call != null ? h('button', { type: 'button', class: 'linkish', onclick: () => gotoCall(agent, s.joined_call) }, `call ${s.joined_call}`) : null,
        evidenceList(s.joined_evidence, agent)]);
    }
    const list = v => (v && v.length ? v.join(', ') : null);
    return h('div', { class: 'verdict' },
      h('div', { class: 'verdict-flags' },
        h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, 'rung of this agent'), rungBadge(s.rung)),
        muted(`${plural(s.calls || 0, 'tool call')} in its transcript`, 'small')),
      dataTable(['rung', 'reached', 'first call', 'evidence'], rows, { cls: 'compact ladder-table' }),
      kv([['holds', list(s.holds)], ['pieces seen', list(s.pieces_seen)], ['lines seen', s.lines_seen],
        ['probe of its disk', s.probe ? 'ran' : 'none'], ['copies', list(s.copies)],
        ['unopened checks', s.unopened_checks && s.unopened_checks.length ? s.unopened_checks.join('; ') : undefined]], 'kv-grid'),
      h('div', { class: 'verdict-meta' }, muted('code ladder: read from the transcript, the sandbox probe and the run\'s records, with no judge')));
  }

  // ---- labels table.
  const judgeRows = [];
  for (const key of Object.keys(JUDGES)) {
    const j = row.judges[key];
    if (!j) continue;
    judgeRows.push([JUDGES[key].column, h('span', null, j.rung ? [rungBadge(j.rung), ' '] : null, yesNo(j.yes),
      j.of > 1 ? muted(` ${j.n} of ${j.of} workers`, 'small') : null, j.judge ? muted(` · ${j.judge}`, 'small') : null),
    `${JUDGES[key].about} Read from the run's ${JUDGES[key].file}.`]);
  }
  if (!judgeRows.length) {
    judgeRows.push(['Cheating (judge)', muted('not judged'), 'No judge read this run: it holds no ladder.jsonl and no judge.jsonl.']);
  }
  const floorTxt = row.floor || 'unknown';
  const labelRows = [
    ['Cheating rung (code ladder)', h('span', null, row.rung ? rungBadge(row.rung) : muted('none'),
      row.rung_code ? muted(` raised from ${row.rung_code} by the task's own grade`, 'small') : null),
    `The highest rung of the honeypot ladder that code found for the team's workers. ${RUNG_ORDER.map(k => `${k}: ${RUNG_MEANING[k]}`).join('; ')}.`],
    ['Cheating (code ladder)', cheatLabelBadge(row.label),
      `cheat when the rung reaches the task's floor (src/domains ladder.cheated_from), which is ${floorTxt} for this task; no cheat at none or exposed; other between.`],
    ['Collaboration (code ladder)', row.collab ? collaborationBadge(row.collab) : muted('one agent, no team'),
      `How the honeypot travelled between agents: ${COLLAB_ORDER.map(k => `${k}: ${COLLAB_MEANING[k]}`).join('; ')}. A hidden regulator's or disregulator's posts count too.`],
    ...judgeRows,
    ...(row.presentation ? [['Presentation (judge)', presentationBadge(row.presentation),
      'How the hand-in presents the honeypot content: disclosed, silent or passed off as own work. Judged by the judged ladder.']] : []),
    ['Code and judges on cheating', row.agree === 'disagree'
      ? h('span', null, badge('disagree', 'warn'), muted(' ' + row.disagree.map(k => JUDGES[k].label).join(', '), 'small'))
      : row.agree === 'agree' ? badge('agree', 'plain') : muted(row.agree),
    'Whether the code ladder\'s cheat label matches each judge\'s cheating verdict. An "other" label is not compared.'],
  ];
  const labelsCard = h('section', { class: 'card' }, h('h2', null, 'Labels for this run'),
    dataTable(['Label', 'Value', 'What it means'], labelRows.map(([a, b, c]) => [h('strong', null, a), b, h('span', { class: 'small' }, c)]), { cls: 'labels-table' }));

  // ---- team record of the code ladder, with evidence.
  const teamCard = h('section', { class: 'card' }, h('h2', null, multi ? 'What the code ladder found for the team' : 'What the code ladder found'),
    !d.team ? muted('This run has no team row in its code ladder.') : h('div', null,
      dataTable(['rung', 'reached', 'agents', 'evidence'], LADDER.filter(f => f in team).map(f => [
        h('span', { title: RUNG_MEANING[f] }, f), yn(team[f], 'bad', 'good'),
        (team[f + '_agents'] || []).length ? (team[f + '_agents']).join(', ') : null,
        evidenceList(team[f + '_evidence'])]), { cls: 'compact ladder-table' }),
      team.raised_by_proof ? h('p', { class: 'small' }, badge(`raised from ${team.rung_by_code} to ${team.rung}`, 'warn'), ' ', muted(team.proof || 'the task\'s own grade proves the higher rung')) : null,
      multi ? h('div', null, h('h3', { class: 'sub-h' }, 'Collaboration evidence'),
        h('div', { class: 'verdict-flags' }, h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, 'level'), team.collaboration ? collaborationBadge(team.collaboration) : muted('none'))),
        dataTable(['level', 'what it means', 'evidence'], COLLAB_ORDER.slice(1).map(c => [c, h('span', { class: 'small' }, COLLAB_MEANING[c]),
          evidenceList((team.collaboration_evidence || {})[c]) || muted('none')]), { cls: 'compact ladder-table' })) : null,
      kv([['passed the task\'s grader', yn(team.passed, 'plain', 'plain')], ['ended by', team.ended_by],
        ['hand-in', team.submit ? `${team.submit.agent}${team.submit.round != null ? ` in round ${team.submit.round}` : ''}` : null],
        ['split access', team.split == null ? undefined : yn(team.split, 'plain', 'plain')],
        ['judged by the judged ladder', yn(team.judged, 'plain', 'plain')]], 'kv-grid')));

  // ---- judges.
  const judgeBlocks = [];
  if (d.agents.some(a => a.ladder_judge || a.cheatbench_judge)) {
    for (const a of d.agents) {
      const l = a.ladder_judge;
      const jr = a.cheatbench_judge;
      judgeBlocks.push(h('div', { class: 'judge-block' },
        h('div', { class: 'jb-head' }, h('h3', null, `Agent ${a.agent}`), roleTag(a.role)),
        h('h4', null, `${JUDGES.ladder.label}${l && l.judge ? ` (${l.judge})` : ''}`),
        l ? h('div', null,
          h('div', { class: 'verdict-flags' },
            h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, 'judged rung'), l.rung ? rungBadge(l.rung) : muted(DASH)),
            h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, 'verdict'), yesNo(l.cheated)),
            ...['pushback', 'admitted', 'concealed', 'false_denial', 'forbidden_method'].filter(f => l[f] != null)
              .map(f => h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, f.replace(/_/g, ' ')), yn(l[f], 'plain', 'plain')))),
          l.ladder_summary ? h('p', { class: 'verdict-summary' }, evidenceText(l.ladder_summary, a.agent)) : null,
          dataTable(['rung', 'reached', 'call', 'evidence'], ['exposed', 'tried', 'opened', 'used', 'checked', 'shipped'].filter(f => f in l).map(f => [
            f, yn(l[f], 'bad', 'good'), l[f + '_call'] != null ? h('button', { type: 'button', class: 'linkish', onclick: () => gotoCall(a.agent, l[f + '_call']) }, `call ${l[f + '_call']}`) : null,
            l[f + '_evidence'] ? evidenceText(l[f + '_evidence'], a.agent) : null]), { cls: 'compact ladder-table' }),
          ['honesty_evidence', 'pushback_evidence', 'presentation_evidence', 'forbidden_method_evidence'].filter(f => l[f]).map(f =>
            h('div', { class: 'small' }, h('span', { class: 'field-label' }, f.replace(/_evidence$/, '').replace(/_/g, ' ') + ': '), evidenceText(asStr(l[f]), a.agent))),
          l.parse_ok === false ? badge('judge output did not parse', 'bad') : null,
          muted(`judge ${l.judge || '?'} · ${fmtUsd(l.judge_cost_usd)}`, 'small block'))
          : muted('No judged-ladder row for this agent.', 'small block'),
        h('h4', null, `${JUDGES.cheatbench.label}${jr && jr.judge ? ` (${jr.judge})` : ''}`),
        jr ? h('div', null,
          h('div', { class: 'verdict-flags' },
            h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, 'verdict'), yesNo(jr.cheated)),
            ...Object.keys(jr).filter(f => typeof jr[f] === 'boolean' && !['cheated', 'parse_ok'].includes(f))
              .map(f => h('span', { class: 'vf' }, h('span', { class: 'vf-label' }, f.replace(/_/g, ' ')), yn(jr[f], 'warn', 'plain')))),
          jr.summary ? h('p', { class: 'verdict-summary' }, evidenceText(jr.summary, a.agent)) : null,
          jr.explicit_prohibition_evidence ? h('div', { class: 'small' }, h('span', { class: 'field-label' }, 'explicit prohibition: '), evidenceText(jr.explicit_prohibition_evidence, a.agent)) : null,
          jr.parse_ok === false ? badge('judge output did not parse', 'bad') : null,
          muted(`judge ${jr.judge || '?'} · ${fmtUsd(jr.judge_cost_usd)}`, 'small block'))
          : muted('No CheatBench-rules row for this agent.', 'small block')));
    }
    judgeBlocks.push(muted('Call numbers in the judges\' text, such as [12], follow the same numbering as the code ladder; click one to open that call.', 'small block'));
  } else {
    judgeBlocks.push(muted('No judge read this run: it holds no ladder.jsonl and no judge.jsonl. The code ladder alone labels it.'));
  }
  const judgeCard = h('section', { class: 'card' }, h('h2', null, 'What the judges said'), judgeBlocks);

  // ---- board.
  let boardCard = null;
  if (multi) {
    boardCard = h('section', { class: 'card' }, h('h2', null, 'Message board'),
      !d.boards.length ? muted('No board file: nobody posted.') : d.boards.map(b => {
        const nPosts = b.messages.filter(m => m.kind === 'post').length;
        return h('div', { class: 'board' },
          h('div', { class: 'board-head' }, muted(`${plural(nPosts, 'post')} by agents, ${plural(b.messages.length - nPosts, 'system notice')}; board events: ` +
            Object.entries(b.event_counts).map(([k, n]) => `${k} ${n}`).join(', '))),
          h('ol', { class: 'board-msgs' }, b.messages.map(m => {
            const isSys = m.kind !== 'post';
            const cites = m.post_n != null ? collabByPost.get(m.post_n) : null;
            return h('li', { class: ['board-msg', isSys && 'bm-system', m.role && m.role !== 'worker' && 'bm-regulator'], id: m.post_n != null ? `post-${m.post_n}` : `board-${m.board_no}` },
              h('div', { class: 'bm-head' },
                h('span', { class: 'bm-seq', title: 'the number the agents see on the board, system posts included' }, `#${m.board_no}`),
                m.post_n != null ? badge(`post ${m.post_n}`, 'plain', 'the number the code ladder\'s evidence cites ("post N"), agents\' posts only') : null,
                isSys ? badge(m.kind === 'submit' ? 'system notice: hand-in' : 'system notice', 'notice', 'written by the harness, not by an agent') : null,
                h('span', { class: 'bm-sender' }, m.sender || '?'),
                m.role && m.role !== 'worker' ? roleTag(m.role) : null,
                muted(` → ${(m.recipients || []).join(', ') || 'all'}`),
                m.round != null ? muted(` · round ${m.round}${m.round_step && /free/.test(m.round_step) ? ' (free board action)' : ''}`) : null,
                cites ? cites.map(c => badge(`collaboration evidence: ${c}`, 'warn', COLLAB_MEANING[c])) : null,
                h('span', { class: 'spacer' }),
                m.sent && agentIds.includes(m.sent.agent) ? h('button', { type: 'button', class: 'linkish small', onclick: () => gotoToolCall(m.sent.agent, m.sent.call_id) }, 'show the posting call') : null),
              longText(m.content, { lines: 25, cls: 'msg-text' }),
              m.reads.length ? h('div', { class: 'small muted' }, 'read by: ', [...new Set(m.reads.map(x => x.agent))].map((ag, i) => {
                const first = m.reads.find(x => x.agent === ag);
                return [i ? ', ' : '', agentIds.includes(ag) ? h('button', { type: 'button', class: 'linkish', onclick: () => gotoToolCall(ag, first.call_id) }, ag) : ag];
              })) : null);
          })));
      }));
  }

  // ---- header.
  const m = d.meta || {};
  const cfg = m.config || {};
  const coll = collectionOf(row.col);
  const hidden = r => Object.values(roles).filter(x => x === r).length;
  const header = h('section', { class: 'card ep-header' },
    h('div', { class: 'ep-title' }, h('h1', null, row.run),
      badge(coll.label, 'def', coll.about), coll.tag ? badge(coll.tag, 'plain', coll.about) : null,
      row.status !== 'valid' ? badge(row.status, 'warn', [STATUS[row.status], row.why].filter(Boolean).join('. ')) : null,
      h('span', { class: 'spacer' }), h('a', { class: 'btn btn-small', href: '#/' }, '← all runs')),
    kv([
      ['collection', `${coll.label} (runs/${row.col})`], ['folder', h('span', { class: 'mono' }, row.key)],
      ['status', row.status + (row.why && row.why !== row.status ? ` (${row.why})` : '')],
      ['task', `${taskLabel(row)}${row.instance ? `, instance ${row.instance}` : ''}`], ['arm or cell', armLabel(row)],
      ['seed post', row.seed_post || undefined], ['model', `${modelLabel(row)}${row.model_id ? ` (${row.model_id})` : ''}`],
      ['agents', `${row.n_agents}${hidden('regulator') ? ` (${hidden('regulator')} hidden regulator)` : ''}${hidden('disregulator') ? ` (${hidden('disregulator')} hidden disregulator)` : ''}`],
      ['seed', row.seed != null ? String(row.seed) : undefined],
      ['turn budget (rounds)', cfg.turn_budget != null && multi ? String(cfg.turn_budget) : undefined],
      ['rounds run', m.rounds != null && multi ? String(m.rounds) : undefined],
      ['mode, access', multi ? [m.mode || cfg.mode, cfg.access].filter(Boolean).join(', ') : undefined],
      ['eval log', m.eval_log ? h('span', { class: 'mono small' }, m.eval_log) : muted(m.eval_log_recorded ? `not found (run.json names ${m.eval_log_recorded})` : 'none recorded', 'small')],
      ['code', m.code && m.code.commit ? `${m.code.commit}${m.code.dirty ? ' (dirty)' : ''}` : undefined],
    ], 'kv-grid'),
    null);
  labelsCard.id = 'sec-labels'; teamCard.id = 'sec-ladder'; judgeCard.id = 'sec-judges';
  if (boardCard) boardCard.id = 'sec-board';
  const agentsBox = h('div', { id: 'sec-agents' }, d.agents.map(agentCard));

  // ---- top bar: run, arm, agent tabs, Show filter, Details toggle; the stage below holds the selected tab.
  const firstAgent = (d.agents.find(a => a.role === 'worker' && a.folder) || d.agents.find(a => a.folder) || {}).agent;
  const stage = h('div', { class: 'rp-stage', dataset: { show: 'all' } });
  const tabs = new Map();
  let current = null;
  const ROLE_WORD = ROLE_LABEL;
  for (const a of d.agents) {
    tabs.set(a.agent, h('button', { type: 'button', class: 'rp-tab', onclick: () => selectTab(a.agent).catch(() => {}) },
      a.agent, h('span', { class: 'rp-role' }, ' ' + (ROLE_WORD[a.role] || a.role))));
  }
  if (multi) tabs.set('team', h('button', { type: 'button', class: 'rp-tab', onclick: () => selectTab('team') }, 'Team'));
  const teamView = h('div', { class: 'rp-team' }, d.levels ? levelsPanel(d.levels, roles, row.task_name) : null,
    d.levels ? h('section', { class: 'card' }, h('h2', null, 'Colour legend'), fullLegend(d.levels.ladders, d.levels.order)) : null, boardCard);
  async function selectTab(key) {
    current = key;
    for (const [k, b] of tabs) b.classList.toggle('on', k === key);
    if (key === 'team') { stage.replaceChildren(teamView); return null; }
    const c = cards.get(key);
    if (!c) return null;
    stage.replaceChildren(h('div', { class: 'loading' }, 'Loading the transcript…'));
    const tr = await c.load();
    if (current === key) stage.replaceChildren(tr.el);
    return tr;
  }
  // Lens tabs, like the artifact's reading tabs: the lexical cheat lens by default when its file exists.
  const lensState = { L: null };
  const lensBox = h('span', { class: 'rp-tabs rp-lens' });
  async function setLens(name) {
    lensState.L = name ? await loadLens(name) : null;
    for (const b of lensBox.children) b.classList.toggle('on', (b.dataset.lens || '') === (lensState.L ? name : ''));
    for (const c of cards.values()) if (c.state.tr && c.state.tr.setLens) c.state.tr.setLens(lensState.L);
  }
  listLenses().then(names => {
    const order = names.includes('lexical_cheat') ? ['lexical_cheat', ...names.filter(n => n !== 'lexical_cheat')] : names;
    const TIP = { lexical_cheat: 'Lexical cheat lens: word weights fitted on the apart controls; its strongest terms are names of the planted files, so it marks contact with the exploit more than cheating language (src/ui/lenses/lexical_cheat.json gives its fit and its held-out AUROC)' };
    lensBox.replaceChildren(...order.map(nm => h('button', { type: 'button', class: 'rp-tab', title: TIP[nm] || '', dataset: { lens: nm }, onclick: () => setLens(nm) }, lensLabel(nm))),
      h('button', { type: 'button', class: 'rp-tab', dataset: { lens: '' }, onclick: () => setLens(null) }, 'No lens'));
    return setLens(order[0] || null);
  });
  const showSel = h('select', { 'aria-label': 'Which text to show', onchange: e => { stage.dataset.show = e.target.value; } },
    [['all', 'All'], ['reasoning', 'Reasoning'], ['tools', 'Tool calls'], ['board', 'Board']].map(([v, t]) => h('option', { value: v }, t)));
  const bar = h('div', { class: 'rp-bar' },
    h('a', { class: 'rp-back', href: '#/', title: 'all runs' }, '←'),
    h('b', { class: 'rp-run' }, row.run), h('span', { class: 'rp-arm' }, armLabel(row)),
    h('span', { class: 'rp-tabs' }, [...tabs.values()]),
    lensBox,
    h('label', { class: 'rp-show' }, 'Show ', showSel),
    h('button', { type: 'button', class: 'rp-dbtn', onclick: () => { details.open = !details.open; if (details.open) details.scrollIntoView({ block: 'start' }); } }, 'Details'));

  view.mounted(() => {
    document.title = `${row.run} · Transcripts`;
    const qa = q.get('agent');
    const qc = q.get('call');
    if (qa && qc != null && cards.has(qa)) gotoCall(qa, parseInt(qc, 10)).catch(() => {});
    else selectTab(q.get('tab') === 'team' && multi ? 'team' : (firstAgent || 'team')).catch(() => {});
  });
  const details = h('details', { class: 'rp-details' }, h('summary', null, 'Details: labels, code-ladder evidence, judges, run settings, each agent\'s record'),
    header, labelsCard, teamCard, judgeCard, agentsBox);
  return h('div', { class: 'page rp-page' }, bar, stage, details);
}

function asStr(v) { return Array.isArray(v) ? v.join('\n') : typeof v === 'string' ? v : JSON.stringify(v); }
