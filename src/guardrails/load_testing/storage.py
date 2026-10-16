"""Atomic private records; callers can select generated UUIDs only."""
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID
from guardrails.load_testing.models import TestRecord


class ResultStore:
    def __init__(self, state_dir: Path):
        self.directory = state_dir / "performance"

    def _path(self, test_id, suffix):
        return self.directory / (str(UUID(test_id)) + suffix)

    def write(self, test_id, suffix, content: bytes):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._path(test_id, suffix))
            from guardrails.persistence.indexer import index_write
            index_write(self._path(test_id, suffix))
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def register_owned(self, test_id, sandbox_id, owner_id, *, provenance='managed_benchmark'):
        receipt = dict(schema_version=1, test_id=str(UUID(test_id)), sandbox_id=str(UUID(sandbox_id)),
                       owner_id=str(UUID(owner_id)), provenance=provenance)
        self.write(test_id, '.ownership.json', json.dumps(receipt).encode())

    def save(self, record):
        self.write(record.test_id, ".json", record.model_dump_json(indent=2).encode())

    def raw(self, test_id, content):
        self.write(test_id, ".summary.json", content)

    def get(self, test_id):
        record = TestRecord.model_validate(json.loads(self._path(test_id, ".json").read_bytes()))
        if record.status == "running":
            record.status = "interrupted"
            record.errors.append("Test has no completed record; it may still be active or was interrupted")
        return record
