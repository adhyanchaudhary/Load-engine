"""Opt-in real Streamlit form -> shared background job -> SQLite/report -> cleanup."""
import json
import os
from pathlib import Path
from time import monotonic,sleep
from uuid import uuid4
import pytest
from streamlit.testing.v1 import AppTest
from docker.errors import NotFound
from guardrails.application import ApplicationServices
from guardrails.docker.manager import SandboxManager
from guardrails.dashboard import services as ui_services
from guardrails.dashboard.components.tables import csv_export,safe_export_record
from guardrails.persistence.repositories.history import HistoryRepository
from guardrails.persistence.database import Database

pytestmark=pytest.mark.docker_integration


def test_real_dashboard_benchmark_and_persistence(tmp_path,monkeypatch):
    if os.environ.get('GUARDRAILS_DOCKER_INTEGRATION')!='1':pytest.skip('Opt in with approved local HTTP fixture and k6 images')
    path=os.environ.get('GUARDRAILS_POLICY_FILE');alias=os.environ.get('GUARDRAILS_HTTP_TEST_IMAGE')
    if not path or not alias:pytest.skip('Approved local fixture policy required')
    manager=SandboxManager(tmp_path/'state',policy_path=Path(path));policy=manager._load_policy()
    if alias not in policy.images or policy.performance is None:pytest.skip('Approved fixture and k6 required')
    if not manager.check_docker().available:pytest.skip('Docker unavailable')
    with manager.client_factory() as client:
        try:client.images.get(policy.images[alias].image_id);client.images.get(policy.performance.image.image_id)
        except NotFound:pytest.skip('Approved image absent locally; no pull attempted')
    services=ApplicationServices(manager)
    created=services.execute('sandbox_create',dict(image=alias,timeout_seconds=300))
    assert created.ok,created
    sid=created.sandbox.sandbox_id
    monkeypatch.setattr(ui_services,'get_services',lambda:services)
    jobs=None
    try:
        app=AppTest.from_string('from guardrails.dashboard.pages.run_test import render\nrender()').run(timeout=10)
        assert not app.exception
        by_label={w.label:w for w in app.number_input}
        by_label['Virtual users'].set_value(2)
        by_label['Duration (seconds)'].set_value(2)
        by_label['p95 latency budget (ms)'].set_value(10000.0)
        app.button[0].click().run(timeout=10)
        assert not app.exception
        assert len(services.history.jobs())==1
        jid=services.history.jobs()[0]['job_id'];jobs=services.jobs
        app.run(timeout=10)
        assert len(services.history.jobs())==1  # ordinary reruns do not start another benchmark
        deadline=monotonic()+30
        while monotonic()<deadline:
            result=jobs.get(jid)
            if result['status'] not in ('QUEUED','RUNNING','CANCEL_REQUESTED'):break
            sleep(.05)
        assert result['status']=='SUCCEEDED',result
        record=json.loads(result['result_json']);assert record['metrics']['request_count']>0
        assert record['configuration']['duration_seconds']==2 and record['configuration']['vus']==2
        assert record['budget']['status']=='PASS'
        row=services.history.benchmark(record['test_id'])
        assert row['p95_ms'] is not None and row['raw_summary_path']
        assert services.history.samples(record['test_id'])
        report=services.execute('analyze',{'test_id':record['test_id']})
        assert services.history.diagnostics(record['test_id'])[0]['report_id']==report.report_id
        reopened=HistoryRepository(Database(manager.registry.directory))
        assert reopened.benchmark(record['test_id'])['request_count']==record['metrics']['request_count']
        assert record['test_id'] in csv_export([row])
        assert safe_export_record(row)['test_id']==record['test_id']
        assert services.history.audit()
    finally:
        if jobs:jobs.close()
        assert manager.destroy(sid).ok
    assert next(r for r in services.history.sandboxes() if r['sandbox_id']==sid)['status']=='DESTROYED'
