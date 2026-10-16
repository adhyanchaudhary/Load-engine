import streamlit as st
from guardrails.dashboard import services as service_provider
from guardrails.dashboard.state import request_key,new_intent
from guardrails.dashboard.components.status import job_panel,error_message
from guardrails.dashboard.components.metrics import benchmark_metrics


def render():
    services=service_provider.get_services();st.title('Run a performance test')
    st.caption('Controlled GET workloads against an owned internal application sandbox.')
    rows=[r for r in services.history.sandboxes() if r['managed'] and r['status']!='EXPIRED']
    if not rows:st.info('No available managed sandboxes. Create one from an approved local application image on Sandbox manager.');return
    ids=[r['sandbox_id'] for r in rows]
    with st.form('run-test-form'):
        sandbox_id=st.selectbox('Managed application sandbox',ids,key='run_sandbox')
        row=next(r for r in rows if r['sandbox_id']==sandbox_id)
        st.caption(f"Configured limits: {row['cpu_limit']} CPUs · {row['memory_limit_mb']} MiB. Running does not imply HTTP readiness.")
        endpoint=st.text_input('Internal endpoint path','/')
        cols=st.columns(2)
        vus=cols[0].number_input('Virtual users',min_value=1,max_value=100,value=10,step=1)
        duration=cols[1].number_input('Duration (seconds)',min_value=1,max_value=120,value=30,step=1)
        p95=cols[0].number_input('p95 latency budget (ms)',min_value=.01,max_value=3600000.0,value=250.0)
        errors=cols[1].number_input('HTTP failure budget (%)',min_value=0.0,max_value=100.0,value=1.0)
        submitted=st.form_submit_button('Execute managed test',type='primary')
    if submitted:
        payload=dict(sandbox_id=sandbox_id,endpoint_path=endpoint,vus=vus,duration_seconds=duration,p95_latency_budget_ms=p95,error_rate_budget=errors/100)
        try:
            job_id=services.jobs.submit('benchmark',payload,request_key('benchmark',payload))
            st.session_state['test_job_id']=job_id
        except Exception as exc:error_message(exc)
    if st.button('Start another test request'):new_intent('benchmark');st.session_state.pop('test_job_id',None)
    if st.session_state.get('test_job_id'):job_panel(services,st.session_state['test_job_id'])
    if st.session_state.get('completed_test_id'):
        row=services.history.benchmark(st.session_state['completed_test_id']);st.subheader('Latest completed result');benchmark_metrics(row)
        st.caption('Open Benchmark history and select '+row['test_id']+' for full metrics and exports.')
