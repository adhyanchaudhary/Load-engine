"""Opt-in real Docker test. Never builds or pulls an image."""
import os
from pathlib import Path

import pytest

from guardrails.docker.client import connect
from guardrails.docker.manager import SandboxManager
from guardrails.docker.security import isolation_discrepancies

pytestmark = pytest.mark.docker_integration


def test_real_local_image_lifecycle(tmp_path):
    if os.environ.get("GUARDRAILS_DOCKER_INTEGRATION") != "1":
        pytest.skip("Opt in with GUARDRAILS_DOCKER_INTEGRATION=1 and an approved local image policy")
    policy = os.environ.get("GUARDRAILS_POLICY_FILE")
    alias = os.environ.get("GUARDRAILS_TEST_IMAGE")
    if not policy or not alias:
        pytest.skip("Set GUARDRAILS_POLICY_FILE and GUARDRAILS_TEST_IMAGE to an operator-approved local test image")
    manager = SandboxManager(tmp_path / "registry", policy_path=Path(policy))
    status = manager.check_docker()
    if not status.available:
        pytest.skip("Docker daemon unavailable")
    assert status.resource_limit_status == "daemon_reports_support", status
    created = manager.create(alias, cpus=0.5, memory_mb=128, pids_limit=32, timeout_seconds=120)
    if created.error and created.error.code == "image_not_local":
        pytest.skip("Approved integration image is not provisioned locally; no pull attempted")
    assert created.ok, created.error
    sandbox_id = created.sandbox.sandbox_id
    try:
        assert created.sandbox.status == "created"
        assert created.sandbox.limits.verified
        started = manager.start(sandbox_id)
        assert started.ok, started.error
        # Operator must approve an image/command that stays alive for this check.
        assert started.sandbox.status == "running", started
        inspected = manager.inspect(sandbox_id)
        assert inspected.ok and inspected.sandbox.limits.verified
        assert inspected.sandbox.limits.cpus == 0.5
        assert inspected.sandbox.limits.memory_mb == 128
        assert inspected.sandbox.limits.pids_limit == 32
        with connect() as client, manager.registry.transaction() as data:
            container = client.containers.get(created.sandbox.container_id)
            assert not isolation_discrepancies(container.attrs, data.entries[sandbox_id])
            host = container.attrs["HostConfig"]
            assert host["NanoCpus"] == 500_000_000
            assert host["Memory"] == host["MemorySwap"] == 128 * 1024**2
            assert host["PidsLimit"] == 32
            assert container.attrs["Config"]["User"] == data.entries[sandbox_id].approved.user
        assert manager.logs(sandbox_id).ok
    finally:
        destroyed = manager.destroy(sandbox_id)
        assert destroyed.ok and destroyed.destroyed, destroyed
    assert manager.destroy(sandbox_id).already_absent
    with connect() as client:
        from docker.errors import NotFound
        with pytest.raises(NotFound):
            client.containers.get(created.sandbox.container_id)
