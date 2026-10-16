"""Measured budget decisions without unsupported root-cause inference."""
from guardrails.load_testing.models import BudgetResult, TestRecord


def evaluate(record: TestRecord) -> BudgetResult:
    m = record.metrics
    reasons = list(record.errors) + list(m.errors)
    if record.status != "completed":
        reasons.append("k6 did not complete correctly")
    if m.application_response_count == 0:
        reasons.append("Application unavailable: no HTTP response was received")
    if not m.request_count:
        reasons.append("No valid requests recorded")
    if record.runner_cpu_throttled:
        reasons.append("Runner reached its CPU quota; load generation was resource constrained")
    if record.runner_oom_killed or record.application.oom_killed:
        reasons.append("Container was OOM killed")
    if m.p95_ms is None or m.http_failure_rate is None:
        reasons.append("Required measurements are absent")
    if reasons:
        return BudgetResult(status="INCONCLUSIVE", sample_count=m.request_count, reasons=reasons)
    latency = m.p95_ms <= record.configuration.p95_latency_budget_ms
    failure = m.http_failure_rate <= record.configuration.error_rate_budget
    return BudgetResult(status="PASS" if latency and failure else "FAIL",
        p95_threshold=latency, http_failure_threshold=failure, sample_count=m.request_count,
        reasons=([f"p95 latency exceeded the configured {record.configuration.p95_latency_budget_ms:g} ms budget."] if not latency else []) +
                (["HTTP failure rate exceeded the configured budget."] if not failure else []))
