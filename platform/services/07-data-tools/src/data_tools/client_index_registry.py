"""Resolves a client_id to that client's isolated Azure AI Search index name
for the current environment. This is the single enforcement point for data
isolation in this component: a tool only ever asks for an index by
client_id, never by index name directly, so there is no code path by which
one client's request could be pointed at another client's data — the only
way to change what a client_id resolves to is a reviewed edit to
config/clients.yaml. Onboarding a new client is exactly that config edit
plus running scripts/provision_client_index.py, not a code change.
"""
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "clients.yaml"


class UnknownClientError(KeyError):
    pass


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_client_index(client_id: str, environment: str) -> str:
    config = _load_config()

    try:
        env_clients = config["environments"][environment]["clients"]
    except KeyError as exc:
        raise UnknownClientError(f"No configuration for environment '{environment}'") from exc

    try:
        entry = env_clients[client_id]
    except KeyError as exc:
        raise UnknownClientError(
            f"Client '{client_id}' is not onboarded in environment '{environment}' — "
            "add it to config/clients.yaml before any tool can query its data"
        ) from exc

    return entry["index_name"]
