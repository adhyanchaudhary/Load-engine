"""Evidence-based verdicts. PASS budgets alone never establish a successful repair."""
from statistics import median
from guardrails.diagnostics.baseline import finite
from guardrails.repair.policy import fingerprint


class RepairEvaluator:
    def evaluate(self,report,baseline,trials,project):
        if not report.build or report.build.status!='PASS':
            report.verdict='REJECTED' if report.build and report.build.status=='FAIL' else 'INCONCLUSIVE'
            report.reasons.append('Approved isolated build procedure did not PASS');return report
        if not report.correctness or report.correctness.status!='PASS':
            report.verdict='REJECTED' if report.correctness and report.correctness.status=='FAIL' else 'INCONCLUSIVE'
            report.reasons.append('Correctness verification did not PASS');return report
        if (report.correctness.recipe_digest!=fingerprint(project.recipe) or report.build.recipe_digest!=fingerprint(project.recipe) or
            report.correctness.exit_code!=0 or report.build.exit_code!=0 or report.correctness.tests_executed is None or
            report.correctness.tests_executed<project.recipe.minimum_tests or report.correctness.failures!=0):
            report.verdict='INCONCLUSIVE';report.reasons.append('Trusted recipe identity or completed correctness evidence is insufficient');return report
        if len(trials)!=project.evaluation.repetitions or len(report.comparisons)!=len(trials):
            report.verdict='INCONCLUSIVE';report.reasons.append('Required measured trials missing');return report
        required=('p50_ms','p95_ms','p99_ms','throughput_rps','http_failure_rate','request_count')
        if any(t.status!='completed' or t.errors or t.metrics.errors or not t.metrics.request_count or
               any(not finite(getattr(t.metrics,k)) for k in required) or t.budget.status=='INCONCLUSIVE' for t in trials):
            report.verdict='INCONCLUSIVE';report.reasons.append('Benchmark execution or required measurements incomplete');return report
        if any(c.compatibility!='COMPATIBLE' for c in report.comparisons):
            report.verdict='INCONCLUSIVE';report.reasons.append('Candidate and baseline are not fully compatible');return report
        if baseline.status!='completed' or not baseline.metrics.request_count or not finite(baseline.metrics.p95_ms) or baseline.metrics.p95_ms<=0:
            report.verdict='INCONCLUSIVE';report.reasons.append('Baseline measurement unavailable or zero');return report
        p95=[t.metrics.p95_ms for t in trials]
        middle=median(p95)
        if middle<=0 or (max(p95)-min(p95))/middle*100>project.evaluation.maximum_trial_spread_percent:
            report.verdict='INCONCLUSIVE';report.reasons.append('Measured p95 trials are unstable');return report
        improvement=(baseline.metrics.p95_ms-middle)/baseline.metrics.p95_ms*100
        throughput=median(t.metrics.throughput_rps for t in trials)
        errors=max(t.metrics.http_failure_rate for t in trials)
        p99=max(t.metrics.p99_ms for t in trials)
        report.statistics.update({'baseline_p95_ms':baseline.metrics.p95_ms,'candidate_median_p95_ms':middle,
            'improvement_percent':improvement,'candidate_median_throughput_rps':throughput,'candidate_max_error_rate':errors,
            'p95_spread_percent':(max(p95)-min(p95))/middle*100})
        policy=project.evaluation;reasons=[]
        if any(t.budget.status!='PASS' for t in trials):reasons.append('A mandatory performance budget failed')
        if improvement<policy.improvement_percent:reasons.append('No meaningful p95 improvement under the configured heuristic')
        if not finite(baseline.metrics.throughput_rps) or baseline.metrics.throughput_rps<=0:
            report.verdict='INCONCLUSIVE';report.reasons.append('Baseline throughput missing or zero');return report
        if min(t.metrics.throughput_rps for t in trials)<baseline.metrics.throughput_rps*(1-policy.maximum_throughput_drop_percent/100):reasons.append('Protected throughput regressed')
        if not finite(baseline.metrics.http_failure_rate) or not finite(baseline.metrics.p99_ms):
            report.verdict='INCONCLUSIVE';report.reasons.append('Protected baseline metrics missing');return report
        if errors-baseline.metrics.http_failure_rate>policy.maximum_error_increase_percentage_points/100:reasons.append('Protected HTTP failure rate regressed')
        if p99>baseline.metrics.p99_ms*(1+policy.maximum_p99_increase_percent/100):reasons.append('Protected p99 latency regressed')
        report.reasons.extend(reasons)
        report.verdict='REJECTED' if reasons else 'VERIFIED'
        if not reasons:report.reasons.append('Verified candidate ready for review; not a production-safety guarantee')
        return report
