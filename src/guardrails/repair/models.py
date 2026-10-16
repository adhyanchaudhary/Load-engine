"""Versioned repair policy, session, candidate and verification contracts."""
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from guardrails.docker.models import ApprovedImage
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.diagnostics.models import Comparison


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Recipe(Model):
    version: str = Field(pattern=r'^[A-Za-z0-9._-]{1,64}$')
    kind: Literal['offline-python-source-v1'] = 'offline-python-source-v1'
    python_executable: str = Field(default='python',pattern=r'^[A-Za-z0-9/_-]+$')
    build_command: list[str] = Field(min_length=1,max_length=32)
    test_command: list[str] = Field(min_length=1,max_length=32)
    application_command: list[str] = Field(min_length=1,max_length=32)
    port: int = Field(default=8080,ge=1024,le=65535)
    build_timeout_seconds: int = Field(default=20,ge=1,le=60)
    test_timeout_seconds: int = Field(default=30,ge=1,le=120)
    minimum_tests: int = Field(default=1,ge=1,le=10000)
    workspace_mb: int = Field(default=16,ge=8,le=64)

    @field_validator('build_command','test_command','application_command')
    @classmethod
    def bounded(cls,value):
        if any(not x or len(x)>1024 or '\0' in x for x in value):raise ValueError('Invalid trusted recipe argv')
        return value


class EvaluationPolicy(Model):
    improvement_percent: float = Field(default=15,ge=1,le=90)
    maximum_throughput_drop_percent: float = Field(default=5,ge=0,le=25)
    maximum_error_increase_percentage_points: float = Field(default=0,ge=0,le=1)
    maximum_p99_increase_percent: float = Field(default=5,ge=0,le=25)
    maximum_trial_spread_percent: float = Field(default=20,ge=1,le=50)
    repetitions: int = Field(default=3,ge=1,le=3)
    warmup_seconds: int = Field(default=1,ge=0,le=3)


class ApprovedProject(Model):
    workspace: str = Field(min_length=1,max_length=1024)
    baseline_revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    runtime_image: ApprovedImage
    recipe: Recipe
    allowed_changes: list[str] = Field(min_length=1,max_length=32)
    protected_paths: list[str] = Field(default_factory=lambda:['tests/'],max_length=32)
    dataset_id: str = Field(pattern=r'^[A-Za-z0-9._:-]{1,128}$')
    application_cpus: float = Field(default=.5,ge=.1,le=4)
    application_memory_mb: int = Field(default=256,ge=128,le=4096)
    application_pids_limit: int = Field(default=64,ge=16,le=256)
    evaluation: EvaluationPolicy = Field(default_factory=EvaluationPolicy)
    baseline_workload: dict = Field(default_factory=lambda:dict(endpoint_path='/',vus=2,duration_seconds=3,
        p95_latency_budget_ms=250.0,error_rate_budget=.01))

    @field_validator('allowed_changes','protected_paths')
    @classmethod
    def paths(cls,value):
        for name in value:
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or not name or name.startswith('.'):
                raise ValueError('Policy paths must be simple repository-relative files or prefixes')
        return value


class RepairPolicy(Model):
    schema_version: Literal[1] = 1
    projects: dict[str,ApprovedProject] = Field(default_factory=dict,max_length=20)
    maximum_active_sessions: int = Field(default=2,ge=1,le=4)
    verification_concurrency: int = Field(default=1,ge=1,le=2)

    @field_validator('projects')
    @classmethod
    def project_ids(cls,value):
        for key in value:
            if str(UUID(key))!=key:raise ValueError('Project IDs must be canonical UUIDs')
        return value


SessionState=Literal['CREATED','AWAITING_CANDIDATE','VERIFYING','VERIFIED','REJECTED','FAILED','CANCELLED','COMPLETED']


class RepairSession(Model):
    schema_version: Literal[1] = 1
    session_id: str
    owner_id: str
    diagnostic_report_id: str
    baseline_test_id: str
    approved_project_id: str
    baseline_revision: str
    baseline_digest: str
    policy_digest: str
    status: SessionState = 'CREATED'
    created_at: datetime
    updated_at: datetime
    maximum_attempts: int = Field(default=3,ge=1,le=5)
    attempts_used: int = Field(default=0,ge=0,le=5)
    accepted_candidate_id: str | None = None
    failure_reason: str | None = None
    candidate_ids: list[str] = Field(default_factory=list,max_length=5)
    managed_sandbox_ids: list[str] = Field(default_factory=list,max_length=20)
    final_decision: Literal['ACCEPT_FOR_REVIEW','REJECT'] | None = None
    finalized_candidate_id: str | None = None
    audit: list[dict] = Field(default_factory=list,max_length=64)


class RepairCandidate(Model):
    schema_version: Literal[1] = 1
    candidate_id: str
    owner_id: str
    session_id: str
    source_revision: str
    source_digest: str
    parent_revision: str
    created_at: datetime
    changed_files: list[str] = Field(max_length=100)
    change_summary: str
    verification_status: Literal['REGISTERED','VERIFYING','VERIFIED','REJECTED','INCONCLUSIVE','CANCELLED'] = 'REGISTERED'
    benchmark_test_ids: list[str] = Field(default_factory=list,max_length=3)
    rejection_reasons: list[str] = Field(default_factory=list,max_length=32)
    verification_id: str | None = None


class StageResult(Model):
    stage: Literal['build','correctness']
    status: Literal['PASS','FAIL','INCONCLUSIVE']
    recipe_version: str
    recipe_digest: str
    exit_code: int | None = None
    tests_executed: int | None = None
    failures: int | None = None
    stdout: str = Field(default='',max_length=8192)
    stderr: str = Field(default='',max_length=8192)
    untrusted_output: Literal[True] = True
    errors: list[str] = Field(default_factory=list,max_length=16)


class VerificationReport(Model):
    schema_version: Literal[1] = 1
    verification_id: str
    owner_id: str
    session_id: str
    candidate_id: str
    source_revision: str
    source_digest: str
    baseline_test_id: str
    recipe_digest: str
    started_at: datetime
    ended_at: datetime | None = None
    verdict: Literal['VERIFYING','VERIFIED','REJECTED','INCONCLUSIVE','CANCELLED'] = 'VERIFYING'
    build: StageResult | None = None
    correctness: StageResult | None = None
    benchmark_test_ids: list[str] = Field(default_factory=list,max_length=3)
    warmup_test_ids: list[str] = Field(default_factory=list,max_length=1)
    comparisons: list[Comparison] = Field(default_factory=list,max_length=3)
    statistics: dict[str,float] = Field(default_factory=dict,max_length=16)
    reasons: list[str] = Field(default_factory=list,max_length=32)
    evidence_references: list[str] = Field(default_factory=list,max_length=32)
    remaining_managed_resources: list[str] = Field(default_factory=list,max_length=20)
    limitations: list[str] = Field(default_factory=lambda:['Engineering heuristics; no statistical significance or production-safety claim'])


class RepairStatus(Model):
    session: RepairSession
    candidates: list[RepairCandidate] = Field(max_length=5)
    attempts_remaining: int
    latest_verification: VerificationReport | None = None
    suggested_next_steps: list[str]
