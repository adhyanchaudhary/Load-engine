"""Opt-in actual fixed-recipe baseline/candidate verification. No image pulls or host project execution."""
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4
import pytest
from docker.errors import NotFound
from guardrails.docker.manager import SandboxManager
from guardrails.docker.models import ImagePolicy
from guardrails.repair.models import ApprovedProject,Recipe,RepairPolicy
from guardrails.repair.manager import RepairManager
from guardrails.diagnostics.models import DiagnosticInput
from repair_fixtures import git_commit

pytestmark=pytest.mark.docker_integration


def test_real_verified_repair_before_after(tmp_path):
    if os.environ.get('GUARDRAILS_DOCKER_INTEGRATION')!='1':
        pytest.skip('Opt in with GUARDRAILS_DOCKER_INTEGRATION=1 and approved local Python/k6 images')
    policy_path=os.environ.get('GUARDRAILS_POLICY_FILE');alias=os.environ.get('GUARDRAILS_HTTP_TEST_IMAGE')
    if not policy_path or not alias:pytest.skip('Approved local Python runtime alias/policy required')
    base=SandboxManager(tmp_path/'state',policy_path=Path(policy_path))
    policy=base._load_policy()
    if alias not in policy.images or policy.performance is None:pytest.skip('Approved local runtime and k6 required')
    if not base.check_docker().available:pytest.skip('Docker unavailable')
    with base.client_factory() as client:
        try:client.images.get(policy.images[alias].image_id);client.images.get(policy.performance.image.image_id)
        except NotFound:pytest.skip('Approved image missing locally; no pull attempted')
    root=tmp_path/'reviewed-fixture';root.mkdir();(root/'tests').mkdir()
    source=Path(__file__).parent/'fixtures/repair'
    (root/'app.py').write_bytes((source/'app.py').read_bytes())
    (root/'tests/test_app.py').write_bytes((source/'correctness_suite.txt').read_bytes())
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    baseline_revision=git_commit(root,'Reviewed deliberately slow fixture')
    (root/'app.py').write_text((root/'app.py').read_text().replace('0.080','0.020'))
    candidate_revision=git_commit(root,'Remove redundant fixture delay; preserve responses')
    project_id=str(uuid4())
    recipe=Recipe(version='fixture-v1',build_command=['python','-I','-m','compileall','-q','.'],
        test_command=['python','-B','-m','unittest','discover','-s','tests','-v'],application_command=['python','-B','app.py'],minimum_tests=3)
    project=ApprovedProject(workspace=str(root),baseline_revision=baseline_revision,runtime_image=policy.images[alias],
        recipe=recipe,allowed_changes=['app.py'],dataset_id='fixed-empty-data-v1')
    repair=RepairManager(base,policy=RepairPolicy(projects={project_id:project}))
    before=(root/'app.py').read_bytes()
    baseline=repair.benchmark_baseline(project_id)
    assert baseline.status=='completed' and baseline.metadata.commit_identifier==baseline_revision
    report=repair.diagnostics.analyze(DiagnosticInput(test_id=baseline.test_id))
    session=repair.create(report.report_id,project_id)
    candidate=repair.register(session.session_id,candidate_revision)
    verified=repair.verify(session.session_id,candidate.candidate_id)
    assert verified.verdict=='VERIFIED',verified.model_dump_json(indent=2)
    assert verified.correctness.tests_executed==3 and verified.correctness.status=='PASS'
    assert len(verified.benchmark_test_ids)==3 and len(verified.comparisons)==3
    assert verified.statistics['improvement_percent']>=15
    assert all(c.compatibility=='COMPATIBLE' for c in verified.comparisons)
    assert not verified.remaining_managed_resources
    final=repair.finalize(session.session_id,candidate.candidate_id,'ACCEPT_FOR_REVIEW')
    assert final.status=='COMPLETED' and final.accepted_candidate_id==candidate.candidate_id
    restored=RepairManager(base,policy=repair.policy()).status(session.session_id)
    assert restored.latest_verification.verdict=='VERIFIED'
    assert (root/'app.py').read_bytes()==before
    assert subprocess.check_output(['git','status','--porcelain'],cwd=root)==b''
    with base.registry.transaction() as data:
        assert not any(e.repair_session_id==session.session_id for e in data.entries.values())
