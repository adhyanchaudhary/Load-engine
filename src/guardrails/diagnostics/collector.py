"""Read historical artifacts only. Never replace past telemetry with live state."""
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID
from guardrails.load_testing.models import TestRecord
from guardrails.load_testing.parser import parse_summary
from guardrails.load_testing.storage import ResultStore
from guardrails.diagnostics.models import DiagnosticInput
from guardrails.diagnostics.evidence import EvidenceBuilder
from guardrails.diagnostics.io import ArtifactError, read_json, read_bytes
from guardrails.diagnostics.ownership import owned_receipt
from guardrails.diagnostics.security import signatures
from guardrails.diagnostics.tracing import TraceAdapter


@dataclass
class Collected:
    record: TestRecord
    evidence: EvidenceBuilder
    missing: list[str]
    log_events: list
    traces: list
    raw_summary_valid: bool


class DiagnosticCollector:
    def __init__(self,state_dir):
        self.store=ResultStore(state_dir)

    def load_record(self,test_id):
        test_id=str(UUID(test_id))
        receipt=owned_receipt(self.store,test_id)
        try:
            record=TestRecord.model_validate(read_json(self.store._path(test_id,'.json')))
            if (record.test_id!=test_id or record.sandbox_id!=receipt['sandbox_id'] or
                record.configuration.sandbox_id!=record.sandbox_id or
                (record.owner_id!=receipt['owner_id'] and receipt['provenance']!='operator_attested_legacy')):
                raise ValueError()
            UUID(record.sandbox_id)
            if len(record.application.samples)>150:raise ValueError()
            if record.started_at.tzinfo is None or (record.ended_at and record.ended_at.tzinfo is None):
                # Keep data readable, but correlation will refuse ambiguous windows.
                pass
            if (record.ended_at and record.started_at.tzinfo is not None and record.ended_at.tzinfo is not None
                and record.ended_at < record.started_at):raise ValueError()
            # Reject impossible normalized numbers, rather than turning them into findings.
            import math
            for field in ('request_count','throughput_rps','p50_ms','p95_ms','p99_ms','http_failure_rate'):
                value=getattr(record.metrics,field)
                if value is not None and (not math.isfinite(value) or value<0):raise ValueError()
            if record.metrics.http_failure_rate is not None and record.metrics.http_failure_rate>1:raise ValueError()
            if record.metadata:
                for field in ('application_cpus','application_memory_bytes','runner_cpus','runner_memory_bytes','runner_pids_limit'):
                    value=getattr(record.metadata,field)
                    if value is not None and (not math.isfinite(value) or value<=0):raise ValueError()
            for s in record.application.samples:
                for value in (s.cpu_percent,s.memory_bytes,s.memory_limit_bytes):
                    if value is not None and (not math.isfinite(value) or value<0):raise ValueError()
            if record.status=='running':record.status='interrupted'
            return record
        except (ValueError,TypeError):
            raise ArtifactError('Benchmark metadata is malformed or does not match ownership') from None

    def collect(self,config: DiagnosticInput):
        r=self.load_record(config.test_id)
        e=EvidenceBuilder(r.test_id,r.started_at,r.ended_at)
        missing=[]
        valid=False
        try:
            raw=parse_summary(read_bytes(self.store._path(r.test_id,'.summary.json')))
            valid=not raw.errors
            for key in ('request_count','throughput_rps','p50_ms','p95_ms','p99_ms','http_failure_rate'):
                actual=getattr(r.metrics,key)
                if getattr(raw,key)!=actual:valid=False
            if not valid:missing.append('Raw summary is incomplete or inconsistent with normalized measurements')
        except (ArtifactError,ValueError):missing.append('Original k6 raw summary unavailable or invalid')
        for name,unit in (('request_count','requests'),('throughput_rps','requests/second'),('p50_ms','ms'),
                          ('p95_ms','ms'),('p99_ms','ms'),('http_failure_rate','fraction')):
            value=getattr(r.metrics,name)
            if value is None:missing.append(f'Missing benchmark metric: {name}')
            else:e.add('benchmark',f'metrics:{name}',value,f'Persisted k6 {name}',unit)
        e.add('benchmark','execution',{'status':r.status,'budget_status':r.budget.status,'runner_exit_code':r.runner_exit_code},
              'Execution and absolute budget outcome; independent of baseline regressions')
        if r.metadata is None:missing.append('Historical environment and resource-limit metadata unavailable')
        elif r.metadata.application_cpus is None:missing.append('Configured application CPU quota unavailable')
        if config.include_resource_metrics:
            if not r.application.samples:missing.append('Timestamped application resource samples unavailable')
            if r.application.oom_killed is not None or r.runner_oom_killed is not None:
                e.add('container_state','container_state',{'application_oom':r.application.oom_killed,
                    'application_exit_code':r.application.exit_code,'runner_oom':r.runner_oom_killed,
                    'runner_cpu_throttled':r.runner_cpu_throttled},'Persisted container state')
            else:missing.append('Historical container OOM/exit state unavailable')
        else:
            r.application.samples=[]
            missing.append('Resource metrics excluded by diagnostic options')
        events=[]
        if config.include_logs:
            logs=r.historical_logs
            if logs is None or logs.error:missing.append('Historical application logs unavailable; current logs were not queried')
            else:
                counts=signatures(logs.text)
                e.add('log_signature','historical_logs:signatures',counts,'Recognizable signatures in untrusted historical logs',
                    'signature matches',limitations=['Signatures are not verified exceptions or causal proof','Raw text is excluded from reports'])
                if logs.truncated:missing.append('Historical log window was truncated')
                # Only parse event times; no raw log string is retained in findings/context.
                from datetime import datetime
                for line in logs.text.splitlines()[:200]:
                    token=line.split(' ',1)[0]
                    try:
                        timestamp=datetime.fromisoformat(token.replace('Z','+00:00'))
                        if timestamp.tzinfo is not None and any(signatures(line).values()):events.append(timestamp)
                    except ValueError:pass
                if any(counts.values()) and not events:missing.append('Error-log timestamps unavailable or not parseable')
        else:missing.append('Logs excluded by diagnostic options')
        traces=[]
        if config.include_traces:
            try:
                bundle=TraceAdapter(self.store).load(r.test_id)
                if bundle:traces=bundle.events
            except ValueError:missing.append('Local trace bundle is malformed or not owned')
            if not traces:missing.append('Database/connection timing traces unavailable')
        else:missing.append('Traces excluded by diagnostic options')
        if r.owner_id is None:missing.append('Legacy test ownership is operator attested, not recorded during execution')
        return Collected(r,e,missing,events,traces,valid)
