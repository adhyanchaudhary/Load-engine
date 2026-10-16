import json
from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import ValidationError
from guardrails.diagnostics.models import DiagnosticInput
from guardrails.diagnostics.io import ArtifactError
from diagnostic_fixtures import artifacts


def test_input_and_uuid_validation():
    value=DiagnosticInput(test_id=str(uuid4()))
    assert value.include_logs and value.include_resource_metrics and value.include_traces
    with pytest.raises(ValueError):DiagnosticInput(test_id='../registry')


def test_historical_collection_never_uses_docker(artifacts,monkeypatch):
    r=artifacts.make('cpu')
    import docker
    monkeypatch.setattr(docker,'from_env',lambda *a,**kw:pytest.fail('Diagnostics must not contact Docker'))
    c=artifacts.service.collector.collect(DiagnosticInput(test_id=r.test_id))
    assert c.raw_summary_valid and c.record.application.samples
    assert any('logs unavailable' in x for x in c.missing)
    assert c.record.metadata.application_cpus==.5


@pytest.mark.parametrize('mutation',['owner','test_id','sandbox','config_sandbox'])
def test_ownership_metadata_rejection(artifacts,mutation):
    r=artifacts.make()
    if mutation=='owner':r.owner_id=str(uuid4())
    if mutation=='test_id':r.test_id=str(uuid4())
    if mutation=='sandbox':r.sandbox_id=str(uuid4())
    if mutation=='config_sandbox':r.configuration.sandbox_id=str(uuid4())
    original=next(artifacts.store.directory.glob('*.ownership.json')).name.split('.')[0]
    artifacts.store.write(original,'.json',r.model_dump_json().encode())
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(original)


def test_unmanaged_id_and_missing_data(artifacts):
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(str(uuid4()))
    r=artifacts.make();artifacts.store._path(r.test_id,'.json').unlink()
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(r.test_id)


@pytest.mark.parametrize('raw',[b'{',b'[]',b'{"schema_version":99}',b'{"metrics":NaN}'])
def test_malformed_records(artifacts,raw):
    r=artifacts.make();artifacts.store.write(r.test_id,'.json',raw)
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(r.test_id)


def test_bounded_and_symlink_reads(artifacts,tmp_path):
    r=artifacts.make();path=artifacts.store._path(r.test_id,'.json')
    path.write_bytes(b' '* (2*1024*1024+1))
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(r.test_id)
    path.unlink();path.symlink_to(tmp_path/'outside.json')
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(r.test_id)


def test_missing_optional_artifacts_and_include_flags(artifacts):
    r=artifacts.make('ambiguous');artifacts.store._path(r.test_id,'.summary.json').unlink()
    c=artifacts.service.collector.collect(DiagnosticInput(test_id=r.test_id))
    assert not c.raw_summary_valid
    assert any('resource samples' in x for x in c.missing)
    assert any('environment' in x for x in c.missing)
    c=artifacts.service.collector.collect(DiagnosticInput(test_id=r.test_id,include_logs=False,include_traces=False,include_resource_metrics=False))
    assert not c.traces and not c.log_events and not c.record.application.samples


def test_legacy_requires_explicit_attestation(artifacts):
    r=artifacts.make();r.owner_id=None;artifacts.store.save(r)
    artifacts.store._path(r.test_id,'.ownership.json').unlink()
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(r.test_id)
    artifacts.store.register_owned(r.test_id,r.sandbox_id,artifacts.owner,provenance='operator_attested_legacy')
    c=artifacts.service.collector.collect(DiagnosticInput(test_id=r.test_id))
    assert any('operator attested' in x for x in c.missing)


@pytest.mark.parametrize('value',[-1,float('inf'),1.1])
def test_invalid_normalized_metrics_rejected(artifacts,value):
    r=artifacts.make();r.metrics.http_failure_rate=value
    artifacts.store.write(r.test_id,'.json',json.dumps(r.model_dump(mode='json')).encode())
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(r.test_id)


def test_registry_owner_change_rejects_prior_receipts(artifacts):
    r=artifacts.make();path=artifacts.path/'registry.json';data=json.loads(path.read_text());data['owner']=str(uuid4());path.write_text(json.dumps(data))
    with pytest.raises(ArtifactError):artifacts.service.collector.load_record(r.test_id)
