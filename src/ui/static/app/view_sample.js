/* View: sample (#/run/<run>/sample).
 * The page shows the sample facts from the eval log: scores, probe, limit and error.
 *
 *   import { viewSample } from './view_sample.js';
 *   const node = await viewSample(route, view);
 */
import { asText, fmtInt, fmtSecs, h } from './dom.js';
import { P, R, api } from './api.js';
import { badge, kv, lazyDetails, link, longText, mono, muted, section } from './components.js';

const META_ORDER = ['listing', 'git', 'agent_evals', 'agent_evals_main', 'probe'];

function probeView(probe) {
  if (!probe || typeof probe !== 'object' || Array.isArray(probe)) return h('pre', { class: 'pre' }, asText(probe));
  return h('div', { class: 'probe' }, Object.entries(probe).map(([k, v]) => {
    if (v && typeof v === 'object' && ('out' in v || 'rc' in v)) {
      return h('div', { class: 'probe-item' },
        h('div', { class: 'cfg-key' }, h('span', { class: 'mono' }, k), ' ',
          badge(`rc ${v.rc}`, v.rc === 0 ? 'good' : 'bad')),
        longText(asText(v.out), { lines: 30 }),
        v.err ? h('pre', { class: 'pre errtext' }, asText(v.err)) : null);
    }
    return h('div', { class: 'probe-item' }, h('div', { class: 'cfg-key mono' }, k), h('pre', { class: 'pre' }, asText(v)));
  }));
}

export async function viewSample({ run }, view) {
  const s = await api(P.sample(run));
  view.mounted(() => { document.title = `sample · ${run} · results`; });
  const lim = s.limit;
  const facts = section('Sample', kv([
    ['status', s.status ? badge(s.status, s.status === 'success' ? 'good' : 'bad') : null],
    ['model', s.model],
    ['eval id', s.eval_id ? mono(s.eval_id) : null],
    ['sample', s.sample_id != null ? `${s.sample_id} (epoch ${s.epoch})` : null],
    ['total time', fmtSecs(s.total_time)],
    ['working time', fmtSecs(s.working_time)],
    ['limit', lim ? `${lim.type}${lim.limit != null ? ' ' + lim.limit : ''}${lim.message ? ': ' + lim.message : ''}` : null],
    ['messages', fmtInt(s.n_messages)],
    ['events', fmtInt(s.n_events)],
  ], 'kv-grid'),
  s.error ? [h('h3', { class: 'sub-h' }, 'Error'), longText(s.error, { lines: 30, cls: 'errtext' })] : null);

  const usage = Object.entries(s.model_usage || {});
  const usageSec = section('Model usage', usage.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', null, h('tr', null, ['model', 'input', 'cache read', 'output', 'reasoning'].map((x, i) => h('th', { class: i ? 'num' : '' }, x)))),
    h('tbody', null, usage.map(([m, u]) => h('tr', null,
      h('td', { class: 'mono' }, m),
      ['input', 'cache_read', 'output', 'reasoning'].map(k => h('td', { class: 'num' }, fmtInt(u && u[k])))))))) : muted('No usage recorded.'));

  const scores = Object.entries(s.scores || {});
  const scoreSecs = scores.length ? scores.map(([name, sc]) => {
    const md = (sc && sc.metadata) || {};
    const keys = [...META_ORDER.filter(k => k in md), ...Object.keys(md).filter(k => !META_ORDER.includes(k))];
    return section(`Score: ${name}`,
      kv([
        ['value', asText(sc.value)],
        ['explanation', sc.explanation],
      ], 'kv-grid'),
      h('h3', { class: 'sub-h' }, 'Answer (message.txt)'),
      sc.answer ? longText(sc.answer, { lines: 40 }) : muted('No answer.'),
      keys.map(k => h('div', { class: 'meta-block' },
        h('h3', { class: 'sub-h' }, k),
        k === 'probe' ? probeView(md[k]) : longText(asText(md[k]), { lines: 60, chars: 40000 }))));
  }) : section('Scores', muted('No scores (the sample did not reach the scorer).'));

  const storeSec = s.store
    ? section('Store', lazyDetails('sample store (JSON)', () => longText(JSON.stringify(s.store, null, 2), { lines: 80, chars: 60000 }), { cls: 'sub' }))
    : null;

  return h('div', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', null, `${run}: sample and workspace`),
      h('div', { class: 'links' }, link(R.run(run), 'run page', 'btn btn-small'), link(R.context(run), 'context windows', 'btn btn-small'))),
    facts, usageSec, scoreSecs, storeSec);
}
