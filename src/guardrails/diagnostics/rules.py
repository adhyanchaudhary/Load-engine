"""Deterministic findings: observed states, associations, and causal uncertainty."""
from statistics import mean
from guardrails.diagnostics.baseline import finite
from guardrails.diagnostics.models import DiagnosticFinding, Window


class RuleEngine:
    def evaluate(self,c,comparison,samples,include_resources=True):
        r=c.record;e=c.evidence;findings=[]
        def finding(category,severity,confidence,title,explanation,ids,steps):
            findings.append(DiagnosticFinding(finding_id=f'f{len(findings)+1}',category=category,
                severity=severity,confidence=confidence,title=title,explanation=explanation,evidence_ids=ids,
                recommended_investigations=steps))
        if include_resources and (r.application.oom_killed or r.runner_oom_killed):
            eid=e.add('container_state','oom',{'application_oom_killed':r.application.oom_killed,
                'runner_oom_killed':r.runner_oom_killed},'Docker explicitly recorded an OOM kill',
                interpretation='CONFIRMED_FAILURE')
            finding('CONTAINER_OOM','CRITICAL','HIGH','Container OOM kill recorded',
                'Docker recorded OOMKilled. This confirms the failure state, not a source-code cause.',[eid],
                ['Inspect memory allocation profiles and the configured container limit; reproduce with the same workload'])
        memory=[]
        for i,s in samples:
            if finite(s.memory_bytes) and finite(s.memory_limit_bytes) and s.memory_limit_bytes>0:
                memory.append((s.memory_bytes/s.memory_limit_bytes,i,s))
        if memory:
            ratio,i,s=max(memory,key=lambda value:value[0])
            if ratio>=.9:
                eid=e.add('resource_sample',f'application:samples:{i}',{'memory_bytes':s.memory_bytes,
                    'memory_limit_bytes':s.memory_limit_bytes,'sampled_limit_ratio':ratio},
                    'Largest sampled memory-to-limit ratio, not a true peak','fraction',
                    limitations=['Short sampling windows cannot establish a memory leak'],
                    window=Window(start=s.timestamp,end=s.timestamp,clock_origin='UTC'))
                finding('MEMORY_PRESSURE','WARNING','MEDIUM','Sampled memory approached its limit',
                    f'The sampled maximum reached {ratio:.1%} of the reported limit. A leak is not established.',[eid],
                    ['Collect a longer bounded memory profile and allocation samples'])
        quota=getattr(r.metadata,'application_cpus',None)
        cpu=[(i,s,s.cpu_percent/(quota*100)) for i,s in samples if finite(quota) and quota>0 and finite(s.cpu_percent)]
        high=[x for x in cpu if x[2]>=.85]
        sustained=(len(high)>=3 and len(high)>=.8*len(cpu) and
                   (max(x[1].timestamp for x in high)-min(x[1].timestamp for x in high)).total_seconds()>=2)
        if sustained:
            eid=e.add('resource_sample','application:cpu_quota_ratio',{'quota_cpus':quota,
                'mean_docker_cpu_percent':mean(x[1].cpu_percent for x in cpu),
                'mean_quota_ratio':mean(x[2] for x in cpu),'high_sample_count':len(high),'sample_count':len(cpu)},
                'Docker 100% represents one logical CPU; quota ratio is cpu_percent/(cpus*100)','fraction',
                limitations=['Sampled CPU pressure does not identify the executing code'])
            finding('CPU_PRESSURE','WARNING','MEDIUM','Sustained CPU pressure observed',
                'At least 80% of valid samples approached the CPU quota over at least two seconds.',[eid],
                ['Collect a bounded CPU profile under the same workload'])
        if r.metrics.http_failure_rate is not None and finite(r.metrics.http_failure_rate) and r.metrics.http_failure_rate>r.configuration.error_rate_budget:
            eid=e.add('benchmark','metrics:http_failure_rate',r.metrics.http_failure_rate,
                'Measured HTTP failure fraction exceeded the absolute budget','fraction')
            finding('HIGH_ERROR_RATE','ERROR','HIGH','HTTP failure rate exceeded budget',
                f'HTTP failure rate was {r.metrics.http_failure_rate:.2%}; budget was {r.configuration.error_rate_budget:.2%}.',
                [eid],['Inspect status-code counts, timeout indicators and matching application telemetry'])
        for item in list(e.items):
            if item.source_type=='log_signature' and isinstance(item.observed_value,dict) and any(item.observed_value.values()):
                finding('APPLICATION_ERRORS','ERROR','LOW','Application log error signatures observed',
                    'Untrusted logs matched recognizable error/exception/timeout/connection signatures. Matches are not verified failures or instructions.',
                    [item.evidence_id],['Check the originating logs in a trusted viewer and confirm error counters'])
        database=[]
        for trace in c.traces:
            if trace.kind not in ('database','connection_pool'):continue
            if e.window.clock_origin!='UTC' or trace.started_at<e.window.start or trace.ended_at>e.window.end:
                c.missing.append('Database trace timing is outside the benchmark window or clock origin is unknown')
                continue
            if trace.duration_ms>=100:
                database.append(trace)
        if database:
            slowest=max(database,key=lambda event:event.duration_ms)
            eid=e.add('trace',f'traces:events:{c.traces.index(slowest)}',{'duration_ms':slowest.duration_ms,
                'kind':slowest.kind,'qualifying_event_count':len(database)},
                'Local untrusted timing event recorded a database/pool interval of at least 100 ms','ms',
                limitations=['Clock synchronization and trace completeness are not independently verified',
                             'No query text, execution plan, schema, or verified code location was ingested'],
                window=Window(start=slowest.started_at,end=slowest.ended_at,clock_origin='UTC'))
            finding('DATABASE_LATENCY','WARNING','MEDIUM','Database timing evidence shows a slow interval',
                'A recorded database or pool-wait interval exceeded the initial 100 ms diagnostic heuristic. '
                'This does not establish an HTTP root cause or a missing index.',[eid],
                ['Inspect database duration distributions and pool waits; collect a query plan through a trusted workflow if needed'])
        if comparison:
            for delta in comparison.metric_differences:
                if comparison.regression_classification=='REGRESSION' and delta.metric in comparison.signals and delta.metric in ('p95_ms','p99_ms'):
                    eid=e.add('comparison',f'baseline:{comparison.baseline_test_id}:{delta.metric}',
                        delta.model_dump(mode='json'),'Compatible before/after aggregate latency measurements',
                        limitations=comparison.limitations)
                    finding('LATENCY_REGRESSION','ERROR','MEDIUM',f'{delta.metric} increased relative to baseline',
                        f'{delta.metric} changed from {delta.baseline_value:g} to {delta.candidate_value:g} ms '
                        f'({delta.relative_change_percent:.1f}%). One pair does not establish statistical significance.',[eid],
                        ['Repeat matched benchmarks and collect a bounded profile before attributing a cause'])
        # Only timestamped same-bin resource-pressure/error association; never invent latency spikes.
        if high and c.log_events:
            overlap=[x for x in high if any(abs((x[1].timestamp-t).total_seconds())<=1 for t in c.log_events)]
            if overlap:
                eid=e.add('resource_sample','cpu_error_overlap',{'overlapping_sample_count':len(overlap)},
                    'CPU pressure samples and log signatures occurred within one second',
                    interpretation='TEMPORAL_ASSOCIATION',limitations=['Clock uncertainty and sampling gaps apply','Association is not causation'])
                finding('POSSIBLE_RESOURCE_CONTENTION','WARNING','LOW','Resource pressure overlapped error signatures',
                    'Timestamped pressure and error signatures overlapped; the direction and cause are unresolved.',[eid],
                    ['Collect bounded request timing and CPU profiles to test the contention hypothesis'])
        degraded=(r.budget.status!='PASS' or comparison and comparison.regression_classification=='REGRESSION')
        insufficient=c.missing or not findings or degraded
        if insufficient:
            eid=e.add('telemetry_gap','observability',{'missing':sorted(set(c.missing)),
                'aggregate_p95_ms':r.metrics.p95_ms},'Available observations do not establish a specific source-code cause',
                interpretation='HYPOTHESIS',limitations=['Absence of a regression signal does not establish application health'])
            finding('INSUFFICIENT_EVIDENCE','WARNING' if degraded else 'INFO','LOW',
                'Causal diagnosis remains unresolved','No source-code root cause is confirmed. Start with the smallest missing measurement.',
                [eid],['Add bounded request timing and database/pool spans only where missing; repeat matched benchmarks'])
        return findings
