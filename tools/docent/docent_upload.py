"""The Docent client: where the API key is found, the collection, and the upload."""
from __future__ import annotations

import os

from docent.data_models.agent_run import AgentRun
from docent.sdk.client import Docent, load_config_file

KEY_VARIABLE = "DOCENT_API_KEY"
KEY_PLACES = (
    "a docent.env in the current directory or one of its parents",
    "~/.docent/docent.env",
    "the DOCENT_API_KEY environment variable",
)
KEY_PRECEDENCE = "The SDK takes the first of these it finds, so a key in a docent.env wins over the environment."


def discovered_key() -> str | None:
    """The API key the SDK will use: a docent.env it discovers, else the environment; None when there is none."""
    config = load_config_file(profile=os.getenv("DOCENT_PROFILE"))
    return config.get(KEY_VARIABLE) or os.getenv(KEY_VARIABLE) or None


def key_problem(key: str | None) -> str | None:
    """Why the key cannot be used, naming the places the SDK looks; None for a usable key."""
    places = "; or ".join(KEY_PLACES)
    if not key:
        return f"docent: no Docent API key found. Set one in {places}. {KEY_PRECEDENCE}"
    if key.startswith("<") and key.endswith(">"):
        return f"docent: the Docent API key is a placeholder ({len(key)} characters in angle brackets). " \
               f"Put the real key in {places}. {KEY_PRECEDENCE}"
    return None


def connect() -> Docent:
    """A client logged in with the key the SDK discovers."""
    return Docent()


def open_collection(client: Docent, collection_id: str | None, name: str, description: str) -> str:
    """The collection to upload to: the given id after checking it exists, else a new collection with that name."""
    if collection_id:
        if client.get_collection(collection_id) is None:
            raise LookupError(f"no Docent collection {collection_id}")
        return collection_id
    return client.create_collection(name=name, description=description)


def collection_url(client: Docent, collection_id: str) -> str:
    """The collection's dashboard URL: the client's frontend URL, then /dashboard/<id>."""
    return f"{client.frontend_url}/dashboard/{collection_id}"


def upload_agent_runs(client: Docent, collection_id: str, agent_runs: list[AgentRun]) -> dict:
    """Add the AgentRuns to the collection and wait for the server to ingest them."""
    return client.add_agent_runs(collection_id, agent_runs)
