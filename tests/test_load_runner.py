from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4
import pytest
from docker.errors import APIError
from filelock import FileLock, Timeout
from guardrails.docker.models import PerformancePolicy, ApprovedImage
from guardrails.docker.networking import labels
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.load_testing.runner import LoadEngine
from guardrails.load_testing.storage import ResultStore
from test_sandbox import engine, created, IMAGE
from test_metrics_parser import summary


@pytest.fixture
def workload(engine,monkeypatch):
    manager,client,app,clock=engine
    sid=created(engine)
    manager._policy.performance=PerformancePolicy(image=ApprovedImage(image_id=IMAGE,entrypoint=['k6']))
    with manager.registry.transaction() as data:
        entry=data.entries[sid]
        entry.approved.application_port=8080
        entry.network_id='net';entry.network_name='owned';entry.prepared=True
        entry.expires_at+=timedelta(seconds=200)
        manager.registry.save(data)
    client.networks.get.return_value=MagicMock(id='net',attrs={
        'Name':'owned','Internal':True,'Driver':'bridge','EnableIPv6':False,
        'Labels':labels(entry,'network'),'Containers':{app.id:{}}})
    app.start()
    e=LoadEngine(manager)
    monkeypatch.setattr('guardrails.load_testing.runner.isolation_discrepancies',lambda *a:[])
    return e,manager,client,app,sid


def cfg(workload):return PerformanceTestConfig(sandbox_id=workload[-1],endpoint_path='/',duration_seconds=1)


def test_success_persistence_and_restart(workload,monkeypatch):
    e,m,c,a,sid=workload
    monkeypatch.setattr(e,'_execute',lambda *args:(summary(),0,False))
    r=e.run(cfg(workload))
    assert r.budget.status=='PASS'
    loaded=ResultStore(m.registry.directory).get(r.test_id)
    assert loaded==r
    assert (e.store.directory/(r.test_id+'.summary.json')).read_bytes()==summary()
    assert not list(e.store.directory.glob('*.tmp'))


def test_measured_threshold_failure(workload,monkeypatch):
    e=workload[0]
    monkeypatch.setattr(e,'_execute',lambda *args:(summary(p95=400),99,False))
    assert e.run(cfg(workload)).budget.status=='FAIL'


@pytest.mark.parametrize('exit_code,oom',[(1,False),(137,True),(143,False)])
def test_execution_failure_and_oom(workload,monkeypatch,exit_code,oom):
    e=workload[0];monkeypatch.setattr(e,'_execute',lambda *args:(summary(),exit_code,oom))
    r=e.run(cfg(workload))
    assert r.budget.status=='INCONCLUSIVE' and r.status=='failed'
    assert e.store.get(r.test_id).runner_oom_killed==oom


def test_invalid_summary(workload,monkeypatch):
    e=workload[0];monkeypatch.setattr(e,'_execute',lambda *args:(b'{}',0,False))
    assert e.run(cfg(workload)).budget.status=='INCONCLUSIVE'


def test_unmanaged_and_expired(workload):
    e,m,c,a,sid=workload
    r=e.run(PerformanceTestConfig(sandbox_id=str(uuid4()),endpoint_path='/'))
    assert r.status=='failed' and 'not registered' in r.errors[0]
    a.attrs['Config']['Labels']={}
    assert e.run(cfg(workload)).budget.status=='INCONCLUSIVE'
    c.containers.create.reset_mock()
    m.clock.return_value+=timedelta(seconds=400)
    assert e.prepare(sid).readiness_status=='unavailable'
    c.containers.create.assert_not_called()


def test_concurrency_same_sandbox(workload):
    e,m,c,a,sid=workload
    with e.admission(sid):
        r=e.run(cfg(workload))
        assert r.budget.status=='INCONCLUSIVE' and 'already active' in r.errors[0]


def test_global_concurrency(workload):
    e,m,c,a,sid=workload
    with e.admission(sid),e.admission(str(uuid4())):
        with pytest.raises(Exception,match='concurrency'):
            with e.admission(str(uuid4())): pass


def setup_execution(workload,monkeypatch,status='exited',code=0,oom=False):
    e,m,c,app,sid=workload
    runner=MagicMock(id='c'*64)
    runner.attrs={'State':{'Status':status,'ExitCode':code,'OOMKilled':oom},'HostConfig':{'Tmpfs':{'/tmp':'rw,size=8m'}}}
    monkeypatch.setattr(e,'_runner',lambda *args:(runner,MagicMock()))
    monkeypatch.setattr('guardrails.load_testing.runner.verify_runner',lambda *args:None)
    monkeypatch.setattr('guardrails.load_testing.runner.verify_membership',lambda *args:'172.20.0.2')
    monkeypatch.setattr('guardrails.load_testing.runner.sample',lambda *args:True)
    cleanup=MagicMock()
    monkeypatch.setattr('guardrails.load_testing.runner.cleanup_runner',cleanup)
    monkeypatch.setattr(e,'_summary',lambda *args:summary())
    return e,runner,cleanup


def test_low_level_failure_cleanup(workload,monkeypatch):
    e,runner,cleanup=setup_execution(workload,monkeypatch)
    runner.start.side_effect=APIError('sensitive daemon detail')
    r=e.run(cfg(workload))
    assert r.status=='failed' and 'sensitive' not in str(r.errors)
    cleanup.assert_called_once()
    runner.attach_socket.return_value.close.assert_called_once()


def test_runner_timeout_cleanup(workload,monkeypatch):
    e,runner,cleanup=setup_execution(workload,monkeypatch,status='running')
    tick=iter(range(1000));e.monotonic=lambda:next(tick);e.pause=lambda _:None
    r=e.run(cfg(workload))
    assert r.status=='failed' and 'deadline' in r.errors[0]
    cleanup.assert_called_once()


def test_application_crash_cleanup(workload,monkeypatch):
    e,runner,cleanup=setup_execution(workload,monkeypatch,status='running')
    states=iter([True,False,False])
    monkeypatch.setattr('guardrails.load_testing.runner.sample',lambda *args:next(states,False))
    r=e.run(cfg(workload))
    assert 'Application stopped' in r.errors[0] and r.budget.status=='INCONCLUSIVE'
    cleanup.assert_called_once()


def test_cancellation_cleanup_and_persistence(workload,monkeypatch):
    from threading import Event
    e,runner,cleanup=setup_execution(workload,monkeypatch,status='running')
    e.cancelled=Event();runner.start.side_effect=lambda:e.cancelled.set()
    r=e.run(cfg(workload))
    assert r.budget.status=='INCONCLUSIVE' and 'cancelled' in r.errors[0]
    cleanup.assert_called_once()


def test_result_path_traversal_rejected(workload):
    with pytest.raises(ValueError):workload[0].store.get('../../registry')


def test_stale_running_record_is_inconclusive(workload,monkeypatch):
    e=workload[0];monkeypatch.setattr(e,'_execute',lambda *args:(summary(),0,False))
    r=e.run(cfg(workload));r.status='running';e.store.save(r)
    assert e.store.get(r.test_id).status=='interrupted'


def test_partial_create_failure_reconciles_owned_runner(workload,monkeypatch):
    e,m,c,app,sid=workload
    monkeypatch.setattr('guardrails.load_testing.runner.verify_membership',lambda *args:'172.20.0.2')
    orphan=MagicMock(id='d'*64)
    from guardrails.docker.networking import runner_labels
    def create_then_timeout(client,entry,*args):
        orphan.attrs={'Config':{'Labels':runner_labels(entry)}}
        client.containers.get.return_value=orphan
        raise APIError('Docker create response lost')
    monkeypatch.setattr(e,'_runner',create_then_timeout)
    r=e.run(cfg(workload))
    assert r.status=='failed'
    orphan.remove.assert_called_once_with(force=True,v=False)
    with m.registry.transaction() as data:assert data.entries[sid].runner_name is None


def test_cleanup_failure_keeps_durable_intent(workload,monkeypatch):
    e,runner,cleanup=setup_execution(workload,monkeypatch)
    cleanup.side_effect=APIError('daemon unavailable')
    r=e.run(cfg(workload))
    assert r.budget.status=='INCONCLUSIVE'
    assert any('cleanup incomplete' in x for x in r.errors)
    with workload[1].registry.transaction() as data:assert data.entries[workload[-1]].runner_name


def test_expired_sandbox_rejected_before_runner(workload,monkeypatch):
    e,m,c,a,sid=workload
    m.clock.return_value+=timedelta(seconds=400)
    execute=MagicMock();monkeypatch.setattr(e,'_execute',execute)
    r=e.run(cfg(workload))
    assert 'expired' in r.errors[0]
    execute.assert_not_called()


def test_oom_real_execution_state_survives_invalid_summary(workload,monkeypatch):
    e,runner,cleanup=setup_execution(workload,monkeypatch,code=137,oom=True)
    monkeypatch.setattr(e,'_summary',lambda *args:(_ for _ in ()).throw(ValueError('invalid summary')))
    r=e.run(cfg(workload))
    assert r.runner_oom_killed and r.runner_exit_code==137
    assert r.budget.status=='INCONCLUSIVE'
    cleanup.assert_called_once()
