"""Transport-independent contracts and safety bounds."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResult(BaseModel):
    service_name: str
    version: str
    status: Literal["healthy"] = "healthy"
    timestamp: datetime


class SystemInfo(BaseModel):
    operating_system: str
    python_version: str
    cpu_count: int | None
    available_memory_mb: float | None


class BenchmarkConfig(BaseModel):
    """Inclusive resource bounds; strict types prevent silent input coercion."""

    model_config = ConfigDict(strict=True, extra="forbid")
    cpus: float = Field(ge=0.1, le=4.0, allow_inf_nan=False)
    memory_mb: int = Field(ge=128, le=4096)
    vus: int = Field(ge=1, le=100)
    duration_seconds: int = Field(ge=1, le=120)


class ValidationIssue(BaseModel):
    field: str
    message: str
    code: str


class ValidationResult(BaseModel):
    valid: bool
    config: BenchmarkConfig | None = None
    errors: list[ValidationIssue] = Field(default_factory=list)
