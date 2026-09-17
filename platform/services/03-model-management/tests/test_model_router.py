import pytest
from model_management.model_router import (
    KindMismatchError,
    ModelNotProvisionedError,
    UnknownAliasError,
    resolve,
)
from model_management.types import ModelKind


def test_resolve_known_alias_returns_expected_provider_and_deployment():
    handle = resolve("reason", "dev")
    assert handle.provider == "azure_openai"
    assert handle.kind == ModelKind.CHAT
    assert handle.deployment


def test_resolve_unknown_alias_raises():
    with pytest.raises(UnknownAliasError):
        resolve("does-not-exist", "dev")


def test_resolve_unknown_environment_raises():
    with pytest.raises(UnknownAliasError):
        resolve("reason", "staging")


def test_resolve_unprovisioned_alias_raises():
    with pytest.raises(ModelNotProvisionedError):
        resolve("voice", "dev")


def test_resolve_kind_mismatch_raises():
    with pytest.raises(KindMismatchError):
        resolve("reason", "dev", expected_kind=ModelKind.EMBEDDING)


def test_resolve_matching_expected_kind_succeeds():
    handle = resolve("embedding", "dev", expected_kind=ModelKind.EMBEDDING)
    assert handle.kind == ModelKind.EMBEDDING
