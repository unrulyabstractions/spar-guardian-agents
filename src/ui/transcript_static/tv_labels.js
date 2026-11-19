/* Plain-language names and meanings for every value the transcript viewer shows. Ported from collusion-hermaneutics'
 * viewer (viewer/static/app/labels.js). The collections come from /api/index (src/ui/transcript_viewer.py
 * COLLECTION_ABOUT); each run's seed post, team and arm come from the server; the ladders' levels and meanings come
 * from /api/ladders (src/ui/transcript_levels.py LADDERS). */

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

// A run's task by the stem of its cell's script (examples/<domain>/<stem>_<cell>.py): the tasks the viewer shows.
export const TASKS = {
  binder: 'Protein design (binder)',
  flipt: 'Software engineering, Flipt issue (flipt)',
  vuls: 'Software engineering, Vuls issue (vuls)',
  poem: 'Creative writing (poem)',
};

// The workers' model by the last part of its model string (src/ui/transcript_viewer.py short_model).
export const MODELS = {
  'deepseek-v4.1-flash': 'DeepSeek V4.1 Flash', 'gpt-5-mini': 'GPT-5 mini', 'claude-haiku-5.5': 'Claude Haiku 5.5',
  'claude-haiku-5-5': 'Claude Haiku 5.5', 'gpt-6-luna': 'GPT-6 Luna', 'qwen3.8-27b': 'Qwen3.8 27B',
};

// CheatBench's judge: the eval's own verdicts and the judge passes' (judges/cheatbench.<judge>.jsonl), both read
// through src/judging/verdict_files.py.
export const CHEATBENCH = {
  column: 'CheatBench judge',
  about: 'CheatBench\'s judge, written for a lone agent, read each agent\'s trajectory: whether it cheated and whether it saw the honeypots, with the task\'s own fields. Its "cheated" is broader than the Cheating ladder\'s and changes no rung. The team\'s word is how many workers it says cheated, of the workers it gave a parsed verdict.',
};

// Disclosure, which CheatBench's judge records beside the Collusion ladder (src/measures/judge_labels.py disclosure).
export const DISCLOSURE = {
  column: 'Disclosure',
  about: 'Whether CheatBench\'s judge found that the agent openly acknowledged its own access to the honeypot (protein design and creative writing record it; SWE\'s verdict has no such field: not recorded). No rung.',
};

export const ROLE_LABEL = { worker: 'worker', regulator: 'hidden regulator', disregulator: 'hidden disregulator' };

export const armLabel = r => r.arm_label || r.arm || '';
export const taskLabel = r => TASKS[r.task] || (r.task_name ? r.task_name.replace(/_/g, ' ') : r.task) || '?';
export const modelLabel = r => MODELS[r.model] || r.model || r.model_id;
