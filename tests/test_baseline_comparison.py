import pytest
from diagnostic_fixtures import artifacts


def pair(artifacts):return artifacts.make(),artifacts.make(offset=60)


def test_compatible_comparison_and_all_metrics(artifacts):
    b,c=pair(artifacts);v=artifacts.service.compare(b.test_id,c.test_id)
    assert v.compatibility=='COMPATIBLE'
    assert v.regression_classification=='NO_REGRESSION_DETECTED'
    assert len(v.metric_differences)==7


@pytest.mark.parametrize('field,value',[('vus',20),('duration_seconds',60),('endpoint_path','/other')])
def test_incompatible_workload(artifacts,field,value):
    b,c=pair(artifacts);setattr(c.configuration,field,value);artifacts.save(c)
    v=artifacts.service.compare(b.test_id,c.test_id)
    assert v.compatibility=='INCOMPATIBLE' and v.regression_classification=='INCONCLUSIVE'


@pytest.mark.parametrize('field,value',[('application_cpus',1),('application_memory_bytes',2000),('runner_cpus',1),
    ('runner_memory_bytes',1000),('dataset_id','other'),('architecture','arm64'),('application_configuration_fingerprint','changed')])
def test_incompatible_environment(artifacts,field,value):
    b,c=pair(artifacts);setattr(c.metadata,field,value);artifacts.save(c)
    result=artifacts.service.compare(b.test_id,c.test_id)
    assert result.compatibility=='INCOMPATIBLE' and field in result.incompatibilities


def test_missing_metadata_does_not_establish_comparability(artifacts):
    b,c=pair(artifacts);c.metadata.environment_fingerprint=None;artifacts.save(c)
    v=artifacts.service.compare(b.test_id,c.test_id)
    assert v.compatibility=='PARTIALLY_COMPATIBLE' and v.regression_classification=='INCONCLUSIVE'
    assert 'environment_fingerprint' in v.missing_compatibility_information


def test_budget_and_regression_are_independent(artifacts):
    b,c=pair(artifacts);c.metrics.p95_ms=26;artifacts.save(c)
    v=artifacts.service.compare(b.test_id,c.test_id)
    assert c.budget.status=='PASS' and v.regression_classification=='REGRESSION'
    b.metrics.p95_ms=500;c.metrics.p95_ms=300;c.budget.status='FAIL';artifacts.save(b);artifacts.save(c)
    assert artifacts.service.compare(b.test_id,c.test_id).regression_classification=='NO_REGRESSION_DETECTED'


def test_failed_or_same_or_reversed_tests_rejected(artifacts):
    b,c=pair(artifacts);c.status='failed';artifacts.save(c)
    assert artifacts.service.compare(b.test_id,c.test_id).compatibility=='INCOMPATIBLE'
    assert artifacts.service.compare(b.test_id,b.test_id).compatibility=='INCOMPATIBLE'
    assert artifacts.service.compare(c.test_id,b.test_id).compatibility=='INCOMPATIBLE'
