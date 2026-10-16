import json
import streamlit as st
from guardrails.dashboard import services as service_provider
from guardrails.dashboard.state import request_key
from guardrails.dashboard.components.status import show_status,error_message,job_panel
from guardrails.dashboard.components.metrics import number


def render():
    services=service_provider.get_services();st.title('AI repair history')
    st.caption('Verified candidates are ready for human review. This interface never edits code, merges, pushes or deploys.')
    try:manager=services.repair_manager();projects=manager.policy().projects
    except Exception:manager=None;projects={};st.info('Repair execution is unavailable until an operator configures GUARDRAILS_REPAIR_POLICY_FILE.')
    reports=services.history.diagnostics()
    if projects and reports:
        with st.form('new-repair-session'):
            rid=st.selectbox('Diagnostic report for a repair',[r['report_id'] for r in reports])
            pid=st.selectbox('Approved local project',list(projects))
            attempts=st.number_input('Maximum attempts',min_value=1,max_value=5,value=3)
            create=st.form_submit_button('Start bounded repair session')
        if create:
            try:
                result=services.create_repair(rid,pid,attempts,request_key('repair_create',{'report':rid,'project':pid,'attempts':attempts}))
                st.success('Created session '+result.session_id)
            except Exception as exc:error_message(exc)
    rows=services.history.repairs()
    if not rows:st.info('No repair sessions recorded. An approved project and comparable baseline are required.');return
    st.dataframe([{k:r[k] for k in ('session_id','project_id','status','updated_at')} for r in rows],hide_index=True)
    sid=st.selectbox('Repair session',[r['session_id'] for r in rows],key='repair_session')
    try:status=services.repair_manager().status(sid)
    except Exception as exc:error_message(exc);return
    show_status(status.session.status,'Session')
    st.caption(f'Baseline {status.session.baseline_test_id} · Diagnostic {status.session.diagnostic_report_id} · Attempts remaining {status.attempts_remaining}')
    if not status.candidates:st.info('No candidate registered. Commit an approved source change, then register its immutable revision through MCP.');return
    cid=st.selectbox('Candidate',[c.candidate_id for c in status.candidates])
    candidate=next(c for c in status.candidates if c.candidate_id==cid)
    st.code(candidate.source_revision,language=None);show_status(candidate.verification_status,'Verification')
    st.caption('Source digest: '+candidate.source_digest)
    st.write(candidate.rejection_reasons)
    runs=services.history.verifications(cid)
    if runs:
        report=json.loads(runs[0]['report_json']);stats=report['statistics']
        cols=st.columns(3)
        cols[0].metric('Baseline p95',number(stats.get('baseline_p95_ms'),'ms'))
        cols[1].metric('Candidate median p95',number(stats.get('candidate_median_p95_ms'),'ms'))
        cols[2].metric('p95 improvement',number(stats.get('improvement_percent'),'%'))
        show_status(report['correctness']['status'] if report.get('correctness') else 'INCONCLUSIVE','Correctness')
        st.write('Measured benchmark IDs:',report['benchmark_test_ids'])
        for tid in report['benchmark_test_ids']:
            try:show_status(services.history.benchmark(tid)['budget_status'],'Budget '+tid[:8])
            except ValueError:st.warning('Measured benchmark history unavailable')
        st.write('Comparisons:',report['comparisons']);st.write('Reasons:',report['reasons'])
        st.download_button('Export verification report',json.dumps(report,indent=2),file_name=cid+'.json',mime='application/json')
    if manager and candidate.verification_status=='REGISTERED':
        if st.button('Verify registered candidate'):
            try:st.session_state['repair_job_id']=services.jobs.submit('repair_verify',{'session_id':sid,'candidate_id':cid},request_key('repair_verify',{'session_id':sid,'candidate_id':cid}))
            except Exception as exc:error_message(exc)
    if st.session_state.get('repair_job_id'):job_panel(services,st.session_state['repair_job_id'])
    with st.form('repair-finalization'):
        decision=st.selectbox('Review decision',['ACCEPT_FOR_REVIEW','REJECT'])
        confirmed=st.checkbox('I confirm this review decision; it does not authorize merging or deployment')
        finalize=st.form_submit_button('Finalize review decision')
    if finalize:
        try:result=services.finalize_repair(sid,cid,decision,confirmed=confirmed);st.success('Final session state: '+result.status)
        except Exception as exc:error_message(exc)
