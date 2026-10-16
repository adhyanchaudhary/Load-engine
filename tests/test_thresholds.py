from datetime import datetime, timezone
from uuid import uuid4
import pytest
from guardrails.load_testing.models import PerformanceTestConfig, TestRecord as Record
from guardrails.load_testing.parser import parse_summary
from guardrails.load_testing.thresholds import evaluate
from test_metrics_parser import summary


def record(**kwargs):
    sid=str(uuid4())
    return Record(test_id=str(uuid4()),sandbox_id=sid,configuration=PerformanceTestConfig(sandbox_id=sid,endpoint_path='/'),
                      started_at=datetime.now(timezone.utc),status='completed',metrics=parse_summary(summary(**kwargs)))


def test_pass():
    assert evaluate(record()).status=='PASS'


@pytest.mark.parametrize('kwargs',[{'p95':251},{'failed':.02},{'p95':251,'failed':.02}])
def test_fail(kwargs):
    r=record(**kwargs);r.runner_exit_code=99
    outcome=evaluate(r)
    assert outcome.status=='FAIL' and outcome.reasons


@pytest.mark.parametrize('condition',['zero','failed','missing','oom','appoom','error'])
def test_inconclusive(condition):
    r=record()
    if condition=='zero':r.metrics.request_count=0
    if condition=='failed':r.status='failed'
    if condition=='missing':r.metrics.p95_ms=None
    if condition=='oom':r.runner_oom_killed=True
    if condition=='appoom':r.application.oom_killed=True
    if condition=='error':r.errors.append('Application unavailable')
    assert evaluate(r).status=='INCONCLUSIVE'


def test_no_http_responses_is_unavailable_not_measured_failure():
    r=record(failed=1)
    r.metrics.application_response_count=0
    assert evaluate(r).status=='INCONCLUSIVE'


def test_received_http_errors_are_measured_failures():
    r=record(failed=1)
    r.metrics.application_response_count=100
    assert evaluate(r).status=='FAIL'
