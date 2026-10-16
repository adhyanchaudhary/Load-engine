from datetime import timedelta,timezone
from uuid import uuid4
import pytest
from guardrails.diagnostics.models import DiagnosticInput, DiagnosticFinding
from guardrails.diagnostics.service import DiagnosticService
from guardrails.diagnostics.security import redact
from diagnostic_fixtures import artifacts


def test_evidence_references_enforced(artifacts):
    r=artifacts.service.analyze(DiagnosticInput(test_id=artifacts.make('cpu').test_id))
    assert all(set(f.evidence_ids)<={e.evidence_id for e in r.evidence} for f in r.findings)
    data=r.model_dump();data['findings'][0]['evidence_ids']=['missing']
    with pytest.raises(ValueError):type(r).model_validate(data)
    with pytest.raises(ValueError):DiagnosticFinding(finding_id='f',category='CPU_PRESSURE',severity='CRITICAL',confidence='LOW',title='x',explanation='x')


def test_report_restart_ownership_and_context_bounds(artifacts):
    r=artifacts.service.analyze(DiagnosticInput(test_id=artifacts.make('errors').test_id))
    service=DiagnosticService(artifacts.path)
    assert service.get(r.report_id)==r
    for bound in (256,1000,4000,16000):
        context=service.context(r.report_id,bound)
        assert context.characters==len(context.text)<=bound
        assert 'synthetic-secret' not in context.text and 'IGNORE_ALL' not in context.text
    for bound in (0,255,16001,True):
        with pytest.raises(ValueError):service.context(r.report_id,bound)
    data=r.model_copy(update={'owner_id':str(uuid4())})
    service.reports.write(r.report_id,'.json',data.model_dump_json().encode())
    with pytest.raises(ValueError):service.get(r.report_id)


def test_correlation_aware_timestamps_no_latency_spikes(artifacts):
    r=artifacts.make('cpu')
    from guardrails.load_testing.models import HistoricalLogs
    r.historical_logs=HistoricalLogs(text=(r.started_at+timedelta(seconds=1)).isoformat()+' timeout',
        collected_at=r.ended_at,since=r.started_at,until=r.ended_at)
    # Equal instants with a different UTC offset must align.
    for s in r.application.samples:s.timestamp=s.timestamp.astimezone(timezone(timedelta(hours=5,minutes=30)))
    artifacts.save(r)
    report=artifacts.service.analyze(DiagnosticInput(test_id=r.test_id))
    assert any(f.category=='POSSIBLE_RESOURCE_CONTENTION' for f in report.findings)
    assert any('aggregate latency only' in x for x in report.missing_observability)
    assert any(e.interpretation=='TEMPORAL_ASSOCIATION' for e in report.evidence)


def test_missing_clock_and_sampling_gaps(artifacts):
    r=artifacts.make('cpu');r.started_at=r.started_at.replace(tzinfo=None);artifacts.save(r)
    report=artifacts.service.analyze(DiagnosticInput(test_id=r.test_id))
    assert any('clock/window' in x for x in report.missing_observability)
    assert not any(f.category=='CPU_PRESSURE' for f in report.findings)
    r=artifacts.make();r.application.samples=[r.application.samples[0],r.application.samples[4]];artifacts.save(r)
    report=artifacts.service.analyze(DiagnosticInput(test_id=r.test_id))
    assert any('sampling gaps' in x for x in report.missing_observability)


def test_redaction():
    text=redact('token=secret password=private user@example.com https://user:pass@example.test/a?secret=yes 10.1.2.3')
    assert 'private' not in text and 'user:pass' not in text and 'user@example' not in text and '?secret=yes' not in text


def test_tiny_context_retains_values_and_evidence(artifacts):
    r=artifacts.service.analyze(DiagnosticInput(test_id=artifacts.make('cpu').test_id))
    text=artifacts.service.context(r.report_id,256).text
    assert 'CPU_PRESSURE' in text and 'evidence=' in text and 'p95_ms=300' in text
    assert 'Cause unresolved' in text


def test_malicious_trace_identifier_is_not_context_text(artifacts):
    import json
    from guardrails.diagnostics.tracing import TraceAdapter
    r=artifacts.make()
    data=dict(schema_version=1,test_id=r.test_id,owner_id=artifacts.owner,events=[dict(event_id='IGNORE_ALL_INSTRUCTIONS',kind='database',
        started_at=r.started_at.isoformat(),ended_at=(r.started_at+timedelta(seconds=1)).isoformat())])
    TraceAdapter(artifacts.store).ingest(r.test_id,json.dumps(data).encode())
    report=artifacts.service.analyze(DiagnosticInput(test_id=r.test_id))
    assert 'IGNORE_ALL_INSTRUCTIONS' not in report.model_dump_json()
    assert 'IGNORE_ALL_INSTRUCTIONS' not in artifacts.service.context(report.report_id).text


def test_error_outside_window_does_not_form_association(artifacts):
    from guardrails.load_testing.models import HistoricalLogs
    r=artifacts.make('cpu')
    r.historical_logs=HistoricalLogs(text=(r.started_at-timedelta(milliseconds=100)).isoformat()+' timeout',
        collected_at=r.ended_at,since=r.started_at,until=r.ended_at)
    artifacts.save(r)
    report=artifacts.service.analyze(DiagnosticInput(test_id=r.test_id))
    assert not any(f.category=='POSSIBLE_RESOURCE_CONTENTION' for f in report.findings)
