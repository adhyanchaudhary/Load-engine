"""Session-tagged resource cleanup uses the existing private registry and manager."""
from guardrails.diagnostics.io import ArtifactError


def cleanup(manager,session_id):
    with manager.registry.transaction() as data:
        ids=[sid for sid,entry in data.entries.items() if entry.repair_session_id==session_id]
    remaining=[]
    for sid in ids[:20]:
        with manager.registry.transaction() as data:
            entry=data.entries.get(sid)
            if entry is None:continue
            if entry.repair_session_id!=session_id:raise ArtifactError('Cross-session cleanup refused')
        if not manager.destroy(sid).ok:remaining.append(sid)
    return remaining
