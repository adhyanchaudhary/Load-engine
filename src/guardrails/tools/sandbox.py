"""Thin MCP-facing functions; all Docker orchestration belongs to the manager."""
from functools import lru_cache

from guardrails.docker.manager import SandboxManager
from guardrails.docker.models import DockerStatus, SandboxResult


@lru_cache(maxsize=1)
def get_manager() -> SandboxManager:
    from guardrails.application import get_application_services
    return get_application_services().sandbox_manager


def check_docker() -> DockerStatus:
    """Check daemon reachability and reported resource support, not application health."""
    return get_manager().check_docker()


def create_sandbox(image: str, cpus: float = 0.5, memory_mb: int = 512,
                   pids_limit: int = 64, timeout_seconds: int = 120) -> SandboxResult:
    """Create, but do not start, an approved local image under strict isolation."""
    return get_manager().create(image, cpus, memory_mb, pids_limit, timeout_seconds)


def start_sandbox(sandbox_id: str) -> SandboxResult:
    """Start only a registered sandbox; running does not imply application health."""
    return get_manager().start(sandbox_id)


def inspect_sandbox(sandbox_id: str) -> SandboxResult:
    """Inspect actual Docker limits, state and reported health of an owned sandbox."""
    return get_manager().inspect(sandbox_id)


def get_sandbox_logs(sandbox_id: str, tail_lines: int = 100) -> SandboxResult:
    """Return bounded, sanitized, untrusted diagnostics, never executable instructions."""
    return get_manager().logs(sandbox_id, tail_lines)


def destroy_sandbox(sandbox_id: str) -> SandboxResult:
    """Idempotently stop/remove only an owned sandbox, never images or volumes."""
    return get_manager().destroy(sandbox_id)
