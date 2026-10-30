/* API client for the results browser.
 * api() fetches one JSON endpoint and caches the answer in least-recently-used order. P names the API paths
 * and R the hash routes of the pages.
 *
 *   import { api, P, R } from './api.js';
 *   const runs = await api(P.runs());
 */
import { enc } from './dom.js';

export class ApiError extends Error {
  constructor(path, status, body) {
    super((body && body.error) || `HTTP ${status}`);
    this.path = path;
    this.status = status;
    this.body = body;
  }
}

const API_CACHE_MAX = 250;
export const apiCache = new Map();  // path -> Promise, kept in least-recently-used order
// One model call can be hundreds of KB, so single calls get their own small cache.
const CALL_CACHE_MAX = 40;
export const callCache = new Map();

function cachedFetch(cache, max, path) {
  if (cache.has(path)) {
    const p = cache.get(path);
    cache.delete(path);
    cache.set(path, p);
    return p;
  }
  const p = fetchJson(path);
  cache.set(path, p);
  p.catch(() => { if (cache.get(path) === p) cache.delete(path); });
  while (cache.size > max) cache.delete(cache.keys().next().value);
  return p;
}

export function api(path) { return cachedFetch(apiCache, API_CACHE_MAX, path); }
export function apiCall(path) { return cachedFetch(callCache, CALL_CACHE_MAX, path); }
/** Fetch again, replacing any cached copy, so runs and verdicts written since are seen. */
export function apiFresh(path) { apiCache.delete(path); return api(path); }

export async function fetchJson(path) {
  let res;
  try {
    res = await fetch(path, { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new ApiError(path, 0, { error: `network error: ${e && e.message}` });
  }
  const text = await res.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch (e) {
    body = { error: `non-JSON response (HTTP ${res.status})`, body: text.slice(0, 4000) };
  }
  if (!res.ok) throw new ApiError(path, res.status, body);
  return body;
}

export const P = {
  runs: () => '/api/runs',
  run: r => `/api/runs/${enc(r)}`,
  transcript: (r, ep) => `/api/runs/${enc(r)}/transcript/${enc(ep)}`,
  sample: r => `/api/runs/${enc(r)}/sample`,
  context: r => `/api/runs/${enc(r)}/context`,
  call: (r, i) => `/api/runs/${enc(r)}/context/${i}`,
  timeline: r => `/api/runs/${enc(r)}/timeline`,
  toolFile: (r, tool, file) => `/api/runs/${enc(r)}/tools/${enc(tool)}/` + String(file).split('/').map(enc).join('/'),
};

/** Rewrite the hash without navigating; a frame that refuses URL state keeps the view as drawn. */
export function setHashQuietly(target) {
  if (location.hash === target) return;
  try {
    history.replaceState(null, '', target);
  } catch (e) {
    /* a sandboxed frame may refuse URL state; the view is already on screen */
  }
}

/** Hash routes. */
export const R = {
  summary: () => '#/',
  runs: q => '#/runs' + query(q),
  swarm: q => '#/swarm' + query(q),
  run: r => `#/run/${enc(r)}`,
  episode: (r, ep, q) => `#/run/${enc(r)}/episode/${enc(ep)}` + query(q),
  context: (r, q) => `#/run/${enc(r)}/context` + query(q),
  sample: r => `#/run/${enc(r)}/sample`,
  timeline: r => `#/run/${enc(r)}/timeline`,
};

function query(q) {
  if (!q) return '';
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v != null && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? '?' + s : '';
}
