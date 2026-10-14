"""Deterministic lifecycle/security tests. No Docker daemon connections."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from docker.errors import APIError, DockerException, NotFound
from mcp import Client
from pydantic import ValidationError

from guardrails.docker.manager import SandboxManager
from guardrails.docker.models import ApprovedImage, ImagePolicy, SandboxConfig
from guardrails.docker.security import LOG_LIMIT_BYTES, ownership_labels, sanitize_logs
from guardrails.server import mcp
from guardrails.tools import sandbox as tools

IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64
CAPABILITIES = dict(OSType="linux", CpuCfsQuota=True, CpuCfsPeriod=True,
                    MemoryLimit=True, SwapLimit=True, PidsLimit=True)


@pytest.fixture
def engine(tmp_path):
    client = MagicMock()
    client.__enter__.return_value = client
    client.info.return_value = CAPABILITIES.copy()
    client.version.return_value = {"Version": "28.4.0"}
    image = MagicMock(id=IMAGE, attrs={"Config": {"Volumes": None}})
    client.images.get.return_value = image
    container = MagicMock(id=CONTAINER)

    def create(**opts):
        container.attrs = {
            "Image": opts["image"],
            "HostConfig": {
                "NanoCpus": opts["nano_cpus"], "Memory": opts["mem_limit"],
                "MemorySwap": opts["memswap_limit"], "PidsLimit": opts["pids_limit"],
                "Privileged": opts["privileged"], "CapDrop": opts["cap_drop"],
                "SecurityOpt": opts["security_opt"], "ReadonlyRootfs": opts["read_only"],
                "NetworkMode": opts["network_mode"], "Init": opts["init"],
                "RestartPolicy": opts["restart_policy"], "LogConfig": opts["log_config"],
            },
            "Config": {"Labels": opts["labels"].copy(), "User": opts["user"],
                       "NetworkDisabled": opts["network_disabled"],
                       "Entrypoint": opts["entrypoint"], "Cmd": opts["command"]},
            "State": {"Status": "created", "OOMKilled": False, "ExitCode": 0},
        }
        return container

    client.containers.create.side_effect = create
    client.containers.get.return_value = container

    def start():
        container.attrs["State"].update(Status="running", StartedAt="2026-01-01T00:00:00Z")

    def stop(**kwargs):
        container.attrs["State"].update(Status="exited", ExitCode=0)

    container.start.side_effect = start
    container.stop.side_effect = stop
    container.logs.side_effect = lambda **kwargs: iter_with_close([b"hello\n"])
    policy = ImagePolicy(images={"test": ApprovedImage(image_id=IMAGE, entrypoint=["/app"], command=["--serve"])})
    clock = MagicMock(return_value=datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc))
    manager = SandboxManager(tmp_path / "state", policy=policy, client_factory=lambda: client, clock=clock)
    return manager, client, container, clock


def iter_with_close(chunks):
    yield from chunks


def created(engine):
    manager, client, container, clock = engine
    result = manager.create("test")
    assert result.ok, result.error
    return result.sandbox.sandbox_id


def test_docker_unavailable(tmp_path):
    def fail():
        raise DockerException("secret host path must not be returned")
    status = SandboxManager(tmp_path, client_factory=fail).check_docker()
    assert not status.available
    assert status.connectivity == "unreachable"
    assert "secret host path" not in status.model_dump_json()


def test_check_docker_reports_not_proves_support(engine):
    status = engine[0].check_docker()
    assert status.available
    assert status.engine_version == "28.4.0"
    assert status.resource_limit_status == "daemon_reports_support"
    assert not status.isolation_verified


def test_config_defaults_and_bounds():
    cfg = SandboxConfig(image="test")
    assert (cfg.cpus, cfg.memory_mb, cfg.pids_limit, cfg.timeout_seconds) == (0.5, 512, 64, 120)
    SandboxConfig(image="test", cpus=0.1, memory_mb=128, pids_limit=16, timeout_seconds=10)
    SandboxConfig(image="test", cpus=4.0, memory_mb=4096, pids_limit=256, timeout_seconds=300)


@pytest.mark.parametrize("field,value", [
    ("cpus", 0.09), ("cpus", 4.01), ("cpus", float("nan")), ("cpus", True),
    ("memory_mb", 127), ("memory_mb", 4097), ("memory_mb", "512"),
    ("pids_limit", 15), ("pids_limit", 257),
    ("timeout_seconds", 9), ("timeout_seconds", 301),
    ("image", "registry.example/app:latest"),
])
def test_invalid_config_before_docker(engine, field, value):
    manager, client, _, _ = engine
    result = manager.create(**({"image": "test"} | {field: value}))
    assert not result.ok
    assert result.error.code == "invalid_configuration"
    client.containers.create.assert_not_called()
    client.images.pull.assert_not_called()


def test_unknown_image_denied_before_docker(engine):
    manager, client, _, _ = engine
    assert manager.create("unknown").error.code == "image_not_approved"
    client.info.assert_not_called()
    client.images.pull.assert_not_called()


def test_default_policy_deny_all(tmp_path):
    factory = MagicMock()
    manager = SandboxManager(tmp_path, client_factory=factory)
    assert manager.create("test").error.code == "image_not_approved"
    factory.assert_not_called()


def test_image_not_local_no_pull(engine):
    manager, client, _, _ = engine
    client.images.get.side_effect = NotFound("missing")
    assert manager.create("test").error.code == "image_not_local"
    client.images.pull.assert_not_called()
    client.containers.create.assert_not_called()


def test_reject_image_declared_volumes(engine):
    manager, client, _, _ = engine
    client.images.get.return_value.attrs = {"Config": {"Volumes": {"/data": {}}}}
    assert manager.create("test").error.code == "image_volumes"
    client.containers.create.assert_not_called()


@pytest.mark.parametrize("user", ["0:0", "root", "1000:0", "0:1000", "user:group"])
def test_root_or_named_user_rejected(user):
    with pytest.raises(ValidationError):
        ApprovedImage(image_id=IMAGE, user=user, entrypoint=["/app"])


@pytest.mark.parametrize("capability", list(CAPABILITIES))
def test_unsupported_controls_fail_closed(engine, capability):
    manager, client, _, _ = engine
    client.info.return_value[capability] = False
    assert manager.create("test").error.code == "unsupported_controls"
    client.containers.create.assert_not_called()


def test_security_options_and_durable_registry(engine):
    manager, client, container, _ = engine
    sandbox_id = created(engine)
    opts = client.containers.create.call_args.kwargs
    assert opts["image"] == IMAGE
    assert opts["entrypoint"] == ["/app"] and opts["command"] == ["--serve"]
    assert opts["user"] == "65534:65534"
    assert opts["privileged"] is False
    assert opts["cap_drop"] == ["ALL"]
    assert opts["security_opt"] == ["no-new-privileges:true"]
    assert opts["read_only"] is True
    assert opts["network_disabled"] is True and opts["network_mode"] == "none"
    assert opts["mem_limit"] == opts["memswap_limit"] == 512 * 1024**2
    assert opts["nano_cpus"] == 500_000_000 and opts["pids_limit"] == 64
    assert opts["init"] is True and opts["restart_policy"] == {"Name": "no"}
    assert not opts["mounts"] and not opts["volumes"] and not opts["ports"]
    assert opts["publish_all_ports"] is False
    container.start.assert_not_called()
    client.images.pull.assert_not_called()
    with manager.registry.transaction() as data:
        entry = data.entries[sandbox_id]
        assert entry.container_id == CONTAINER
        assert opts["labels"] == ownership_labels(entry)
        assert (entry.expires_at - entry.created_at).total_seconds() == 120
    assert manager.registry.path.exists()
    assert not list(manager.registry.directory.glob("*.tmp"))


def test_start_and_inspect_running_unknown_health(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    result = manager.start(sandbox_id)
    assert result.ok and result.sandbox.status == "running"
    assert result.sandbox.health == "unknown"
    assert result.sandbox.uptime_seconds == 5
    assert result.sandbox.exit_code is None
    inspected = manager.inspect(sandbox_id)
    assert inspected.sandbox.limits.verified
    assert inspected.sandbox.limits.cpus == 0.5
    assert inspected.sandbox.limits.memory_mb == 512
    assert inspected.sandbox.limits.pids_limit == 64
    assert manager.start(sandbox_id).ok
    container.start.assert_called_once()


def test_start_failure_preserves_cleanup_record(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    container.start.side_effect = APIError("failure containing private path")
    result = manager.start(sandbox_id)
    assert not result.ok and result.error.code == "docker_error"
    assert "private path" not in result.model_dump_json()
    assert manager.destroy(sandbox_id).ok


def test_real_health_and_oom_state(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    container.attrs["State"].update(Status="exited", OOMKilled=True, ExitCode=137,
                                    Health={"Status": "unhealthy"})
    result = manager.inspect(sandbox_id)
    assert result.sandbox.oom_killed
    assert result.sandbox.exit_code == 137
    assert result.sandbox.health == "unhealthy"


def test_limit_tampering_prevents_start(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    container.attrs["HostConfig"]["Memory"] = 0
    assert manager.start(sandbox_id).error.code == "isolation_unverified"
    container.start.assert_not_called()
    inspected = manager.inspect(sandbox_id)
    assert not inspected.sandbox.limits.verified
    assert "memory limit" in inspected.sandbox.limits.discrepancies


def test_running_limit_tampering_stops_owned_container(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    assert manager.start(sandbox_id).ok
    container.attrs["HostConfig"]["Privileged"] = True
    result = manager.inspect(sandbox_id)
    assert not result.ok
    container.stop.assert_called_once_with(timeout=2)


def test_failed_create_inspection_rolls_back(engine):
    manager, client, container, _ = engine
    create = client.containers.create.side_effect
    def unsupported(**opts):
        result = create(**opts)
        result.attrs["HostConfig"]["ReadonlyRootfs"] = False
        return result
    client.containers.create.side_effect = unsupported
    assert manager.create("test").error.code == "isolation_unverified"
    container.remove.assert_called_once_with(force=True, v=False)
    container.start.assert_not_called()
    with manager.registry.transaction() as data:
        assert not data.entries


def test_cleanup_and_repeated_cleanup(engine):
    manager, client, container, _ = engine
    sandbox_id = created(engine)
    assert manager.start(sandbox_id).ok
    assert manager.destroy(sandbox_id).destroyed
    container.stop.assert_called_once_with(timeout=2)
    container.remove.assert_called_once_with(force=True, v=False)
    client.containers.get.reset_mock()
    assert manager.destroy(sandbox_id).already_absent
    client.containers.get.assert_not_called()
    with manager.registry.transaction() as data:
        assert not data.entries


def test_already_removed_container(engine):
    manager, client, _, _ = engine
    sandbox_id = created(engine)
    client.containers.get.side_effect = NotFound("already gone")
    assert manager.destroy(sandbox_id).ok
    assert manager.destroy(sandbox_id).already_absent


@pytest.mark.parametrize("method", ["start", "inspect", "logs"])
def test_unknown_sandbox_rejected(engine, method):
    manager, client, _, _ = engine
    assert getattr(manager, method)(str(uuid4())).error.code == "unknown_sandbox"
    assert getattr(manager, method)(CONTAINER).error.code == "unknown_sandbox"
    client.containers.get.assert_not_called()


@pytest.mark.parametrize("method", ["start", "inspect", "logs", "destroy"])
def test_unmanaged_container_never_modified(engine, method):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    container.attrs["Config"]["Labels"] = {}
    result = getattr(manager, method)(sandbox_id)
    assert result.error.code == "ownership_mismatch"
    container.start.assert_not_called()
    container.stop.assert_not_called()
    container.remove.assert_not_called()
    container.logs.assert_not_called()
    with manager.registry.transaction() as data:
        assert sandbox_id in data.entries


def test_id_mismatch_even_with_copied_labels(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    container.id = "c" * 64
    assert manager.destroy(sandbox_id).error.code == "ownership_mismatch"
    container.remove.assert_not_called()


def test_registry_survives_manager_restart(engine):
    manager, client, _, _ = engine
    sandbox_id = created(engine)
    restored = SandboxManager(manager.registry.directory, client_factory=lambda: client, clock=manager.clock)
    assert restored.inspect(sandbox_id).ok
    assert restored.destroy(sandbox_id).ok  # Policy removal never prevents cleanup.


def test_timeout_and_bounded_cleanup(engine):
    manager, _, container, clock = engine
    sandbox_id = created(engine)
    clock.return_value += timedelta(seconds=121)
    assert manager.start(sandbox_id).error.code == "expired"
    assert manager.logs(sandbox_id).error.code == "expired"
    outcome = manager.cleanup_expired(limit=1)
    assert (outcome.examined, outcome.destroyed, outcome.remaining_expired) == (1, 1, 0)
    container.remove.assert_called_once()
    assert manager.cleanup_expired().destroyed == 0


def test_cleanup_never_removes_unexpired_or_unowned(engine):
    manager, _, container, clock = engine
    created(engine)
    assert manager.cleanup_expired().examined == 0
    clock.return_value += timedelta(seconds=121)
    container.attrs["Config"]["Labels"] = {}
    result = manager.cleanup_expired()
    assert result.errors[0].code == "ownership_mismatch"
    assert result.remaining_expired == 1
    container.remove.assert_not_called()


def test_creation_intent_reconciles_crash_without_scanning_unowned(engine):
    manager, client, container, clock = engine
    create = client.containers.create.side_effect
    def timed_out(**opts):
        create(**opts)
        raise DockerException("response lost after successful create")
    client.containers.create.side_effect = timed_out
    assert not manager.create("test").ok
    with manager.registry.transaction() as data:
        entry = next(iter(data.entries.values()))
        assert entry.container_id is None
        expected_labels = [f"{k}={v}" for k, v in ownership_labels(entry).items()]
    client.containers.list.return_value = [container]
    clock.return_value += timedelta(seconds=121)
    assert manager.cleanup_expired().destroyed == 1
    client.containers.list.assert_called_once_with(all=True, filters={"label": expected_labels})
    container.remove.assert_called_once_with(force=True, v=False)


def test_corrupt_registry_fails_closed(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    manager.registry.path.write_text("not JSON")
    assert manager.destroy(sandbox_id).error.code == "invalid_configuration"
    container.remove.assert_not_called()


def test_policy_file_loaded_and_malformed_fails_closed(engine, tmp_path):
    manager, client, _, _ = engine
    path = tmp_path / "policy.json"
    path.write_text(manager._policy.model_dump_json())
    file_manager = SandboxManager(tmp_path / "file-state", policy_path=path, client_factory=lambda: client)
    assert file_manager.create("test").ok
    path.write_text('{"images":{"test":{"image_id":"remote:latest","entrypoint":["/app"]}}}')
    assert not file_manager.create("test").ok
    client.images.pull.assert_not_called()


def test_logs_bounded_sanitized_and_closed(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    stream = iter_with_close([
        b'\x1b[31mapi_key=secret-value\x1b[0m\x00\npassword="two words"\nAuthorization: Bearer abc123\n',
        b"x" * (LOG_LIMIT_BYTES * 3),
    ])
    container.logs.side_effect = None
    container.logs.return_value = stream
    result = manager.logs(sandbox_id, tail_lines=200)
    assert result.ok
    assert result.logs.untrusted and result.logs.truncated
    assert len(result.logs.text.encode()) <= LOG_LIMIT_BYTES
    assert "secret-value" not in result.logs.text and "two words" not in result.logs.text
    assert "abc123" not in result.logs.text
    assert "\x1b" not in result.logs.text and "\x00" not in result.logs.text
    assert stream.gi_frame is None
    container.logs.assert_called_once_with(stdout=True, stderr=True, stream=True, follow=False, tail=200)


@pytest.mark.parametrize("tail", [0, 201, True, 1.5])
def test_bad_log_tail(engine, tail):
    assert engine[0].logs(str(uuid4()), tail).error.code == "invalid_tail"


def test_secret_redaction_patterns():
    raw = b'''{"access_token":"hello", "password":"long value"}
https://name:pass@example.test/path
Bearer token-value
ghp_1234567890abc
sk-1234567890abcdefghijkl
eyJhbGciOiJIUzI1NiJ9.abcdefgh.signature
-----BEGIN PRIVATE KEY-----\nprivate data\n-----END PRIVATE KEY-----
'''
    text = sanitize_logs(raw, False).text
    for secret in ("hello", "long value", "name:pass", "token-value", "ghp_", "sk-", "eyJ", "private data"):
        assert secret not in text
    assert "REDACTED" in text


def test_new_mcp_tool_delegation_and_original_health(engine, monkeypatch):
    manager, client, _, _ = engine
    monkeypatch.setattr(tools, "get_manager", lambda: manager)

    async def scenario():
        async with Client(mcp) as connection:
            check = await connection.call_tool("check_docker", {})
            assert check.structured_content["available"] is True
            invalid = await connection.call_tool("create_sandbox", {"image": "unknown"})
            assert invalid.structured_content["error"]["code"] == "image_not_approved"
            result = await connection.call_tool("create_sandbox", {"image": "test"})
            assert result.structured_content["ok"]
            sid = result.structured_content["sandbox"]["sandbox_id"]
            for name in ("start_sandbox", "inspect_sandbox", "get_sandbox_logs", "destroy_sandbox"):
                response = await connection.call_tool(name, {"sandbox_id": sid})
                assert response.structured_content["ok"], response
            health = await connection.call_tool("health_check", {})
            assert health.structured_content["status"] == "healthy"
    asyncio.run(scenario())
    client.images.pull.assert_not_called()


def test_cleanup_batch_limit_is_enforced(engine, monkeypatch):
    manager, _, _, clock = engine
    sandbox_id = created(engine)
    with manager.registry.transaction() as data:
        second = data.entries[sandbox_id].model_copy(deep=True)
        second.sandbox_id = str(uuid4())
        data.entries[second.sandbox_id] = second
        manager.registry.save(data)
    clock.return_value += timedelta(seconds=121)
    visited = []

    def destroy_one(client, data, key):
        visited.append(key)
        del data.entries[key]
        manager.registry.save(data)
    monkeypatch.setattr(manager, "_destroy", destroy_one)
    outcome = manager.cleanup_expired(limit=1)
    assert len(visited) == 1
    assert (outcome.examined, outcome.destroyed, outcome.remaining_expired) == (1, 1, 1)


def test_startup_runs_cleanup_before_stdio(monkeypatch):
    from guardrails import server
    from guardrails.docker.models import CleanupResult
    order = []
    manager = MagicMock()
    manager.cleanup_expired.side_effect = lambda: (order.append("cleanup") or CleanupResult())
    monkeypatch.setattr(server, "get_manager", lambda: manager)
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: order.append(kwargs["transport"]))
    server.main()
    assert order == ["cleanup", "stdio"]


def test_removal_failure_preserves_registry_for_retry(engine):
    manager, _, container, _ = engine
    sandbox_id = created(engine)
    container.remove.side_effect = APIError("busy")
    assert not manager.destroy(sandbox_id).ok
    with manager.registry.transaction() as data:
        assert sandbox_id in data.entries
    container.remove.side_effect = None
    assert manager.destroy(sandbox_id).ok


def test_ambiguous_creation_intent_never_removes_anything(engine):
    manager, client, container, clock = engine
    created(engine)
    with manager.registry.transaction() as data:
        next(iter(data.entries.values())).container_id = None
        manager.registry.save(data)
    client.containers.list.return_value = [container, MagicMock()]
    clock.return_value += timedelta(seconds=121)
    result = manager.cleanup_expired()
    assert result.errors[0].code == "ownership_mismatch"
    container.remove.assert_not_called()
