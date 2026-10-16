"""Ownership-checked internal networks and durable ephemeral runner intents."""
from docker.errors import NotFound
from guardrails.docker.manager import SandboxError
from guardrails.docker.security import ownership_labels, LABEL_PREFIX


def labels(entry, role):
    return ownership_labels(entry) | {f"{LABEL_PREFIX}.role": role}


def verify_network(network, entry):
    network.reload()
    attrs = network.attrs
    if (attrs.get("Name") != entry.network_name or
        (entry.network_id is not None and network.id != entry.network_id) or
        any((attrs.get("Labels") or {}).get(k) != v for k, v in labels(entry, "network").items()) or
        attrs.get("Internal") is not True or attrs.get("Driver") != "bridge" or attrs.get("EnableIPv6")):
        raise SandboxError("network_isolation", "Network ownership or internal bridge isolation could not be verified")
    return network


def ensure_network(manager, client, data, entry):
    try:
        network = client.networks.get(entry.network_id or entry.network_name)
    except NotFound:
        if entry.network_id:
            raise SandboxError("network_missing", "Owned test network disappeared; recreate the sandbox") from None
        network = client.networks.create(entry.network_name, driver="bridge", internal=True,
            enable_ipv6=False, labels=labels(entry, "network"), check_duplicate=True,
            options={"com.docker.network.bridge.enable_icc": "true"})
    verify_network(network, entry)
    entry.network_id = network.id
    manager.registry.save(data)
    return network


def verify_membership(client, entry, app, runner=None):
    network = verify_network(client.networks.get(entry.network_id), entry)
    app.reload()
    allowed = {app.id}
    if runner is not None:
        runner.reload()
        allowed.add(runner.id)
    members = set((network.attrs.get("Containers") or {}).keys())
    runner_running = runner is not None and runner.attrs.get("State", {}).get("Status") not in ("exited", "dead")
    if members - allowed or app.id not in members or (runner_running and runner.id not in members):
        raise SandboxError("network_membership", "Unexpected or missing container on the owned test network")
    ip = None
    for container in (app, runner):
        if container is None or (container is runner and not runner_running):
            continue
        nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        if set(nets) != {entry.network_name} or nets[entry.network_name].get("NetworkID") != entry.network_id:
            raise SandboxError("network_membership", "Container is attached to an unexpected network")
        if container is app:
            import ipaddress
            ip = str(ipaddress.IPv4Address(nets[entry.network_name]["IPAddress"]))
    return ip


def runner_labels(entry):
    return labels(entry, "runner") | {f"{LABEL_PREFIX}.run": entry.runner_nonce}


def verify_runner(container, entry):
    container.reload()
    if ((entry.runner_id is not None and container.id != entry.runner_id) or
        any((container.attrs.get("Config", {}).get("Labels") or {}).get(k) != v
            for k, v in runner_labels(entry).items())):
        raise SandboxError("ownership_mismatch", "Runner ownership did not match durable intent")


def cleanup_runner(manager, client, data, entry):
    if not entry.runner_name:
        return
    try:
        container = client.containers.get(entry.runner_id or entry.runner_name)
        verify_runner(container, entry)
        container.remove(force=True, v=False)
    except NotFound:
        pass
    entry.runner_name = entry.runner_id = entry.runner_nonce = None
    manager.registry.save(data)


def cleanup_resources(manager, client, data, entry):
    cleanup_runner(manager, client, data, entry)
    if entry.network_name:
        try:
            network = verify_network(client.networks.get(entry.network_id or entry.network_name), entry)
            if network.attrs.get("Containers"):
                raise SandboxError("network_membership", "Owned network still has members; refusing to detach or remove unrelated resources")
            network.remove()
        except NotFound:
            pass
