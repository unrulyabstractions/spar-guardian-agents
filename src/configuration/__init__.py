"""Settings, the swarm config, its presets and model-facing text (prompts.yaml, instruction_levels.yaml).

API modules:
- settings.py: paths, model names and fixed settings shared by the whole of src/.
- swarm_config.py: SwarmConfig, every knob of one swarm run.
- presets.py: SwarmConfig from text: the presets, `key=value` pairs and the shared command-line flags.
- scales.py: the size of a run as one name.
- access_policy.py: which parts of the exploit each agent holds.
- prompt_texts.py: the system prompt texts that depend on a setting.
"""
