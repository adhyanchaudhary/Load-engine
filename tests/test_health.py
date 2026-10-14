from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from guardrails.config import SERVICE_NAME, VERSION
from guardrails.tools import health


def test_health_check(capsys):
    before = datetime.now(timezone.utc)
    result = health.health_check()
    assert result.service_name == SERVICE_NAME
    assert result.version == VERSION == "0.1.0"
    assert result.status == "healthy"
    assert before <= result.timestamp <= datetime.now(timezone.utc)
    assert result.timestamp.utcoffset().total_seconds() == 0
    assert capsys.readouterr().out == ""


def test_system_info_allowlist(monkeypatch, capsys):
    monkeypatch.setattr(health.platform, "system", lambda: "TestOS")
    monkeypatch.setattr(health.platform, "python_version", lambda: "3.11.0")
    monkeypatch.setattr(health.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(health.psutil, "virtual_memory", lambda: SimpleNamespace(available=512 * 1024**2))
    assert health.get_system_info().model_dump() == {
        "operating_system": "TestOS", "python_version": "3.11.0",
        "cpu_count": 8, "available_memory_mb": 512.0,
    }
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("error", [OSError, NotImplementedError, health.psutil.Error])
def test_unavailable_memory_and_cpu(monkeypatch, error):
    def unsupported():
        raise error("unsupported")
    monkeypatch.setattr(health.psutil, "virtual_memory", unsupported)
    monkeypatch.setattr(health.os, "cpu_count", lambda: None)
    result = health.get_system_info()
    assert result.available_memory_mb is None
    assert result.cpu_count is None
