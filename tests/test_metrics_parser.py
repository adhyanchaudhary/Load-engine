import json
import pytest
from guardrails.load_testing.parser import parse_summary


def summary(count=100,p95=25,failed=0):
    return json.dumps({'metrics':{
        'http_reqs':{'values':{'count':count,'rate':12.5}},
        'http_req_duration':{'values':{'med':10,'p(95)':p95,'p(99)':40},
            'thresholds':{'p(95)<=250':{'ok':p95<=250}}},
        'http_req_failed':{'values':{'rate':failed},'thresholds':{'rate<=0.01':{'ok':failed<=.01}}}}}).encode()


def test_summary_metrics_and_units():
    m=parse_summary(summary())
    assert (m.request_count,m.throughput_rps,m.p50_ms,m.p95_ms,m.p99_ms,m.http_failure_rate)==(100,12.5,10,25,40,0)
    assert m.thresholds['http_req_duration:p(95)<=250'] is True
    assert not m.errors and m.parser_schema_version==1


def test_p50_fallback():
    data=json.loads(summary());v=data['metrics']['http_req_duration']['values'];v['p(50)']=v.pop('med')
    assert parse_summary(json.dumps(data).encode()).p50_ms==10


@pytest.mark.parametrize('raw',[b'{}',b'not json',b'[]',b'{"metrics":[]}', b'{"type":"Point","metric":"http_reqs"}'])
def test_invalid_or_streaming_summary(raw):
    m=parse_summary(raw)
    assert m.errors and m.request_count is None


def test_missing_nonfinite_and_fractional_count():
    data=json.loads(summary());data['metrics']['http_req_duration']['values']['p(95)']=float('nan')
    data['metrics']['http_reqs']['values']['count']=1.2
    data['metrics']['http_req_failed']['values']['rate']=2
    m=parse_summary(json.dumps(data).encode())
    assert m.p95_ms is None and m.request_count is None and m.http_failure_rate is None
    assert len(m.errors)==3
