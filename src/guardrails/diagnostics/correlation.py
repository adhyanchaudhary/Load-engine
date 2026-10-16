"""UTC window alignment. Aggregate k6 percentiles are never per-second spikes."""
from datetime import timezone
from guardrails.diagnostics.models import Window
from guardrails.diagnostics.baseline import finite


class CorrelationEngine:
    def align(self,collected):
        r=collected.record;e=collected.evidence
        window=e.window
        if window.clock_origin!='UTC' or window.end<window.start:
            collected.missing.append('Benchmark clock/window unavailable; temporal association disabled')
            return []
        start,end=window.start,window.end
        samples=[]
        bins={}
        for i,s in enumerate(r.application.samples):
            if s.timestamp.tzinfo is None:
                collected.missing.append('Resource sample has unknown clock origin')
                continue
            if not start<=s.timestamp<=end:continue
            samples.append((i,s))
            index=int((s.timestamp-start).total_seconds())
            if index>180:continue
            bucket=bins.setdefault(index,{'resource_samples':0,'error_signatures':0})
            bucket['resource_samples']+=1
        collected.log_events=[t for t in collected.log_events if start<=t<=end]
        for timestamp in collected.log_events:
            if start<=timestamp<=end:
                index=int((timestamp-start).total_seconds())
                if index<=180:bins.setdefault(index,{'resource_samples':0,'error_signatures':0})['error_signatures']+=1
        times=sorted(s.timestamp for _,s in samples)
        gaps=[(b-a).total_seconds() for a,b in zip(times,times[1:])]
        if any(g>2.5 for g in gaps):collected.missing.append('Resource sampling gaps exceed 2.5 seconds')
        # State a limitation even if no resource/error overlap exists.
        collected.missing.append('k6 has aggregate latency only; no per-second latency/resource correlation is possible')
        if samples or collected.log_events:
            e.add('resource_sample','aligned_windows',{'one_second_bins':{str(k):v for k,v in sorted(bins.items())},
                'largest_sampling_gap_seconds':max(gaps) if gaps else None},
                'UTC wall-clock alignment of resources and log signatures within the benchmark',
                limitations=['Clock synchronization was not independently verified','Within-window association is not causation',
                             'No individual HTTP latency spikes can be inferred from the k6 summary'])
        return samples
