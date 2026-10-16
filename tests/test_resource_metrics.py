from unittest.mock import MagicMock
from guardrails.load_testing.metrics import sample
from guardrails.load_testing.models import ResourceUsage


def test_sampled_cpu_memory_and_bounded_samples():
    container=MagicMock(attrs={'State':{'Status':'running','OOMKilled':False}})
    usage=ResourceUsage()
    def stats(cpu,system,memory):
        return {'cpu_stats':{'cpu_usage':{'total_usage':cpu},'system_cpu_usage':system,'online_cpus':2},
                'memory_stats':{'usage':memory,'limit':4096}}
    container.stats.side_effect=[stats(100,1000,2000),stats(200,2000,3000)]
    assert sample(container,usage)
    assert sample(container,usage)
    assert usage.samples[0].cpu_percent is None
    assert usage.samples[1].cpu_percent==20
    assert usage.peak_observed_memory_bytes==3000
    assert usage.samples[1].memory_limit_bytes==4096
    container.stats.side_effect=None;container.stats.return_value=stats(200,2000,1000)
    for _ in range(200):sample(container,usage)
    assert len(usage.samples)==150 and usage.peak_observed_memory_bytes==3000


def test_unavailable_stats_and_oom_state():
    c=MagicMock(attrs={'State':{'Status':'exited','OOMKilled':True,'ExitCode':137}})
    c.stats.side_effect=OSError('private host detail')
    usage=ResourceUsage()
    assert not sample(c,usage)
    assert usage.oom_killed and usage.exit_code==137
    assert usage.errors==['Application resource sampling unavailable']
    assert usage.peak_observed_memory_bytes is None


def test_runner_throttling_is_retained():
    from guardrails.load_testing.metrics import sample_runner
    from test_thresholds import record
    from guardrails.load_testing.thresholds import evaluate
    r=record()
    c=MagicMock()
    c.stats.return_value={'cpu_stats':{'throttling_data':{'throttled_periods':1}}}
    sample_runner(c,r)
    assert r.runner_cpu_throttled and evaluate(r).status=='INCONCLUSIVE'
    c.stats.return_value={}
    sample_runner(c,r)
    assert r.runner_cpu_throttled
