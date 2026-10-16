"""Bounded local k6 execution using existing ownership and Docker lifecycle logic."""
import time
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from filelock import FileLock, Timeout
from guardrails.docker.manager import SandboxError, error_result
from guardrails.docker.models import RegistryEntry, SandboxConfig
from guardrails.docker.security import create_options, isolation_discrepancies
from guardrails.docker.networking import (
    verify_membership, cleanup_runner, runner_labels, verify_runner,
)
from guardrails.load_testing.models import PerformanceTestConfig, TestRecord, EnvironmentResult
from guardrails.load_testing.script_generator import generate_script
from guardrails.load_testing.parser import parse_summary, MAX_SUMMARY_BYTES
from guardrails.load_testing.thresholds import evaluate
from guardrails.load_testing.storage import ResultStore
from guardrails.load_testing.metrics import sample, sample_runner
from guardrails.load_testing.telemetry import capture_metadata, capture_logs

STARTUP_SECONDS = 10
SUMMARY_SECONDS = 5
CLEANUP_SECONDS = 10


def utcnow():
    return datetime.now(timezone.utc)


class LoadEngine:
    def __init__(self, manager, *, monotonic=time.monotonic, pause=time.sleep, cancelled=None):
        self.manager = manager
        self.store = ResultStore(manager.registry.directory)
        self.monotonic = monotonic
        self.pause = pause
        self.cancelled = cancelled

    @contextmanager
    def admission(self, sandbox_id):
        sandbox_id = str(UUID(sandbox_id))
        directory = self.manager.registry.directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        policy = self.manager._load_policy().performance
        if policy is None:
            raise SandboxError("k6_not_approved", "Operator must approve a pinned local k6 image in performance policy")
        with FileLock(str(directory / f"test-{sandbox_id}.lock"), timeout=0):
            slot = None
            try:
                for i in range(policy.concurrency):
                    candidate = FileLock(str(directory / f"load-slot-{i}.lock"), timeout=0)
                    try:
                        candidate.acquire()
                        slot = candidate
                        break
                    except Timeout:
                        continue
                if slot is None:
                    raise SandboxError("tests_busy", "Global benchmark concurrency limit reached")
                yield policy
            finally:
                if slot:
                    slot.release()

    def resolve(self, client, data, sandbox_id):
        entry, app = self.manager._resolve(client, data, sandbox_id)
        self.manager._require_unexpired(entry)
        if not entry.network_id or not entry.approved.application_port:
            raise SandboxError("not_test_enabled", "Create an application from an approved image with application_port; old network-disabled sandboxes cannot be attached")
        discrepancies = isolation_discrepancies(app.attrs, entry)
        if discrepancies:
            raise SandboxError("isolation_unverified", "Application isolation changed: " + ", ".join(discrepancies))
        return entry, app

    def _runner(self, client, entry, policy, script, ip):
        # Local inspection only. Never images.run(), pull(), exec(), or a shell.
        image = client.images.get(policy.image.image_id)
        if image.id != policy.image.image_id or image.attrs.get("Config", {}).get("Volumes"):
            raise SandboxError("k6_image_invalid", "Approved local k6 image mismatched or declares writable volumes")
        config = SandboxConfig(image="k6", cpus=policy.cpus, memory_mb=policy.memory_mb,
                               pids_limit=policy.pids_limit)
        command = ["run", "--quiet", "--no-usage-report", "-"]
        approved = policy.image.model_copy(update={"command": command})
        intent = RegistryEntry(sandbox_id=entry.sandbox_id, created_at=entry.created_at,
            expires_at=entry.expires_at, owner=entry.owner, nonce=entry.nonce,
            config=config, approved=approved, network_name=entry.network_name, network_id=entry.network_id)
        opts = create_options(config, approved, intent)
        opts.update(name=entry.runner_name, labels=runner_labels(entry), stdin_open=True,
            environment={"APP_IP": ip, "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "",
                         "http_proxy": "", "https_proxy": "", "all_proxy": "", "NO_PROXY": "*",
                         "K6_NO_USAGE_REPORT": "true"},
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=8m,mode=1777"})
        runner = client.containers.create(**opts)
        return runner, intent

    def _execute(self, client, entry, app, policy, config, record=None, probe=False):
        manager = self.manager
        if self.cancelled and self.cancelled.is_set():
            raise SandboxError("test_cancelled", "MCP request cancelled before execution")
        with manager.registry.transaction() as data:
            if data.entries[entry.sandbox_id].runner_name:
                cleanup_runner(manager, client, data, data.entries[entry.sandbox_id])
        ip = verify_membership(client, entry, app)
        script = generate_script(config, entry.approved.application_port, probe=probe)
        with manager.registry.transaction() as data:
            current = data.entries[entry.sandbox_id]
            if current.runner_name:
                cleanup_runner(manager, client, data, current)
            current.runner_nonce = str(uuid4())
            current.runner_name = "guardrails-k6-" + current.runner_nonce
            current.runner_id = None
            manager.registry.save(data)
            entry = current.model_copy(deep=True)
        raw = None
        exit_code = None
        oom = None
        try:
            # k6 reads a trusted script from stdin; no host mounts or shell.
            startup_deadline = self.monotonic() + STARTUP_SECONDS
            runner, intent = self._runner(client, entry, policy, script, ip)
            entry.runner_id = runner.id
            with manager.registry.transaction() as data:
                data.entries[entry.sandbox_id].runner_id = runner.id
                manager.registry.save(data)
            verify_runner(runner, entry)
            self._verify_runner_isolation(runner, intent)
            # k6 supports '-' as the script file. Attach before start, close stdin
            # after the deterministic script. Summary is a framed stdout record.
            sock = runner.attach_socket(params={"stdin": 1, "stream": 1})
            try:
                if record:
                    if not sample(app, record.application):
                        raise SandboxError("application_unavailable", "Application unavailable before benchmark")
                runner.start()
                verify_runner(runner, entry)
                verify_membership(client, entry, app, runner)
                self._verify_runner_isolation(runner, intent)
                transport = getattr(sock, "_sock", sock)
                transport.settimeout(STARTUP_SECONDS)
                transport.sendall(script.encode())
                import socket
                transport.shutdown(socket.SHUT_WR)
            finally:
                sock.close()
            if self.monotonic() > startup_deadline:
                raise SandboxError("startup_timeout", "k6 startup grace expired")
            verify_runner(runner, entry)
            verify_membership(client, entry, app, runner)
            deadline = self.monotonic() + (1 if probe else config.duration_seconds) + 7
            next_sample = self.monotonic()
            while True:
                if self.cancelled and self.cancelled.is_set():
                    raise SandboxError("test_cancelled", "MCP request cancelled; benchmark stopped without retry")
                self.manager._require_unexpired(entry)
                verify_runner(runner, entry)
                state = runner.attrs.get("State", {})
                if self.monotonic() >= next_sample:
                    if record:
                        sample_runner(runner, record)
                    manager._verify_owner(app, entry)
                    running = sample(app, record.application) if record else app.attrs.get("State", {}).get("Status") == "running"
                    if not running:
                        raise SandboxError("application_unavailable", "Application stopped during the test")
                    verify_membership(client, entry, app, runner)
                    next_sample = self.monotonic() + 1
                if state.get("Status") in ("exited", "dead"):
                    exit_code, oom = state.get("ExitCode"), state.get("OOMKilled")
                    break
                if self.monotonic() > deadline:
                    raise SandboxError("test_timeout", "k6 exceeded its execution deadline; no automatic retry")
                self.pause(0.1)
            if record:
                manager._verify_owner(app, entry)
                if not sample(app, record.application):
                    raise SandboxError("application_unavailable", "Application stopped during the test")
            raw = self._summary(runner)
            return raw, exit_code, oom
        finally:
            if record:
                try:
                    manager._verify_owner(app, entry)
                    sample(app, record.application)
                except Exception:
                    record.application.errors.append("Final application resource state unavailable")
            if record:
                try:
                    manager._verify_owner(app, entry)
                    capture_logs(app, record)
                except Exception:
                    record.application.errors.append("Historical logs could not be collected from the owned application")
            if record and 'runner' in locals():
                try:
                    verify_runner(runner, entry)
                    record.runner_oom_killed = runner.attrs.get("State", {}).get("OOMKilled")
                    record.runner_exit_code = runner.attrs.get("State", {}).get("ExitCode")
                except Exception:
                    record.errors.append("Runner final state unavailable")
            try:
                cleanup_deadline = self.monotonic() + CLEANUP_SECONDS
                previous_timeout = client.api.timeout
                client.api.timeout = 2
                with manager.registry.transaction() as data:
                    current = data.entries.get(entry.sandbox_id)
                    if current and current.runner_nonce == entry.runner_nonce:
                        cleanup_runner(manager, client, data, current)
                if self.monotonic() > cleanup_deadline:
                    raise SandboxError("cleanup_timeout", "Runner cleanup exceeded its time budget")
            except Exception:
                if record:
                    record.errors.append("Runner cleanup incomplete; durable intent retained for cleanup")
                else:
                    raise
            finally:
                if 'previous_timeout' in locals():
                    client.api.timeout = previous_timeout

    @staticmethod
    def _verify_runner_isolation(runner, intent):
        mismatches = [x for x in isolation_discrepancies(runner.attrs, intent) if x != "no mounts"]
        host = runner.attrs.get("HostConfig", {})
        mounts = runner.attrs.get("Mounts") or []
        if (mismatches or host.get("Binds") or host.get("Mounts") or host.get("VolumesFrom") or
            set(host.get("Tmpfs", {})) != {"/tmp"} or
            any(m.get("Type") != "tmpfs" or m.get("Destination") != "/tmp" for m in mounts) or
            "size=8m" not in host.get("Tmpfs", {}).get("/tmp", "")):
            raise SandboxError("runner_isolation", "Runner resource/security isolation could not be verified")

    def _summary(self, runner):
        deadline = self.monotonic() + SUMMARY_SECONDS
        chunks = runner.logs(stdout=True, stderr=False, stream=True, follow=False)
        output = bytearray()
        try:
            for chunk in chunks:
                if len(output) + len(chunk) > MAX_SUMMARY_BYTES or self.monotonic() > deadline:
                    raise SandboxError("invalid_summary", "Summary output exceeded size/time bound")
                output.extend(chunk)
        finally:
            if hasattr(chunks, "close"):
                chunks.close()
        marker = b"GUARDRAILS_SUMMARY="
        summaries = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
        if len(summaries) != 1:
            raise SandboxError("invalid_summary", "Exactly one end-of-test summary is required")
        return summaries[0]

    def prepare(self, sandbox_id):
        result = EnvironmentResult(sandbox_id=sandbox_id)
        try:
            with self.admission(sandbox_id) as policy, self.manager.client_factory() as client:
                with self.manager.registry.transaction() as data:
                    entry, app = self.resolve(client, data, sandbox_id)
                    if app.attrs.get("State", {}).get("Status") != "running":
                        app.start()
                    self.manager._verify_owner(app, entry)
                    cleanup_runner(self.manager, client, data, entry)
                    verify_membership(client, entry, app)
                    entry.prepared = False
                    self.manager.registry.save(data)
                result.environment_id = entry.network_id
                result.network_status = "verified_internal"
                result.application_status = app.attrs.get("State", {}).get("Status", "unknown")
                raw, exit_code, oom = self._execute(client, entry, app, policy,
                    PerformanceTestConfig(sandbox_id=sandbox_id, endpoint_path="/", duration_seconds=1), probe=True)
                metrics = parse_summary(raw)
                if exit_code not in (0, 99) or oom or not metrics.request_count or json.loads(raw).get("metrics", {}).get("checks", {}).get("values", {}).get("rate") != 1:
                    raise SandboxError("application_unavailable", "HTTP readiness probe at / failed; running is not application readiness")
                with self.manager.registry.transaction() as data:
                    current, _ = self.resolve(client, data, sandbox_id)
                    current.prepared = True
                    self.manager.registry.save(data)
                result.readiness_status = "ready"
        except Exception as exc:
            result.errors.append("A test or preparation is already active" if isinstance(exc, Timeout) else error_result(exc).message)
        return result

    def run(self, config):
        record = TestRecord(test_id=str(uuid4()), sandbox_id=config.sandbox_id,
                            configuration=config, started_at=utcnow())
        try:
            with self.admission(config.sandbox_id) as policy, self.manager.client_factory() as client:
                self.store.save(record)
                with self.manager.registry.transaction() as data:
                    entry, app = self.resolve(client, data, config.sandbox_id)
                    record.owner_id = entry.owner
                    record.metadata = capture_metadata(entry, policy, client.info())
                    self.store.register_owned(record.test_id, entry.sandbox_id, entry.owner)
                    if not entry.prepared:
                        raise SandboxError("not_prepared", "Call prepare_test_environment successfully first")
                    if (entry.expires_at - self.manager.clock()).total_seconds() < config.duration_seconds + 20:
                        raise SandboxError("insufficient_lifetime", "Sandbox expires too soon; create one with a longer timeout")
                raw, exit_code, oom = self._execute(client, entry, app, policy, config, record)
                self.store.raw(record.test_id, raw)
                record.metrics = parse_summary(raw)
                record.runner_exit_code, record.runner_oom_killed = exit_code, oom
                if exit_code == 99 and not any(v is False for v in record.metrics.thresholds.values()):
                    record.errors.append("k6 reported threshold failure without a valid failed threshold in the summary")
                if exit_code not in (0, 99):
                    record.errors.append("k6 execution failed")
                if oom or exit_code in (137, 143):
                    record.errors.append("Runner was killed or reached a resource limit")
                record.status = "completed" if exit_code in (0, 99) and not oom else "failed"
        except BaseException as exc:
            record.status = "cancelled" if (not isinstance(exc, Exception) or
                isinstance(exc, SandboxError) and exc.error.code == "test_cancelled") else "failed"
            record.errors.append("A test or preparation is already active" if isinstance(exc, Timeout) else error_result(exc).message)
            if not isinstance(exc, Exception):
                raise
        finally:
            record.ended_at = utcnow()
            record.budget = evaluate(record)
            self.store.save(record)
        return record
