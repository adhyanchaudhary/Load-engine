"""Thin MCP adapters: historical reads plus diagnostic report persistence only."""
from guardrails.tools.sandbox import get_manager
from guardrails.diagnostics.service import DiagnosticService
from guardrails.diagnostics.models import DiagnosticInput, AnalysisSummary, Comparison, DiagnosticReport, DiagnosticContext


def get_service():
    from guardrails.application import services_for
    return services_for(get_manager()).diagnostic_service


def analyze_performance(test_id: str, baseline_test_id: str | None = None) -> AnalysisSummary:
    """Analyze owned historical artifacts, persist evidence-backed findings, return a summary."""
    service=get_service()
    return service.summary(service.analyze(DiagnosticInput(test_id=test_id,baseline_test_id=baseline_test_id)))


def compare_performance_tests(baseline_test_id: str, candidate_test_id: str) -> Comparison:
    """Check workload/environment compatibility before heuristic regression detection."""
    return get_service().compare(baseline_test_id,candidate_test_id)


def get_diagnostic_report(report_id: str) -> DiagnosticReport:
    """Read the owned persisted report with evidence, confidence, severity and gaps."""
    return get_service().get(report_id)


def get_diagnostic_context(report_id: str, max_characters: int = 4000) -> DiagnosticContext:
    """Return 256–16000 characters of generated diagnostic context, never raw logs."""
    return get_service().context(report_id,max_characters)
