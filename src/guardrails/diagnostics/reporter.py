"""Bounded agent context contains generated observations, never raw logs or trace text."""
from guardrails.diagnostics.models import DiagnosticContext

PRIORITY={'CRITICAL':0,'ERROR':1,'WARNING':2,'INFO':3}


def context(report,max_characters=4000):
    if type(max_characters) is not int or not 256<=max_characters<=16000:
        raise ValueError('max_characters must be an integer from 256 to 16000')
    ordered=sorted(report.findings,key=lambda f:PRIORITY[f.severity])
    lines=[f'Report {report.report_id}; budget={report.budget_status}.']
    if ordered:
        f=ordered[0]
        lines.append(f'{f.category} {f.severity}/{f.confidence}; evidence='+','.join(f.evidence_ids))
    metrics={e.source_reference.rsplit(':',1)[-1]:e for e in report.evidence if e.source_type=='benchmark'}
    for key in ('p95_ms','http_failure_rate'):
        if key in metrics:
            item=metrics[key]
            lines.append(f'{key}={item.observed_value} [{item.evidence_id}]')
    lines.append('Cause unresolved. Investigate: '+(ordered[0].recommended_investigations[0] if ordered and ordered[0].recommended_investigations else 'collect missing timings'))
    lines.append('Associations and hypotheses do not prove causation.')
    for finding in ordered:
        line=f'{finding.severity}/{finding.confidence} {finding.category}: {finding.explanation} Evidence: '+','.join(finding.evidence_ids)
        lines.append(line)
        for eid in finding.evidence_ids:
            item=next(e for e in report.evidence if e.evidence_id==eid)
            # Only numeric benchmark/resource observations. Log and trace free text never enters the context.
            if item.source_type in ('benchmark','resource_sample','comparison','trace'):
                import json
                lines.append(f'Observation {eid} ({item.source_reference}): '+json.dumps(item.observed_value,sort_keys=True)[:800])
        lines.extend('Investigate: '+step for step in finding.recommended_investigations[:1])
    lines.append('Uncertainty: '+'; '.join(report.missing_observability))
    full='\n'.join(lines)
    truncated=len(full)>max_characters
    text=full if not truncated else full[:max_characters-15]+'\n[truncated]'
    return DiagnosticContext(report_id=report.report_id,text=text,characters=len(text),truncated=truncated)
