"""Persist historical provenance and bounded logs during the original benchmark."""
import hashlib
import json
import time
from datetime import datetime, timezone
from guardrails.load_testing.models import RunMetadata, HistoricalLogs
from guardrails.diagnostics.security import redact
from guardrails.docker.security import sanitize_logs


def capture_metadata(entry, policy, info):
    digest = lambda obj: hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()
    a = entry.approved
    architecture = info.get('Architecture')
    environment = {k:info.get(k) for k in ('ID', 'Architecture', 'OSType', 'ServerVersion', 'NCPU', 'MemTotal')}
    return RunMetadata(application_identity=a.application_identity or entry.config.image,
        application_version=a.application_version, commit_identifier=a.commit_identifier, dataset_id=a.dataset_id,
        application_configuration_fingerprint=digest({'entrypoint':a.entrypoint,'command':a.command,'port':a.application_port}),
        architecture=architecture if isinstance(architecture,str) else None,
        environment_fingerprint=digest(environment) if info.get('ID') and architecture else None,
        application_cpus=entry.config.cpus, application_memory_bytes=entry.config.memory_mb*1024**2,
        runner_cpus=policy.cpus, runner_memory_bytes=policy.memory_mb*1024**2,
        runner_pids_limit=policy.pids_limit, runner_image_id=policy.image.image_id,
        application_image_id=a.image_id)


def capture_logs(container, record):
    end = datetime.now(timezone.utc)
    result = HistoricalLogs(collected_at=end, since=record.started_at, until=end)
    stream = None
    try:
        stream = container.logs(stdout=True, stderr=True, timestamps=True, stream=True,
            follow=False, since=record.started_at, until=end, tail=200)
        data = bytearray()
        deadline = time.monotonic()+5
        for chunk in stream:
            data.extend(chunk[:16385-len(data)])
            if len(data)>16384 or time.monotonic()>deadline:
                result.truncated=True
                break
        clean = sanitize_logs(bytes(data[:16384]),result.truncated)
        result.text=redact(clean.text)
        result.truncated=clean.truncated
    except Exception:
        result.error='Historical application logs unavailable during benchmark collection'
    finally:
        if stream is not None and hasattr(stream,'close'):stream.close()
    record.historical_logs=result
