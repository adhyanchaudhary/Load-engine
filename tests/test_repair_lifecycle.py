from threading import Event
from unittest.mock import MagicMock
from filelock import Timeout
import pytest
from guardrails.repair.correctness import Cancelled
from guardrails.repair.lifecycle import cleanup
from repair_fixtures import repair,registered,fake_verification


def test_verified_finalization_idempotent_no_workspace_change(repair,monkeypatch):
    s,c=registered(repair);before=(repair.root/'app.py').read_bytes();fake_verification(repair,monkeypatch)
    result=repair.manager.verify(s.session_id,c.candidate_id)
    assert result.verdict=='VERIFIED'
    done=repair.manager.finalize(s.session_id,c.candidate_id,'ACCEPT_FOR_REVIEW')
    assert done.status=='COMPLETED' and done.accepted_candidate_id==c.candidate_id
    assert repair.manager.finalize(s.session_id,c.candidate_id,'ACCEPT_FOR_REVIEW')==done
    assert (repair.root/'app.py').read_bytes()==before
    with pytest.raises(ValueError):repair.manager.finalize(s.session_id,c.candidate_id,'REJECT')


def test_unverified_acceptance_rejected_explicit_reject_allowed(repair):
    s,c=registered(repair)
    with pytest.raises(ValueError,match='verified'):repair.manager.finalize(s.session_id,c.candidate_id,'ACCEPT_FOR_REVIEW')
    result=repair.manager.finalize(s.session_id,c.candidate_id,'REJECT')
    assert result.status=='COMPLETED' and result.accepted_candidate_id is None


def test_cancellation_and_session_lock(repair,monkeypatch):
    s,c=registered(repair);fake_verification(repair,monkeypatch)
    with repair.manager.store.lock(s.session_id):
        with pytest.raises(Timeout):repair.manager.verify(s.session_id,c.candidate_id)
    monkeypatch.setattr('guardrails.repair.manager.CorrectnessVerifier.run',MagicMock(side_effect=Cancelled()))
    result=repair.manager.verify(s.session_id,c.candidate_id)
    assert result.verdict=='CANCELLED' and repair.manager.status(s.session_id).session.status=='CANCELLED'


@pytest.mark.parametrize('stage',['build','test','benchmark'])
def test_cleanup_always_runs(repair,monkeypatch,stage):
    s,c=registered(repair);fake_verification(repair,monkeypatch,build='FAIL' if stage=='build' else 'PASS',correctness='FAIL' if stage=='test' else 'PASS')
    if stage=='benchmark':monkeypatch.setattr('guardrails.repair.manager.RepairBenchmarker.run',MagicMock(side_effect=OSError()))
    cleanup_mock=MagicMock(return_value=[]);monkeypatch.setattr('guardrails.repair.manager.cleanup',cleanup_mock)
    repair.manager.verify(s.session_id,c.candidate_id)
    cleanup_mock.assert_called_once_with(repair.base,s.session_id)


def test_cleanup_failure_blocks_acceptance_and_preserves_resources(repair,monkeypatch):
    s,c=registered(repair);fake_verification(repair,monkeypatch)
    monkeypatch.setattr('guardrails.repair.manager.cleanup',lambda *args:['owned-sandbox'])
    result=repair.manager.verify(s.session_id,c.candidate_id)
    assert result.verdict=='INCONCLUSIVE' and result.remaining_managed_resources==['owned-sandbox']
    with pytest.raises(ValueError):repair.manager.finalize(s.session_id,c.candidate_id,'ACCEPT_FOR_REVIEW')


def test_cleanup_does_not_touch_unrelated_registry_entries(repair):
    s,c=registered(repair)
    repair.base.destroy=MagicMock()
    assert cleanup(repair.base,s.session_id)==[]
    repair.base.destroy.assert_not_called()


def test_cross_session_candidate_access_rejected(repair):
    s,c=registered(repair)
    other=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    with pytest.raises(ValueError,match='belong'):repair.manager.verify(other.session_id,c.candidate_id)


def test_snapshot_tampering_rejected(repair):
    s,c=registered(repair)
    repair.manager.store.files._path(c.candidate_id,'.source.tar').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='integrity'):repair.manager.verify(s.session_id,c.candidate_id)


def test_acceptance_rechecks_measurements(repair,monkeypatch):
    s,c=registered(repair);fake_verification(repair,monkeypatch)
    verified=repair.manager.verify(s.session_id,c.candidate_id)
    path=repair.artifacts.store._path(verified.benchmark_test_ids[0],'.summary.json');path.unlink()
    with pytest.raises(ValueError,match='evidence'):repair.manager.finalize(s.session_id,c.candidate_id,'ACCEPT_FOR_REVIEW')


def test_recovery_marks_interrupted_candidate_inconclusive(repair,monkeypatch):
    from guardrails.repair.models import VerificationReport
    from guardrails.repair.policy import fingerprint
    from guardrails.repair.sessions import now
    from uuid import uuid4
    s,c=registered(repair);s=repair.manager.store.get(s.session_id,'session');s.status='VERIFYING'
    c.verification_status='VERIFYING';c.verification_id=str(uuid4())
    report=VerificationReport(verification_id=c.verification_id,owner_id=s.owner_id,session_id=s.session_id,candidate_id=c.candidate_id,
        source_revision=c.source_revision,source_digest=c.source_digest,baseline_test_id=s.baseline_test_id,
        recipe_digest=fingerprint(repair.project.recipe),started_at=now())
    repair.manager.store.save(report);repair.manager.store.save(c);repair.manager.store.save(s)
    assert repair.manager.recover()==[]
    status=repair.manager.status(s.session_id)
    assert status.session.status=='REJECTED' and status.latest_verification.verdict=='INCONCLUSIVE'
    assert status.candidates[0].verification_status=='INCONCLUSIVE'


def test_cleanup_checks_session_tag_even_with_own_registry(repair):
    from test_sandbox import IMAGE,CAPABILITIES
    from guardrails.docker.models import RegistryEntry,SandboxConfig
    from guardrails.repair.sessions import now
    from datetime import timedelta
    from uuid import uuid4
    s,c=registered(repair);foreign=str(uuid4());sid=str(uuid4());stamp=now()
    with repair.base.registry.transaction() as data:
        data.entries[sid]=RegistryEntry(sandbox_id=sid,container_id='b'*64,owner=data.owner,nonce=str(uuid4()),
            config=SandboxConfig(image='test'),approved=repair.project.runtime_image,created_at=stamp,
            expires_at=stamp+timedelta(seconds=120),repair_session_id=foreign)
        repair.base.registry.save(data)
    repair.base.destroy=MagicMock()
    assert cleanup(repair.base,s.session_id)==[]
    repair.base.destroy.assert_not_called()
