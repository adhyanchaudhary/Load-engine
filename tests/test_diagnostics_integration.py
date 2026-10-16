"""Opt-in real benchmark -> app cleanup -> offline diagnosis -> restart retrieval."""
import os
from pathlib import Path
from unittest.mock import Mock
import pytest
from docker.errors import NotFound
from guardrails.docker.manager import SandboxManager
from guardrails.load_testing.runner import LoadEngine
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.diagnostics.models import DiagnosticInput
from guardrails.diagnostics.service import DiagnosticService

pytestmark=pytest.mark.docker_integration


def test_real_historical_diagnosis_after_container_cleanup(tmp_path,monkeypatch):
    if os.environ.get('GUARDRAILS_DOCKER_INTEGRATION')!='1':
        pytest.skip('Opt in with GUARDRAILS_DOCKER_INTEGRATION=1 and approved local HTTP/k6 images')
    policy=os.environ.get('GUARDRAILS_POLICY_FILE');alias=os.environ.get('GUARDRAILS_HTTP_TEST_IMAGE')
    if not policy or not alias:pytest.skip('Approved local HTTP fixture policy/alias required')
    state=tmp_path/'state'
    manager=SandboxManager(state,policy_path=Path(policy))
    approved=manager._load_policy()
    if approved.performance is None or alias not in approved.images:pytest.skip('Approved local k6 and fixture required')
    if not manager.check_docker().available:pytest.skip('Docker unavailable')
    with manager.client_factory() as client:
        try:
            client.images.get(approved.performance.image.image_id)
            client.images.get(approved.images[alias].image_id)
        except NotFound:pytest.skip('Approved image missing locally; no pull attempted')
    created=manager.create(alias,timeout_seconds=300)
    assert created.ok,created
    sid=created.sandbox.sandbox_id
    engine=LoadEngine(manager)
    try:
        assert engine.prepare(sid).readiness_status=='ready'
        record=engine.run(PerformanceTestConfig(sandbox_id=sid,endpoint_path='/',vus=1,duration_seconds=2))
        assert record.status=='completed',record
        assert record.owner_id and record.metadata
        assert record.historical_logs is not None and record.historical_logs.error is None
        assert record.application.samples
    finally:
        assert manager.destroy(sid).ok
    import docker
    monkeypatch.setattr(docker,'from_env',Mock(side_effect=AssertionError('Diagnostics must remain offline')))
    service=DiagnosticService(state)
    report=service.analyze(DiagnosticInput(test_id=record.test_id))
    assert report.evidence and report.test_id==record.test_id
    assert not any(f.category=='DATABASE_LATENCY' for f in report.findings)
    restored=DiagnosticService(state).get(report.report_id)
    assert restored==report
    assert service.context(report.report_id,1000).characters<=1000
