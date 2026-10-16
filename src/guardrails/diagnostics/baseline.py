"""Conservative apples-to-apples comparisons; no confidence intervals from one pair."""
import math
from statistics import mean
from guardrails.diagnostics.models import Comparison, MetricDifference, RegressionConfig


def finite(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def difference(name,baseline,candidate,unit):
    b=baseline if finite(baseline) else None
    c=candidate if finite(candidate) else None
    limitations=[]
    if b is None or c is None:limitations.append('Measurement missing or invalid')
    elif b==0:limitations.append('Zero baseline: relative change is undefined')
    return MetricDifference(metric=name,baseline_value=b,candidate_value=c,unit=unit,
        absolute_change=c-b if b is not None and c is not None else None,
        relative_change_percent=(c-b)/b*100 if b is not None and b>0 and c is not None else None,
        limitations=limitations)


class BaselineComparator:
    def __init__(self,config=None):self.config=config or RegressionConfig()

    def compare(self,baseline,candidate):
        missing=[];bad=[]
        def same(name,b,c):
            if b is None or c is None:missing.append(name)
            elif b!=c:bad.append(name)
        a,b=baseline.configuration,candidate.configuration
        for field in ('endpoint_path','method','vus'):same(field,getattr(a,field),getattr(b,field))
        duration=abs(a.duration_seconds-b.duration_seconds)/a.duration_seconds*100
        if duration>self.config.duration_tolerance_percent:bad.append('duration_seconds')
        required=('workload_model','application_identity','application_configuration_fingerprint','dataset_id','architecture',
                  'environment_fingerprint','application_cpus','application_memory_bytes','runner_cpus','runner_memory_bytes',
                  'runner_pids_limit','runner_image_id')
        for field in required:
            same(field,getattr(baseline.metadata,field,None),getattr(candidate.metadata,field,None))
        if baseline.test_id==candidate.test_id:bad.append('Baseline and candidate must be different tests')
        if (baseline.started_at.tzinfo is None or candidate.started_at.tzinfo is None):missing.append('Aware benchmark timestamps')
        elif baseline.started_at>=candidate.started_at:bad.append('Baseline must precede candidate')
        for record in (baseline,candidate):
            if record.status!='completed' or not record.metrics.request_count or record.errors or record.metrics.errors:
                bad.append('A benchmark did not complete with valid nonzero measurements')
            if record.runner_oom_killed or record.application.oom_killed or record.runner_cpu_throttled:
                bad.append('A benchmark was resource constrained or OOM killed')
        result=Comparison(baseline_test_id=baseline.test_id,candidate_test_id=candidate.test_id,
            compatibility='INCOMPATIBLE' if bad else 'PARTIALLY_COMPATIBLE' if missing else 'COMPATIBLE',
            missing_compatibility_information=sorted(set(missing)),incompatibilities=sorted(set(bad)),thresholds=self.config,
            baseline_sample_test_ids=[baseline.test_id],candidate_sample_test_ids=[candidate.test_id],
            limitations=['Single-run comparisons are heuristic; no statistical significance or confidence interval is claimed',
                         'Absolute performance budgets are separate from baseline regression classification'])
        for field,unit in (('p50_ms','ms'),('p95_ms','ms'),('p99_ms','ms'),('throughput_rps','requests/second'),('http_failure_rate','fraction')):
            result.metric_differences.append(difference(field,getattr(baseline.metrics,field),getattr(candidate.metrics,field),unit))
        def in_window(record):
            if record.started_at.tzinfo is None or record.ended_at is None or record.ended_at.tzinfo is None:return []
            return [s for s in record.application.samples if s.timestamp.tzinfo is not None and record.started_at<=s.timestamp<=record.ended_at]
        def memory(record):
            values=[s.memory_bytes for s in in_window(record) if finite(s.memory_bytes)]
            return max(values) if values else None
        result.metric_differences.append(difference('observed_peak_memory_bytes',memory(baseline),
                                                   memory(candidate),'bytes (sampled maximum)'))
        def cpu(record):
            values=[s.cpu_percent for s in in_window(record) if finite(s.cpu_percent)]
            return mean(values) if values else None
        result.metric_differences.append(difference('mean_cpu_percent',cpu(baseline),cpu(candidate),'percent of one CPU'))
        for record in (baseline,candidate):
            for field in ('application_version','commit_identifier'):
                if getattr(record.metadata,field,None) is None:
                    result.limitations.append(f'{field} was not recorded for {record.test_id}')
        return result
