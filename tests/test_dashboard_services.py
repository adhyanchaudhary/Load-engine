import json
from datetime import timedelta
from threading import Event
from time import monotonic,sleep
from uuid import uuid4
from unittest.mock import MagicMock
import pytest
from guardrails.application import ApplicationServices
from guardrails.jobs import JobManager
from guardrails.persistence.database import utc
from guardrails.dashboard.components.tables import csv_export,safe_export_record
from diagnostic_fixtures import artifacts
from repair_fixtures import repair
from test_sandbox import engine


def wait_job(jobs,job_id):
    deadline=monotonic()+5
    while monotonic()<deadline:
        result=jobs.get(job_id)
        if result['status'] not in ('QUEUED','RUNNING','CANCEL_REQUESTED'):return result
        sleep(.01)
    raise AssertionError('Job did not finish')


def test_parameterized_filters_pagination_and_kpis(artifacts):
    from guardrails.persistence.repositories.history import HistoryRepository
    from guardrails.persistence.database import database_for
    records=[artifacts.make(offset=i*60) for i in range(32)]
    history=HistoryRepository(database_for(artifacts.path))
    rows,total=history.benchmarks(page=1,page_size=10)
    assert total==32 and len(rows)==10
    assert history.benchmarks(search="' OR 1=1 --")[1]==0
    assert history.benchmarks(status='completed')[1]==32
    assert history.benchmarks(since=records[30].started_at)[1]==2
    assert history.benchmarks(sandbox_id=records[0].sandbox_id)[1]==1
    assert history.benchmarks(endpoint='/')[1]==32
    data=history.overview();assert data['completed']==32 and data['pass_rate']==1 and data['median_p95_ms']==20
    records[0].configuration.vus=20;artifacts.save(records[0])
    assert history.overview()['median_p95_ms'] is None
    key=history.benchmark(records[1].test_id)['workload_key']
    assert history.benchmarks(workload_key=key)[1]==31


def test_job_duplicate_prevention_failure_and_cancellation(engine,monkeypatch):
    manager,_,_,_=engine;services=ApplicationServices(manager)
    monkeypatch.setattr(services,'validate_action',lambda kind,payload:payload)
    gate=Event();started=Event();calls=[]
    def execute(kind,payload,cancelled=None,actor=None):
        calls.append(kind);started.set()
        while not gate.is_set() and not cancelled.is_set():sleep(.01)
        return {'done':True}
    monkeypatch.setattr(services,'execute',execute)
    jobs=services.jobs
    try:
        first=jobs.submit('test',{},'same-intent');assert started.wait(1)
        assert jobs.submit('test',{},'same-intent')==first
        assert jobs.get(first)['status']=='RUNNING'
        jobs.cancel(first)
        assert wait_job(jobs,first)['status']=='CANCELLED' and calls==['test']
        monkeypatch.setattr(services,'execute',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('private secret')))
        failed=jobs.submit('test',{},'new-intent')
        r=wait_job(jobs,failed);assert r['status']=='FAILED' and 'private secret' not in r['error']
    finally:gate.set();jobs.close()


def test_cross_process_cancel_flag_is_observed(engine,monkeypatch):
    services=ApplicationServices(engine[0]);monkeypatch.setattr(services,'validate_action',lambda k,p:p)
    started=Event()
    def execute(kind,payload,cancelled=None,actor=None):
        started.set()
        while not cancelled.is_set():sleep(.01)
        return {}
    monkeypatch.setattr(services,'execute',execute)
    jobs=services.jobs
    try:
        jid=jobs.submit('test',{},'intent');assert started.wait(1)
        with services.persistence.connect(write=True) as con:con.execute("UPDATE jobs SET status='CANCEL_REQUESTED' WHERE job_id=?",(jid,))
        assert wait_job(jobs,jid)['status']=='CANCELLED'
    finally:jobs.close()


def test_job_recovery_is_interrupted_not_replayed(engine):
    services=ApplicationServices(engine[0]);jid=str(uuid4());worker=str(uuid4())
    with services.persistence.connect(write=True) as con:
        con.execute('INSERT INTO jobs(job_id,owner_id,request_key,kind,payload_json,status,worker_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (jid,services.owner_id,'orphan','benchmark','{}','RUNNING',worker,utc()))
    jobs=services.jobs
    try:assert jobs.get(jid)['status']=='INTERRUPTED'
    finally:jobs.close()


def test_backend_validation_and_destructive_confirmation(engine):
    services=ApplicationServices(engine[0])
    with pytest.raises(ValueError):services.validate_action('benchmark',dict(sandbox_id=str(uuid4()),endpoint_path='https://example.com'))
    with pytest.raises(ValueError):services.validate_action('sandbox_start',{'sandbox_id':str(uuid4())})
    with pytest.raises(ValueError,match='Confirm'):services.destroy(str(uuid4()))
    with pytest.raises(ValueError,match='confirmation'):services.finalize_repair(str(uuid4()),str(uuid4()),'ACCEPT_FOR_REVIEW')
    with pytest.raises(ValueError):services.validate_action('sandbox_create',{'image':'arbitrary-public-image'})


def test_exports_are_bounded_safe_and_missing_values_stay_missing(artifacts):
    r=artifacts.make('errors')
    row={'record_json':r.model_dump_json(),'test_id':r.test_id,'endpoint':'=HYPERLINK("evil")','p95_ms':None}
    text=csv_export([row]);assert "'=HYPERLINK" in text and 'None' not in text
    data=safe_export_record(row)
    assert 'synthetic-secret' not in json.dumps(data) and 'IGNORE_ALL_INSTRUCTIONS' not in json.dumps(data)


def test_unverified_repair_acceptance_reuses_manager_rules(repair):
    from repair_fixtures import registered
    s,c=registered(repair);services=ApplicationServices(repair.base)
    services.repair_manager=lambda **kwargs:repair.manager
    with pytest.raises(ValueError):services.finalize_repair(s.session_id,c.candidate_id,'ACCEPT_FOR_REVIEW',confirmed=True)
