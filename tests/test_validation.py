import socket
import subprocess

import pytest

from guardrails.tools.validation import validate_test_config

DEFAULT = dict(cpus=1.0, memory_mb=512, vus=10, duration_seconds=30)


@pytest.mark.parametrize("values", [DEFAULT, dict(cpus=0.1, memory_mb=128, vus=1, duration_seconds=1), dict(cpus=4.0, memory_mb=4096, vus=100, duration_seconds=120)])
def test_valid_config(values):
    result = validate_test_config(**values)
    assert result.valid
    assert result.config.model_dump() == values
    assert result.errors == []


@pytest.mark.parametrize("field,value", [
    ("cpus", 0.09), ("cpus", 4.01), ("cpus", float("nan")),
    ("cpus", float("inf")), ("cpus", True), ("cpus", "1.0"),
    ("memory_mb", 127), ("memory_mb", 4097), ("memory_mb", 512.5),
    ("vus", 0), ("vus", 101), ("vus", True), ("vus", "10"),
    ("duration_seconds", 0), ("duration_seconds", 121),
    ("duration_seconds", None),
])
def test_rejected_config(field, value):
    result = validate_test_config(**(DEFAULT | {field: value}))
    assert not result.valid
    assert result.config is None
    assert [issue.field for issue in result.errors] == [field]
    assert result.errors[0].message
    assert result.errors[0].code
    assert "input" not in result.errors[0].model_dump()


def test_collects_all_errors():
    result = validate_test_config(cpus=0, memory_mb=1, vus=0, duration_seconds=0)
    assert {e.field for e in result.errors} == set(DEFAULT)


def test_validation_has_no_process_or_network_side_effects(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Validation attempted process or network access")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert validate_test_config(**DEFAULT).valid
    assert not validate_test_config(**(DEFAULT | {"vus": 101})).valid
    assert capsys.readouterr().out == ""
