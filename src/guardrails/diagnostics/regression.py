"""Configurable heuristic regression signals, independent of absolute budgets."""
class RegressionDetector:
    def detect(self,comparison):
        if comparison.compatibility!='COMPATIBLE':return comparison
        config=comparison.thresholds
        limits={'p95_ms':config.p95_increase_percent,'p99_ms':config.p99_increase_percent,
                'observed_peak_memory_bytes':config.memory_increase_percent}
        missing=[]
        for delta in comparison.metric_differences:
            if delta.metric in limits:
                if delta.relative_change_percent is None:missing.append(delta.metric)
                elif delta.relative_change_percent>limits[delta.metric]:comparison.signals.append(delta.metric)
            if delta.metric=='http_failure_rate':
                if delta.absolute_change is None:missing.append(delta.metric)
                elif delta.absolute_change*100>config.error_increase_percentage_points:
                    comparison.signals.append(delta.metric)
        if comparison.signals:comparison.regression_classification='REGRESSION'
        elif missing:
            comparison.regression_classification='INCONCLUSIVE'
            comparison.limitations.append('Required regression measurements missing/zero baseline: '+', '.join(missing))
        else:comparison.regression_classification='NO_REGRESSION_DETECTED'
        return comparison
