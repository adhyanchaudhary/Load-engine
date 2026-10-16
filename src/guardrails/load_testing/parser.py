"""Parse k6 handleSummary output, never streaming JSON events."""
import json
import math
from guardrails.load_testing.models import Metrics

MAX_SUMMARY_BYTES = 2 * 1024 * 1024


def parse_summary(raw: bytes) -> Metrics:
    result = Metrics()
    try:
        if len(raw) > MAX_SUMMARY_BYTES:
            raise ValueError()
        doc = json.loads(raw)
        metrics = doc["metrics"]
        if not isinstance(metrics, dict):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        result.errors.append("Invalid or missing k6 end-of-test summary")
        return result

    def number(metric, *names):
        node = metrics.get(metric, {})
        values = node.get("values", {}) if isinstance(node, dict) else {}
        if not isinstance(values, dict):
            return None
        for name in names:
            value = values.get(name)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                return float(value)
        return None

    count = number("http_reqs", "count")
    if count is not None and count.is_integer():
        result.request_count = int(count)
    responses = number("responses_received", "count")
    if responses is not None and responses.is_integer():
        result.application_response_count = int(responses)
    result.throughput_rps = number("http_reqs", "rate")
    result.p50_ms = number("http_req_duration", "med", "p(50)")
    result.p95_ms = number("http_req_duration", "p(95)")
    result.p99_ms = number("http_req_duration", "p(99)")
    result.http_failure_rate = number("http_req_failed", "rate")
    if result.http_failure_rate is not None and result.http_failure_rate > 1:
        result.http_failure_rate = None
    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            continue
        thresholds = metric.get("thresholds", {})
        if isinstance(thresholds, dict):
            for expr, outcome in thresholds.items():
                ok = outcome.get("ok") if isinstance(outcome, dict) else None
                result.thresholds[f"{name}:{expr}"] = ok if type(ok) is bool else None
    for name in ("request_count", "throughput_rps", "p50_ms", "p95_ms", "p99_ms", "http_failure_rate"):
        if getattr(result, name) is None:
            result.errors.append(f"Missing or invalid {name}")
    return result
