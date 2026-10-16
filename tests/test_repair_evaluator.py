import pytest
from repair_fixtures import repair,registered,fake_verification


@pytest.mark.parametrize('p95,throughput,error,verdict,reason',[(60,15,0,'VERIFIED','ready for review'),
    (300,15,0,'REJECTED','budget'),(95,15,0,'REJECTED','meaningful'),(110,15,0,'REJECTED','meaningful'),
    (60,8,0,'REJECTED','throughput'),(60,15,.005,'REJECTED','failure rate'),(60,15,.1,'REJECTED','budget')])
def test_verdicts_require_real_evidence(repair,monkeypatch,p95,throughput,error,verdict,reason):
    s,c=registered(repair);fake_verification(repair,monkeypatch,p95=p95,throughput=throughput,error=error)
    r=repair.manager.verify(s.session_id,c.candidate_id)
    assert r.verdict==verdict and any(reason in x for x in r.reasons)
    if verdict=='VERIFIED':assert r.statistics['improvement_percent']==40


def test_missing_metric_and_unstable_trials(repair,monkeypatch):
    s,c=registered(repair);mock=fake_verification(repair,monkeypatch)
    original=mock.side_effect
    def unstable(*args):
        trials=original(*args);trials[0].metrics.p95_ms=10;return trials
    mock.side_effect=unstable
    assert repair.manager.verify(s.session_id,c.candidate_id).verdict=='INCONCLUSIVE'


def test_unstable_baseline_repetitions_are_inconclusive(repair,monkeypatch):
    import json
    s,c=registered(repair);fake_verification(repair,monkeypatch)
    other=repair.artifacts.make(offset=10)
    other.metadata=repair.baseline.metadata.model_copy(deep=True)
    other.metrics.p95_ms=500;repair.artifacts.save(other)
    repair.manager.store.files.write(repair.baseline.test_id,'.baseline-trials.json',json.dumps({'test_ids':[repair.baseline.test_id,other.test_id]}).encode())
    result=repair.manager.verify(s.session_id,c.candidate_id)
    assert result.verdict=='INCONCLUSIVE' and result.build is None
