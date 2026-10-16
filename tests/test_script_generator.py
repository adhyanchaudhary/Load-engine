from uuid import uuid4
import pytest
from pydantic import ValidationError
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.load_testing.script_generator import generate_script


def config(**kwargs):
    return PerformanceTestConfig(**(dict(sandbox_id=str(uuid4()), endpoint_path='/') | kwargs))


def test_valid_configuration_and_script():
    c = config(endpoint_path='/hello world/é')
    assert c.endpoint_path == '/hello%20world/%C3%A9'
    assert c.vus == 10 and c.duration_seconds == 30
    script = generate_script(c,8080)
    assert script == generate_script(c,8080)
    for text in ('constant-vus', 'k6/http', 'handleSummary', 'GUARDRAILS_SUMMARY=', 'p(99)', 'redirects: 0'):
        assert text in script
    assert 'http://localhost' not in script


@pytest.mark.parametrize('field,value', [('vus',0),('vus',101),('vus',True),('vus','3'),
    ('duration_seconds',0),('duration_seconds',121),('duration_seconds',True),
    ('p95_latency_budget_ms',float('nan')),('p95_latency_budget_ms',0),
    ('error_rate_budget',-0.01),('error_rate_budget',1.1),('method','POST'),('sandbox_id','../../x')])
def test_invalid_fields(field,value):
    with pytest.raises(ValidationError): config(**{field:value})


@pytest.mark.parametrize('path', ['http://evil.test/', 'https://evil/', '//evil', '/%2f%2fevil',
    '/%252f%252fevil', '/\\evil', '/%5cevil', '/a/../b', '/./b', '/%2e%2e/b',
    '/a#fragment', '/a?next=http://evil', '/a\n', '/%00', '/%0d%0a', '/user@evil', '/bad%ZZ'])
def test_malicious_path(path):
    with pytest.raises(ValidationError): config(endpoint_path=path)


def test_safe_literal_serialization():
    script = generate_script(config(endpoint_path='/"throw new Error("oops")'),8080)
    assert 'throw new Error' not in script
    assert '%22' in script and '%20' in script
    with pytest.raises(ValueError): generate_script(config(),80)
    with pytest.raises(ValidationError): config(headers={'Authorization':'secret'})
