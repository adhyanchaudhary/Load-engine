"""Shared application services for MCP, Streamlit and bounded local workers."""
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4,UUID
import json
import os
from guardrails.docker.manager import SandboxManager
from guardrails.docker.models import SandboxConfig
from guardrails.load_testing.runner import LoadEngine
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.diagnostics.models import DiagnosticInput
from guardrails.diagnostics.service import DiagnosticService
from guardrails.repair.manager import RepairManager
from guardrails.persistence.database import database_for,utc
from guardrails.persistence.repositories.history import HistoryRepository


class ApplicationServices:
    def __init__(self,sandbox_manager):
        self.sandbox_manager=sandbox_manager;self.state_dir=sandbox_manager.registry.directory
        with sandbox_manager.registry.transaction() as data:
            if not sandbox_manager.registry.path.exists():sandbox_manager.registry.save(data)
        self.persistence=database_for(self.state_dir)
        self.history=HistoryRepository(self.persistence)
        self.diagnostic_service=DiagnosticService(self.state_dir)
        self._jobs=None;self._lock=Lock()

    @property
    def owner_id(self):return self.history.owner
    def benchmark_service(self,*,cancelled=None,engine_factory=LoadEngine):return make_benchmark_service(self.sandbox_manager,cancelled=cancelled,engine_factory=engine_factory)
    def repair_manager(self,*,cancelled=None):
        value=os.environ.get('GUARDRAILS_REPAIR_POLICY_FILE')
        return RepairManager(self.sandbox_manager,policy_path=Path(value) if value else None,cancelled=cancelled)
    @property
    def jobs(self):
        with self._lock:
            if self._jobs is None:
                from guardrails.jobs import JobManager
                self._jobs=JobManager(self)
        return self._jobs

    def audit(self,action,target,outcome,actor='mcp',detail=None):
        with self.persistence.connect(write=True) as con:
            con.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)',(str(uuid4()),self.owner_id,utc(),actor,action,target,outcome,detail))

    def owned_sandbox(self,sandbox_id):
        sandbox_id=str(UUID(sandbox_id))
        with self.sandbox_manager.registry.transaction() as data:
            entry=data.entries.get(sandbox_id)
            if entry is None:raise ValueError('Sandbox is not in the private managed registry')
            self.sandbox_manager._require_unexpired(entry)
            return entry

    def validate_action(self,kind,payload):
        if kind=='import_history':
            if payload:raise ValueError('Import accepts no filesystem paths or parameters')
            return {}
        if kind=='benchmark':
            config=PerformanceTestConfig.model_validate(payload);self.owned_sandbox(config.sandbox_id)
            return config.model_dump()
        if kind=='sandbox_create':
            config=SandboxConfig.model_validate(payload)
            if config.image not in self.sandbox_manager._load_policy().images:raise ValueError('Choose an approved local image alias')
            return config.model_dump()
        if kind in ('sandbox_start','sandbox_inspect','sandbox_destroy','sandbox_logs'):
            if set(payload)!={'sandbox_id'}:raise ValueError('Only a managed sandbox UUID is accepted')
            sid=str(UUID(payload['sandbox_id']))
            if kind!='sandbox_destroy':self.owned_sandbox(sid)
            return {'sandbox_id':sid}
        if kind=='analyze':return DiagnosticInput.model_validate(payload).model_dump()
        if kind=='repair_verify':
            if set(payload)!={'session_id','candidate_id'}:raise ValueError('Repair IDs required')
            return {k:str(UUID(v)) for k,v in payload.items()}
        raise ValueError('Unsupported local job action')

    def execute(self,kind,payload,*,cancelled=None,actor='mcp'):
        payload=self.validate_action(kind,payload);target=payload.get('sandbox_id') or payload.get('test_id') or payload.get('session_id')
        self.audit(kind,target,'REQUESTED',actor)
        try:
            if kind=='import_history':
                from guardrails.persistence.migrations import import_artifacts
                result=import_artifacts(self.state_dir)
            elif kind=='benchmark':
                engine=self.benchmark_service(cancelled=cancelled)
                ready=engine.prepare(payload['sandbox_id'])
                if ready.readiness_status!='ready':raise ValueError('Application is not ready; inspect managed sandbox logs')
                if cancelled and cancelled.is_set():raise ValueError('Job cancelled before load generation')
                result=engine.run(PerformanceTestConfig.model_validate(payload))
            elif kind=='analyze':result=self.diagnostic_service.analyze(DiagnosticInput.model_validate(payload))
            elif kind=='repair_verify':result=self.repair_manager(cancelled=cancelled).verify(**payload)
            elif kind=='sandbox_create':result=self.sandbox_manager.create(**payload)
            else:
                action={'sandbox_start':'start','sandbox_inspect':'inspect','sandbox_destroy':'destroy','sandbox_logs':'logs'}[kind]
                result=getattr(self.sandbox_manager,action)(**payload)
            self.audit(kind,target,'COMPLETED' if getattr(result,'ok',True) else 'FAILED',actor)
            return result
        except BaseException:
            self.audit(kind,target,'FAILED',actor)
            raise

    def compare(self,baseline,candidate):return self.diagnostic_service.compare(baseline,candidate)
    def create_repair(self,report_id,project_id,attempts,request_key):
        # A durable UI action token makes refreshes idempotent, without bypassing RepairManager.
        from filelock import FileLock
        import hashlib
        key=hashlib.sha256(request_key.encode()).hexdigest()
        with FileLock(str(self.state_dir/('repair-action-'+key+'.lock')),timeout=5):
            rows=self.persistence.rows("SELECT detail FROM audit_events WHERE owner_id=? AND action='repair_create' AND target_id=? AND outcome='COMPLETED' ORDER BY timestamp DESC LIMIT 1",(self.owner_id,key))
            manager=self.repair_manager()
            if rows:return manager.status(rows[0]['detail']).session
            self.audit('repair_create',key,'REQUESTED','dashboard')
            result=manager.create(report_id,project_id,attempts)
            self.audit('repair_create',key,'COMPLETED','dashboard',result.session_id)
            return result

    def finalize_repair(self,session_id,candidate_id,decision,*,confirmed=False):
        if not confirmed:raise ValueError('Explicit confirmation is required')
        self.audit('finalize_repair',session_id,'REQUESTED','dashboard')
        result=self.repair_manager().finalize(session_id,candidate_id,decision)
        self.audit('finalize_repair',session_id,'COMPLETED','dashboard');return result
    def destroy(self,sandbox_id,*,confirmed=False,request_key=None):
        if not confirmed:raise ValueError('Confirm destruction of this managed sandbox')
        return self.jobs.submit('sandbox_destroy',{'sandbox_id':sandbox_id},request_key or str(uuid4()))


@lru_cache(maxsize=1)
def get_application_services():return ApplicationServices(SandboxManager.from_environment())


def services_for(manager):return ApplicationServices(manager)


def make_benchmark_service(manager,*,cancelled=None,engine_factory=LoadEngine):
    return engine_factory(manager,cancelled=cancelled)
