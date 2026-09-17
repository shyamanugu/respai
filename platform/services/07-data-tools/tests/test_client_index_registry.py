import pytest
from data_tools.client_index_registry import UnknownClientError, resolve_client_index


def test_unknown_client_raises_in_dev():
    with pytest.raises(UnknownClientError):
        resolve_client_index("does_not_exist", "dev")


def test_unknown_environment_raises():
    with pytest.raises(UnknownClientError):
        resolve_client_index("anyone", "not_a_real_environment")
