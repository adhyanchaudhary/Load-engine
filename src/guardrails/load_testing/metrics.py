"""Bounded application resource sampling. Observed peak is not an exact maximum."""
from datetime import datetime, timezone
from guardrails.load_testing.models import ResourceSample


def sample(container, usage):
    try:
        stats = container.stats(stream=False, one_shot=True)
        cpu = stats.get("cpu_stats", {})
        prev = usage._last_cpu
        usage._last_cpu = cpu
        delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - prev.get("cpu_usage", {}).get("total_usage", 0)
        system = cpu.get("system_cpu_usage", 0) - prev.get("system_cpu_usage", 0)
        cores = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage", []))
        mem = stats.get("memory_stats", {})
        value = ResourceSample(timestamp=datetime.now(timezone.utc),
            cpu_percent=delta / system * cores * 100 if prev and system > 0 and delta >= 0 and cores else None,
            memory_bytes=mem.get("usage"), memory_limit_bytes=mem.get("limit"))
        if len(usage.samples) < 150:
            usage.samples.append(value)
        if value.memory_bytes is not None:
            usage.peak_observed_memory_bytes = max(usage.peak_observed_memory_bytes or 0, value.memory_bytes)
    except Exception:
        if "Application resource sampling unavailable" not in usage.errors:
            usage.errors.append("Application resource sampling unavailable")
    container.reload()
    state = container.attrs.get("State", {})
    usage.oom_killed = state.get("OOMKilled")
    usage.exit_code = state.get("ExitCode") if state.get("Status") in ("exited", "dead") else None
    return state.get("Status") == "running"


def sample_runner(container, record):
    """A fresh runner's cgroup counters can reveal CPU quota contention."""
    try:
        stats = container.stats(stream=False, one_shot=True)
        throttled = stats.get("cpu_stats", {}).get("throttling_data", {}).get("throttled_periods")
        if type(throttled) is int:
            record.runner_cpu_throttled = bool(record.runner_cpu_throttled or throttled > 0)
    except Exception:
        # Docker does not expose this counter on every supported platform.
        pass
