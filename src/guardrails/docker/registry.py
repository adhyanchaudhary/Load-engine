"""Atomic, private JSON registry with cross-process locking (Linux/macOS/Windows)."""
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock

from guardrails.docker.models import RegistryData


class Registry:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "registry.json"

    @contextmanager
    def transaction(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.directory / "registry.lock"), timeout=5):
            if self.path.exists():
                data = RegistryData.model_validate_json(self.path.read_bytes())
                UUID(data.owner)
                for key, entry in data.entries.items():
                    if key != entry.sandbox_id or entry.owner != data.owner:
                        raise ValueError("Invalid registry ownership")
                    UUID(key)
                    UUID(entry.nonce)
                    if entry.created_at.tzinfo is None or entry.expires_at.tzinfo is None:
                        raise ValueError("Registry timestamps must be timezone aware")
            else:
                data = RegistryData(owner=str(uuid4()))
            yield data

    def save(self, data: RegistryData) -> None:
        """Caller must hold transaction lock. Persist intent before Docker calls."""
        fd, name = tempfile.mkstemp(prefix="registry-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(data.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
