"""Versioned, bounded diagnostic contracts; observations and hypotheses stay distinct."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class DiagnosticInput(Contract):
    test_id: str
    baseline_test_id: str | None = None
    include_logs: bool = True
    include_resource_metrics: bool = True
    include_traces: bool = True

    @field_validator('test_id', 'baseline_test_id')
    @classmethod
    def valid_uuid(cls, value):
        return str(UUID(value)) if value is not None else None


class Window(Contract):
    start: datetime | None = None
    end: datetime | None = None
    clock_origin: Literal['UTC', 'unknown'] = 'unknown'


class Evidence(Contract):
    evidence_id: str
    source_type: Literal['benchmark', 'resource_sample', 'container_state', 'log_signature', 'trace', 'comparison', 'telemetry_gap']
    source_reference: str = Field(max_length=256)
    observed_value: JsonValue
    unit: str | None = None
    observation_window: Window = Field(default_factory=Window)
    explanation: str = Field(max_length=1024)
    limitations: list[str] = Field(default_factory=list, max_length=32)
    interpretation: Literal['OBSERVATION', 'TEMPORAL_ASSOCIATION', 'HYPOTHESIS', 'CONFIRMED_FAILURE'] = 'OBSERVATION'


Category = Literal['LATENCY_REGRESSION', 'HIGH_ERROR_RATE', 'CPU_PRESSURE', 'MEMORY_PRESSURE',
    'CONTAINER_OOM', 'APPLICATION_ERRORS', 'DATABASE_LATENCY', 'POSSIBLE_RESOURCE_CONTENTION', 'INSUFFICIENT_EVIDENCE']


class DiagnosticFinding(Contract):
    finding_id: str
    category: Category
    severity: Literal['INFO', 'WARNING', 'ERROR', 'CRITICAL']
    confidence: Literal['LOW', 'MEDIUM', 'HIGH']
    title: str
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)
    insufficient_evidence: bool = False
    recommended_investigations: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def supported(self):
        if not self.evidence_ids and not self.insufficient_evidence:
            raise ValueError('A finding must cite evidence or declare insufficient evidence')
        return self


class MetricDifference(Contract):
    metric: str
    baseline_value: float | None = None
    candidate_value: float | None = None
    absolute_change: float | None = None
    relative_change_percent: float | None = None
    unit: str
    limitations: list[str] = Field(default_factory=list)


class RegressionConfig(Contract):
    p95_increase_percent: float = Field(default=20, ge=0, le=1000)
    p99_increase_percent: float = Field(default=25, ge=0, le=1000)
    error_increase_percentage_points: float = Field(default=1, ge=0, le=100)
    memory_increase_percent: float = Field(default=20, ge=0, le=1000)
    duration_tolerance_percent: float = Field(default=5, ge=0, le=25)


class Comparison(Contract):
    baseline_test_id: str
    candidate_test_id: str
    compatibility: Literal['COMPATIBLE', 'PARTIALLY_COMPATIBLE', 'INCOMPATIBLE']
    metric_differences: list[MetricDifference] = Field(default_factory=list)
    regression_classification: Literal['REGRESSION', 'NO_REGRESSION_DETECTED', 'INCONCLUSIVE'] = 'INCONCLUSIVE'
    missing_compatibility_information: list[str] = Field(default_factory=list)
    incompatibilities: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    thresholds: RegressionConfig = Field(default_factory=RegressionConfig)
    # Future repeated measurements; never imply statistical significance from one pair.
    baseline_sample_test_ids: list[str] = Field(default_factory=list, max_length=30)
    candidate_sample_test_ids: list[str] = Field(default_factory=list, max_length=30)


class DiagnosticReport(Contract):
    schema_version: Literal[1] = 1
    report_id: str
    owner_id: str
    test_id: str
    baseline_test_id: str | None = None
    generated_at: datetime
    overall_status: Literal['FINDINGS', 'NO_FINDINGS', 'INCONCLUSIVE']
    budget_status: Literal['PASS', 'FAIL', 'INCONCLUSIVE']
    regression_summary: Comparison | None = None
    findings: list[DiagnosticFinding] = Field(default_factory=list, max_length=64)
    evidence: list[Evidence] = Field(default_factory=list, max_length=512)
    missing_observability: list[str] = Field(default_factory=list, max_length=128)
    recommended_next_steps: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def references(self):
        ids = {e.evidence_id for e in self.evidence}
        if len(ids) != len(self.evidence) or any(set(f.evidence_ids) - ids for f in self.findings):
            raise ValueError('Invalid or duplicate evidence references')
        for value in (self.report_id, self.owner_id, self.test_id, self.baseline_test_id):
            if value is not None:
                UUID(value)
        return self


class AnalysisSummary(Contract):
    report_id: str
    test_id: str
    overall_status: str
    budget_status: str
    regression_status: str
    finding_categories: list[Category]
    missing_observability: list[str]


class DiagnosticContext(Contract):
    report_id: str
    text: str
    characters: int
    truncated: bool
    untrusted_content_included: Literal[False] = False
