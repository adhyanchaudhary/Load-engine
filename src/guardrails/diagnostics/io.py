"""Bounded private artifact reads. Reject symlinks and never expose path arguments."""
import json
import os
from pathlib import Path

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


class ArtifactError(ValueError):
    pass


def read_bytes(path: Path, limit=MAX_ARTIFACT_BYTES):
    try:
        if path.parent.is_symlink():
            raise ArtifactError('Symlink artifact directories are forbidden')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            import stat
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ArtifactError('Artifact must be a regular file')
            data = stream.read(limit+1)
        if len(data) > limit:
            raise ArtifactError('Artifact exceeds its size bound')
        return data
    except OSError:
        raise ArtifactError('Artifact is missing or cannot be safely read') from None


def read_json(path, limit=MAX_ARTIFACT_BYTES):
    try:
        return json.loads(read_bytes(path, limit), parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError, UnicodeError):
        raise ArtifactError('Artifact is missing, malformed, or exceeds its size bound') from None
