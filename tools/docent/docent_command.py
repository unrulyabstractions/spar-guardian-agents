"""`python -m tools docent <run> ...`: the runs' agent episodes (and with --board their boards) as Docent AgentRuns.

Without --dry-run the AgentRuns go to one Docent collection: a new one named by --collection, or the one --collection-id
names. With --dry-run nothing leaves the machine and no key is needed.
"""
from __future__ import annotations

import argparse
import json
import sys

from tools.docent.docent_convert import message_count, run_agent_runs
from tools.docent.docent_upload import (
    collection_url,
    connect,
    discovered_key,
    key_problem,
    open_collection,
    upload_agent_runs,
)
from tools.run_reader import load_runs, tools_dir, write_summary

NAME = "docent"
HELP = "the runs' agent episodes as Docent AgentRuns, uploaded to a collection (or --dry-run to a JSON file)"
TOOL = "docent"
DEFAULT_COLLECTION = "spar-guardian-agents"
COLLECTION_DESCRIPTION = "CheatBench tasks run as ORBIT swarms on a message board; one AgentRun per agent episode."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The runs, the collection, --board and --dry-run."""
    parser.add_argument("runs", nargs="+", help="run names of the study or run directories")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION,
                        help=f"name of the collection to create (default {DEFAULT_COLLECTION!r})")
    parser.add_argument("--collection-id", default=None, help="upload to this existing collection instead")
    parser.add_argument("--board", action="store_true", help="also one AgentRun per run holding its board")
    parser.add_argument("--dry-run", action="store_true",
                        help="convert only: write agent_runs.json and summary.json, contact no server")


def run(args: argparse.Namespace) -> int:
    """Convert every run, then upload unless --dry-run; 0 ok, 1 failure, 2 no usable key or an unreadable run."""
    problem = None if args.dry_run else key_problem(discovered_key())
    if problem:
        print(problem, file=sys.stderr)
        return 2
    runs = load_runs(args.runs, NAME)
    if runs is None:
        return 2
    converted = [(loaded, run_agent_runs(loaded, args.board)) for loaded in runs]
    if args.dry_run:
        for loaded, agent_runs in converted:
            write_agent_runs(loaded, agent_runs)
            path = write_summary(loaded, TOOL, dry_run_payload(agent_runs, args.board))
            print(f"docent: {loaded.name}: {len(agent_runs)} AgentRuns converted, {path}")
        return 0
    return upload_runs(converted, args)


def upload_runs(converted: list, args: argparse.Namespace) -> int:
    """Open the collection once, add every run's AgentRuns to it and record the ids in each run's summary.

    A failure after the collection exists names it, so the runs not yet uploaded can go to it with --collection-id.
    """
    collection_id = None
    try:
        client = connect()
        collection_id = open_collection(client, args.collection_id, args.collection, COLLECTION_DESCRIPTION)
        url = collection_url(client, collection_id)
        for loaded, agent_runs in converted:
            result = upload_agent_runs(client, collection_id, agent_runs)
            payload = {
                **dry_run_payload(agent_runs, args.board),
                "dry_run": False,
                "collection_id": collection_id,
                "collection_name": args.collection if not args.collection_id else None,
                "collection_url": url,
                "agent_run_ids": [agent_run.id for agent_run in agent_runs],
                "upload_status": result.get("status"),
                "job_ids": result.get("job_ids", []),
            }
            write_agent_runs(loaded, agent_runs)
            path = write_summary(loaded, TOOL, payload)
            print(f"docent: {loaded.name}: {len(agent_runs)} AgentRuns uploaded, {path}")
    except Exception as error:
        print(f"docent: upload failed: {type(error).__name__}: {error}", file=sys.stderr)
        if collection_id:
            print(f"docent: the collection {collection_id} exists; retry the runs without an uploaded summary.json "
                  f"with --collection-id {collection_id}", file=sys.stderr)
        return 1
    print(f"docent: collection {collection_id}: {url}")
    print(f"docent: add more runs to it with --collection-id {collection_id}")
    return 0


def write_agent_runs(loaded, agent_runs: list) -> None:
    """runs/<study>/<name>/tools/docent/agent_runs.json: every AgentRun as model_dump gives it."""
    path = tools_dir(loaded, TOOL) / "agent_runs.json"
    dumped = [agent_run.model_dump(mode="json") for agent_run in agent_runs]
    path.write_text(json.dumps(dumped, indent=1, ensure_ascii=False) + "\n")


def dry_run_payload(agent_runs: list, board: bool) -> dict:
    """The summary fields of a conversion: how many AgentRuns, and per AgentRun its name, id, kind and messages."""
    return {
        "dry_run": True,
        "board": board,
        "n_agent_runs": len(agent_runs),
        "agent_runs_file": "agent_runs.json",
        "agent_runs": [
            {"name": agent_run.name, "id": agent_run.id, "kind": agent_run.metadata.get("kind"),
             "agent": agent_run.metadata.get("agent"), "cheated": agent_run.metadata.get("cheated"),
             "messages": message_count(agent_run)}
            for agent_run in agent_runs
        ],
    }
