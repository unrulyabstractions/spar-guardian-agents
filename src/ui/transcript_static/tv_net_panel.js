/* The Board tab's Network panel: the run's network log at the position, from the board payload's `network`
 * (src/ui/transcript_network.py network_view). Shown only on a run with network.jsonl.
 *
 *   const panel = netPanel(view, { callHref });
 *   panel.el;                    // the card, to place in the tab
 *   panel.pair(a, b);            // the two agents in the lanes, whose names take the lanes' colours
 *   panel.at(t, tPrev);          // the position: what listens at t, and every connect, accept and send up to t
 *
 * The head holds the log's method and status, the counts at the position, and three filters: named agents only,
 * cross-agent calls only, and TCP and UDP only. Listening: each socket open at the position, by machine, with the
 * call that opened it. Calls: newest first, a `+` on those since the stop before, a cross-agent call marked in the
 * bad colour. A row off the clock (no call, no time) counts at the end. The panel carries no sentence: labels,
 * values, chips and short hovers. */
import { h, plural } from '/static/app/dom.js';
import { muted } from '/static/app/components.js';

const SHOWN = 200;            // the most calls listed at once
const LOG_CLASS = { valid: 'tmp-c-added', partial: 'tmp-c-changed', broken: 'tmp-c-removed',
  absent: 'tmp-c-removed' };
const LOG_TIP = { valid: 'every control seen, nothing lost', partial: 'controls seen, something lost',
  broken: 'a control missing: an absence means nothing', absent: 'no record came back' };
const METHOD_TIP = { ebpf: 'kernel trace of every socket call', procnet: 'socket tables read every 0.25 s' };
const OP_TIP = { connect: 'connect', accept: 'accept', send: 'UDP send to an address' };

const at = row => (row.t == null ? Infinity : row.t);
const address = (addr, port) => (addr == null ? '' : port ? `${addr}:${port}` : addr);

export function netPanel(view, opts) {
  const block = view.block || {};
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
  const counts = h('span', { class: 'tmp-counts' });
  const head = h('div', { class: 'tmp-head' },
    h('h3', { class: 'bd-board-head', title: 'socket calls in the agents\' machines' }, 'Network'),
    block.method ? h('span', { class: 'bd-count', title: [METHOD_TIP[block.method], block.fallback]
      .filter(Boolean).join(' · ') }, block.method) : null,
    block.status ? h('span', { class: ['bd-count', LOG_CLASS[block.status]], title: LOG_TIP[block.status] },
      block.status) : null,
    counts, h('span', { class: 'spacer' }),
    toggle('agents', 'named agents only', () => agentsOnly, v => { agentsOnly = v; }),
    toggle('cross', 'cross-agent calls only', () => crossOnly, v => { crossOnly = v; }),
    toggle('inet', 'TCP and UDP only', () => inetOnly, v => { inetOnly = v; }));
  const listenBox = h('div', { class: 'tmp-machine-box net-box' });
  const callBox = h('div', { class: 'tmp-machine-box net-box net-calls' });
  const el = h('section', { class: 'card bd-tmp bd-net' }, head, h('div', { class: 'tmp-body' }, listenBox, callBox));

  const sideOf = agent => (agent && agent === pair.a ? 'a' : agent && agent === pair.b ? 'b' : null);
  const who = (agent, origin) => {
    if (!agent) return h('span', { class: 'tmp-agent muted', title: origin || null }, origin || '—');
    const side = sideOf(agent);
    return h('span', { class: ['tmp-agent', side ? `bd-key-${side}` : null] }, agent);
  };
  const callLink = c => (c ? h('a', { class: 'bd-call-link tmp-by', href: opts.callHref(c.agent, c.k),
    title: `open call ${c.k} in ${c.agent}'s tab` }, `call ${c.k}`) : muted('no call', 'small'));
  const result = r => h('span', { class: ['bd-count', r === 'ok' ? 'tmp-c-added' : r === 'EINPROGRESS' ? null
    : 'tmp-c-removed'], title: r === 'EINPROGRESS' ? 'connect did not wait' : null }, r || '?');
  const keep = row => (!agentsOnly || row.agent) && (!crossOnly || row.cross) && (!inetOnly || row.proto !== 'unix');

  function draw(force) {
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
    listenBox.replaceChildren(h('div', { class: 'tmp-mhead' }, h('b', null, 'Listening')),
      open.length ? h('div', { class: 'tmp-rows' }, ...open.map(l => h('div', { class: 'tmp-row net-lrow',
        title: l.at_start ? 'there at the start' : null },
      h('span', { class: 'tmp-mark' }, at(l) > tPrev && at(l) <= t ? '+' : ''),
      h('span', { class: 'tmp-name' }, address(l.laddr, l.lport)),
      h('span', { class: 'bd-count' }, l.proto),
      who(l.agent, l.origin),
      h('span', { class: 'tmp-agent muted small', title: 'machine' }, l.machine || ''),
      h('span', { class: 'tmp-by-cell' }, l.at_start ? muted('at start', 'small') : callLink(l.call)))))
        : muted('none', 'small block net-none'));
    const shown = done.filter(keep).reverse();
    callBox.replaceChildren(h('div', { class: 'tmp-mhead' }, h('b', null, 'Calls'),
      shown.length > SHOWN ? h('span', { class: 'bd-count', title: 'older calls not listed' },
        `+${shown.length - SHOWN} older`) : null),
    shown.length ? h('div', { class: 'tmp-rows' }, ...shown.slice(0, SHOWN).map(c => h('div', {
      class: ['tmp-row', 'net-crow', c.cross ? 'net-cross' : null, at(c) > tPrev ? 'tmp-added' : null] },
    h('span', { class: 'tmp-mark', title: at(c) > tPrev ? 'since the previous stop' : null },
      at(c) > tPrev ? '+' : ''),
    h('span', { class: 'bd-count', title: OP_TIP[c.op] }, c.op),
    h('span', { class: 'bd-count' }, c.proto),
    h('span', { class: 'net-who' }, who(c.agent, c.origin), c.peer_agent
      ? h('span', { class: 'muted' }, c.op === 'accept' ? ' ← ' : ' → ') : null,
    c.peer_agent ? who(c.peer_agent) : null),
    h('span', { class: 'tmp-name', title: c.machine ? `on ${c.machine}` : null }, address(c.raddr, c.rport)),
    result(c.result),
    h('span', { class: 'tmp-by-cell' }, callLink(c.call)))))
      : muted('none', 'small block net-none'));
  }

  return {
    el,
    pair(a, b) { pair = { a, b }; draw(true); },
    at(t, tPrev) { pos = [t, tPrev]; draw(false); },
  };
}
