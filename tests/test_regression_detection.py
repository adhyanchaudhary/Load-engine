import pytest
from guardrails.diagnostics.models import RegressionConfig
from guardrails.diagnostics.service import DiagnosticService
from diagnostic_fixtures import artifacts


@pytest.mark.parametrize('metric,value,signal',[('p95_ms',25,'p95_ms'),('p99_ms',40,'p99_ms'),('http_failure_rate',.02,'http_failure_rate')])
def test_configured_changes(artifacts,metric,value,signal):
    b=artifacts.make();c=artifacts.make(offset=60);setattr(c.metrics,metric,value);artifacts.save(c)
    result=artifacts.service.compare(b.test_id,c.test_id)
    assert signal in result.signals and result.regression_classification=='REGRESSION'
    delta=next(d for d in result.metric_differences if d.metric==metric)
    assert delta.absolute_change==pytest.approx(value-getattr(b.metrics,metric))


def test_memory_increase(artifacts):
    b=artifacts.make();c=artifacts.make('memory',offset=60)
    assert 'observed_peak_memory_bytes' in artifacts.service.compare(b.test_id,c.test_id).signals


def test_zero_baseline_missing_p95_and_raw_consistency(artifacts):
    b=artifacts.make();c=artifacts.make(offset=60)
    b.metrics.p95_ms=0;artifacts.save(b)
    result=artifacts.service.compare(b.test_id,c.test_id)
    delta=next(d for d in result.metric_differences if d.metric=='p95_ms')
    assert delta.relative_change_percent is None and result.regression_classification=='INCONCLUSIVE'
    c.metrics.p95_ms=None;artifacts.save(c)
    assert artifacts.service.compare(b.test_id,c.test_id).regression_classification=='INCONCLUSIVE'
    c=artifacts.make(offset=120);c.metrics.p95_ms=100;artifacts.store.save(c)
    assert artifacts.service.compare(b.test_id,c.test_id).regression_classification=='INCONCLUSIVE'


def test_custom_thresholds(artifacts):
    b=artifacts.make();c=artifacts.make(offset=60);c.metrics.p95_ms=25;artifacts.save(c)
    service=DiagnosticService(artifacts.path,regression_config=RegressionConfig(p95_increase_percent=50))
    assert service.compare(b.test_id,c.test_id).regression_classification=='NO_REGRESSION_DETECTED'
