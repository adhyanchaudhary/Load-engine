"""Explicit operator attestation for legacy local M3 records without receipts."""
import argparse
from uuid import UUID
from guardrails.tools.sandbox import get_manager
from guardrails.load_testing.models import TestRecord
from guardrails.load_testing.storage import ResultStore
from guardrails.diagnostics.io import read_json
from guardrails.diagnostics.ownership import current_owner


def main():
    parser=argparse.ArgumentParser(description='Attest provenance of a reviewed local legacy benchmark; no container/source changes')
    parser.add_argument('action',choices=['adopt-legacy'])
    parser.add_argument('test_id')
    args=parser.parse_args()
    store=ResultStore(get_manager().registry.directory)
    test_id=str(UUID(args.test_id))
    record=TestRecord.model_validate(read_json(store._path(test_id,'.json')))
    owner=current_owner(store.directory.parent)
    if record.test_id!=test_id or record.configuration.sandbox_id!=record.sandbox_id or record.owner_id not in (None,owner):
        raise ValueError('Record ownership or identity mismatch')
    if record.owner_id is not None:raise ValueError('Only legacy records without ownership can be attested')
    store.register_owned(test_id,record.sandbox_id,owner,provenance='operator_attested_legacy')
    print('Legacy provenance attested. Historical telemetry gaps remain explicit.')


if __name__=='__main__':main()
