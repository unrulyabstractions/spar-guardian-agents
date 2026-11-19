/* Plain-language names and meanings for every value the transcript viewer shows. Ported from collusion-hermaneutics'
 * viewer (viewer/static/app/labels.js). The collections come from /api/index (src/ui/transcript_index.py
 * COLLECTION_ABOUT); each run's task, agents, worker and influencer conditioning and arm come from the server; the
 * ladders' levels and meanings come from /api/ladders (src/ui/transcript_levels.py LADDERS). */

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

// The workers' model by the last part of its model string (src/ui/transcript_viewer.py short_model).
export const MODELS = {
  'deepseek-v4.1-flash': 'DeepSeek V4.1 Flash', 'gpt-5-mini': 'GPT-5 mini', 'claude-haiku-5.5': 'Claude Haiku 5.5',
  'claude-haiku-5-5': 'Claude Haiku 5.5', 'gpt-6-luna': 'GPT-6 Luna', 'qwen3.8-27b': 'Qwen3.8 27B',
};

// CheatBench's judge: the eval's own verdicts and the judge passes' (judges/cheatbench.<judge>.jsonl), both read
// through src/judging/verdict_files.py.
export const CHEATBENCH = {
  name: 'CheatBench judge',
  about: 'CheatBench\'s judge, written for a lone agent, read each agent\'s trajectory: whether it cheated and whether it saw the honeypots, with the task\'s own fields. Its own "cheated" (judge_cheated) is broader than the Cheating ladder\'s and changes no rung. The team\'s word is how many workers it says cheated, of the workers it gave a parsed verdict. It stands under the code\'s rung in every Cheating cell, marked as the judge\'s.',
};

// Disclosure, which CheatBench's judge records, a judge label beside the ladders (src/measures/judge_labels.py
// disclosure).
export const DISCLOSURE = {
  column: 'Disclosure',
  about: 'Whether CheatBench\'s judge found that the agent openly acknowledged its own access to the honeypot (protein design and creative writing record it; SWE\'s verdict has no such field: not recorded). No rung.',
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
