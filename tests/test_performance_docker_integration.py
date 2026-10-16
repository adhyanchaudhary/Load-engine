"""Opt-in, local images only. No image pulls, public targets, or host ports."""
import os
from pathlib import Path
import pytest
from docker.errors import NotFound
from guardrails.docker.manager import SandboxManager
from guardrails.load_testing.runner import LoadEngine
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.load_testing.thresholds import evaluate

pytestmark=pytest.mark.docker_integration


def test_real_isolated_http_benchmark(tmp_path,monkeypatch):
    if os.environ.get('GUARDRAILS_DOCKER_INTEGRATION')!='1':
        pytest.skip('Opt in with GUARDRAILS_DOCKER_INTEGRATION=1; approved local HTTP fixture and k6 required')
    policy_path=os.environ.get('GUARDRAILS_POLICY_FILE')
    alias=os.environ.get('GUARDRAILS_HTTP_TEST_IMAGE')
    if not policy_path or not alias:
        pytest.skip('Set GUARDRAILS_POLICY_FILE and GUARDRAILS_HTTP_TEST_IMAGE to the approved local deterministic GET fixture')
    manager=SandboxManager(tmp_path/'state',policy_path=Path(policy_path))
    policy=manager._load_policy()
    if policy.performance is None:
        pytest.skip('No approved k6 performance policy; no image pull attempted')
    if alias not in policy.images or policy.images[alias].application_port is None:
        pytest.skip('Approved HTTP fixture requires a trusted application_port')
    status=manager.check_docker()
    if not status.available:pytest.skip('Docker daemon unavailable')
    with manager.client_factory() as client:
        try:client.images.get(policy.performance.image.image_id)
        except NotFound:pytest.skip('Approved k6 image missing locally; no pull attempted')
    created=manager.create(alias,timeout_seconds=300,memory_mb=128,pids_limit=32)
    if created.error and created.error.code=='image_not_local':
        pytest.skip('Approved HTTP fixture missing locally; no pull attempted')
    assert created.ok,created
    sid=created.sandbox.sandbox_id
    engine=LoadEngine(manager)
    observed=[]
    original=engine._runner
    def checked(client,entry,policy,script,ip):
        runner,intent=original(client,entry,policy,script,ip)
        runner.reload();host=runner.attrs['HostConfig']
        assert host['NanoCpus']==round(policy.cpus*1e9)
        assert host['Memory']==host['MemorySwap']==policy.memory_mb*1024**2
        assert host['PidsLimit']==policy.pids_limit
        assert host['ReadonlyRootfs'] and not host.get('PortBindings')
        assert host['NetworkMode']==entry.network_name
        observed.append(runner.id)
        return runner,intent
    monkeypatch.setattr(engine,'_runner',checked)
    network_id=None
    try:
        ready=engine.prepare(sid)
        assert ready.readiness_status=='ready',ready
        network_id=ready.environment_id
        result=engine.run(PerformanceTestConfig(sandbox_id=sid,endpoint_path='/',vus=2,duration_seconds=2,
            p95_latency_budget_ms=10000,error_rate_budget=0.01))
        assert result.status=='completed',result
        assert result.budget.status=='PASS',result
        assert result.metrics.request_count>0
        assert all(getattr(result.metrics,x) is not None for x in ('throughput_rps','p50_ms','p95_ms','p99_ms'))
        assert len(result.application.samples)>=3
        assert result.application.peak_observed_memory_bytes>0
        assert engine.store.get(result.test_id).model_dump()==result.model_dump()
        # A completed measured failure must remain FAIL, including k6 exit 99.
        failed=engine.run(PerformanceTestConfig(sandbox_id=sid,endpoint_path='/missing-endpoint',vus=1,duration_seconds=1))
        assert failed.status=='completed' and failed.budget.status=='FAIL',failed
        assert failed.runner_exit_code==99
        with manager.client_factory() as client:
            network=client.networks.get(network_id)
            assert network.attrs['Internal'] and network.attrs['Driver']=='bridge'
            assert set(network.attrs['Containers'])=={created.sandbox.container_id}
            for runner_id in observed:
                with pytest.raises(NotFound):client.containers.get(runner_id)
        assert manager.inspect(sid).sandbox.limits.verified
    finally:
        assert manager.destroy(sid).ok
    with manager.client_factory() as client:
        if network_id:
            with pytest.raises(NotFound):client.networks.get(network_id)
