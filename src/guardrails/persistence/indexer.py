"""Validated idempotent projection of application-controlled artifacts into SQLite."""
import hashlib
import json
from uuid import UUID
from guardrails.persistence.database import database_for,utc
from guardrails.diagnostics.io import read_json,read_bytes,ArtifactError
from guardrails.diagnostics.ownership import current_owner
from guardrails.load_testing.models import TestRecord
from guardrails.diagnostics.models import DiagnosticReport
from guardrails.repair.models import RepairSession,RepairCandidate,VerificationReport


def encode(value):return json.dumps(value,separators=(',',':'),allow_nan=False)


def upsert(con,table,data):
    # table/columns come only from hard-coded application mappings, never user inputs.
    columns=list(data)
    sql=f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT({columns[0]}) DO UPDATE SET "+','.join(f'{c}=excluded.{c}' for c in columns[1:])
    con.execute(sql,list(data.values()))


def workload_key(record):
    m=record.metadata
    fields=('workload_model','application_identity','application_configuration_fingerprint','dataset_id',
        'architecture','environment_fingerprint','application_cpus','application_memory_bytes','runner_cpus',
        'runner_memory_bytes','runner_pids_limit','runner_image_id','test_recipe_digest')
    values={k:getattr(m,k,None) for k in fields}
    # Missing compatibility metadata must not pool unrelated historical runs.
    required=[v for k,v in values.items() if k!='test_recipe_digest']
    if any(v is None for v in required):return 'incomplete:'+record.test_id
    values.update({k:getattr(record.configuration,k) for k in ('endpoint_path','method','vus','duration_seconds')})
    return hashlib.sha256(encode(values).encode()).hexdigest()


class ArtifactIndexer:
    def __init__(self,state_dir):self.root=state_dir;self.db=database_for(state_dir)

    def registry(self,data):
        owner=str(UUID(data.owner));present=set(data.entries)
        with self.db.connect(write=True) as con:
            rows=con.execute('SELECT sandbox_id FROM sandboxes WHERE owner_id=? AND managed=1',(owner,)).fetchall()
            for row in rows:
                if row['sandbox_id'] not in present:
                    con.execute("UPDATE sandboxes SET managed=0,status='DESTROYED' WHERE sandbox_id=? AND owner_id=?",(row['sandbox_id'],owner))
            for sid,entry in data.entries.items():
                existing=con.execute('SELECT status,health,last_observed_at,last_error FROM sandboxes WHERE sandbox_id=?',(sid,)).fetchone()
                upsert(con,'sandboxes',dict(sandbox_id=sid,owner_id=owner,container_id=entry.container_id,
                    image_identifier=entry.config.image,cpu_limit=entry.config.cpus,memory_limit_mb=entry.config.memory_mb,
                    status=existing['status'] if existing else 'UNKNOWN',health=existing['health'] if existing else None,
                    created_at=utc(entry.created_at),expires_at=utc(entry.expires_at),managed=1,
                    last_observed_at=existing['last_observed_at'] if existing else None,
                    last_error=existing['last_error'] if existing else None,metadata_json=encode({'image_id':entry.approved.image_id,
                    'test_enabled':entry.approved.application_port is not None,'repair_session_id':entry.repair_session_id})))

    def sandbox_observation(self,info,owner,error=None):
        with self.db.connect(write=True) as con:
            con.execute('UPDATE sandboxes SET status=?,health=?,last_observed_at=?,last_error=? WHERE sandbox_id=? AND owner_id=?',
                (info.status,info.health,utc(),error,info.sandbox_id,owner))

    def benchmark(self,con,r,owner):
        provenance='legacy_unverified'
        receipt_path=self.root/'performance'/f'{r.test_id}.ownership.json'
        if receipt_path.exists():
            receipt=read_json(receipt_path,8192)
            if receipt.get('owner_id')!=owner or receipt.get('sandbox_id')!=r.sandbox_id or receipt.get('test_id')!=r.test_id:
                raise ArtifactError('Benchmark receipt ownership mismatch')
            provenance=receipt.get('provenance','legacy_unverified')
        if r.owner_id is not None and r.owner_id!=owner:raise ArtifactError('Foreign benchmark owner')
        if r.configuration.sandbox_id!=r.sandbox_id:raise ArtifactError('Benchmark identity mismatch')
        UUID(r.test_id);UUID(r.sandbox_id)
        if len(r.application.samples)>150:raise ArtifactError('Resource sample count exceeds bound')
        con.execute("INSERT OR IGNORE INTO sandboxes(sandbox_id,owner_id,status,managed,metadata_json) VALUES(?,?,'HISTORICAL_UNKNOWN',0,'{}')",(r.sandbox_id,owner))
        m=r.metrics;c=r.configuration;meta=r.metadata
        import math
        for value in (m.p50_ms,m.p95_ms,m.p99_ms,m.throughput_rps,m.http_failure_rate,m.request_count):
            if value is not None and (not math.isfinite(value) or value<0):raise ArtifactError('Invalid normalized measurement')
        if m.http_failure_rate is not None and m.http_failure_rate>1:raise ArtifactError('Invalid failure fraction')
        raw=self.root/'performance'/f'{r.test_id}.summary.json'
        upsert(con,'benchmarks',dict(test_id=r.test_id,owner_id=owner,sandbox_id=r.sandbox_id,
            application_revision=getattr(meta,'commit_identifier',None),started_at=utc(r.started_at),completed_at=utc(r.ended_at) if r.ended_at else None,
            status=r.status,endpoint=c.endpoint_path,method=c.method,vus=c.vus,duration_seconds=c.duration_seconds,
            p50_ms=m.p50_ms,p95_ms=m.p95_ms,p99_ms=m.p99_ms,throughput_rps=m.throughput_rps,error_rate=m.http_failure_rate,
            request_count=m.request_count,budget_status=r.budget.status,configuration_json=c.model_dump_json(),
            raw_summary_path=str(raw.relative_to(self.root)) if raw.is_file() and not raw.is_symlink() else None,
            workload_key=workload_key(r),provenance=provenance,record_json=r.model_dump_json()))
        con.execute('DELETE FROM resource_samples WHERE test_id=?',(r.test_id,))
        con.executemany('INSERT INTO resource_samples(test_id,sample_index,timestamp,cpu_percent,memory_bytes,memory_limit_bytes) VALUES(?,?,?,?,?,?)',
            [(r.test_id,i,utc(s.timestamp),s.cpu_percent,s.memory_bytes,s.memory_limit_bytes) for i,s in enumerate(r.application.samples)])

    def ensure_benchmark(self,con,test_id,owner):
        if con.execute('SELECT 1 FROM benchmarks WHERE test_id=? AND owner_id=?',(test_id,owner)).fetchone():return
        record=TestRecord.model_validate(read_json(self.root/'performance'/f'{str(UUID(test_id))}.json'))
        if record.test_id!=test_id:raise ArtifactError('Benchmark path/ID mismatch')
        self.benchmark(con,record,owner)

    def artifact(self,path,*,force=False):
        relative=path.relative_to(self.root).as_posix();owner=current_owner(self.root)
        raw=read_bytes(path);content=read_json(path)
        digest_input=raw
        if path.parent.name=='performance' and not path.name.endswith(('.ownership.json','.summary.json','.traces.json')):
            receipt=path.with_name(path.stem+'.ownership.json')
            digest_input+=read_bytes(receipt,8192) if receipt.exists() else b'no-receipt'
            summary=path.with_name(path.stem+'.summary.json')
            digest_input+=b'raw-present' if summary.is_file() and not summary.is_symlink() else b'raw-absent'
        digest=hashlib.sha256(digest_input).hexdigest()
        group=path.parent.name
        if group=='performance':
            if path.name.endswith(('.ownership.json','.summary.json','.traces.json')):
                tid=path.name.split('.')[0];path=self.root/'performance'/f'{str(UUID(tid))}.json'
                return self.artifact(path,force=True) if path.exists() else 'SKIPPED'
            record=TestRecord.model_validate(content)
            if path.name!=record.test_id+'.json':raise ArtifactError('Benchmark path/ID mismatch')
        elif group=='diagnostics':
            record=DiagnosticReport.model_validate(content)
            if path.name!=record.report_id+'.json' or record.owner_id!=owner:raise ArtifactError('Report ownership mismatch')
        elif group=='repair':
            model=RepairSession if path.name.endswith('.session.json') else RepairCandidate if path.name.endswith('.candidate.json') else VerificationReport if path.name.endswith('.verification.json') else None
            if model is None:return 'SKIPPED'
            record=model.model_validate(content)
            if record.owner_id!=owner:raise ArtifactError('Repair ownership mismatch')
            identifier=record.session_id if isinstance(record,RepairSession) else record.candidate_id if isinstance(record,RepairCandidate) else record.verification_id
            if path.name.split('.')[0]!=identifier:raise ArtifactError('Repair path/ID mismatch')
        else:return 'SKIPPED'
        with self.db.connect(write=True) as con:
            prior=con.execute('SELECT digest,status FROM artifact_imports WHERE source=?',(relative,)).fetchone()
            if prior and prior['digest']==digest and prior['status']=='IMPORTED' and not force:return 'UNCHANGED'
            if group=='performance':self.benchmark(con,record,owner)
            elif group=='diagnostics':
                self.ensure_benchmark(con,record.test_id,owner)
                if record.baseline_test_id:self.ensure_benchmark(con,record.baseline_test_id,owner)
                upsert(con,'diagnostic_reports',dict(report_id=record.report_id,owner_id=owner,test_id=record.test_id,
                    baseline_test_id=record.baseline_test_id,generated_at=utc(record.generated_at),status=record.overall_status,
                    regression_status=record.regression_summary.regression_classification if record.regression_summary else None,report_json=record.model_dump_json()))
            elif isinstance(record,RepairSession):
                did=record.diagnostic_report_id if record.diagnostic_report_id!=str(UUID(int=0)) else None
                bid=record.baseline_test_id if record.baseline_test_id!=str(UUID(int=0)) else None
                if bid:self.ensure_benchmark(con,bid,owner)
                upsert(con,'repair_sessions',dict(session_id=record.session_id,owner_id=owner,project_id=record.approved_project_id,
                    diagnostic_report_id=did,baseline_test_id=bid,status=record.status,created_at=utc(record.created_at),updated_at=utc(record.updated_at),
                    accepted_candidate_id=record.accepted_candidate_id,record_json=record.model_dump_json()))
            elif isinstance(record,RepairCandidate):
                upsert(con,'repair_candidates',dict(candidate_id=record.candidate_id,owner_id=owner,session_id=record.session_id,
                    commit_sha=record.source_revision,source_digest=record.source_digest,verification_status=record.verification_status,
                    created_at=utc(record.created_at),metadata_json=record.model_dump_json()))
            else:
                upsert(con,'repair_verifications',dict(verification_id=record.verification_id,owner_id=owner,session_id=record.session_id,
                    candidate_id=record.candidate_id,verdict=record.verdict,started_at=utc(record.started_at),ended_at=utc(record.ended_at) if record.ended_at else None,
                    report_json=record.model_dump_json()))
                first=record.benchmark_test_ids[0] if record.benchmark_test_ids else None
                if first:self.ensure_benchmark(con,first,owner)
                con.execute('UPDATE repair_candidates SET correctness_status=?,benchmark_test_id=?,improvement_percent=? WHERE candidate_id=? AND owner_id=?',
                    (record.correctness.status if record.correctness else None,first,record.statistics.get('improvement_percent'),record.candidate_id,owner))
            upsert(con,'artifact_imports',dict(source=relative,digest=digest,status='IMPORTED',detail=None,updated_at=utc()))
        return 'IMPORTED'

    def failure(self,path):
        if path.parent.name=='performance' and path.name.endswith(('.ownership.json','.summary.json','.traces.json')):
            path=path.with_name(path.name.split('.')[0]+'.json')
        relative=path.relative_to(self.root).as_posix()
        with self.db.connect(write=True) as con:
            upsert(con,'artifact_imports',dict(source=relative,digest=None,status='ERROR',detail='Malformed, incompatible, unowned, or unresolved artifact; original retained',updated_at=utc()))


def index_write(path):
    """JSON is durable first. Index failure is visible/retryable, never JSON data loss."""
    if path.parent.name not in ('performance','diagnostics','repair') or not path.name.endswith('.json'):return
    root=path.parent.parent
    index=None
    try:
        index=ArtifactIndexer(root);index.artifact(path,force=True)
    except Exception:
        try:
            if index:index.failure(path)
        except Exception:
            import logging
            logging.warning('SQLite index unavailable; original JSON retained. Run persistence import to reconcile.')
