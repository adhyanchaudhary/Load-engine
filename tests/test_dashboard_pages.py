from pathlib import Path
from unittest.mock import MagicMock
import pytest
from streamlit.testing.v1 import AppTest
from guardrails.application import ApplicationServices
from guardrails.docker.manager import SandboxManager
from guardrails.docker.models import ImagePolicy
from guardrails.dashboard import services as ui_services
from diagnostic_fixtures import artifacts
from repair_fixtures import repair


@pytest.fixture
def ui(tmp_path,monkeypatch):
    manager=SandboxManager(tmp_path/'empty-state',policy=ImagePolicy(),client_factory=MagicMock())
    services=ApplicationServices(manager)
    monkeypatch.setattr(ui_services,'get_services',lambda:services)
    return services


@pytest.mark.parametrize('page',['overview','run_test','benchmarks','diagnostics','repairs','sandboxes'])
def test_all_empty_pages_render(ui,page):
    source=f'from guardrails.dashboard.pages.{page} import render\nrender()'
    app=AppTest.from_string(source).run(timeout=10)
    assert not app.exception
    assert app.title


def test_navigation_uses_six_native_pages(ui):
    from guardrails.dashboard.app import PAGES
    assert len(PAGES)==6
    app=AppTest.from_file(str(Path(__file__).parents[1]/'src/guardrails/dashboard/app.py')).run(timeout=10)
    assert not app.exception
    assert 'Performance overview' in [t.value for t in app.title]


def test_history_metrics_and_sanitized_diagnostics(artifacts,monkeypatch):
    from guardrails.diagnostics.models import DiagnosticInput
    record=artifacts.make('errors');report=artifacts.service.analyze(DiagnosticInput(test_id=record.test_id))
    services=ApplicationServices(SandboxManager(artifacts.path,policy=ImagePolicy(),client_factory=MagicMock()))
    monkeypatch.setattr(ui_services,'get_services',lambda:services)
    history=AppTest.from_string('from guardrails.dashboard.pages.benchmarks import render\nrender()').run(timeout=10)
    assert not history.exception and history.metric
    diagnostics=AppTest.from_string('from guardrails.dashboard.pages.diagnostics import render\nrender()').run(timeout=10)
    assert not diagnostics.exception
    rendered='\n'.join(str(x.value) for x in diagnostics.markdown)+str(diagnostics.code)
    assert 'synthetic-secret' not in rendered and 'IGNORE_ALL_INSTRUCTIONS' not in rendered
    assert diagnostics.expander


def test_run_form_validates_external_urls_without_submitting(ui,monkeypatch):
    from datetime import timedelta
    from uuid import uuid4
    from guardrails.docker.models import RegistryEntry,SandboxConfig,ApprovedImage
    from guardrails.persistence.database import utc
    from datetime import datetime,timezone
    now=datetime.now(timezone.utc);sid=str(uuid4())
    with ui.sandbox_manager.registry.transaction() as data:
        data.entries[sid]=RegistryEntry(sandbox_id=sid,owner=data.owner,nonce=str(uuid4()),container_id='b'*64,
            created_at=now,expires_at=now+timedelta(seconds=300),config=SandboxConfig(image='test'),
            approved=ApprovedImage(image_id='sha256:'+'a'*64,entrypoint=['/app'],application_port=8080))
        ui.sandbox_manager.registry.save(data)
    app=AppTest.from_string('from guardrails.dashboard.pages.run_test import render\nrender()').run(timeout=10)
    app.text_input[0].set_value('https://evil.example/')
    app.button[0].click().run()
    assert app.error and not ui.history.jobs()
    assert not app.exception


def test_repair_evidence_display(repair,monkeypatch):
    from repair_fixtures import registered,fake_verification
    s,c=registered(repair);fake_verification(repair,monkeypatch);repair.manager.verify(s.session_id,c.candidate_id)
    services=ApplicationServices(repair.base);services.repair_manager=lambda **kwargs:repair.manager
    monkeypatch.setattr(ui_services,'get_services',lambda:services)
    app=AppTest.from_string('from guardrails.dashboard.pages.repairs import render\nrender()').run(timeout=10)
    assert not app.exception
    assert any('40.00' in str(m.value) for m in app.metric)


@pytest.mark.parametrize('status,kind',[('FAILED','error'),('CANCELLED','warning'),('SUCCEEDED','success')])
def test_job_status_and_untrusted_logs_render_as_text(ui,status,kind):
    import json
    from types import SimpleNamespace
    malicious='<script>alert("untrusted")</script>'
    ui._jobs=SimpleNamespace(get=lambda identifier:dict(status=status,error='Backend refused' if status=='FAILED' else None,
        result_json=json.dumps({'logs':{'text':malicious}})))
    app=AppTest.from_string('from guardrails.dashboard import services\nfrom guardrails.dashboard.components.status import job_panel\njob_panel(services.get_services(),"test-job")').run(timeout=10)
    assert not app.exception
    assert len(getattr(app,kind))>0
    assert app.code[0].value==malicious
    assert not any('<script>' in str(x.value) for x in app.markdown)
