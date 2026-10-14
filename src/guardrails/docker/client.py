"""Lazy Docker SDK client; no connection at module import and no image pulls."""
from collections.abc import Iterator
from contextlib import contextmanager

import docker
from docker.client import DockerClient


@contextmanager
def connect() -> Iterator[DockerClient]:
    # Honor the trusted operator's DOCKER_HOST/TLS settings. Bound API waits.
    client = docker.from_env(timeout=5)
    try:
        yield client
    finally:
        client.close()


def support(info: dict) -> dict[str, bool]:
    return {
        "linux_containers": info.get("OSType") == "linux",
        "cpu": info.get("CpuCfsQuota") is True and info.get("CpuCfsPeriod") is True,
        "memory": info.get("MemoryLimit") is True,
        "swap": info.get("SwapLimit") is True,
        "pids": info.get("PidsLimit") is True,
    }
