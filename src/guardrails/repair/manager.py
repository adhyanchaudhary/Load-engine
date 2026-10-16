"""Bounded repair orchestration. The connected coding agent proposes commits."""
from contextlib import contextmanager
import os
from pathlib import Path
from statistics import median
from uuid import UUID,uuid4
from filelock import FileLock,Timeout
from guardrails.diagnostics.io import ArtifactError,read_json
from guardrails.diagnostics.ownership import current_owner
from guardrails.diagnostics.service import DiagnosticService
from guardrails.diagnostics.models import DiagnosticInput
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.load_testing.runner import LoadEngine
from guardrails.repair.models import RepairSession,RepairCandidate,VerificationReport,RepairStatus
from guardrails.repair.storage import RepairStore
from guardrails.repair.policy import load_policy,fingerprint
from guardrails.repair.candidates import CandidateResolver,digest,git
from guardrails.repair.sessions import now,transition
from guardrails.repair.correctness import CorrectnessVerifier,Cancelled
from guardrails.repair.benchmark import RepairBenchmarker
from guardrails.repair.evaluator import RepairEvaluator
from guardrails.repair.lifecycle import cleanup


class RepairManager:
    def __init__(self,manager,*,policy=None,policy_path=None,cancelled=None):
        self.manager=manager;self.state_dir=manager.registry.directory
        self.store=RepairStore(self.state_dir);self.diagnostics=DiagnosticService(self.state_dir)
        self._policy=policy;self.policy_path=policy_path;self.cancelled=cancelled

    def policy(self):return self._policy if self._policy is not None else load_policy(self.policy_path)

    def project(self,project_id):
        project_id=str(UUID(project_id))
        project=self.policy().projects.get(project_id)
        if project is None:raise ArtifactError('Project ID is not in the trusted repair registry')
        # Revalidate injected policies too; production paths are read from trusted config only.
        for argv in (project.recipe.build_command,project.recipe.test_command,project.recipe.application_command):
            if argv[0]!=project.recipe.python_executable or '-c' in argv:raise ArtifactError('Unsupported trusted recipe')
        return project

    @contextmanager
    def admission(self,session_id):
        with self.store.lock(session_id):
            slot=None
            try:
                for i in range(self.policy().verification_concurrency):
                    trial=FileLock(str(self.store.directory/f'verification-slot-{i}.lock'),timeout=0)
                    try:trial.acquire();slot=trial;break
                    except Timeout:continue
                if slot is None:raise ArtifactError('Global repair verification limit reached')
                yield
            finally:
                if slot:slot.release()

    def create(self,diagnostic_report_id,approved_project_id,maximum_attempts=3):
        if type(maximum_attempts) is not int or not 1<=maximum_attempts<=5:raise ArtifactError('maximum_attempts must be 1–5')
        report=self.diagnostics.get(str(UUID(diagnostic_report_id)))
        baseline=self.diagnostics.collector.load_record(report.test_id)
        project=self.project(approved_project_id)
        revision=CandidateResolver(project).resolve(project.baseline_revision)
        if (baseline.metadata is None or baseline.metadata.commit_identifier!=revision or
            baseline.metadata.application_identity!=approved_project_id or
            baseline.metadata.test_recipe_digest!=fingerprint(project.recipe)):
            raise ArtifactError('Baseline revision, project or trusted recipe metadata missing/mismatched; benchmark it through the approved recipe first')
        if baseline.status!='completed' or not baseline.metrics.request_count:
            raise ArtifactError('Baseline benchmark is incomplete')
        snapshot=CandidateResolver(project).archive(revision)
        stamp=now();session=RepairSession(session_id=str(uuid4()),owner_id=current_owner(self.state_dir),
            diagnostic_report_id=report.report_id,baseline_test_id=baseline.test_id,approved_project_id=approved_project_id,
            baseline_revision=revision,baseline_digest=digest(snapshot),policy_digest=fingerprint(project),
            created_at=stamp,updated_at=stamp,maximum_attempts=maximum_attempts)
        with FileLock(str(self.store.directory/'sessions.lock'),timeout=5):
            active=0
            for path in self.store.directory.glob('*.session.json'):
                other=self.store.get(path.name.split('.')[0],'session')
                if other.status not in ('COMPLETED','FAILED','CANCELLED'):active+=1
            if active>=self.policy().maximum_active_sessions:raise ArtifactError('Active repair session limit reached')
            transition(session,'AWAITING_CANDIDATE')
            self.store.save(session)
        return session

    def register(self,session_id,candidate_revision):
        with self.store.lock(session_id):
            session=self.store.get(session_id,'session');project=self.project(session.approved_project_id)
            if session.policy_digest!=fingerprint(project):raise ArtifactError('Approved project policy changed; create a new session')
            if session.status not in ('AWAITING_CANDIDATE','REJECTED'):raise ArtifactError('Session is not accepting candidates')
            if session.attempts_used>=session.maximum_attempts:raise ArtifactError('Maximum repair attempts reached')
            if session.candidate_ids:
                latest=self.store.get(session.candidate_ids[-1],'candidate')
                if latest.verification_status=='REGISTERED':raise ArtifactError('Verify or reject the registered candidate first')
            resolver=CandidateResolver(project)
            if digest(resolver.archive(session.baseline_revision))!=session.baseline_digest:raise ArtifactError('Baseline source identity changed')
            revision,changed,snapshot=resolver.candidate(candidate_revision)
            for identifier in session.candidate_ids:
                if self.store.get(identifier,'candidate').source_revision==revision:raise ArtifactError('Candidate revision was already attempted')
            parent=git(resolver.root,'rev-parse','--verify',revision+'^1').decode().strip()
            candidate=RepairCandidate(candidate_id=str(uuid4()),owner_id=session.owner_id,session_id=session.session_id,
                source_revision=revision,source_digest=digest(snapshot),parent_revision=parent,created_at=now(),
                changed_files=changed,change_summary=f'Immutable revision changes {len(changed)} approved file(s)')
            self.store.snapshot(candidate.candidate_id,snapshot)
            session.attempts_used+=1;session.candidate_ids.append(candidate.candidate_id)
            if session.status=='REJECTED':transition(session,'AWAITING_CANDIDATE')
            self.store.save(candidate);self.store.save(session)
            return candidate

    def candidate(self,session,candidate_id):
        candidate=self.store.get(candidate_id,'candidate')
        if candidate.session_id!=session.session_id or candidate.candidate_id not in session.candidate_ids:
            raise ArtifactError('Candidate does not belong to this session')
        return candidate

    def baseline_quality(self,baseline,project,report=None):
        path=self.store.files._path(baseline.test_id,'.baseline-trials.json')
        if not path.exists():
            if report:report.limitations.append('Baseline repetition evidence unavailable; comparison uses one observed baseline')
            return
        data=read_json(path,8192)
        ids=data.get('test_ids') if isinstance(data,dict) else None
        if (not isinstance(ids,list) or not 1<=len(ids)<=3 or len(set(ids))!=len(ids) or baseline.test_id not in ids):
            raise ArtifactError('Baseline repetition evidence is malformed')
        values=[]
        for test_id in ids:
            collected=self.diagnostics.collector.collect(DiagnosticInput(test_id=test_id,include_logs=False,include_traces=False))
            trial=collected.record
            if (not collected.raw_summary_valid or trial.status!='completed' or trial.runner_cpu_throttled or
                trial.runner_oom_killed or trial.application.oom_killed or not trial.metrics.request_count or
                trial.metrics.p95_ms is None or trial.metadata is None or
                trial.metadata.commit_identifier!=project.baseline_revision or
                trial.metadata.test_recipe_digest!=fingerprint(project.recipe)):
                raise ArtifactError('Baseline repeated measurements are incomplete or resource constrained')
            for field in ('endpoint_path','method','vus','duration_seconds'):
                if getattr(trial.configuration,field)!=getattr(baseline.configuration,field):
                    raise ArtifactError('Baseline repeated workloads differ')
            for field in ('application_identity','application_configuration_fingerprint','application_cpus','application_memory_bytes',
                          'runner_cpus','runner_memory_bytes','runner_pids_limit','runner_image_id','architecture','environment_fingerprint','dataset_id'):
                if getattr(trial.metadata,field,None)!=getattr(baseline.metadata,field,None):
                    raise ArtifactError('Baseline repetition environments differ')
            values.append(trial.metrics.p95_ms)
        middle=median(values)
        if middle<=0 or (max(values)-min(values))/middle*100>project.evaluation.maximum_trial_spread_percent:
            raise ArtifactError('Baseline repeated measurements are unstable')
        if report:report.statistics['baseline_p95_spread_percent']=(max(values)-min(values))/middle*100

    def verify(self,session_id,candidate_id):
        with self.admission(session_id):
            session=self.store.get(session_id,'session');candidate=self.candidate(session,candidate_id)
            if session.status!='AWAITING_CANDIDATE' or candidate.verification_status!='REGISTERED':
                raise ArtifactError('Candidate is not eligible for verification; no automatic retries')
            project=self.project(session.approved_project_id)
            if session.policy_digest!=fingerprint(project):raise ArtifactError('Project policy changed; verification refused')
            baseline=self.diagnostics.collector.load_record(session.baseline_test_id)
            snapshot=self.store.read_snapshot(candidate.candidate_id)
            if digest(snapshot)!=candidate.source_digest:raise ArtifactError('Immutable source archive integrity mismatch')
            # Re-resolve provenance without checking out or executing repository content.
            resolver=CandidateResolver(project)
            if resolver.archive(candidate.source_revision)!=snapshot:raise ArtifactError('Candidate commit no longer matches registered source')
            report=VerificationReport(verification_id=str(uuid4()),owner_id=session.owner_id,session_id=session.session_id,
                candidate_id=candidate.candidate_id,source_revision=candidate.source_revision,source_digest=candidate.source_digest,
                baseline_test_id=session.baseline_test_id,recipe_digest=fingerprint(project.recipe),started_at=now())
            transition(session,'VERIFYING');candidate.verification_status='VERIFYING';candidate.verification_id=report.verification_id
            self.store.save(candidate);self.store.save(session);self.store.save(report)
            verifier=CorrectnessVerifier(self.manager,self.store,self.cancelled)
            trials=[]
            try:
                self.baseline_quality(baseline,project,report)
                if not self.diagnostics.collector.collect(DiagnosticInput(test_id=session.baseline_test_id,include_logs=False,include_traces=False)).raw_summary_valid:
                    raise ArtifactError('Baseline raw measurements are incomplete')
                report.build=verifier.run(session,project,snapshot,'build');self.store.save(report)
                if report.build.status=='PASS':
                    report.correctness=verifier.run(session,project,snapshot,'correctness');self.store.save(report)
                if report.correctness and report.correctness.status=='PASS':
                    trials=RepairBenchmarker(verifier).run(session,project,candidate,snapshot,baseline,report)
                RepairEvaluator().evaluate(report,baseline,trials,project)
            except Cancelled:
                report.verdict='CANCELLED';report.reasons.append('Verification cancelled; no automatic retry')
            except BaseException as exc:
                report.verdict='INCONCLUSIVE';report.reasons.append('Verification infrastructure failed or benchmark evidence unavailable')
                if not isinstance(exc,Exception):raise
            finally:
                try:report.remaining_managed_resources=cleanup(self.manager,session.session_id)
                except Exception:report.remaining_managed_resources=list(session.managed_sandbox_ids)
                if report.remaining_managed_resources:
                    report.verdict='INCONCLUSIVE';report.reasons.append('Owned cleanup incomplete; run repair recovery before acceptance')
                session.managed_sandbox_ids=report.remaining_managed_resources
                candidate.verification_status=report.verdict
                candidate.benchmark_test_ids=list(report.benchmark_test_ids)
                candidate.rejection_reasons=list(report.reasons) if report.verdict!='VERIFIED' else []
                target='VERIFIED' if report.verdict=='VERIFIED' else 'CANCELLED' if report.verdict=='CANCELLED' else 'REJECTED'
                transition(session,target,'; '.join(report.reasons) if report.verdict!='VERIFIED' else None)
                report.ended_at=now();self.store.save(report);self.store.save(candidate);self.store.save(session)
                # Source archive is retained as immutable audit evidence; no host checkout exists.
            return report

    def status(self,session_id):
        session=self.store.get(session_id,'session')
        candidates=[self.candidate(session,cid) for cid in session.candidate_ids]
        report=None
        if candidates and candidates[-1].verification_id:report=self.store.get(candidates[-1].verification_id,'verification')
        return RepairStatus(session=session,candidates=candidates,attempts_remaining=session.maximum_attempts-session.attempts_used,
            latest_verification=report,suggested_next_steps=(['Attempt limit reached; stop proposing candidates and explicitly finalize REJECT']
                if session.attempts_used>=session.maximum_attempts and session.status not in ('VERIFIED','COMPLETED') else ['Review the verified revision; merge/deploy only with explicit developer approval']
                if session.status=='VERIFIED' else ['Inspect evidence; create a new immutable revision only if another bounded attempt is justified']))

    def finalize(self,session_id,candidate_id,decision):
        if decision not in ('ACCEPT_FOR_REVIEW','REJECT'):raise ArtifactError('Invalid finalization decision')
        with self.store.lock(session_id):
            session=self.store.get(session_id,'session');candidate=self.candidate(session,candidate_id)
            if session.final_decision:
                if session.final_decision==decision and session.finalized_candidate_id==candidate_id:return session
                raise ArtifactError('Session already finalized with a different decision')
            if decision=='ACCEPT_FOR_REVIEW':
                if session.status!='VERIFIED' or candidate.verification_status!='VERIFIED' or not candidate.verification_id:
                    raise ArtifactError('ACCEPT_FOR_REVIEW requires completed verified evidence')
                report=self.store.get(candidate.verification_id,'verification')
                if (report.verdict!='VERIFIED' or report.candidate_id!=candidate_id or report.session_id!=session_id or
                    report.source_digest!=candidate.source_digest or not report.correctness or report.correctness.status!='PASS' or
                    not report.benchmark_test_ids or report.remaining_managed_resources):raise ArtifactError('Verification evidence is incomplete')
                project=self.project(session.approved_project_id)
                if session.policy_digest!=fingerprint(project) or report.recipe_digest!=fingerprint(project.recipe):
                    raise ArtifactError('Policy/recipe changed after verification')
                baseline=self.diagnostics.collector.load_record(session.baseline_test_id)
                self.baseline_quality(baseline,project)
                trials=[];report.comparisons=[]
                for test_id in report.benchmark_test_ids:
                    collected=self.diagnostics.collector.collect(DiagnosticInput(test_id=test_id,include_logs=False,include_traces=False))
                    trial=collected.record
                    if (not collected.raw_summary_valid or trial.metadata is None or trial.metadata.commit_identifier!=candidate.source_revision or
                        trial.metadata.test_recipe_digest!=report.recipe_digest or trial.metadata.test_recipe_version!=project.recipe.version or
                        trial.metadata.application_image_id!=project.runtime_image.image_id):
                        raise ArtifactError('Candidate measured evidence missing or revision mismatch')
                    report.comparisons.append(self.diagnostics.compare(baseline.test_id,test_id));trials.append(trial)
                if RepairEvaluator().evaluate(report,baseline,trials,project).verdict!='VERIFIED':
                    raise ArtifactError('Persisted verification no longer supports acceptance')
                session.accepted_candidate_id=candidate_id
            elif session.status not in ('AWAITING_CANDIDATE','REJECTED','VERIFIED'):
                raise ArtifactError('Session cannot be finalized in its current state')
            if decision=='REJECT':candidate.verification_status='REJECTED';candidate.rejection_reasons.append('Explicitly rejected by developer/agent decision');self.store.save(candidate)
            transition(session,'COMPLETED')
            session.final_decision=decision;session.finalized_candidate_id=candidate_id
            self.store.save(session)
            return session

    def recover(self):
        remaining=[]
        for path in list(self.store.directory.glob('*.session.json'))[:100]:
            sid=path.name.split('.')[0]
            try:
                with self.store.lock(sid):
                    session=self.store.get(sid,'session')
                    pending=cleanup(self.manager,sid);remaining.extend(pending);session.managed_sandbox_ids=pending
                    if session.status=='VERIFYING':
                        for cid in session.candidate_ids:
                            candidate=self.candidate(session,cid)
                            if candidate.verification_status=='VERIFYING':
                                candidate.verification_status='INCONCLUSIVE';candidate.rejection_reasons.append('Process interrupted; explicit new candidate required')
                                if candidate.verification_id:
                                    report=self.store.get(candidate.verification_id,'verification');report.verdict='INCONCLUSIVE'
                                    report.reasons.append('Verification process interrupted');report.ended_at=now();report.remaining_managed_resources=pending;self.store.save(report)
                                self.store.save(candidate)
                        transition(session,'REJECTED','Verification interrupted; no automatic retry')
                    self.store.save(session)
            except Timeout:continue
        return remaining

    def benchmark_baseline(self,project_id):
        """Trusted operator API/CLI. Execute approved baseline recipe before a repair session exists."""
        with self.manager.registry.transaction() as data:self.manager.registry.save(data)
        project=self.project(project_id);resolver=CandidateResolver(project);revision=resolver.resolve(project.baseline_revision)
        snapshot=resolver.archive(revision);stamp=now()
        session=RepairSession(session_id=str(uuid4()),owner_id=current_owner(self.state_dir),diagnostic_report_id=str(UUID(int=0)),
            baseline_test_id=str(UUID(int=0)),approved_project_id=project_id,baseline_revision=revision,baseline_digest=digest(snapshot),
            policy_digest=fingerprint(project),created_at=stamp,updated_at=stamp)
        transition(session,'AWAITING_CANDIDATE');self.store.save(session)
        records=[]
        with self.admission(session.session_id):
            verifier=CorrectnessVerifier(self.manager,self.store,self.cancelled)
            try:
                for stage in ('build','correctness'):
                    result=verifier.run(session,project,snapshot,stage)
                    self.store.files.write(session.session_id,'.baseline-'+stage+'.json',result.model_dump_json().encode())
                    if result.status!='PASS':raise ArtifactError('Baseline approved recipe did not PASS')
                manager,sid=verifier.create_and_deliver(session,project,project.recipe.application_command,snapshot,application=True,revision=revision)
                engine=LoadEngine(manager,cancelled=self.cancelled)
                if engine.prepare(sid).readiness_status!='ready':raise ArtifactError('Baseline HTTP application unavailable')
                config=PerformanceTestConfig.model_validate(project.baseline_workload|{'sandbox_id':sid})
                if project.evaluation.warmup_seconds:
                    warmup=engine.run(config.model_copy(update={'duration_seconds':project.evaluation.warmup_seconds}))
                    if warmup.status!='completed':raise ArtifactError('Baseline warmup failed')
                for _ in range(project.evaluation.repetitions):
                    verifier.check_cancelled();record=engine.run(config)
                    if record.status!='completed' or record.metrics.p95_ms is None:raise ArtifactError('Baseline trial incomplete')
                    records.append(record)
                chosen=sorted(records,key=lambda r:r.metrics.p95_ms)[len(records)//2]
                session.baseline_test_id=chosen.test_id
                self.store.files.write(chosen.test_id,'.baseline-trials.json',__import__('json').dumps({'test_ids':[r.test_id for r in records]}).encode())
                return chosen
            finally:
                pending=cleanup(self.manager,session.session_id);session.managed_sandbox_ids=pending
                transition(session,'FAILED' if pending else 'COMPLETED','Baseline preparation audit; not a repair acceptance')
                self.store.save(session)
