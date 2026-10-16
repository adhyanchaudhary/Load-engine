from unittest.mock import MagicMock
from guardrails.docker.models import RegistryEntry
from guardrails.docker.networking import verify_membership, verify_network, labels, cleanup_runner, runner_labels
from guardrails.docker.manager import SandboxError
from test_sandbox import engine,created
import pytest


def network_case(engine):
    m,c,app,_=engine;sid=created(engine)
    with m.registry.transaction() as data:
        entry=data.entries[sid];entry.network_name='test';entry.network_id='net'
    network=MagicMock(id='net',attrs={'Name':'test','Driver':'bridge','Internal':True,'EnableIPv6':False,
        'Labels':labels(entry,'network'),'Containers':{app.id:{}}})
    c.networks.get.return_value=network
    app.attrs['NetworkSettings']={'Networks':{'test':{'NetworkID':'net','IPAddress':'172.20.0.2'}}}
    return m,c,app,entry,network


def test_membership_and_owned_network(engine):
    m,c,a,e,n=network_case(engine)
    assert verify_membership(c,e,a)=='172.20.0.2'


@pytest.mark.parametrize('tamper',['external','host','labels','extra','missing','app_extra'])
def test_reject_bad_network(engine,tamper):
    m,c,a,e,n=network_case(engine)
    if tamper=='external':n.attrs['Internal']=False
    if tamper=='host':n.attrs['Driver']='host'
    if tamper=='labels':n.attrs['Labels']={}
    if tamper=='extra':n.attrs['Containers']['foreign']={}
    if tamper=='missing':n.attrs['Containers']={}
    if tamper=='app_extra':a.attrs['NetworkSettings']['Networks']['bridge']={}
    with pytest.raises(SandboxError):verify_membership(c,e,a)
    n.remove.assert_not_called()


def test_unowned_runner_not_removed(engine):
    m,c,a,e,n=network_case(engine)
    e.runner_name='runner';e.runner_id='c'*64;e.runner_nonce='secret'
    runner=MagicMock(id=e.runner_id,attrs={'Config':{'Labels':{}}});c.containers.get.return_value=runner
    with m.registry.transaction() as data:
        with pytest.raises(SandboxError):cleanup_runner(m,c,data,e)
    runner.remove.assert_not_called()
