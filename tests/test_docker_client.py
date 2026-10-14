"""Check our connection wrapper against the SDK's actual non-context API."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from guardrails.docker import client


def test_connection_wraps_and_closes_sdk_client(monkeypatch):
    sdk_client = SimpleNamespace(close=Mock())  # No __enter__/__exit__ methods.
    factory = Mock(return_value=sdk_client)
    monkeypatch.setattr(client.docker, "from_env", factory)
    with client.connect() as connected:
        assert connected is sdk_client
    factory.assert_called_once_with(timeout=5)
    sdk_client.close.assert_called_once()


def test_connection_closes_after_failure(monkeypatch):
    sdk_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(client.docker, "from_env", lambda **kwargs: sdk_client)
    with pytest.raises(RuntimeError):
        with client.connect():
            raise RuntimeError("operation failed")
    sdk_client.close.assert_called_once()
