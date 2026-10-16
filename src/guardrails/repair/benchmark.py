"""Reuse k6 unchanged; exact baseline workloads, separate warmup and bounded trials."""
from guardrails.load_testing.runner import LoadEngine
from guardrails.load_testing.models import PerformanceTestConfig
from guardrails.diagnostics.baseline import BaselineComparator
from guardrails.repair.policy import fingerprint
from guardrails.diagnostics.io import ArtifactError


class RepairBenchmarker:
    def __init__(self,verifier):self.verifier=verifier

    def run(self,session,project,candidate,snapshot,baseline,report):
        verifier=self.verifier
        if project.evaluation.repetitions*(baseline.configuration.duration_seconds+15)+project.evaluation.warmup_seconds+20>280:
            raise ArtifactError('Exact repeated workload cannot fit the managed application lifetime')
        runner=verifier.base._load_policy().performance
        expected={'application_cpus':project.application_cpus,'application_memory_bytes':project.application_memory_mb*1024**2,
            'runner_cpus':getattr(runner,'cpus',None),'runner_memory_bytes':getattr(runner,'memory_mb',0)*1024**2,
            'runner_pids_limit':getattr(runner,'pids_limit',None),'dataset_id':project.dataset_id}
        if any(getattr(baseline.metadata,k,None)!=v for k,v in expected.items()):
            raise ArtifactError('Trusted recipe limits/dataset do not match the baseline; workload substitution refused')
        manager,sid=verifier.create_and_deliver(session,project,project.recipe.application_command,snapshot,
            application=True,revision=candidate.source_revision)
        engine=LoadEngine(manager,cancelled=verifier.cancelled)
        ready=engine.prepare(sid)
        verifier.check_cancelled()
        if ready.readiness_status!='ready':raise ArtifactError('Candidate application readiness failed')
        config=PerformanceTestConfig.model_validate(baseline.configuration.model_dump()|{'sandbox_id':sid})
        records=[]
        if project.evaluation.warmup_seconds:
            verifier.check_cancelled()
            warmup=engine.run(config.model_copy(update={'duration_seconds':project.evaluation.warmup_seconds}))
            report.warmup_test_ids.append(warmup.test_id)
            verifier.check_cancelled()
            if warmup.status!='completed' or warmup.metrics.errors:raise ArtifactError('Warmup execution incomplete')
        for _ in range(project.evaluation.repetitions):
            verifier.check_cancelled()
            measured=engine.run(config)
            records.append(measured);report.benchmark_test_ids.append(measured.test_id)
            verifier.check_cancelled()
            report.evidence_references.append('performance:'+measured.test_id)
            result=BaselineComparator().compare(baseline,measured)
            for name in ('test_recipe_version','test_recipe_digest','application_image_id'):
                a=getattr(baseline.metadata,name,None);b=getattr(measured.metadata,name,None)
                if a is None or b is None:
                    result.compatibility='PARTIALLY_COMPATIBLE';result.missing_compatibility_information.append(name)
                elif a!=b:
                    result.compatibility='INCOMPATIBLE';result.incompatibilities.append(name)
            if baseline.configuration.duration_seconds!=measured.configuration.duration_seconds:
                result.compatibility='INCOMPATIBLE';result.incompatibilities.append('Exact duration mismatch')
            report.comparisons.append(result)
            verifier.store.save(report)
            if measured.status!='completed':break
        return records
