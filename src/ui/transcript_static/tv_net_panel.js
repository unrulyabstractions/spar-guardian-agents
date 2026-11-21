/* The Board tab's Network panel: the run's network log at the position, from /api/network?id= (src/ui/transcript_board.py
 * network_payload, src/ui/transcript_network.py network_view). Shown only when the board payload says the run has
 * network.jsonl (BoardTab.network).
 *
 *   const panel = netPanel(url, { callHref, onLoad });
 *   panel.el;                    // the card, to place in the tab
 *   panel.pair(a, b);            // the two agents in the lanes, whose names take the lanes' colours
 *   panel.at(t, tPrev);          // the position: what listens at t, and every connect, accept and send up to t
 *   onLoad(view)                 // once the log has come: the tab marks the lanes with its calls (laneMarks)
 *
 * The head holds the log's method and status, the counts at the position, and three filters: named agents only,
 * cross-agent calls only, and TCP and UDP only. Listening: each socket open at the position, by machine, with its port,
 * its program, its agent and the call that opened it. Calls: newest first, a `+` on those since the stop before, a
 * cross-agent call in the bad colour with its two agents and the port it reached. A row off the clock (no call, no
 * time) counts at the end. The panel carries no sentence: labels, values, chips and short hovers. */
import { enc, h, plural } from '/static/app/dom.js';
import { muted } from '/static/app/components.js';

export const netUrl = id => `/api/network?id=${enc(id)}`;

const SHOWN = 200;            // the most calls listed at once
const LOG_CLASS = { valid: 'tmp-c-added', partial: 'tmp-c-changed', broken: 'tmp-c-removed',
  absent: 'tmp-c-removed' };
const LOG_TIP = { valid: 'every control seen, nothing lost', partial: 'controls seen, something lost',
  broken: 'a control missing: an absence means nothing', absent: 'no record came back' };
const METHOD_TIP = { ebpf: 'kernel trace of every socket call', procnet: 'socket tables read every 0.25 s' };
const OP_TIP = { connect: 'connect', accept: 'accept', send: 'UDP send to an address' };

export const at = row => (row.t == null ? Infinity : row.t);
const address = (addr, port) => (addr == null ? '' : port ? `${addr}:${port}` : addr);
// The server's end of a call: the address a connect or a send reached; the listening address an accept took it on.
export const serverEnd = c => (c.op === 'accept' ? [c.laddr, c.lport] : [c.raddr, c.rport]);
// The process of a row, for a hover: its command line, pid and uid.
const procTip = r => [r.cmd || r.exe, r.pid != null ? `pid ${r.pid}` : null, r.uid != null ? `uid ${r.uid}` : null]
  .filter(Boolean).join(' · ');
// A call that marks the lanes: one over TCP or UDP, or any cross-agent call.
export const marksLane = c => c.proto !== 'unix' || c.cross;
// One short line for a call: "a01 connect → a02:8180", "a02 accept ← a01 :8180".
export function callWords(c) {
  const [addr, port] = serverEnd(c);
  const peer = c.peer_agent ? `${c.peer_agent}${port ? `:${port}` : ''}` : address(addr, port);
  return `${c.agent || c.origin || '?'} ${c.op} ${c.op === 'accept' ? '←' : '→'} ${peer}`;
}

export function netPanel(url, opts) {
  let view = null;
  let pair = { a: null, b: null };
  let pos = [0, -Infinity];
  let agentsOnly = false;
  let crossOnly = false;
  let inetOnly = false;
  let drawn = null;

  const toggle = (label, tip, get, set) => {
    const b = h('button', { type: 'button', class: 'bd-count net-toggle', title: tip,
      onclick: () => { set(!get()); b.classList.toggle('net-on', get()); draw(true); } }, label);
    return b;
  };
  const chips = h('span', { class: 'tmp-counts' });
  const counts = h('span', { class: 'tmp-counts' });
  const head = h('div', { class: 'tmp-head' },
    h('h3', { class: 'bd-board-head', title: 'socket calls in the agents\' machines' }, 'Network'),
    chips, counts, h('span', { class: 'spacer' }),
    toggle('agents', 'named agents only', () => agentsOnly, v => { agentsOnly = v; }),
    toggle('cross', 'cross-agent calls only', () => crossOnly, v => { crossOnly = v; }),
    toggle('inet', 'TCP and UDP only', () => inetOnly, v => { inetOnly = v; }));
  const listenBox = h('div', { class: 'tmp-machine-box net-box' }, muted('loading', 'small block net-none'));
  const callBox = h('div', { class: 'tmp-machine-box net-box net-calls' });
  const el = h('section', { class: 'card bd-tmp bd-net' }, head, h('div', { class: 'tmp-body' }, listenBox, callBox));

  fetch(url).then(async r => {
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || `HTTP ${r.status}`);
    view = doc;
    const block = view.block || {};
    chips.replaceChildren(...[
      block.method ? h('span', { class: 'bd-count', title: [METHOD_TIP[block.method], block.fallback]
        .filter(Boolean).join(' · ') }, block.method) : null,
      block.status ? h('span', { class: ['bd-count', LOG_CLASS[block.status]], title: LOG_TIP[block.status] },
        block.status) : null].filter(Boolean));
    draw(true);
    if (opts.onLoad) opts.onLoad(view);
  }).catch(e => {
    listenBox.replaceChildren(h('span', { class: 'error-box small' }, `network: ${e.message}`));
  });

  const sideOf = agent => (agent && agent === pair.a ? 'a' : agent && agent === pair.b ? 'b' : null);
  const who = (agent, origin) => {
    if (!agent) return h('span', { class: 'tmp-agent muted', title: origin || null }, origin || '—');
    const side = sideOf(agent);
    return h('span', { class: ['tmp-agent', side ? `bd-key-${side}` : null] }, agent);
  };
  // The call a row sits in; the agent's name too when the call is not the row's own agent's.
  const callLink = (c, agent) => (c ? h('a', { class: 'bd-call-link tmp-by', href: opts.callHref(c.agent, c.k),
    title: `open call ${c.k} in ${c.agent}'s tab` }, c.agent !== agent ? `${c.agent} ` : '', `call ${c.k}`)
    : muted('no call', 'small'));
  const prog = r => h('span', { class: 'net-prog', title: procTip(r) || null }, r.prog || '?');
  const result = r => h('span', { class: ['bd-count', r === 'ok' ? 'tmp-c-added' : r === 'EINPROGRESS' ? null
    : 'tmp-c-removed'], title: r === 'EINPROGRESS' ? 'connect did not wait' : null }, r || '?');
  const keep = row => (!agentsOnly || row.agent) && (!crossOnly || row.cross) && (!inetOnly || row.proto !== 'unix');
  // The machine's name: an agent's own machine (named as the agent) in the agent's colour, a shared one plain.
  const machineName = m => {
    const side = sideOf(m);
    return h('b', { class: ['tmp-machine', side ? `bd-key-${side}` : 'tmp-machine-name'] }, m || '?');
  };

  function draw(force) {
    if (!view) return;
    const [t, tPrev] = pos;
    const open = view.listeners.filter(l => (l.at_start || at(l) <= t) && (l.until == null || l.until > t))
      .filter(l => (!agentsOnly || l.agent) && (!inetOnly || l.proto !== 'unix'));
    const done = view.calls.filter(c => at(c) <= t);
    const key = [open.length, done.length, tPrev, agentsOnly, crossOnly, inetOnly, pair.a, pair.b].join('/');
    if (!force && key === drawn) return;
    drawn = key;
    const cross = done.filter(c => c.cross).length;
    counts.replaceChildren(...[
      h('span', { class: 'bd-count', title: 'sockets listening at the position' }, plural(open.length, 'listener')),
      h('span', { class: 'bd-count', title: 'connects, accepts and sends so far' }, plural(done.length, 'call')),
      h('span', { class: ['bd-count', cross ? 'tmp-c-removed' : null], title: 'from one agent to another' },
        `${cross} cross-agent`),
      view.cut ? h('span', { class: 'bd-count', title: 'calls past the panel\'s cap' }, `+${view.cut} cut`) : null,
    ].filter(Boolean));
    const machines = [...new Set(open.map(l => l.machine))].sort();
    listenBox.replaceChildren(h('div', { class: 'tmp-mhead' }, h('b', null, 'Listening')),
      open.length ? h('div', { class: 'tmp-rows' }, ...machines.flatMap(m => [
        h('div', { class: 'net-machine' }, machineName(m),
          h('span', { class: 'muted small' }, plural(open.filter(l => l.machine === m).length, 'socket'))),
        ...open.filter(l => l.machine === m).map(l => h('div', { class: ['tmp-row', 'net-lrow',
          at(l) > tPrev && at(l) <= t ? 'tmp-added' : null], title: l.at_start ? 'there at the start' : null },
        h('span', { class: 'tmp-mark' }, at(l) > tPrev && at(l) <= t ? '+' : ''),
        h('span', { class: 'tmp-name', title: address(l.laddr, l.lport) }, l.lport ? h('b', null, `:${l.lport}`) : '',
          h('span', { class: 'muted' }, ` ${l.laddr || ''}`)),
        h('span', { class: 'bd-count' }, l.proto),
        prog(l),
        who(l.agent, l.origin),
        h('span', { class: 'tmp-by-cell' }, l.at_start ? muted('at start', 'small') : callLink(l.call, l.agent))))]))
        : muted('none', 'small block net-none'));
    const shown = done.filter(keep).reverse();
    callBox.replaceChildren(h('div', { class: 'tmp-mhead' }, h('b', null, 'Calls'),
      shown.length > SHOWN ? h('span', { class: 'bd-count', title: 'older calls not listed' },
        `+${shown.length - SHOWN} older`) : null),
    shown.length ? h('div', { class: 'tmp-rows' }, ...shown.slice(0, SHOWN).map(c => {
      const [addr, port] = serverEnd(c);
      const isNew = at(c) > tPrev;
      return h('div', { class: ['tmp-row', 'net-crow', c.cross ? 'net-cross' : isNew ? 'tmp-added' : null] },
        h('span', { class: 'tmp-mark', title: isNew ? 'since the previous stop' : null }, isNew ? '+' : ''),
        h('span', { class: 'bd-count', title: OP_TIP[c.op] }, c.op),
        h('span', { class: 'bd-count' }, c.proto),
        h('span', { class: 'net-who', title: c.cross ? `${c.agent} reached ${c.peer_agent}` : null },
          who(c.agent, c.origin),
          c.peer_agent ? [h('span', { class: 'muted' }, c.op === 'accept' ? ' ← ' : ' → '), who(c.peer_agent),
            c.op !== 'accept' && port ? h('span', { class: 'net-port' }, `:${port}`) : null] : null),
        prog(c),
        h('span', { class: 'tmp-name', title: [c.machine ? `on ${c.machine}` : null,
          c.op === 'accept' ? `from ${address(c.raddr, c.rport)}` : null].filter(Boolean).join(' · ') || null },
        address(addr, port)),
        result(c.result),
        h('span', { class: 'tmp-by-cell' }, callLink(c.call, c.agent)));
    })) : muted('none', 'small block net-none'));
  }

  return {
    el,
    pair(a, b) { pair = { a, b }; draw(true); },
    at(t, tPrev) { pos = [t, tPrev]; draw(false); },
  };
}
