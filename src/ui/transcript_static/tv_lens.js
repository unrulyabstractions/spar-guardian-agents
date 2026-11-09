/* Sentence lenses: a score per sentence span (span.sn, see tv_case_view.js), drawn as a blue-to-red diverging tint over
 * -2 sd to +2 sd, faint near zero. Copied from collusion-hermaneutics' viewer (viewer/static/app/lens.js); only this
 * comment changed. Lenses are JSON files at src/ui/lenses/<name>.json, served by /api/lens?name=<name>.
 *
 * Two shapes plug into the same spans:
 *  1. Lexical lens (computed here):
 *       {"name": "lexical_cheat", "token_regex": "[a-z_]+", "weights": {"term": weight, ...},
 *        "sentence_mean": m, "sentence_sd": s, ...}
 *     tokens = lowercase matches of token_regex; score = mean weight of the known tokens (0 if none);
 *     z = (score - sentence_mean) / sentence_sd.
 *  2. Precomputed lens (a future activation reader):
 *       {"name": "...", "kind": "precomputed", "sentence_mean": m, "sentence_sd": s,   // omit both if scores are z
 *        "scores": {"<run id, e.g. study_exploration_1/flipt_minimal_luna_fixed1>": {"<agent>": {"<call k>": [...]}}}}
 *     Sentence i of call k is the i-th span.sn inside that call's block, in document order (reasoning, text, tool
 *     call, tool results). Messages outside any tool call use call "-1" in the same order.
 */
const CACHE = new Map();

export async function listLenses() {
  if (!CACHE.has('__list')) CACHE.set('__list', fetch('/api/lenses').then(r => (r.ok ? r.json() : { lenses: [] })).catch(() => ({ lenses: [] })));
  return (await CACHE.get('__list')).lenses || [];
}
export async function loadLens(name) {
  if (!name) return null;
  if (!CACHE.has(name)) {
    CACHE.set(name, fetch('/api/lens?name=' + encodeURIComponent(name)).then(r => (r.ok ? r.json() : null)).then(L => { if (L) L._key = name; return prep(L); }).catch(() => null));
  }
  return CACHE.get(name);
}
function prep(L) {
  if (!L) return null;
  if (L.kind !== 'precomputed') {
    let src = String(L.token_regex || '[a-z]+').replace(/^\(\?[a-z]+\)/, '');
    try { L._re = new RegExp(src, 'g'); } catch (e) { L._re = /[a-z0-9_]+/g; }
    L._w = new Map(Object.entries(L.weights || {}));
  }
  L._m = Number(L.sentence_mean) || 0;
  L._s = Number(L.sentence_sd) || 1;
  return L;
}
export function lensLabel(name) {
  if (/lens/i.test(name)) return name;
  return String(name).split('_').map((w, i) => (i ? w : w[0].toUpperCase() + w.slice(1))).join(' ') + ' lens';
}
/** z of one sentence; runId/agent/call/idx are used by precomputed lenses. NaN when the lens has no score. */
export function scoreZ(L, text, runId, agent, call, idx) {
  if (L.kind === 'precomputed') {
    const v = (((L.scores || {})[runId] || {})[agent] || {})[String(call)];
    const x = v ? v[idx] : null;
    return x == null ? NaN : (x - L._m) / L._s;
  }
  const toks = String(text).toLowerCase().match(L._re) || [];
  let sum = 0, nk = 0;
  for (const t of toks) if (L._w.has(t)) { sum += Number(L._w.get(t)); nk++; }
  const score = nk ? sum / nk : 0;
  return (score - L._m) / L._s;
}
// visualize_case.html colour(z, alpha): rgba of --neg or --pos at |z/2| (clamped) times alpha
export function colour(z, alpha) {
  if (!Number.isFinite(z)) return 'transparent';
  const t = Math.max(-1, Math.min(1, z / 2));
  return `rgba(var(${t < 0 ? '--neg' : '--pos'}),${(Math.abs(t) * alpha).toFixed(3)})`;
}
