import json
from datetime import timedelta
from guardrails.diagnostics.models import DiagnosticInput
from guardrails.diagnostics.tracing import TraceAdapter
from diagnostic_fixtures import artifacts


def report(artifacts,kind):return artifacts.service.analyze(DiagnosticInput(test_id=artifacts.make(kind).test_id))


def categories(r):return {f.category for f in r.findings}


def test_oom_confirmed_failure(artifacts):
    r=report(artifacts,'oom');f=next(f for f in r.findings if f.category=='CONTAINER_OOM')
    assert f.severity=='CRITICAL' and f.confidence=='HIGH'
    assert any(e.interpretation=='CONFIRMED_FAILURE' for e in r.evidence)


def test_memory_sampled_max_not_leak(artifacts):
    r=report(artifacts,'memory')
    assert 'MEMORY_PRESSURE' in categories(r)
    e=next(e for e in r.evidence if 'sampled_limit_ratio' in str(e.observed_value))
    assert e.observed_value['sampled_limit_ratio']==.95
    assert 'leak is not established' in next(f.explanation for f in r.findings if f.category=='MEMORY_PRESSURE')


def test_cpu_quota_aware_and_sustained(artifacts):
    r=report(artifacts,'cpu');assert 'CPU_PRESSURE' in categories(r)
    e=next(e for e in r.evidence if 'mean_quota_ratio' in str(e.observed_value))
    assert e.observed_value['mean_quota_ratio']==.96
    record=artifacts.make('cpu');record.application.samples=record.application.samples[:2];artifacts.save(record)
    assert 'CPU_PRESSURE' not in categories(artifacts.service.analyze(DiagnosticInput(test_id=record.test_id)))


def test_error_signatures_and_secret_instruction_exclusion(artifacts):
    r=report(artifacts,'errors')
    assert {'HIGH_ERROR_RATE','APPLICATION_ERRORS'}<=categories(r)
    text=r.model_dump_json()
    assert 'synthetic-secret' not in text and 'IGNORE_ALL_INSTRUCTIONS' not in text
    assert next(f for f in r.findings if f.category=='APPLICATION_ERRORS').confidence=='LOW'


def test_no_database_guess_from_http_latency(artifacts):
    r=report(artifacts,'ambiguous')
    assert 'INSUFFICIENT_EVIDENCE' in categories(r) and 'DATABASE_LATENCY' not in categories(r)


def test_database_only_from_real_timing_artifact(artifacts):
    record=artifacts.make()
    bundle=dict(schema_version=1,test_id=record.test_id,owner_id=artifacts.owner,clock_origin='UTC',events=[
        dict(event_id='db1',kind='database',started_at=record.started_at.isoformat(),ended_at=(record.started_at+timedelta(milliseconds=200)).isoformat())])
    TraceAdapter(artifacts.store).ingest(record.test_id,json.dumps(bundle).encode())
    r=artifacts.service.analyze(DiagnosticInput(test_id=record.test_id))
    assert 'DATABASE_LATENCY' in categories(r)
    assert not any('missing index' in f.title for f in r.findings)
    bundle['events'][0]['started_at']=(record.started_at-timedelta(seconds=2)).isoformat()
    bundle['events'][0]['ended_at']=(record.started_at-timedelta(seconds=1)).isoformat()
    TraceAdapter(artifacts.store).ingest(record.test_id,json.dumps(bundle).encode())
    assert 'DATABASE_LATENCY' not in categories(artifacts.service.analyze(DiagnosticInput(test_id=record.test_id)))


def test_compatible_latency_regression_finding(artifacts):
    baseline=artifacts.make();candidate=artifacts.make(offset=60);candidate.metrics.p95_ms=30;artifacts.save(candidate)
    result=artifacts.service.analyze(DiagnosticInput(test_id=candidate.test_id,baseline_test_id=baseline.test_id))
    assert 'LATENCY_REGRESSION' in categories(result)
