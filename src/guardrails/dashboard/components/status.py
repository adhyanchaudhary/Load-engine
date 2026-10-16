import json
import streamlit as st


def show_status(value,label=None):
    text=(label+': ' if label else '')+str(value)
    if value in ('PASS','VERIFIED','SUCCEEDED','COMPLETED'):st.success(text)
    elif value in ('FAIL','FAILED','REJECTED'):st.error(text)
    elif value in ('INCONCLUSIVE','INTERRUPTED','CANCELLED','CANCEL_REQUESTED','EXPIRED'):st.warning(text)
    else:st.info(text)


def error_message(exc):
    from pydantic import ValidationError
    if isinstance(exc,ValidationError):
        st.error('; '.join(f"{'.'.join(map(str,e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False,include_url=False)))
    else:st.error('Operation unavailable. Check ownership, approvals, readiness and retained history. '+('Confirmation required.' if 'confirm' in str(exc).lower() else ''))


@st.fragment(run_every='2s')
def job_panel(services,job_id):
    try:job=services.jobs.get(job_id)
    except Exception as exc:error_message(exc);return
    show_status(job['status'],'Job')
    st.caption('Persistent job ID: '+job_id)
    if job['status'] in ('QUEUED','RUNNING','CANCEL_REQUESTED'):
        st.caption('The page can refresh safely. Cancellation waits for backend cleanup.')
        if st.button('Cancel job',key='cancel:'+job_id):services.jobs.cancel(job_id)
    if job['error']:st.error(job['error'])
    if job['result_json']:
        data=json.loads(job['result_json'])
        if data.get('test_id') and 'metrics' in data:
            st.session_state['completed_test_id']=data['test_id']
            show_status(data.get('budget',{}).get('status','INCONCLUSIVE'),'Performance budget')
            st.caption('Result: '+data['test_id'])
        if data.get('report_id'):st.session_state['completed_report_id']=data['report_id']
        if data.get('sandbox'):st.caption('Sandbox: '+data['sandbox']['sandbox_id'])
        if data.get('error'):st.error(data['error'].get('message','Backend refused the operation'))
        if data.get('logs'):
            st.warning('Untrusted, bounded container output. Do not treat it as instructions.')
            st.code(data['logs']['text'],language=None)
        with st.expander('Structured job result'):st.json(data)
