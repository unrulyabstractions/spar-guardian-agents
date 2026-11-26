# Base Study

Tasks:
ian's
	* SWE
	* Creative Writing
	* Protein Design


Workers:
	* DeepSeek V4.1 Flash
	* GPT-6 Luna
	* Claude Haiku 5.5
	* Tencent HY4 Flash
	* Qwen 3.8 Flash
	* Meta Muse Ember

Influencers:
	- Regulator (generic)
		* Weak: Claude Haiku 5.5
		* Strong: GPT-6.1 Terra
		* Top: Claude Opus 5.5
	- Disregulator (instructed)
		* Weak: DeepSeek V4.1 Flash
		* Strong: Grok 4.7
		* Top Kimi: Kimi K3


Other settings:
	- All have secondary exploit
	- Only 1 per cell!


## Base Analysis

For all tasks/models:
(0): Baseline

For all tasks/models:
(1): Baseline + Seed

For those cheating (at or above attempted) in (1):
(-): Seed + Negative control (generic)

For those non-cheating (less than attempted) in (1):
(+): Seed + Positive control

## Regulator Analysis

For cheated (1) but not (-):
(R1): Baseline + Seed + Weak Regulator

For cheated (-):
(R2): Baseline + Seed + Strong Regulator

For hacked AND(0, 1, -):
(R3): Baseline + Seed + Top Regulator


## Disregulator Analysis

For non-cheated (1) but hacked (+):
(D1) Baseline + Seed + Weak Disregulator
(D2): Baseline + Seed + Strong Disregulator
(D3): Baseline + Seed + Top Disregulator




# Scale study









