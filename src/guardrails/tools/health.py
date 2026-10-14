"""Read-only service and non-identifying system information."""
import os
import platform
from datetime import datetime, timezone

import psutil

from guardrails.config import SERVICE_NAME, VERSION
from guardrails.models import HealthResult, SystemInfo


def health_check() -> HealthResult:
    """Return service identity and current UTC health timestamp."""
    return HealthResult(
        service_name=SERVICE_NAME, version=VERSION,
        timestamp=datetime.now(timezone.utc),
    )


def get_system_info() -> SystemInfo:
    """Return OS, Python, CPU count and available memory without host identity."""
    try:
        memory = psutil.virtual_memory().available / (1024 * 1024)
    except (OSError, NotImplementedError, psutil.Error):
        memory = None
    return SystemInfo(
        operating_system=platform.system(),
        python_version=platform.python_version(),
        cpu_count=os.cpu_count(), available_memory_mb=memory,
    )
