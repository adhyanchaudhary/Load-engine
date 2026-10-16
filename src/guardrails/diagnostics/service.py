"""Orchestrate historical collection, comparison, classification, and persistence."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from guardrails.load_testing.storage import ResultStore
from guardrails.diagnostics.models import DiagnosticInput, DiagnosticReport, AnalysisSummary, RegressionConfig
from guardrails.diagnostics.collector import DiagnosticCollector
from guardrails.diagnostics.baseline import BaselineComparator
from guardrails.diagnostics.regression import RegressionDetector
from guardrails.diagnostics.correlation import CorrelationEngine
from guardrails.diagnostics.rules import RuleEngine
from guardrails.diagnostics.ownership import current_owner, owned_receipt
from guardrails.diagnostics.io import read_json, ArtifactError, MAX_ARTIFACT_BYTES
from guardrails.diagnostics.reporter import context


class DiagnosticService:
    def __init__(self,state_dir,*,regression_config=None):
        self.state_dir=state_dir
        self.collector=DiagnosticCollector(state_dir)
        self.reports=ResultStore(state_dir)
        self.reports.directory=state_dir/'diagnostics'
        self.thresholds=regression_config or RegressionConfig()

    def compare(self,baseline_test_id,candidate_test_id):
        b=self.collector.collect(DiagnosticInput(test_id=baseline_test_id,include_logs=False,include_traces=False))
        c=self.collector.collect(DiagnosticInput(test_id=candidate_test_id,include_logs=False,include_traces=False))
        result=BaselineComparator(self.thresholds).compare(b.record,c.record)
        result=RegressionDetector().detect(result)
        if not b.raw_summary_valid or not c.raw_summary_valid:
            result.regression_classification='INCONCLUSIVE'
            result.signals=[]
            result.limitations.append('Original raw summaries are unavailable, invalid, or inconsistent')
        return result

    def analyze(self,config: DiagnosticInput):
        c=self.collector.collect(config)
        comparison=self.compare(config.baseline_test_id,config.test_id) if config.baseline_test_id else None
        if comparison:
            c.missing.extend('Baseline compatibility metadata missing: '+field for field in comparison.missing_compatibility_information)
            if comparison.compatibility=='INCOMPATIBLE':c.missing.append('Baseline is incompatible; strict regression attribution is disabled')
        samples=CorrelationEngine().align(c)
        findings=RuleEngine().evaluate(c,comparison,samples,config.include_resource_metrics)
        substantive=any(f.category!='INSUFFICIENT_EVIDENCE' for f in findings)
        status='FINDINGS' if substantive else 'INCONCLUSIVE' if c.missing or c.record.status!='completed' else 'NO_FINDINGS'
        report=DiagnosticReport(report_id=str(uuid4()),owner_id=current_owner(self.state_dir),test_id=config.test_id,
            baseline_test_id=config.baseline_test_id,generated_at=datetime.now(timezone.utc),overall_status=status,
            budget_status=c.record.budget.status,regression_summary=comparison,findings=findings,evidence=c.evidence.items,
            missing_observability=sorted(set(c.missing)),
            recommended_next_steps=list(dict.fromkeys(step for f in findings for step in f.recommended_investigations)))
        data=report.model_dump_json(indent=2).encode()
        if len(data)>MAX_ARTIFACT_BYTES:raise ArtifactError('Diagnostic report exceeds storage bound')
        self.reports.write(report.report_id,'.json',data)
        return report

    def get(self,report_id):
        report_id=str(UUID(report_id))
        try:
            report=DiagnosticReport.model_validate(read_json(self.reports._path(report_id,'.json')))
            if report.report_id!=report_id or report.owner_id!=current_owner(self.state_dir):raise ValueError()
            owned_receipt(self.collector.store,report.test_id)
            if report.baseline_test_id:owned_receipt(self.collector.store,report.baseline_test_id)
            return report
        except ValueError:
            raise ArtifactError('Diagnostic report is malformed, unavailable, or not owned') from None

    def summary(self,report):
        return AnalysisSummary(report_id=report.report_id,test_id=report.test_id,overall_status=report.overall_status,
            budget_status=report.budget_status,regression_status=report.regression_summary.regression_classification if report.regression_summary else 'INCONCLUSIVE',
            finding_categories=[f.category for f in report.findings],missing_observability=report.missing_observability)

    def context(self,report_id,max_characters=4000):return context(self.get(report_id),max_characters)
