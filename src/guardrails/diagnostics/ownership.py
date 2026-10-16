"""Historical ownership receipts survive app cleanup. No Docker calls."""
from uuid import UUID
from guardrails.diagnostics.io import ArtifactError, read_json


def current_owner(state_dir):
    data = read_json(state_dir / 'registry.json')
    try:
        return str(UUID(data['owner']))
    except (KeyError, TypeError, ValueError):
        raise ArtifactError('Private registry ownership is unavailable or invalid') from None


def owned_receipt(store, test_id):
    test_id = str(UUID(test_id))
    receipt = read_json(store._path(test_id, '.ownership.json'),8192)
    owner = current_owner(store.directory.parent)
    if (not isinstance(receipt,dict) or receipt.get('schema_version') != 1 or
        receipt.get('test_id') != test_id or receipt.get('owner_id') != owner or
        receipt.get('provenance') not in ('managed_benchmark','operator_attested_legacy')):
        raise ArtifactError('Test is not owned by this installation')
    try:UUID(receipt['sandbox_id'])
    except (KeyError, ValueError, TypeError):raise ArtifactError('Invalid ownership receipt') from None
    return receipt
