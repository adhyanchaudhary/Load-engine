from uuid import uuid4
import pytest
from guardrails.repair.manager import RepairManager
from guardrails.repair.sessions import transition
from repair_fixtures import repair,registered


def test_session_creation_and_restart(repair):
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    assert s.status=='AWAITING_CANDIDATE' and s.maximum_attempts==3
    restored=RepairManager(repair.base,policy=repair.manager.policy())
    assert restored.status(s.session_id).session==s


@pytest.mark.parametrize('kind',['missing_report','project','revision','attempts'])
def test_invalid_session_inputs(repair,kind):
    report=repair.diagnostic.report_id;project=repair.project_id;attempts=3
    if kind=='missing_report':report=str(uuid4())
    if kind=='project':project=str(uuid4())
    if kind=='revision':repair.baseline.metadata.commit_identifier='0'*40;repair.artifacts.save(repair.baseline)
    if kind=='attempts':attempts=6
    with pytest.raises(ValueError):repair.manager.create(report,project,attempts)


def test_invalid_state_transition_and_active_limit(repair):
    s=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    with pytest.raises(ValueError):transition(s,'VERIFIED')
    repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    with pytest.raises(ValueError,match='limit'):repair.manager.create(repair.diagnostic.report_id,repair.project_id)


def test_global_verification_limit(repair):
    from filelock import FileLock
    from uuid import uuid4
    with repair.manager.admission(str(uuid4())):
        with pytest.raises(ValueError,match='Global'):
            with repair.manager.admission(str(uuid4())):pass
