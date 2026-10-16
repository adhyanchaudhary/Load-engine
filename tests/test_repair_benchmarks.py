from unittest.mock import MagicMock
import pytest
from repair_fixtures import repair,registered,fake_verification


@pytest.mark.parametrize('build,correctness',[('FAIL','PASS'),('INCONCLUSIVE','PASS'),('PASS','FAIL'),('PASS','INCONCLUSIVE')])
def test_abort_before_benchmark(repair,monkeypatch,build,correctness):
    s,c=registered(repair);benchmark=fake_verification(repair,monkeypatch,build=build,correctness=correctness)
    r=repair.manager.verify(s.session_id,c.candidate_id)
    benchmark.assert_not_called()
    assert r.verdict in ('REJECTED','INCONCLUSIVE')


@pytest.mark.parametrize('reason',['timeout','missing_summary'])
def test_benchmark_infrastructure_failures(repair,monkeypatch,reason):
    s,c=registered(repair);fake_verification(repair,monkeypatch)
    monkeypatch.setattr('guardrails.repair.manager.RepairBenchmarker.run',MagicMock(side_effect=ValueError(reason)))
    r=repair.manager.verify(s.session_id,c.candidate_id)
    assert r.verdict=='INCONCLUSIVE' and r.ended_at


def test_compatibility_failure(repair,monkeypatch):
    s,c=registered(repair);fake_verification(repair,monkeypatch)
    repair.baseline.metadata.environment_fingerprint=None;repair.artifacts.save(repair.baseline)
    r=repair.manager.verify(s.session_id,c.candidate_id)
    assert r.verdict=='INCONCLUSIVE'


def test_missing_baseline_raw_summary(repair,monkeypatch):
    s,c=registered(repair);benchmark=fake_verification(repair,monkeypatch)
    repair.artifacts.store._path(repair.baseline.test_id,'.summary.json').unlink()
    result=repair.manager.verify(s.session_id,c.candidate_id)
    assert result.verdict=='INCONCLUSIVE';benchmark.assert_not_called()
