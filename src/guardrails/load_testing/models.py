"""Versioned configuration and measurement contracts. Missing values stay null."""
import re
import unicodedata
from datetime import datetime
from typing import Literal
from urllib.parse import unquote, quote
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator


def safe_path(value: str) -> str:
    if len(value) > 2048:
        raise ValueError("Endpoint path is too long")
    decoded = value
    # Reject ambiguous/double escaping rather than interpreting multiple times.
    if re.search(r"%(?![0-9a-fA-F]{2})", decoded):
        raise ValueError("Invalid percent encoding")
    decoded = unquote(decoded, errors="strict")
    if (not decoded.startswith("/") or "//" in decoded or
        any(c in decoded for c in '\\#?@:;%') or
        any(unicodedata.category(c).startswith("C") for c in decoded) or
        any(part in (".", "..") for part in decoded.split("/"))):
        raise ValueError("Use an absolute application path without authority, query, fragments or traversal")
    return quote(decoded, safe="/-._~!$&'()*+,=")


class PerformanceTestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sandbox_id: str
    endpoint_path: str
    method: Literal["GET"] = "GET"
    vus: int = Field(default=10, ge=1, le=100)
    duration_seconds: int = Field(default=30, ge=1, le=120)
    p95_latency_budget_ms: float = Field(default=250, gt=0, le=3_600_000, allow_inf_nan=False)
    error_rate_budget: float = Field(default=0.01, ge=0, le=1, allow_inf_nan=False)

    @field_validator("sandbox_id")
    @classmethod
    def valid_id(cls, value):
        return str(UUID(value))

    @field_validator("endpoint_path")
    @classmethod
    def valid_path(cls, value):
        return safe_path(value)


class Metrics(BaseModel):
    parser_schema_version: Literal[1] = 1
    application_response_count: int | None = None
    request_count: int | None = None
    throughput_rps: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    http_failure_rate: float | None = None
    thresholds: dict[str, bool | None] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class BudgetResult(BaseModel):
    status: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    p95_threshold: bool | None = None
    http_failure_threshold: bool | None = None
    sample_count: int | None = None
    reasons: list[str] = Field(default_factory=list)


class ResourceSample(BaseModel):
    timestamp: datetime
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None


class ResourceUsage(BaseModel):
    _last_cpu: dict = PrivateAttr(default_factory=dict)
    samples: list[ResourceSample] = Field(default_factory=list)
    peak_observed_memory_bytes: int | None = None
    oom_killed: bool | None = None
    exit_code: int | None = None
    errors: list[str] = Field(default_factory=list)


class RunMetadata(BaseModel):
    schema_version: Literal[1] = 1
    test_recipe_version: str | None = None
    test_recipe_digest: str | None = None
    workload_model: Literal["constant-vus"] = "constant-vus"
    application_identity: str | None = None
    application_version: str | None = None
    commit_identifier: str | None = None
    dataset_id: str | None = None
    application_configuration_fingerprint: str | None = None
    architecture: str | None = None
    environment_fingerprint: str | None = None
    application_cpus: float | None = None
    application_memory_bytes: int | None = None
    runner_cpus: float | None = None
    runner_memory_bytes: int | None = None
    runner_pids_limit: int | None = None
    runner_image_id: str | None = None
    application_image_id: str | None = None
    resource_source: str = "Docker stats one_shot, UTC wall clock"


class HistoricalLogs(BaseModel):
    schema_version: Literal[1] = 1
    text: str = Field(default="", max_length=16384)
    collected_at: datetime
    since: datetime
    until: datetime
    source: Literal["Docker timestamped logs"] = "Docker timestamped logs"
    untrusted: Literal[True] = True
    truncated: bool = False
    error: str | None = None


class TestRecord(BaseModel):
    schema_version: Literal[1] = 1
    owner_id: str | None = None
    metadata: RunMetadata | None = None
    historical_logs: HistoricalLogs | None = None
    test_id: str
    sandbox_id: str
    configuration: PerformanceTestConfig
    status: Literal["running", "completed", "failed", "cancelled", "interrupted"] = "running"
    started_at: datetime
    ended_at: datetime | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    budget: BudgetResult = Field(default_factory=lambda: BudgetResult(status="INCONCLUSIVE"))
    application: ResourceUsage = Field(default_factory=ResourceUsage)
    runner_cpu_throttled: bool | None = None
    runner_oom_killed: bool | None = None
    runner_exit_code: int | None = None
    errors: list[str] = Field(default_factory=list)


class EnvironmentResult(BaseModel):
    sandbox_id: str
    environment_id: str | None = None
    network_status: str = "unverified"
    application_status: str = "unknown"
    readiness_status: Literal["ready", "unavailable"] = "unavailable"
    errors: list[str] = Field(default_factory=list)
