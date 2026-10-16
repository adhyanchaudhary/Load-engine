"""Mandatory create options, inspection checks and untrusted log handling."""
import re
import unicodedata

from docker.types import LogConfig

from guardrails.docker.models import ApprovedImage, RegistryEntry, SandboxConfig, SandboxLogs

LOG_LIMIT_BYTES = 16 * 1024
LABEL_PREFIX = "io.ai-performance-guardrails"


def ownership_labels(entry: RegistryEntry) -> dict[str, str]:
    return {
        f"{LABEL_PREFIX}.managed": "true",
        f"{LABEL_PREFIX}.owner": entry.owner,
        f"{LABEL_PREFIX}.sandbox": entry.sandbox_id,
        f"{LABEL_PREFIX}.nonce": entry.nonce,
    }


def create_options(config: SandboxConfig, approved: ApprovedImage, entry: RegistryEntry) -> dict:
    memory = config.memory_mb * 1024**2
    return dict(
        image=approved.image_id, entrypoint=approved.entrypoint, command=approved.command,
        user=approved.user, labels=ownership_labels(entry),
        privileged=False, cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
        read_only=True, network_disabled=entry.network_name is None, network_mode=entry.network_name or "none",
        use_config_proxy=False, environment={},
        pids_limit=config.pids_limit, mem_limit=memory, memswap_limit=memory,
        nano_cpus=round(config.cpus * 1_000_000_000), init=True,
        restart_policy={"Name": "no"},
        volumes={}, mounts=[], ports={}, publish_all_ports=False,
        log_config=LogConfig(type="local", config={"max-size": "1m", "max-file": "1", "compress": "false"}),
    )


def isolation_discrepancies(attrs: dict, entry: RegistryEntry) -> list[str]:
    """Compare effective daemon config; absent mandatory properties fail closed."""
    host = attrs.get("HostConfig", {})
    cfg = attrs.get("Config", {})
    expected = entry.config
    checks = {
        "CPU limit": host.get("NanoCpus") == round(expected.cpus * 1_000_000_000),
        "memory limit": host.get("Memory") == expected.memory_mb * 1024**2,
        "swap limit": host.get("MemorySwap") == expected.memory_mb * 1024**2,
        "PID limit": host.get("PidsLimit") == expected.pids_limit,
        "unprivileged": host.get("Privileged") is False,
        "capability drop": "ALL" in (host.get("CapDrop") or []) and not host.get("CapAdd"),
        "no new privileges": "no-new-privileges:true" in (host.get("SecurityOpt") or []),
        "read-only filesystem": host.get("ReadonlyRootfs") is True,
        "network isolation": (
            host.get("NetworkMode") in (entry.network_name, entry.network_id)
            and cfg.get("NetworkDisabled", False) is False
            and set(attrs.get("NetworkSettings", {}).get("Networks", {})) == {entry.network_name}
        ) if entry.network_name else (host.get("NetworkMode") == "none" and cfg.get("NetworkDisabled") is True),
        "init": host.get("Init") is True,
        "restart disabled": host.get("RestartPolicy", {}).get("Name") == "no",
        "non-root identity": cfg.get("User") == entry.approved.user,
        "no mounts": not any((host.get("Binds"), host.get("Mounts"), host.get("VolumesFrom"),
                                   host.get("Tmpfs"), attrs.get("Mounts"), cfg.get("Volumes"))),
        "no published ports": not host.get("PortBindings") and not host.get("PublishAllPorts"),
        "no devices": not host.get("Devices") and not host.get("DeviceRequests"),
        "approved image": attrs.get("Image") == entry.approved.image_id,
        "approved entrypoint": cfg.get("Entrypoint") == entry.approved.entrypoint,
        "approved command": (cfg.get("Cmd") or []) == entry.approved.command,
        "bounded log storage": host.get("LogConfig", {}).get("Type") == "local"
            and host.get("LogConfig", {}).get("Config", {}).get("max-size") == "1m"
            and host.get("LogConfig", {}).get("Config", {}).get("max-file") == "1",
    }
    return [name for name, valid in checks.items() if not valid]


def sanitize_logs(raw: bytes, truncated: bool) -> SandboxLogs:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
                  "[REDACTED PRIVATE KEY]", text, flags=re.S)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
    text = re.sub(
        r'''(?ix)(["']?\b(?:[a-z0-9_]*[_-])?(?:api[_-]?key|token|secret|password|passwd|authorization|credential)["']?\s*[:=]\s*)(?:"[^"\n]*(?:"|$)|'[^'\n]*(?:'|$)|[^\s,;]+)''',
        r"\1[REDACTED]", text,
    )
    text = re.sub(r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})\b", "[REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    encoded = text.encode("utf-8")
    truncated = truncated or len(encoded) > LOG_LIMIT_BYTES
    return SandboxLogs(text=encoded[:LOG_LIMIT_BYTES].decode("utf-8", errors="ignore"), truncated=truncated)
