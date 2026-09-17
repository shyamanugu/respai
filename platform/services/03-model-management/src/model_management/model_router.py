"""Resolves a stable task alias (e.g. "reason", "embedding") to an actual
provider + deployment for the current environment. Orchestration code should
only ever import `resolve` from here — never reference a model name directly.
"""
from pathlib import Path

import yaml

from .types import ModelHandle, ModelKind

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


class UnknownAliasError(KeyError):
    pass


class ModelNotProvisionedError(RuntimeError):
    """Raised when an alias resolves but has no deployment yet (e.g. `voice`)."""


class KindMismatchError(ValueError):
    pass


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve(
    alias: str, environment: str, expected_kind: ModelKind | None = None
) -> ModelHandle:
    config = _load_config()

    try:
        env_models = config["environments"][environment]["models"]
    except KeyError as exc:
        raise UnknownAliasError(f"No configuration for environment '{environment}'") from exc

    try:
        entry = env_models[alias]
    except KeyError as exc:
        raise UnknownAliasError(
            f"No model alias '{alias}' configured for environment '{environment}'"
        ) from exc

    if entry.get("deployment") is None:
        raise ModelNotProvisionedError(
            f"Alias '{alias}' is configured but not provisioned in '{environment}'"
        )

    kind = ModelKind(entry["kind"])
    if expected_kind is not None and kind != expected_kind:
        raise KindMismatchError(
            f"Alias '{alias}' is kind '{kind.value}', caller expected '{expected_kind.value}'"
        )

    return ModelHandle(
        alias=alias,
        provider=entry["provider"],
        deployment=entry["deployment"],
        kind=kind,
    )
