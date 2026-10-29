"""MAST: the LLM annotator of Cemri et al. (2025) labels which of the fourteen multi-agent failure modes a run shows.

The prompt and the parser are the notebook llm_judge_pipeline.ipynb of
https://github.com/multi-agent-systems-failure-taxonomy/MAST at commit a70542e541b2104ef8fcd785778179e173fb8d70:
`mast_prompt` is cell 0's `openai_evaluator` prompt verbatim and `parse_responses` is cell 8 verbatim, except that its
function-level `import re` moved to the top of this module. The notebook appends definitions.txt and examples.txt
from taxonomy_definitions_examples/; `python -m tools.failures.fetch_mast_taxonomy` downloads them into mast_taxonomy/.

The trace is the run's merged trace (board posts and agent steps) rendered one entry per line as
"round r | agent (role) | post: ..." or "round r | agent (role) | step: <tool calls> | <text>". It is cut as cell 6
cuts it: when trace plus examples pass 1048570 characters, the trace keeps its first 1048570 - len(examples).

The notebook's numbering differs from definitions.txt for two modes: the prompt calls 3.2 "No or Incorrect
Verification" and 3.3 "Weak Verification", while definitions.txt names 3.2 "Weak Verification" and 3.3 "No or
Incorrect Verification". The model does not settle on either: one reply names 3.2 one way and the next reply the
other way. A label is therefore read by its number, and mast.json keeps the name the reply gave each number
(reply_names). `renamed`
lists the modes whose reply name differs from the prompt's name, so a reader can tell which of the two modes a 3.2 or
3.3 label means in that reply.

The notebook's parser takes the first "yes" or "no" after the mode's number, inside words too. A reply that repeats
the mode's name breaks two modes: "2.5 Ignored Other Agent's Input: yes" and "3.2 No or Incorrect Verification: yes"
both parse as "no". `strict_labels` reads the answer after the colon of each mode's own line, and mast.json lists
the modes where the two parsers disagree.
"""
from __future__ import annotations

import re
from pathlib import Path

from inspect_ai.model import ChatMessageUser, GenerateConfig

from tools.failures.fetch_mast_taxonomy import TAXONOMY_DIR, taxonomy_present
from tools.failures.model_calls import complete
from tools.run_reader import Run, TraceEntry, merged_trace

TRUNCATE_AT = 1048570
MODES = ("1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "3.1", "3.2", "3.3")
PROMPT_NAMES = {
    "1.1": "Disobey Task Specification", "1.2": "Disobey Role Specification", "1.3": "Step Repetition",
    "1.4": "Loss of Conversation History", "1.5": "Unaware of Termination Conditions", "2.1": "Conversation Reset",
    "2.2": "Fail to Ask for Clarification", "2.3": "Task Derailment", "2.4": "Information Withholding",
    "2.5": "Ignored Other Agent's Input", "2.6": "Action-Reasoning Mismatch", "3.1": "Premature Termination",
    "3.2": "No or Incorrect Verification", "3.3": "Weak Verification",
}
DEFINITION_NAME = re.compile(r"^(\d\.\d) ([^:\n]+):", re.MULTILINE)
SUMMARY_LINE = re.compile(r"A\.\s*(?:Freeform text summary[^:]*:\s*)?(.*?)\s*(?=\n\s*B\.|\Z)", re.DOTALL)
COMPLETED_LINE = re.compile(r"B\.[^\n]*?\b(yes|no)\b", re.IGNORECASE)
STRICT_LABEL = r"^\W*(?:C\.\s*)?{mode}\b[^\n]*?[:\s]\s*\**(yes|no)\**\W*$"
REPLY_NAME = r"^\W*(?:C\.\s*)?{mode}\s+\**([^:\n*]+?)\**\s*:"


def render_entry(entry: TraceEntry) -> str:
    """One merged-trace entry as one line of the MAST trace."""
    round_label = "?" if entry.round is None else str(entry.round)
    head = f"round {round_label} | {entry.agent} ({entry.role})"
    if entry.kind == "post":
        return f"{head} | post: {entry.text}"
    return f"{head} | step: {'; '.join(entry.calls)} | {entry.text}"


def render_trace(entries: list[TraceEntry]) -> str:
    """The merged trace as the text MAST annotates, one entry per line."""
    return "\n".join(render_entry(entry) for entry in entries)


def truncate_trace(trace: str, examples: str) -> str:
    """Cut the trace as notebook cell 6 does."""
    if len(trace + examples) > TRUNCATE_AT:
        return trace[:TRUNCATE_AT - len(examples)]
    return trace


def read_taxonomy(directory: Path = TAXONOMY_DIR) -> tuple[str, str]:
    """definitions.txt and examples.txt as text."""
    return (directory / "definitions.txt").read_text(), (directory / "examples.txt").read_text()


def definition_names(definitions: str) -> dict[str, str]:
    """Each mode's name as definitions.txt gives it, such as {"3.2": "Weak Verification"}."""
    return {code: name.strip() for code, name in DEFINITION_NAME.findall(definitions)}


def mast_prompt(trace, definitions, examples=''):
    """Cell 0's openai_evaluator prompt, verbatim; its six long lines stay as the notebook writes them (noqa E501)."""
    prompt = (
    "Below I will provide a multiagent system trace. provide me an analysis of the failure modes and inefficiencies as I will say below. \n"  # noqa: E501
    "In the traces, analyze the system behaviour."
    "There are several failure modes in multiagent systems I identified. I will provide them below. Tell me if you encounter any of them, as a binary yes or no. \n"  # noqa: E501
    "Also, give me a one sentence (be brief) summary of the problems with the inefficiencies or failure modes in the trace. Only mark a failure mode if you can provide an example of it in the trace, and specify that in your summary at the end"  # noqa: E501
    "Also tell me whether the task is successfully completed or not, as a binary yes or no."
    "At the very end, I provide you with the definitions of the failure modes and inefficiencies. After the definitions, I will provide you with examples of the failure modes and inefficiencies for you to understand them better."  # noqa: E501
    "Tell me if you encounter any of them between the @@ symbols as I will say below, as a binary yes or no."
    "Here are the things you should answer. Start after the @@ sign and end before the next @@ sign (do not include the @@ symbols in your answer):"  # noqa: E501
    "*** begin of things you should answer *** @@"
    "A. Freeform text summary of the problems with the inefficiencies or failure modes in the trace: <summary>"
    "B. Whether the task is successfully completed or not: <yes or no>"
    "C. Whether you encounter any of the failure modes or inefficiencies:"
    "1.1 Disobey Task Specification: <yes or no>"
    "1.2 Disobey Role Specification: <yes or no>"
    "1.3 Step Repetition: <yes or no>"
    "1.4 Loss of Conversation History: <yes or no>"
    "1.5 Unaware of Termination Conditions: <yes or no>"
    "2.1 Conversation Reset: <yes or no>"
    "2.2 Fail to Ask for Clarification: <yes or no>"
    "2.3 Task Derailment: <yes or no>"
    "2.4 Information Withholding: <yes or no>"
    "2.5 Ignored Other Agent's Input: <yes or no>"
    "2.6 Action-Reasoning Mismatch: <yes or no>"
    "3.1 Premature Termination: <yes or no>"
    "3.2 No or Incorrect Verification: <yes or no>"
    "3.3 Weak Verification: <yes or no>"
    "@@*** end of your answer ***"
    "An example answer is: \n"
    "A. The task is not completed due to disobeying role specification as agents went rogue and started to chat with each other instead of completing the task. Agents derailed and verifier is not strong enough to detect it.\n"  # noqa: E501
    "B. no \n"
    "C. \n"
    "1.1 no \n"
    "1.2 no \n"
    "1.3 no \n"
    "1.4 no \n"
    "1.5 no \n"
    "1.6 yes \n"
    "2.1 no \n"
    "2.2 no \n"
    "2.3 yes \n"
    "2.4 no \n"
    "2.5 no \n"
    "2.6 yes \n"
    "2.7 no \n"
    "3.1 no \n"
    "3.2 yes \n"
    "3.3 no \n"
    "Here is the trace: \n"
    f"{trace}"
    "Also, here are the explanations (definitions) of the failure modes and inefficiencies: \n"
    f"{definitions} \n"
    "Here are some examples of the failure modes and inefficiencies: \n"
    f"{examples}"
)
    return prompt


def parse_responses(responses):
    """
    Parse the LLM responses to extract yes/no answers for each failure mode.

    Args:
        responses: List of LLM responses evaluating traces

    Returns:
        Dictionary mapping failure mode codes to lists of binary values (0 for no, 1 for yes)
    """

    # Initialize dictionary with empty lists for each failure mode
    failure_modes = {
        '1.1': [], '1.2': [], '1.3': [], '1.4': [], '1.5': [],
        '2.1': [], '2.2': [], '2.3': [], '2.4': [], '2.5': [], '2.6': [],
        '3.1': [], '3.2': [], '3.3': []
    }

    for i, response in enumerate(responses):
        try:
            # Clean up the response - remove @@ markers if present
            cleaned_response = response.strip()
            if cleaned_response.startswith('@@'):
                cleaned_response = cleaned_response[2:]
            if cleaned_response.endswith('@@'):
                cleaned_response = cleaned_response[:-2]

            # Process each failure mode
            for mode in failure_modes.keys():
                # Various patterns to match different response formats
                patterns = [
                    # Format with C. prefix and colon
                    rf"C\..*?{mode}.*?(yes|no)",
                    # Format with just C prefix without dot
                    rf"C{mode}\s+(yes|no)",
                    # Format with mode directly (with or without spaces)
                    rf"{mode}\s*[:]\s*(yes|no)",
                    rf"{mode}\s+(yes|no)",
                    # Format with newlines
                    rf"{mode}\s*\n\s*(yes|no)",
                    # Format with C prefix and newlines
                    rf"C\.{mode}\s*\n\s*(yes|no)"
                ]

                found = False
                for pattern in patterns:
                    matches = re.findall(pattern, cleaned_response, re.IGNORECASE | re.DOTALL)
                    if matches:
                        # Use the first match
                        value = 1 if matches[0].lower() == 'yes' else 0
                        failure_modes[mode].append(value)
                        found = True
                        break

                if not found:
                    # If we still can't find a match, try a more general approach
                    # Look for the mode number followed by any text and then yes/no
                    general_pattern = rf"(?:C\.)?{mode}.*?(yes|no)"
                    match = re.search(general_pattern, cleaned_response, re.IGNORECASE | re.DOTALL)

                    if match:
                        value = 1 if match.group(1).lower() == 'yes' else 0
                        failure_modes[mode].append(value)
                    else:
                        # If all attempts fail, default to 'no'
                        print(f"Warning: Could not find mode {mode} in response {i}")
                        failure_modes[mode].append(0)

        except Exception as e:
            print(f"Error processing response {i}: {e}")
            # If there's an error, default to 'no' for all modes for this response
            for mode in failure_modes:
                if len(failure_modes[mode]) <= i:  # Only append if we haven't already
                    failure_modes[mode].append(0)

    # Ensure all lists have the same length
    max_length = max(len(values) for values in failure_modes.values())
    for mode in failure_modes:
        if len(failure_modes[mode]) < max_length:
            failure_modes[mode].extend([0] * (max_length - len(failure_modes[mode])))

    return failure_modes


def parse_reply(reply: str) -> dict:
    """The fourteen labels through parse_responses, plus the summary (A.) and task completion (B.) of the reply."""
    labels = parse_responses([reply])
    modes = {mode: "yes" if labels[mode][0] == 1 else "no" for mode in MODES}
    strict = strict_labels(reply)
    summary = SUMMARY_LINE.search(reply)
    completed = COMPLETED_LINE.search(reply)
    names = reply_names(reply)
    return {
        "modes": modes,
        "summary": summary.group(1).strip() if summary else "",
        "task_completed": completed.group(1).lower() if completed else None,
        "modes_strict": strict,
        "disagreements": [mode for mode in MODES if strict.get(mode) is not None and strict[mode] != modes[mode]],
        "reply_names": names,
        "renamed": {mode: name for mode, name in names.items()
                    if name is not None and name.lower() != PROMPT_NAMES[mode].lower()},
    }


def reply_names(reply: str) -> dict[str, str | None]:
    """The name the reply gives each mode's number, such as {"3.2": "Weak Verification"}; None when it gives none."""
    names = {}
    for mode in MODES:
        pattern = re.compile(REPLY_NAME.format(mode=re.escape(mode)), re.MULTILINE)
        match = pattern.search(reply)
        names[mode] = match.group(1).strip() if match else None
    return names


def strict_labels(reply: str) -> dict[str, str | None]:
    """Each mode's answer from its own line, the last word after the number; None when no line answers it."""
    labels = {}
    for mode in MODES:
        pattern = re.compile(STRICT_LABEL.format(mode=re.escape(mode)), re.IGNORECASE | re.MULTILINE)
        match = pattern.search(reply)
        labels[mode] = match.group(1).lower() if match else None
    return labels


def annotate_run(run: Run, model: str, config: GenerateConfig) -> dict:
    """Annotate one run with MAST; the taxonomy files must be on disk."""
    if not taxonomy_present():
        raise FileNotFoundError("the MAST taxonomy files are missing or altered")
    definitions, examples = read_taxonomy()
    trace = truncate_trace(render_trace(merged_trace(run)), examples)
    prompt = mast_prompt(trace, definitions=definitions, examples=examples)
    reply = complete(model, [ChatMessageUser(content=prompt)], config)
    names = definition_names(definitions)
    if reply is None:
        return {"modes": {}, "summary": "", "task_completed": None, "modes_strict": {}, "disagreements": [],
                "reply_names": {}, "renamed": {}, "raw": None, "error": "the model call failed", "names": names,
                "prompt_names": PROMPT_NAMES, "trace_characters": len(trace)}
    return {**parse_reply(reply), "raw": reply, "names": names, "prompt_names": PROMPT_NAMES,
            "trace_characters": len(trace)}
