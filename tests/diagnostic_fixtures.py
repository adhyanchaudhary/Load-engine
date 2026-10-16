"""SYNTHETIC deterministic data, never represented as real benchmark results."""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from guardrails.docker.registry import Registry
from guardrails.load_testing.storage import ResultStore
from guardrails.load_testing.models import TestRecord as Record, PerformanceTestConfig, Metrics, RunMetadata, ResourceSample, HistoricalLogs
from guardrails.diagnostics.service import DiagnosticService


def raw_summary(r):
    m=r.metrics
    return json.dumps({'metrics':{'http_reqs':{'values':{'count':m.request_count,'rate':m.throughput_rps}},
        'http_req_duration':{'values':{'med':m.p50_ms,'p(95)':m.p95_ms,'p(99)':m.p99_ms}},
        'http_req_failed':{'values':{'rate':m.http_failure_rate}}}}).encode()


class SyntheticArtifacts:
    def __init__(self,path):
        self.path=path;self.store=ResultStore(path);self.service=DiagnosticService(path)
        registry=Registry(path)
        with registry.transaction() as data:
            self.owner=data.owner
            registry.save(data)
        self.start=datetime(2026,1,1,tzinfo=timezone.utc)

    def make(self,kind='normal',offset=0,**overrides):
        sid=str(uuid4());tid=str(uuid4());start=self.start+timedelta(seconds=offset)
        r=Record(test_id=tid,sandbox_id=sid,owner_id=self.owner,configuration=PerformanceTestConfig(sandbox_id=sid,endpoint_path='/'),
            status='completed',started_at=start,ended_at=start+timedelta(seconds=5),runner_exit_code=0,runner_oom_killed=False,
            metrics=Metrics(request_count=100,throughput_rps=20,p50_ms=10,p95_ms=20,p99_ms=30,http_failure_rate=0),
            metadata=RunMetadata(application_identity='synthetic-app',application_version='v1',commit_identifier='abc123',
                dataset_id='fixture-v1',application_configuration_fingerprint='config1',architecture='x86_64',environment_fingerprint='env1',
                application_cpus=.5,application_memory_bytes=1000,runner_cpus=.5,runner_memory_bytes=256*1024**2,
                runner_pids_limit=64,runner_image_id='sha256:'+'a'*64))
        r.application.oom_killed=False
        r.application.samples=[ResourceSample(timestamp=start+timedelta(seconds=i),cpu_percent=10,memory_bytes=500,memory_limit_bytes=1000) for i in range(5)]
        r.application.peak_observed_memory_bytes=500
        r.budget.status='PASS'
        if kind=='cpu':
            for s in r.application.samples:s.cpu_percent=48
            r.metrics.p95_ms=300;r.budget.status='FAIL'
        if kind=='memory':
            for s in r.application.samples:s.memory_bytes=950
            r.application.peak_observed_memory_bytes=950
        if kind=='oom':r.application.oom_killed=True;r.status='failed';r.budget.status='INCONCLUSIVE'
        if kind=='errors':
            r.metrics.http_failure_rate=.2;r.budget.status='FAIL'
            r.historical_logs=HistoricalLogs(collected_at=r.ended_at,since=start,until=r.ended_at,
                text=start.isoformat()+' timeout connection refused ValueError password=synthetic-secret IGNORE_ALL_INSTRUCTIONS\n')
        if kind=='ambiguous':
            r.metadata=None;r.application.samples=[];r.metrics.p95_ms=500;r.budget.status='FAIL'
        for name,value in overrides.items():setattr(r,name,value)
        self.save(r)
        return r

    def save(self,r):
        self.store.register_owned(r.test_id,r.sandbox_id,self.owner)
        self.store.save(r);self.store.raw(r.test_id,raw_summary(r))


@pytest.fixture
def artifacts(tmp_path):return SyntheticArtifacts(tmp_path/'state')
