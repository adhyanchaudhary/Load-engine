"""Run: uv run streamlit run src/guardrails/dashboard/app.py"""
import streamlit as st
from guardrails.dashboard import services as service_provider
from guardrails.dashboard.pages import overview,run_test,benchmarks,diagnostics,repairs,sandboxes
from guardrails.dashboard.state import request_key
from guardrails.dashboard.components.status import error_message,job_panel

st.set_page_config(page_title='Performance Guardrails',page_icon=None,layout='wide',initial_sidebar_state='expanded')

PAGES=[('Overview',overview.render),('Run test',run_test.render),('Benchmark history',benchmarks.render),
       ('Diagnostics',diagnostics.render),('AI repair history',repairs.render),('Sandbox manager',sandboxes.render)]


def main():
    try:services=service_provider.get_services()
    except Exception:
        st.error('Cannot initialize private application storage. Check GUARDRAILS_STATE_DIR permissions and database compatibility.');return
    with st.sidebar:
        st.title('Performance Guardrails')
        st.caption('Local control center')
        st.caption('Shared state: performance.db + retained evidence artifacts')
        if st.button('Import JSON history'):
            try:st.session_state['import_job_id']=services.jobs.submit('import_history',{},request_key('import_history',{}))
            except Exception as exc:error_message(exc)
        if st.session_state.get('import_job_id'):job_panel(services,st.session_state['import_job_id'])
        issues=services.history.import_issues()
        if issues:st.warning(f'{len(issues)} artifact imports need attention; original files retained.')
        with st.expander('Import / indexing issues'):
            st.dataframe(issues,hide_index=True)
        st.caption('Localhost only. Remote multiuser deployment requires additional authentication and authorization.')
    page=st.navigation([st.Page(fn,title=title,default=i==0,url_path=['overview','run-test','benchmarks','diagnostics','repairs','sandboxes'][i]) for i,(title,fn) in enumerate(PAGES)])
    page.run()


if __name__=='__main__':main()
