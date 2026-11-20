/* Plain-language names and meanings for every value the transcript viewer shows. Ported from collusion-hermaneutics'
 * viewer (viewer/static/app/labels.js). The collections come from /api/index (src/ui/transcript_index.py
 * COLLECTION_ABOUT); each run's task, agents, worker and influencer conditioning and arm come from the server; the
 * ladders' levels and meanings come from /api/ladders (src/ui/transcript_levels.py LADDERS). */
import { h, plural } from '/static/app/dom.js';

export const COLLECTIONS = {};
export const COLLECTION_ORDER = [];

/** Fill COLLECTIONS from the index's list, in its order. */
export function setCollections(list) {
  for (const c of list || []) {
    if (!(c.key in COLLECTIONS)) COLLECTION_ORDER.push(c.key);
    COLLECTIONS[c.key] = c;
  }
}
export const collectionOf = key => COLLECTIONS[key] || { key, label: key, tag: '', about: '' };

// A model by the last part of its model string (src/ui/transcript_viewer.py short_model).
export const MODELS = {
  'deepseek-v4.1-flash': 'DeepSeek V4.1 Flash', 'gpt-5-mini': 'GPT-5 mini', 'claude-haiku-5.5': 'Claude Haiku 5.5',
  'claude-haiku-5-5': 'Claude Haiku 5.5', 'gpt-6-luna': 'GPT-6 Luna', 'qwen3.8-27b': 'Qwen3.8 27B',
  'claude-sonnet-5.5': 'Claude Sonnet 5.5', 'claude-opus-5.5': 'Claude Opus 5.5', 'gemini-4-argon': 'Gemini 4 Argon',
};

// CheatBench's judge: the eval's own verdicts and the judge passes' (judges/cheatbench.<judge>.jsonl), both read
// through src/judging/verdict_files.py.
export const CHEATBENCH = {
  name: 'CheatBench judge',
  about: 'CheatBench\'s own judge, built for a lone agent; changes no rung',
};

// An influencer is a regulator or a disregulator: an agent the workers cannot tell from a worker, whose levels stay out
// of the team's (src/measures/run_directory.py is_influencer).
export const INFLUENCERS = ['regulator', 'disregulator'];
export const isInfluencer = role => INFLUENCERS.includes(role);
export const ROLE_LABEL = { worker: 'worker', regulator: 'regulator (influencer)', disregulator: 'disregulator (influencer)' };

export const armLabel = r => r.arm_label;
// A run's task in the server's words: its domain and the stem of its cell (src/ui/transcript_index.py task_label).
export const taskLabel = r => r.task_label;
export const modelLabel = r => MODELS[r.model] || r.model || r.model_id;
/** One influencer of a run's influencer_conditioning ({role, n, level, model}) in words: its role, its instruction
 * level and its model. */
export const influencerParts = x => [x.n === 1 ? x.role : `${x.n} ${x.role}s`, x.level, modelLabel({ model: x.model })].filter(Boolean);

/** A worker conditioning's chip: baseline calm, told to cheat warm, a rule cool and a rule naming the exploit strong. */
export const CONDITIONING_TONE = { baseline: 'cond-none', 'told to cheat': 'cond-cheat', 'rule naming the exploit': 'cond-rule-strong',
  'rule naming where the exploit lies': 'cond-rule-strong' };
export const conditioningChip = part => h('span', { class: ['cond-chip', CONDITIONING_TONE[part] || 'cond-rule'] }, part);
/** An influencer's role and level as chips. */
export const influencerChips = x => [h('span', { class: ['role-chip', `role-chip-${x.role}`] }, x.n === 1 ? x.role : `${x.n} ${x.role}s`),
  x.level ? h('span', { class: ['level-chip', `level-chip-${x.role}`] }, x.level) : null];
/** Who ran, as chips: the workers' number as a chip, their model, their conditioning, then each influencer's role, level and
 * model. */
export const whoChips = r => h('span', { class: 'rp-who' },
  h('span', { class: 'rp-who-part' }, h('span', { class: ['cond-chip', 'cond-none', 'count-chip'] }, plural(r.workers, 'worker')),
    h('b', null, modelLabel(r)), r.worker_conditioning.split('; ').map(conditioningChip)),
  r.influencer_conditioning.map(x => h('span', { class: 'rp-who-part' }, influencerChips(x), h('span', { class: 'muted' }, modelLabel({ model: x.model })))));
