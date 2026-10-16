import streamlit as st
from guardrails.dashboard import services as service_provider
from guardrails.dashboard.state import request_key,new_intent
from guardrails.dashboard.components.status import job_panel,show_status,error_message


def render():
    services=service_provider.get_services();st.title('Diagnostic investigation')
    st.caption('Historical observations and hypotheses remain separate. Analysis changes no source or containers.')
    rows,_=services.history.benchmarks(page_size=200)
    if not rows:st.info('No benchmarks available for investigation. Run a test or import existing history.');return
    ids=[r['test_id'] for r in rows]
    with st.form('diagnostic-analysis'):
        tid=st.selectbox('Benchmark to investigate',ids,key='diagnostic_test')
        baseline=st.selectbox('Optional prior baseline',['None']+ids)
        submitted=st.form_submit_button('Analyze stored evidence',type='primary')
    if submitted:
        payload={'test_id':tid,'baseline_test_id':None if baseline=='None' else baseline}
        try:st.session_state['diagnostic_job_id']=services.jobs.submit('analyze',payload,request_key('analyze',payload))
        except Exception as exc:error_message(exc)
    if st.button('New analysis request'):new_intent('analyze')
    if st.session_state.get('diagnostic_job_id'):job_panel(services,st.session_state['diagnostic_job_id'])
    reports=services.history.diagnostics()
    if not reports:st.info('No saved diagnostic reports yet. Analysis is explicit; simply opening this page does not run it.');return
    rid=st.selectbox('Saved report',[r['report_id'] for r in reports],key='diagnostic_report')
    try:report=services.diagnostic_service.get(rid)
    except Exception as exc:error_message(exc);return
    show_status(report.overall_status,'Investigation');show_status(report.budget_status,'Absolute budget')
    st.caption('Generated at '+report.generated_at.isoformat())
    for finding in report.findings:
        with st.expander(f'{finding.severity} · {finding.confidence} confidence · {finding.title}',expanded=True):
            st.write(finding.explanation)
            st.caption('Evidence references: '+', '.join(finding.evidence_ids))
            st.write(finding.recommended_investigations)
    st.subheader('Supporting evidence')
    import json
    evidence_rows=[dict(e.model_dump(mode='json'),observed_value=json.dumps(e.observed_value,sort_keys=True)) for e in report.evidence]
    st.dataframe(evidence_rows,hide_index=True)
    st.subheader('Missing observability');st.write(report.missing_observability)
    st.subheader('Recommended investigations');st.write(report.recommended_next_steps)
    st.subheader('Bounded agent context');st.code(services.diagnostic_service.context(rid,4000).text,language=None)
    st.download_button('Export diagnostic report',report.model_dump_json(indent=2),file_name=rid+'.json',mime='application/json')
