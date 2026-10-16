"""Synthetic verification data with real local Git objects; never host-execute project code."""
from datetime import timedelta
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock
import pytest
from guardrails.docker.manager import SandboxManager
from guardrails.docker.models import ApprovedImage,ImagePolicy,PerformancePolicy
from guardrails.repair.models import ApprovedProject,Recipe,RepairPolicy,StageResult
from guardrails.repair.manager import RepairManager
from guardrails.repair.policy import fingerprint
from guardrails.diagnostics.models import DiagnosticInput
from guardrails.diagnostics.baseline import BaselineComparator
from diagnostic_fixtures import SyntheticArtifacts


def git_commit(root,message):
    run=lambda *args:subprocess.run(['git','-c','core.hooksPath=/dev/null',*args],cwd=root,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.decode().strip()
    run('add','.')
    run('-c','user.name=Guardrails Fixture','-c','user.email=fixture@example.test','commit','-m',message)
    return run('rev-parse','HEAD')


@pytest.fixture
def repair(tmp_path):
    root=tmp_path/'approved';root.mkdir()
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    (root/'app.py').write_text('def value():\n    return 42\n')
    (root/'tests').mkdir();(root/'tests/test_app.py').write_text('# trusted fixture test procedure\n')
    baseline_revision=git_commit(root,'baseline')
    (root/'app.py').write_text('def value():\n    return 6 * 7\n')
    candidate_revision=git_commit(root,'candidate')
    project_id=str(uuid4());image=ApprovedImage(image_id='sha256:'+'a'*64,entrypoint=['python'])
    recipe=Recipe(version='fixture-v1',build_command=['python','-I','-m','compileall','-q','.'],
        test_command=['python','-m','unittest','discover','-s','tests'],application_command=['python','-B','app.py'])
    project=ApprovedProject(workspace=str(root),baseline_revision=baseline_revision,runtime_image=image,recipe=recipe,
        allowed_changes=['app.py'],dataset_id='fixture-v1')
    artifacts=SyntheticArtifacts(tmp_path/'state');baseline=artifacts.make()
    baseline.metadata.commit_identifier=baseline_revision;baseline.metadata.application_identity=project_id
    baseline.metadata.test_recipe_version=recipe.version;baseline.metadata.test_recipe_digest=fingerprint(recipe)
    baseline.metadata.application_memory_bytes=256*1024**2
    baseline.metadata.application_image_id=image.image_id
    baseline.metrics.p95_ms=100;baseline.metrics.p99_ms=120;baseline.metrics.throughput_rps=10
    artifacts.save(baseline)
    report=artifacts.service.analyze(DiagnosticInput(test_id=baseline.test_id))
    manager=SandboxManager(artifacts.path,policy=ImagePolicy(images={},performance=PerformancePolicy(image=image)),client_factory=MagicMock())
    policy=RepairPolicy(projects={project_id:project})
    repair_manager=RepairManager(manager,policy=policy)
    return SimpleNamespace(manager=repair_manager,base=manager,artifacts=artifacts,root=root,project=project,project_id=project_id,
        baseline=baseline,diagnostic=report,revision=candidate_revision)


def registered(repair):
    session=repair.manager.create(repair.diagnostic.report_id,repair.project_id)
    candidate=repair.manager.register(session.session_id,repair.revision)
    return session,candidate


def fake_verification(repair,monkeypatch,*,build='PASS',correctness='PASS',p95=60,throughput=15,error=0):
    def stage(self,session,project,snapshot,kind):
        return StageResult(stage=kind,status=build if kind=='build' else correctness,recipe_version=project.recipe.version,
            recipe_digest=fingerprint(project.recipe),exit_code=0 if (build if kind=='build' else correctness)=='PASS' else 1,
            tests_executed=3 if kind=='correctness' else None,failures=0)
    monkeypatch.setattr('guardrails.repair.manager.CorrectnessVerifier.run',stage)
    def benchmark(self,session,project,candidate,snapshot,baseline,report):
        records=[]
        for index in range(project.evaluation.repetitions):
            r=repair.artifacts.make(offset=60+index*60)
            r.metadata=baseline.metadata.model_copy(deep=True);r.metadata.commit_identifier=candidate.source_revision
            r.metrics.p95_ms=p95;r.metrics.p99_ms=80;r.metrics.throughput_rps=throughput;r.metrics.http_failure_rate=error
            r.budget.status='PASS' if error<=.01 and p95<=250 else 'FAIL'
            repair.artifacts.save(r);records.append(r)
            report.benchmark_test_ids.append(r.test_id);report.comparisons.append(BaselineComparator().compare(baseline,r))
        return records
    mock=MagicMock(side_effect=benchmark)
    monkeypatch.setattr('guardrails.repair.manager.RepairBenchmarker.run',lambda self,*args:mock(self,*args))
    return mock
