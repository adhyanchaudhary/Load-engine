import subprocess
from uuid import uuid4
import pytest
from guardrails.repair.candidates import CandidateResolver
from repair_fixtures import repair,registered,git_commit


def test_immutable_candidate_and_original_tree_preserved(repair):
    before=(repair.root/'app.py').read_bytes()
    session,candidate=registered(repair)
    assert candidate.source_revision==repair.revision and candidate.changed_files==['app.py']
    assert len(candidate.source_digest)==64 and candidate.parent_revision==repair.project.baseline_revision
    assert (repair.root/'app.py').read_bytes()==before
    assert subprocess.check_output(['git','status','--porcelain'],cwd=repair.root)==b''
    assert repair.manager.status(session.session_id).attempts_remaining==2


@pytest.mark.parametrize('revision',['main','../secret','--help','f'*40])
def test_foreign_or_mutable_revision_rejected(repair,revision):
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    with pytest.raises(ValueError):repair.manager.register(s.session_id,revision)


def test_identical_and_protected_changes_rejected(repair):
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    with pytest.raises(ValueError,match='differ'):repair.manager.register(s.session_id,repair.project.baseline_revision)
    (repair.root/'tests/test_app.py').write_text('malicious test change')
    revision=git_commit(repair.root,'changed trusted test')
    with pytest.raises(ValueError,match='protected'):repair.manager.register(s.session_id,revision)


def test_unrelated_history_and_dirty_repository(repair):
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    (repair.root/'app.py').write_text('uncommitted')
    with pytest.raises(ValueError,match='Commit or stash'):repair.manager.register(s.session_id,repair.revision)


def test_symlink_snapshot_rejected(repair):
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    (repair.root/'app.py').unlink();(repair.root/'app.py').symlink_to('/etc/passwd')
    revision=git_commit(repair.root,'unsafe symlink')
    with pytest.raises(ValueError,match='symlink'):repair.manager.register(s.session_id,revision)


def test_attempt_limit(repair):
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id,1)
    candidate=repair.manager.register(s.session_id,repair.revision)
    s=repair.manager.store.get(s.session_id,'session');s.status='REJECTED';repair.manager.store.save(s)
    c=repair.manager.store.get(candidate.candidate_id,'candidate');c.verification_status='REJECTED';repair.manager.store.save(c)
    (repair.root/'app.py').write_text('def value(): return 42 # next\n');revision=git_commit(repair.root,'next')
    with pytest.raises(ValueError,match='Maximum'):repair.manager.register(s.session_id,revision)


def test_real_foreign_history_is_rejected(repair,tmp_path):
    import shutil
    other=tmp_path/'foreign';other.mkdir();subprocess.run(['git','init','-q'],cwd=other,check=True)
    (other/'app.py').write_text('foreign history')
    foreign=git_commit(other,'foreign')
    # Even if the object is provisioned locally, unrelated history is not a repair candidate.
    subprocess.run(['git','fetch',str(other),foreign],cwd=repair.root,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    with pytest.raises(ValueError):repair.manager.register(s.session_id,foreign)
