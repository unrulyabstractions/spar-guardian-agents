/* The temporary directories of a run's machines on the Board tab's clock, from tmp_snapshots.json (/api/tmp?id=,
 * src/replay/tmp_snapshots.py). Pure: no DOM, no import, so node runs it in tests/unit/test_transcript_viewer_tmp.py.
 *
 *   import { tmpTimeline, countAt, stateAt, changesIn, goneAt, treeRows, readersOf } from './tv_tmp_state.js';
 *   const tl = tmpTimeline(doc, (agent, k) => t);   // t: the call's place on the board's clock
 *   const c0 = countAt(tl, tPrev), c1 = countAt(tl, t);
 *   const now = stateAt(tl, c1);                     // {machine: {path: entry}}
 *   const ch = changesIn(tl, c0, c1);                // {machine: {path: {kind, by}}}
 *   const gone = goneAt(tl, c1);                     // {machine: {path: {entry, by}}}: removed before c1, not back
 *   treeRows(tl.dirs, now.default, ch.default, stateAt(tl, c0).default, gone.default);
 *   readersOf(now.default, '/tmp/ref/part1', tl.users.default);   // ['s7m7z']: the users who can read it
 *
 * A step is a call that ran in a machine ("ran": true), in the file's order (n). Its time is its call's t on the
 * board's clock, never earlier than the step before it, so the steps applied at any time are a prefix and every delta
 * applies to the state it was taken against. A machine's state before its first call is that call's `before` (its
 * baseline): no call ran in it earlier. A later call's `before` holds what changed between calls (by no call: `by`
 * null). A directory whose mtime or size alone changed is not changed: its entries say what changed.
 *
 * Who can read an entry follows the kernel's check (acl(5)): the owner's bits; else a named-user ACL entry under the
 * mask; else any group entry that matches (the owning group, or a named one) under the mask; else other. Every
 * directory above it in the snapshot must also grant search (x). Root is left out. A file of schema 1 has no users,
 * group or acl: no readers. */

const DIR_NOISE = ['mtime', 'size'];
const sameValue = (x, y) => x === y || (Array.isArray(x) && Array.isArray(y) && x.length === y.length
  && x.every((v, i) => v === y[i]));

// Whether two entries of one path are the same, a directory's mtime and size left out.
export function sameEntry(a, b) {
  if (!a || !b) return a === b;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  const dir = a.type === 'dir' && b.type === 'dir';
  for (const k of keys) {
    if (dir && DIR_NOISE.includes(k)) continue;
    if (!sameValue(a[k], b[k])) return false;
  }
  return true;
}

function apply(state, change) {
  const out = { ...state };
  for (const [path, entry] of Object.entries(change || {})) {
    if (entry === null) delete out[path];
    else out[path] = entry;
  }
  return out;
}

// The steps that ran, each with its machine's baseline flag and its time; the machines and their baselines.
export function tmpTimeline(doc, timeOf) {
  const seen = new Set();
  const steps = [];
  let last = -Infinity;
  for (const s of doc.steps || []) {
    if (!s.ran) continue;
    const t = timeOf(s.agent, s.k);
    last = Math.max(last, Number.isFinite(t) ? t : last === -Infinity ? 0 : last);
    steps.push({ n: s.n, agent: s.agent, k: s.k, machine: s.machine, t: last, first: !seen.has(s.machine),
      before: s.before || {}, after: s.after || {} });
    seen.add(s.machine);
  }
  const baseline = {};
  for (const s of steps) if (s.first) baseline[s.machine] = apply({}, s.before);
  const machines = Object.keys(doc.machines || {}).filter(m => m in baseline);
  for (const m of Object.keys(baseline)) if (!machines.includes(m)) machines.push(m);
  return { dirs: doc.dirs || [], machines, agents: doc.machines || {}, users: doc.users || {}, steps,
    times: steps.map(s => s.t), baseline, memo: new Map([[0, baseline]]), goneMemo: new Map() };
}

// How many steps have run at time t: those whose time is at or before it.
export function countAt(tl, t) {
  let lo = 0;
  let hi = tl.times.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tl.times[mid] <= t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

// Every machine's state once the first `count` steps have run (each machine's baseline before its first).
export function stateAt(tl, count) {
  if (tl.memo.has(count)) return tl.memo.get(count);
  let from = count;
  while (!tl.memo.has(from)) from -= 1;
  let state = tl.memo.get(from);
  for (let i = from; i < count; i += 1) {
    const s = tl.steps[i];
    const base = s.first ? state[s.machine] : apply(state[s.machine], s.before);
    state = { ...state, [s.machine]: apply(base, s.after) };
    tl.memo.set(i + 1, state);
  }
  return state;
}

// What steps c0 to c1 changed, per machine: {path: {kind: added | changed | removed, by}}, by the last step that
// touched the path ({n, agent, k}), or null for a change between calls.
export function changesIn(tl, c0, c1) {
  const s0 = stateAt(tl, c0);
  const s1 = stateAt(tl, c1);
  const by = {};
  for (let i = c0; i < c1; i += 1) {
    const s = tl.steps[i];
    const mine = by[s.machine] || (by[s.machine] = {});
    if (!s.first) for (const p of Object.keys(s.before)) mine[p] = null;
    for (const p of Object.keys(s.after)) mine[p] = { n: s.n, agent: s.agent, k: s.k };
  }
  const out = {};
  for (const m of tl.machines) {
    const a = s0[m] || {};
    const b = s1[m] || {};
    const got = {};
    for (const p of new Set([...Object.keys(a), ...Object.keys(b)])) {
      const kind = !(p in a) ? 'added' : !(p in b) ? 'removed' : sameEntry(a[p], b[p]) ? null : 'changed';
      if (kind) got[p] = { kind, by: (by[m] || {})[p] ?? null };
    }
    out[m] = got;
  }
  return out;
}

// Every path removed before the first `count` steps had run and not there again, per machine: {path: {entry, by}},
// entry as it was last seen, by the step that removed it ({n, agent, k}), or null for a removal between calls.
export function goneAt(tl, count) {
  if (tl.goneMemo.has(count)) return tl.goneMemo.get(count);
  const gone = {};
  for (let i = 0; i < count; i += 1) {
    const s = tl.steps[i];
    const prev = stateAt(tl, i)[s.machine] || {};
    const mid = s.first ? prev : apply(prev, s.before);
    const next = stateAt(tl, i + 1)[s.machine] || {};
    const mine = gone[s.machine] || (gone[s.machine] = {});
    const by = { n: s.n, agent: s.agent, k: s.k };
    if (!s.first) {
      for (const p of Object.keys(s.before)) if (p in prev && !(p in mid)) mine[p] = { entry: prev[p], by: null };
    }
    for (const p of Object.keys(s.after)) if (p in mid && !(p in next)) mine[p] = { entry: mid[p], by };
    for (const p of Object.keys(mine)) if (p in next) delete mine[p];
  }
  tl.goneMemo.set(count, gone);
  return gone;
}

const parts = p => p.split('/').filter(Boolean);
function comparePaths(x, y) {
  const a = parts(x);
  const b = parts(y);
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return a.length - b.length;
}

// The rows of one machine's tree: the entries now, the paths removed in the window (from the state before it) and the
// paths removed earlier (gone: goneAt's, row.gone its {by}), the roots in the file's order, each path after its parent.
// Row: {path, name, depth, root, entry, change, gone, parent}.
export function treeRows(dirs, now, changes, before, gone) {
  const all = { ...(now || {}) };
  const ghost = {};
  for (const [p, c] of Object.entries(changes || {})) if (c.kind === 'removed') all[p] = (before || {})[p];
  for (const [p, g] of Object.entries(gone || {})) {
    if (!(p in all)) { all[p] = g.entry; ghost[p] = { by: g.by }; }
  }
  const rootOf = p => dirs.find(r => p === r || p.startsWith(r + '/'));
  const rows = [];
  for (const root of dirs) {
    const mine = Object.keys(all).filter(p => rootOf(p) === root).sort(comparePaths);
    for (const p of mine) {
      const depth = parts(p).length - parts(root).length;
      const cut = p.lastIndexOf('/');
      rows.push({ path: p, name: depth === 0 ? p : p.slice(cut + 1), depth, root, entry: all[p],
        change: (changes || {})[p] || null, gone: ghost[p] || null, parent: depth === 0 ? null : p.slice(0, cut) });
    }
  }
  return rows;
}

// rwx of three characters of a mode or an ACL line as bits (4 2 1); s and t count as x, S and T do not.
const bits = t => (t[0] === 'r' ? 4 : 0) | (t[1] === 'w' ? 2 : 0) | ('xst'.includes(t[2]) ? 1 : 0);
const R = 4;
const X = 1;

// Whether the user ({uid, gid, groups}, by name) holds `want` (bits) on the entry, by acl(5)'s access check.
export function allows(name, user, e, want) {
  if (!user || !e || !e.mode) return false;
  if (user.uid === 0) return true;
  const has = b => (b & want) === want;
  if (user.uid === e.uid) return has(bits(e.mode.slice(1, 4)));
  const inGroup = g => (user.groups || []).includes(g) || (g === e.group && user.gid === e.gid);
  if (e.acl) {
    const acl = e.acl.map(line => line.split(':'));
    const mask = acl.find(([tag]) => tag === 'mask');
    const m = mask ? bits(mask[2]) : 7;
    const named = acl.find(([tag, who]) => tag === 'user' && who && (who === name || who === String(user.uid)));
    if (named) return has(bits(named[2]) & m);
    const groups = acl.filter(([tag, who]) => tag === 'group' && inGroup(who || e.group));
    if (groups.length) return groups.some(([, , p]) => has(bits(p) & m));
    const other = acl.find(([tag]) => tag === 'other');
    return has(bits(other ? other[2] : e.mode.slice(7, 10)));
  }
  if (inGroup(e.group)) return has(bits(e.mode.slice(4, 7)));
  return has(bits(e.mode.slice(7, 10)));
}

// The machine's users who are its agents ({name: user}); every user when the file names no agent there.
export function agentUsers(tl, m) {
  const users = tl.users[m] || {};
  const agents = (tl.agents[m] || []).filter(a => a in users);
  return agents.length ? Object.fromEntries(agents.map(a => [a, users[a]])) : users;
}

// The users (of `users`, sorted, root left out) who can read the path in the state: read on it and search on every
// directory above it that the state holds. [] without users or without the entry's group.
export function readersOf(state, path, users) {
  const e = (state || {})[path];
  if (!e || !users || e.gid == null) return [];
  const above = [];
  for (let cut = path.lastIndexOf('/'); cut > 0; cut = path.lastIndexOf('/', cut - 1)) {
    const dir = state[path.slice(0, cut)];
    if (dir) above.push(dir);
  }
  return Object.keys(users).sort().filter(name => users[name].uid !== 0 && allows(name, users[name], e, R)
    && above.every(d => allows(name, users[name], d, X)));
}
