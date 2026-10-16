import streamlit as st
from guardrails.dashboard import services as service_provider
from guardrails.dashboard.state import request_key,new_intent
from guardrails.dashboard.components.status import job_panel,error_message


def render():
    services=service_provider.get_services();st.title('Sandbox manager')
    st.caption('Only private-registry resources and approved local images are actionable. Historical rows are not live Docker observations.')
    if st.button('Check Docker'):
        result=services.sandbox_manager.check_docker()
        (st.success if result.available else st.warning)('Docker reachable' if result.available else 'Docker unavailable; verify daemon permissions and local configuration')
    try:images=list(services.sandbox_manager._load_policy().images)
    except Exception:images=[]
    if images:
        with st.form('create-sandbox'):
            image=st.selectbox('Approved local image alias',images)
            cols=st.columns(3)
            cpus=cols[0].number_input('CPU limit',min_value=.1,max_value=4.0,value=.5)
            memory=cols[1].number_input('Memory limit (MiB)',min_value=128,max_value=4096,value=512,step=128)
            ttl=cols[2].number_input('Lifetime (seconds)',min_value=10,max_value=300,value=300,step=10)
            submitted=st.form_submit_button('Create managed sandbox',type='primary')
        if submitted:
            payload=dict(image=image,cpus=cpus,memory_mb=memory,pids_limit=64,timeout_seconds=ttl)
            try:st.session_state['sandbox_job_id']=services.jobs.submit('sandbox_create',payload,request_key('sandbox_create',payload))
            except Exception as exc:error_message(exc)
        if st.button('New sandbox creation request'):new_intent('sandbox_create')
    else:st.info('No local images approved. Configure the trusted image policy; image pulling is not available here.')
    rows=services.history.sandboxes()
    if not rows:st.info('No sandbox history yet.')
    else:
        fields=('sandbox_id','status','health','image_identifier','cpu_limit','memory_limit_mb','created_at','expires_at','last_observed_at','last_error')
        st.dataframe([{k:r.get(k) for k in fields} for r in rows],hide_index=True)
        managed=[r for r in rows if r['managed']]
        if managed:
            sid=st.selectbox('Managed sandbox UUID',[r['sandbox_id'] for r in managed],key='selected_sandbox')
            action=st.selectbox('Action',['Inspect','Start','Logs','Destroy'])
            confirmed=st.checkbox('Confirm destruction of the selected managed sandbox') if action=='Destroy' else False
            if st.button('Apply sandbox action'):
                try:
                    payload={'sandbox_id':sid}
                    if action=='Destroy':job=services.destroy(sid,confirmed=confirmed,request_key=request_key('sandbox_destroy',payload))
                    else:
                        kind={'Inspect':'sandbox_inspect','Start':'sandbox_start','Logs':'sandbox_logs'}[action]
                        job=services.jobs.submit(kind,payload,request_key(kind,payload))
                    st.session_state['sandbox_job_id']=job
                except Exception as exc:error_message(exc)
            st.caption('Inspect/Start/Logs observations are timestamped. Use New action request to refresh an earlier completed action.')
            if st.button('New action request'):
                for kind in ('sandbox_inspect','sandbox_start','sandbox_logs'):new_intent(kind)
    if st.session_state.get('sandbox_job_id'):job_panel(services,st.session_state['sandbox_job_id'])
