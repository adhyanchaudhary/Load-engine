from unittest.mock import MagicMock
import pytest
from guardrails.repair.correctness import CorrectnessVerifier
from repair_fixtures import repair,registered


@pytest.mark.parametrize('code,text,status,tests',[(0,'Ran 3 tests in 0.1s\nOK','PASS',3),
    (1,'FAILED (failures=1)','FAIL',None),(0,'no test output','INCONCLUSIVE',None),
    (0,'Ran 0 tests','INCONCLUSIVE',0),(137,'','INCONCLUSIVE',None)])
def test_correctness_results(repair,monkeypatch,code,text,status,tests):
    s,c=registered(repair);verifier=CorrectnessVerifier(repair.base,repair.manager.store)
    manager=MagicMock();client=MagicMock();manager.client_factory.return_value.__enter__.return_value=client
    manager.registry.transaction.return_value.__enter__.return_value=MagicMock()
    container=MagicMock(attrs={'State':{'Status':'exited','ExitCode':code,'OOMKilled':code==137}})
    manager._resolve.return_value=(MagicMock(),container)
    monkeypatch.setattr(verifier,'create_and_deliver',lambda *args:(manager,'sandbox'))
    monkeypatch.setattr(verifier,'output',lambda container,stderr=False:text if stderr else '')
    result=verifier.run(s,repair.project,b'fixture','correctness')
    assert result.status==status and result.tests_executed==tests
    assert result.recipe_digest


def test_unavailable_test_runner_never_passes(repair,monkeypatch):
    s,c=registered(repair);v=CorrectnessVerifier(repair.base,repair.manager.store)
    monkeypatch.setattr(v,'create_and_deliver',lambda *args:(_ for _ in ()).throw(OSError('private information')))
    r=v.run(s,repair.project,b'fixture','correctness')
    assert r.status=='INCONCLUSIVE' and 'private information' not in str(r.errors)


def test_test_stage_timeout_is_inconclusive(repair,monkeypatch):
    s,c=registered(repair);verifier=CorrectnessVerifier(repair.base,repair.manager.store)
    manager=MagicMock();client=MagicMock();manager.client_factory.return_value.__enter__.return_value=client
    manager.registry.transaction.return_value.__enter__.return_value=MagicMock()
    container=MagicMock(attrs={'State':{'Status':'running'}});manager._resolve.return_value=(MagicMock(),container)
    monkeypatch.setattr(verifier,'create_and_deliver',lambda *args:(manager,'owned'))
    times=iter(range(1000));monkeypatch.setattr('guardrails.repair.correctness.time.monotonic',lambda:next(times))
    monkeypatch.setattr('guardrails.repair.correctness.time.sleep',lambda _:None)
    result=verifier.run(s,repair.project,b'fixture','correctness')
    assert result.status=='INCONCLUSIVE' and 'timed out' in result.errors[0]
