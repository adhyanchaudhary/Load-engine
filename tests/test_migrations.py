import hashlib
import json
from pathlib import Path
from guardrails.persistence.migrations import import_artifacts
from guardrails.persistence.indexer import ArtifactIndexer
from guardrails.persistence.repositories.history import HistoryRepository
from guardrails.diagnostics.models import DiagnosticInput
from diagnostic_fixtures import artifacts
from repair_fixtures import repair,registered,fake_verification


def test_import_idempotency_preserves_json_and_missing_values(artifacts):
    r=artifacts.make();r.metrics.p99_ms=None;artifacts.store.save(r)
    path=artifacts.store._path(r.test_id,'.json');before=path.read_bytes()
    first=import_artifacts(artifacts.path);second=import_artifacts(artifacts.path)
    assert second['imported']==0 and second['unchanged']>=1
    history=HistoryRepository(ArtifactIndexer(artifacts.path).db)
    assert history.benchmark(r.test_id)['p99_ms'] is None
    assert path.read_bytes()==before
    assert len(history.samples(r.test_id))==5


def test_legacy_unverified_history_is_labeled_not_fabricated(artifacts):
    r=artifacts.make();r.owner_id=None;artifacts.store.save(r)
    artifacts.store._path(r.test_id,'.ownership.json').unlink()
    import_artifacts(artifacts.path)
    row=HistoryRepository(ArtifactIndexer(artifacts.path).db).benchmark(r.test_id)
    assert row['provenance']=='legacy_unverified' and row['request_count']==100


def test_malformed_import_rolls_back_and_records_outcome(artifacts):
    r=artifacts.make();path=artifacts.store._path(r.test_id,'.json');path.write_text('{broken')
    outcome=import_artifacts(artifacts.path)
    assert outcome['errors']==1 and path.read_text()=='{broken'
    db=ArtifactIndexer(artifacts.path).db
    assert db.rows("SELECT status FROM artifact_imports WHERE source=?",('performance/'+r.test_id+'.json',))[0]['status']=='ERROR'


def test_diagnostics_and_repair_relations_persist(repair,monkeypatch):
    session,candidate=registered(repair);fake_verification(repair,monkeypatch)
    report=repair.manager.verify(session.session_id,candidate.candidate_id)
    outcome=import_artifacts(repair.artifacts.path)
    history=HistoryRepository(ArtifactIndexer(repair.artifacts.path).db)
    assert history.diagnostics() and history.repairs()
    assert history.candidates(session.session_id)[0]['verification_status']=='VERIFIED'
    assert history.verifications(candidate.candidate_id)[0]['verdict']=='VERIFIED'
    assert history.candidates(session.session_id)[0]['improvement_percent']==40


def test_owner_scope_and_foreign_receipt_rejected(artifacts):
    from uuid import uuid4
    r=artifacts.make();path=artifacts.store._path(r.test_id,'.ownership.json');data=json.loads(path.read_text());data['owner_id']=str(uuid4());path.write_text(json.dumps(data))
    outcome=import_artifacts(artifacts.path)
    # Existing index provenance must be rechecked when source receipt changes.
    try:ArtifactIndexer(artifacts.path).artifact(artifacts.store._path(r.test_id,'.json'),force=True)
    except ValueError:pass
    else:raise AssertionError('Foreign receipt accepted')
