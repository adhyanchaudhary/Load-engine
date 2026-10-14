"""Pure configuration validation: no execution, network or container access."""
from pydantic import ValidationError

from guardrails.models import BenchmarkConfig, ValidationIssue, ValidationResult


def validate_test_config(
    cpus: float, memory_mb: int, vus: int, duration_seconds: int,
) -> ValidationResult:
    """Validate CPU [0.1,4], MB [128,4096], VUs [1,100], seconds [1,120]."""
    try:
        config = BenchmarkConfig(
            cpus=cpus, memory_mb=memory_mb, vus=vus,
            duration_seconds=duration_seconds,
        )
    except ValidationError as exc:
        return ValidationResult(valid=False, errors=[
            ValidationIssue(
                field=".".join(str(part) for part in error["loc"]),
                message=error["msg"], code=error["type"],
            )
            for error in exc.errors(include_input=False, include_url=False)
        ])
    return ValidationResult(valid=True, config=config)
