"""Owned sandbox lifecycle and bounded cleanup; never pull images or run exec."""
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from uuid import UUID, uuid4

from docker.errors import DockerException, NotFound
from filelock import Timeout as LockTimeout
from pydantic import ValidationError
from requests.exceptions import RequestException

from guardrails.docker import client as docker_client
from guardrails.docker.models import (
    CleanupResult, DockerStatus, EffectiveLimits, ImagePolicy, OperationError,
    RegistryEntry, SandboxConfig, SandboxInfo, SandboxResult,
)
from guardrails.docker.registry import Registry
from guardrails.docker.security import (
    LOG_LIMIT_BYTES, create_options, isolation_discrepancies, ownership_labels,
    sanitize_logs,
)


class SandboxError(Exception):
    def __init__(self, code: str, message: str):
        self.error = OperationError(code=code, message=message)
        super().__init__(message)


def error_result(exc: Exception) -> OperationError:
    # Never expose arbitrary daemon errors (they can contain paths or credentials).
    if isinstance(exc, SandboxError):
        return exc.error
    if isinstance(exc, ValidationError):
        return OperationError(code="invalid_configuration", message="; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        ))
    if isinstance(exc, (DockerException, RequestException)):
        return OperationError(code="docker_error", message="Docker operation failed. Check daemon connectivity, permissions, local image and supported isolation controls.")
    if isinstance(exc, LockTimeout):
        return OperationError(code="registry_busy", message="Registry is busy; retry after the other lifecycle operation completes.")
    return OperationError(code="local_configuration_error", message="Cannot read or update trusted policy/registry. Check file access and JSON format; do not delete ownership records before cleanup.")


HANDLED = (SandboxError, DockerException, RequestException, OSError, ValueError, LockTimeout)


def operation(method):
    @wraps(method)
    def wrapped(*args, **kwargs) -> SandboxResult:
        try:
            return method(*args, **kwargs)
        except HANDLED as exc:
            return SandboxResult(ok=False, error=error_result(exc))
    return wrapped


class SandboxManager:
    def __init__(self, state_dir: Path, *, policy: ImagePolicy | None = None,
                 policy_path: Path | None = None, client_factory=docker_client.connect,
                 clock=None):
        self.registry = Registry(state_dir)
        self._policy = policy
        self.policy_path = policy_path
        self.client_factory = client_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_environment(cls):
        state_dir = Path(os.environ.get("GUARDRAILS_STATE_DIR", str(Path.home() / ".ai-performance-guardrails")))
        policy_path = os.environ.get("GUARDRAILS_POLICY_FILE")
        return cls(state_dir, policy_path=Path(policy_path) if policy_path else None)

    def _load_policy(self) -> ImagePolicy:
        if self._policy is not None:
            return self._policy
        if self.policy_path is None:
            return ImagePolicy()  # Explicitly deny all images until operator approval.
        return ImagePolicy.model_validate_json(self.policy_path.read_bytes())

    def check_docker(self) -> DockerStatus:
        try:
            with self.client_factory() as client:
                if not client.ping():
                    raise SandboxError("unreachable", "Docker ping failed")
                capabilities = docker_client.support(client.info())
                supported = all(capabilities.values())
                return DockerStatus(
                    available=True, connectivity="reachable",
                    engine_version=client.version().get("Version"),
                    resource_limit_status="daemon_reports_support" if supported else "unsupported_or_unknown",
                    resource_support=capabilities,
                    errors=[] if supported else ["Mandatory Linux resource controls are unsupported or unreported; creation is disabled."],
                )
        except HANDLED as exc:
            return DockerStatus(available=False, connectivity="unreachable", errors=[error_result(exc).message])

    def _verify_owner(self, container, entry: RegistryEntry) -> None:
        container.reload()
        actual = container.attrs.get("Config", {}).get("Labels") or {}
        if container.id != entry.container_id or any(actual.get(k) != v for k, v in ownership_labels(entry).items()):
            raise SandboxError("ownership_mismatch", "Container ownership does not match the private registry; operation refused.")

    def _resolve(self, client, data, sandbox_id: str):
        try:
            UUID(sandbox_id)
        except (ValueError, AttributeError, TypeError):
            raise SandboxError("unknown_sandbox", "Use a generated sandbox ID, never a Docker container ID.") from None
        entry = data.entries.get(sandbox_id)
        if entry is None:
            raise SandboxError("unknown_sandbox", "Sandbox ID is not registered.")
        if entry.container_id is None:
            # Reconcile a crash between create and registry finalization. A durable
            # intent with a random nonce is required, not merely public labels.
            matches = client.containers.list(all=True, filters={
                "label": [f"{k}={v}" for k, v in ownership_labels(entry).items()],
            })
            if len(matches) > 1:
                raise SandboxError("ownership_mismatch", "Ambiguous creation intent; refusing to modify containers.")
            if not matches:
                raise NotFound("No container for registered intent")
            entry.container_id = matches[0].id
            container = matches[0]
            self._verify_owner(container, entry)
            self.registry.save(data)
        else:
            container = client.containers.get(entry.container_id)
            self._verify_owner(container, entry)
        return entry, container

    def _require_unexpired(self, entry: RegistryEntry) -> None:
        if entry.expires_at <= self.clock():
            raise SandboxError("expired", "Sandbox has expired. Destroy it or run the cleanup command; it cannot be started or queried.")

    def _snapshot(self, container, entry: RegistryEntry, support: dict[str, bool]) -> SandboxInfo:
        attrs = container.attrs
        host = attrs.get("HostConfig", {})
        state = attrs.get("State", {})
        discrepancies = isolation_discrepancies(attrs, entry)
        discrepancies += [f"host does not report {name} support" for name, enabled in support.items() if not enabled]
        status = state.get("Status", "unknown")
        uptime = None
        if status == "running" and state.get("StartedAt"):
            try:
                start = datetime.fromisoformat(state["StartedAt"].replace("Z", "+00:00"))
                if start.tzinfo is not None:
                    uptime = max(0.0, (self.clock() - start).total_seconds())
            except ValueError:
                pass
        return SandboxInfo(
            sandbox_id=entry.sandbox_id, container_id=container.id,
            status=status, health=state.get("Health", {}).get("Status", "unknown"),
            created_at=entry.created_at, expires_at=entry.expires_at,
            uptime_seconds=uptime, oom_killed=bool(state.get("OOMKilled", False)),
            exit_code=state.get("ExitCode") if status in ("exited", "dead") else None,
            limits=EffectiveLimits(
                cpus=host["NanoCpus"] / 1_000_000_000 if host.get("NanoCpus") else None,
                memory_mb=host["Memory"] / 1024**2 if host.get("Memory") else None,
                memory_swap_mb=host["MemorySwap"] / 1024**2 if host.get("MemorySwap") else None,
                pids_limit=host.get("PidsLimit"), verified=not discrepancies,
                discrepancies=discrepancies,
            ),
        )

    @operation
    def create(self, image: str, cpus: float = 0.5, memory_mb: int = 512,
               pids_limit: int = 64, timeout_seconds: int = 120) -> SandboxResult:
        config = SandboxConfig(image=image, cpus=cpus, memory_mb=memory_mb,
                               pids_limit=pids_limit, timeout_seconds=timeout_seconds)
        approved = self._load_policy().images.get(config.image)
        if approved is None:
            raise SandboxError("image_not_approved", "Image alias is not in the trusted operator allowlist. No images are pulled.")
        with self.registry.transaction() as data:
            if len(data.entries) >= 100:
                raise SandboxError("registry_full", "Registry limit reached; destroy or clean up existing sandboxes.")
            with self.client_factory() as client:
                capabilities = docker_client.support(client.info())
                if not all(capabilities.values()):
                    raise SandboxError("unsupported_controls", "Mandatory Linux CPU/memory/swap/PID controls are unsupported or unreported; creation refused.")
                try:
                    local = client.images.get(approved.image_id)
                except NotFound:
                    raise SandboxError("image_not_local", "Approved image is not available locally. Provision it explicitly outside MCP; no pull was attempted.") from None
                if local.id != approved.image_id:
                    raise SandboxError("image_mismatch", "Local image digest does not match approval.")
                if local.attrs.get("Config", {}).get("Volumes"):
                    raise SandboxError("image_volumes", "Approved image declares volumes; automatic writable mounts are forbidden.")
                now = self.clock()
                entry = RegistryEntry(
                    sandbox_id=str(uuid4()), created_at=now,
                    expires_at=now + timedelta(seconds=config.timeout_seconds),
                    owner=data.owner, nonce=str(uuid4()), config=config, approved=approved,
                )
                data.entries[entry.sandbox_id] = entry
                self.registry.save(data)
                # A timed-out create can still succeed in Docker. Keep its durable
                # intent for cleanup rather than losing ownership information.
                container = client.containers.create(**create_options(config, approved, entry))
                entry.container_id = container.id
                self.registry.save(data)
                self._verify_owner(container, entry)
                snapshot = self._snapshot(container, entry, capabilities)
                if not snapshot.limits.verified:
                    container.remove(force=True, v=False)
                    del data.entries[entry.sandbox_id]
                    self.registry.save(data)
                    raise SandboxError("isolation_unverified", "Daemon inspection did not confirm mandatory isolation: " + ", ".join(snapshot.limits.discrepancies))
                return SandboxResult(ok=True, sandbox=snapshot)

    @operation
    def start(self, sandbox_id: str) -> SandboxResult:
        with self.registry.transaction() as data, self.client_factory() as client:
            entry, container = self._resolve(client, data, sandbox_id)
            self._require_unexpired(entry)
            capabilities = docker_client.support(client.info())
            snapshot = self._snapshot(container, entry, capabilities)
            if not snapshot.limits.verified:
                raise SandboxError("isolation_unverified", "Mandatory isolation could not be verified; start refused.")
            if snapshot.status != "running":
                container.start()
            self._verify_owner(container, entry)
            snapshot = self._snapshot(container, entry, docker_client.support(client.info()))
            if not snapshot.limits.verified:
                # Roll back only the registry-owned container if controls changed.
                container.stop(timeout=2)
                raise SandboxError("isolation_unverified", "Isolation changed during startup; owned container stopped.")
            return SandboxResult(ok=True, sandbox=snapshot)

    @operation
    def inspect(self, sandbox_id: str) -> SandboxResult:
        with self.registry.transaction() as data, self.client_factory() as client:
            entry, container = self._resolve(client, data, sandbox_id)
            self._require_unexpired(entry)
            snapshot = self._snapshot(container, entry, docker_client.support(client.info()))
            if not snapshot.limits.verified and snapshot.status in ("running", "paused", "restarting"):
                if snapshot.status == "paused":
                    container.unpause()
                container.stop(timeout=2)
                # Snapshot reports the observed violation; it is not a healthy claim.
                return SandboxResult(ok=False, sandbox=snapshot, error=OperationError(
                    code="isolation_unverified", message="Isolation could not be verified; owned container stopped.",
                ))
            return SandboxResult(ok=True, sandbox=snapshot)

    @operation
    def logs(self, sandbox_id: str, tail_lines: int = 100) -> SandboxResult:
        if type(tail_lines) is not int or not 1 <= tail_lines <= 200:
            raise SandboxError("invalid_tail", "tail_lines must be an integer between 1 and 200.")
        with self.registry.transaction() as data, self.client_factory() as client:
            entry, container = self._resolve(client, data, sandbox_id)
            self._require_unexpired(entry)
            stream = container.logs(stdout=True, stderr=True, stream=True, follow=False, tail=tail_lines)
            raw = bytearray()
            try:
                for chunk in stream:
                    raw.extend(chunk[: LOG_LIMIT_BYTES + 1 - len(raw)])
                    if len(raw) > LOG_LIMIT_BYTES:
                        break
            finally:
                stream.close()
            return SandboxResult(ok=True, logs=sanitize_logs(bytes(raw[:LOG_LIMIT_BYTES]), len(raw) > LOG_LIMIT_BYTES))

    def _destroy(self, client, data, sandbox_id: str) -> SandboxResult:
        try:
            UUID(sandbox_id)
        except (ValueError, TypeError, AttributeError):
            raise SandboxError("unknown_sandbox", "Use a generated sandbox UUID, never a Docker container ID.") from None
        if sandbox_id not in data.entries:
            # Idempotent absent result, without looking up anything in Docker.
            return SandboxResult(ok=True, destroyed=True, already_absent=True)
        try:
            _, container = self._resolve(client, data, sandbox_id)
            state = container.attrs.get("State", {}).get("Status")
            if state == "paused":
                container.unpause()
            if state in ("running", "paused", "restarting"):
                container.stop(timeout=2)
            container.remove(force=True, v=False)
        except NotFound:
            pass  # Already removed externally; registry still needs cleanup.
        del data.entries[sandbox_id]
        self.registry.save(data)
        return SandboxResult(ok=True, destroyed=True, already_absent=False)

    @operation
    def destroy(self, sandbox_id: str) -> SandboxResult:
        with self.registry.transaction() as data:
            # No connection needed for repeated cleanup of an absent sandbox.
            if sandbox_id not in data.entries:
                return self._destroy(None, data, sandbox_id)
            with self.client_factory() as client:
                return self._destroy(client, data, sandbox_id)

    def cleanup_expired(self, limit: int = 20) -> CleanupResult:
        result = CleanupResult()
        try:
            if type(limit) is not int or not 1 <= limit <= 100:
                raise SandboxError("invalid_limit", "Cleanup batch limit must be between 1 and 100.")
            with self.registry.transaction() as data:
                expired = [key for key, entry in data.entries.items() if entry.expires_at <= self.clock()]
                result.remaining_expired = len(expired)
                if not expired:
                    return result
                with self.client_factory() as client:
                    for sandbox_id in expired[:limit]:
                        result.examined += 1
                        try:
                            self._destroy(client, data, sandbox_id)
                            result.destroyed += 1
                        except HANDLED as exc:
                            result.errors.append(error_result(exc))
                    result.remaining_expired -= result.destroyed
        except HANDLED as exc:
            result.errors.append(error_result(exc))
        return result
