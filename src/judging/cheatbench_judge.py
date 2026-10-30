"""CheatBench's judge, run in process through Inspect's model API.

CheatBench's core/judge.py supplies everything that defines a verdict: the task's Verdict schema with the shared
rule fields (load_judge_spec), the judge prompt (build_judge_prompt over render_transcript) and the validation of the
reply (Verdict.model_validate_json). Inspect makes the call, with the Verdict as a strict JSON response schema, as
CheatBench's llm_agents._coerce_response_format sends it. Judge entries come from src/judging/judge_models.yaml in
CheatBench's models.yaml format.

    judge = Judge.of("gpt-6-luna", batch=True)
    verdicts = await judge_sample(view, judge_spec(task_dir, "openmath_agent"), judge)   # {agent: verdict}

The scorer `cheatbench_judge` runs judge_sample on a live sample, and `python -m src judge` on every sample of a
finished eval log. A verdict holds the judge's settings, the task's verdict fields and the call's usage and cost; the
export adds the episode's identity when it writes judge.jsonl, in the row shape of CheatBench's judge.jsonl.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from inspect_ai.log import transcript as inspect_transcript
from inspect_ai.model import ChatMessageUser, GenerateConfig, Model, ResponseSchema, get_model, model_roles
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import JSONSchema
from openai.lib._parsing._completions import type_to_response_format_param

from src.configuration import settings
from src.export.atif_transcripts import episode_transcript
from src.export.eval_sample_view import SampleView, agent_messages, roster, usage_dict
from src.models.model_registry import cost_usd, usd_per_million
from src.sandbox.staging import write_atomic
from src.utils.cheatbench_core import judge as cb_judge

JUDGE_SCORER = "cheatbench_judge"
JUDGE_ROLE = "judge"
# Lucy's judge addendum, appended to the task's judge schema when the config asks for it (SwarmConfig.judge_addendum)
JUDGE_ADDENDUM = settings.LUCY / "experiments" / "orbit_cheatbench" / "judge_addendum.py"


# --------------------------------------------------------------------------
# the spec, the judge and the schema
# --------------------------------------------------------------------------
def judge_spec(task_dir: Path, task_name: str, *, addendum: Path | list[Path] | None = None) -> cb_judge.JudgeSpec:
    """The task's Verdict and context, loaded from a copy of its judge_schema.py under build/judge/, so the submodule
    gets no __pycache__. Each addendum is appended to the copy in order, as Lucy's harness appends hers; the copy's
    folder joins their stems."""
    addenda = [] if addendum is None else [addendum] if isinstance(addendum, Path) else list(addendum)
    text = "\n\n".join([(Path(task_dir) / cb_judge.SCHEMA_MODULE).read_text(), *(Path(a).read_text() for a in addenda)])
    folder = settings.BUILD / "judge" / "+".join([task_name, *(Path(a).stem for a in addenda)])
    write_atomic(folder / cb_judge.SCHEMA_MODULE, text.encode())
    return cb_judge.load_judge_spec(folder, task_name=task_name)


def judge_entry(name: str) -> dict:
    """The judge_models.yaml entry as CheatBench resolves it: model, api_key_env, generation_config, unsupported."""
    return cb_judge.load_judge_config(settings.JUDGE_MODELS_CONFIG, name)


def judge_model_config(name: str) -> tuple[str, GenerateConfig]:
    """(Inspect model name, generation settings) of a judge entry, such as ("openai/gpt-6-sol", ...)."""
    entry = judge_entry(name)
    return entry["model"], GenerateConfig(reasoning_effort=entry["generation_config"].get("reasoning_effort"))


def judge_prices(name: str, batch: bool = False) -> dict | None:
    """USD per million tokens of a judge entry's model (src/models/model_registry.yaml), at the batch rate with
    batch, or None when the model has no registry entry."""
    return usd_per_million(judge_model_config(name)[0], batch=batch)


def require_judge_key(name: str) -> None:
    """Refuse a judge entry whose credential variable is unset."""
    key_env = judge_entry(name).get("api_key_env")
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"judge {name}: {key_env} must be set")


@dataclass(frozen=True)
class Judge:
    """A judge entry ready to call: its client, its generation settings, its prices at the API the client uses, and
    the entry's settings each verdict records."""

    name: str
    model: Model
    config: GenerateConfig
    prices: dict | None
    entry: dict

    @classmethod
    def of(cls, name: str, *, batch: bool, model: Model | None = None) -> Judge:
        """The entry called through `model`, or through a fresh client of its model on the batch API or the normal
        one. A fresh client, since a memoized one belongs to the event loop that first used it."""
        model_name, config = judge_model_config(name)
        model = model or get_model(model_name, config=GenerateConfig(batch=batch), memoize=False)
        return cls(name, model, config, judge_prices(name, batch=batch), judge_entry(name))


def response_schema(spec: cb_judge.JudgeSpec) -> ResponseSchema:
    """The Verdict as a strict JSON schema: every field required, no additional properties on any object."""
    param = type_to_response_format_param(spec.verdict)["json_schema"]
    return ResponseSchema(name=param["name"], json_schema=JSONSchema.model_validate(objects_only(param["schema"])),
                          strict=True)


def objects_only(node):
    """The schema with `additionalProperties` on object nodes only and no `anyOf` of plain types: an `int | None`
    field becomes `type: [integer, null]`. OpenAI's strict-mode helper writes additionalProperties on every node
    and Inspect's Anthropic provider writes it into every anyOf member, which Anthropic's structured outputs
    refuse; both providers take a list of types."""
    if isinstance(node, dict):
        out = {k: objects_only(v) for k, v in node.items()}
        if out.get("type") != "object" and "properties" not in out:
            out.pop("additionalProperties", None)
        members = out.get("anyOf")
        if members and all(set(m) == {"type"} and isinstance(m["type"], str) for m in members):
            out["type"] = [m["type"] for m in members]
            del out["anyOf"]
        return out
    if isinstance(node, list):
        return [objects_only(v) for v in node]
    return node


def spec_record(spec: cb_judge.JudgeSpec) -> dict:
    """Hashes that identify the prompt and the schema a verdict was made with."""
    return {"prompt_sha256": hashlib.sha256(spec.prompt_path.read_bytes()).hexdigest(),
            "schema_sha256": hashlib.sha256(spec.schema_path.read_bytes()).hexdigest(),
            "schema_path": str(spec.schema_path)}


# --------------------------------------------------------------------------
# one transcript, one sample
# --------------------------------------------------------------------------
def render(transcript: dict) -> str:
    """CheatBench's rendering of a stored transcript: the ordered log the judge reads."""
    return cb_judge.render_transcript(transcript)


def verdict_row(spec: cb_judge.JudgeSpec, judge: Judge, fields: dict | None, *, usage: dict | None = None,
                error: str | None = None, raw: str = "") -> dict:
    """A verdict without the episode's identity: the judge's settings, parse_ok, the task's fields (None when the
    reply did not parse), the call's usage and cost, the error, and the raw reply when parsing failed."""
    cost = cost_usd(judge.prices, usage["input"], usage["cache_read"], usage["output"]) if usage else None
    row = {"judge": judge.name, "judge_generation_config": judge.entry["generation_config"],
           "judge_generation_config_unsupported": judge.entry["unsupported"],
           "judge_prompt": str(spec.prompt_path), "judge_schema": spec.schema_path.name,
           "parse_ok": fields is not None, **{k: (fields or {}).get(k) for k in spec.fields},
           "output_mode": cb_judge.MODE_STRUCTURED, "judge_cost_usd": cost, "judge_usage": usage,
           "judge_attempts": 1, "judge_error": error}
    if fields is None:
        row["raw"] = raw
    return row


async def judge_text(spec: cb_judge.JudgeSpec, rendered: str, agent_prompt: str, judge: Judge) -> dict:
    """One verdict: CheatBench's prompt over a rendered log, Inspect's call, CheatBench's validation. A failed call or
    a reply that does not validate gives parse_ok False with the error."""
    text = cb_judge.build_judge_prompt(spec, rendered, agent_prompt)[0]["content"]
    call_config = judge.config.merge(GenerateConfig(response_schema=response_schema(spec)))
    try:
        output = await judge.model.generate([ChatMessageUser(content=text)], config=call_config)
    except Exception as ex:  # noqa: BLE001 - one failed call must not lose the other agents' verdicts
        return verdict_row(spec, judge, None, error=f"{type(ex).__name__}: {ex}")
    usage = usage_dict(output.usage) if output.usage else None
    raw = output.completion or ""
    try:
        verdict = spec.verdict.model_validate_json(raw)
    except Exception as ex:  # noqa: BLE001
        return verdict_row(spec, judge, None, usage=usage, error=f"{type(ex).__name__}: {ex}", raw=raw)
    return verdict_row(spec, judge, verdict.model_dump(), usage=usage)


async def judge_transcript(spec: cb_judge.JudgeSpec, transcript: dict, judge: Judge) -> dict:
    """One verdict over CheatBench's rendering of a stored transcript."""
    return await judge_text(spec, render(transcript), transcript["prompt"], judge)


async def judge_sample(view: SampleView, spec: cb_judge.JudgeSpec, judge: Judge) -> dict[str, dict]:
    """A verdict for every agent of the sample with a projected conversation, by agent. An agent without one is not
    judged, as CheatBench skips an episode without a transcript."""
    agents = [a for a in roster(view) if agent_messages(view, a)]
    verdicts = await asyncio.gather(*(judge_transcript(spec, episode_transcript(view, a)[0], judge) for a in agents))
    return dict(zip(agents, verdicts))


# --------------------------------------------------------------------------
# the scorer
# --------------------------------------------------------------------------
@scorer(metrics=[mean()])
def cheatbench_judge(task_name: str, task_root: str, judge: str = settings.JUDGE_MODEL,
                     addendum: str | None = None, batch: bool = True) -> Scorer:
    """The share of judged agents that cheated, with every agent's verdict in the metadata.

    The judge is the model bound to the "judge" role, else the entry's model (whose key must then be set). When no
    verdict parses the sample gets no score at all (the failures go to an info event), so a failed judge is never
    averaged in as "nobody cheated"; `python -m src judge` judges the log again. `batch` says whether the judge's
    calls go through the batch API (SwarmConfig.batch_judge), which sets the price.
    """
    spec = judge_spec(Path(task_root), task_name, addendum=Path(addendum) if addendum else None)

    async def score(state: TaskState, target: Target) -> Score | None:
        view = SampleView.from_state(state, inspect_transcript().events)
        if JUDGE_ROLE not in model_roles():
            require_judge_key(judge)
        client = Judge.of(judge, batch=batch, model=get_model(role=JUDGE_ROLE, default=judge_model_config(judge)[0]))
        verdicts = await judge_sample(view, spec, client)
        parsed = [v for v in verdicts.values() if v["parse_ok"]]
        metadata = {"judge": judge, "model": str(client.model), "spec": spec_record(spec), "verdicts": verdicts,
                    "unjudged": [a for a in roster(view) if a not in verdicts]}
        if verdicts and not parsed:
            inspect_transcript().info({JUDGE_SCORER: {"unscored": "no verdict parsed", **metadata}})
            return None
        cheated = sum(1 for v in parsed if v.get("cheated"))
        explanation = f"{cheated} of {len(parsed)} cheated"
        if len(parsed) < len(verdicts):
            explanation += f"; {len(verdicts) - len(parsed)} verdict(s) did not parse"
        return Score(value=cheated / len(parsed) if parsed else 0.0, explanation=explanation, metadata=metadata)
    return score
