from datetime import datetime,timedelta,timezone
import streamlit as st
from guardrails.dashboard import services as service_provider
from guardrails.dashboard.components.metrics import overview_metrics
from guardrails.dashboard.components.charts import latency_trend_chart,repair_history_chart
from guardrails.dashboard.components.tables import benchmark_table
from guardrails.dashboard.components.status import error_message


def render():
    services=service_provider.get_services();st.title('Performance overview')
    st.caption('Local measurements, evidence-backed findings, and verified repair history.')
    days=st.selectbox('Recent time window',[7,30,90],index=1)
    since=datetime.now(timezone.utc)-timedelta(days=days)
    populations=services.history.populations()
    keys=['All populations (latency trend hidden)']+[p['workload_key'] for p in populations]
    lookup={p['workload_key']:f"{p['endpoint']} · {p['vus']} VUs · {p['duration_seconds']}s · {p['workload_key'][:10]}" for p in populations}
    selected=st.selectbox('Comparable workload population',keys,format_func=lambda k:lookup.get(k,k))
    workload=selected if selected in lookup else None
    data=services.history.overview(since=since,workload_key=workload);overview_metrics(data)
    st.caption('Pass rate denominator: completed tests in the selected time/population; INCONCLUSIVE is not PASS. Median p95 summarizes recorded per-run percentiles, not individual requests. A mixed-population median is hidden; select a workload for a comparable value.')
    rows,total=services.history.benchmarks(since=since,workload_key=workload,page_size=100)
    if not total:st.info('No recorded benchmarks yet. Create an approved sandbox or import retained JSON history.')
    cols=st.columns(2)
    cols[0].metric('Active observed sandboxes',data['active_sandboxes'])
    cols[1].caption('Docker availability is an explicit observation, not inferred from historical records.')
    if cols[1].button('Check Docker availability'):
        status=services.sandbox_manager.check_docker()
        (st.success if status.available else st.warning)('Docker reachable' if status.available else 'Docker unavailable; check the local daemon and permissions')
    if not total:
        st.caption('No benchmark activity in this time window. Repair and sandbox history remain available in their pages.')
        return
    import pandas as pd
    frame=pd.DataFrame(rows)
    frame['day']=pd.to_datetime(frame['started_at'],utc=True,errors='coerce').dt.strftime('%Y-%m-%d')
    st.subheader('Benchmark activity')
    st.bar_chart(frame.groupby('day').size().rename('Recorded benchmarks'))
    if workload:st.plotly_chart(latency_trend_chart(list(reversed(rows))),width='stretch')
    else:st.info('Select one comparable workload population to view latency trends.')
    st.subheader('Latest benchmark results');benchmark_table(rows[:10])
    regressions=[r for r in services.history.diagnostics() if r['regression_status']=='REGRESSION']
    st.subheader('Recent regression findings')
    st.dataframe([{k:r[k] for k in ('report_id','test_id','generated_at','regression_status')} for r in regressions[:10]],hide_index=True)
    st.subheader('Recent repair activity')
    repairs=services.history.repairs();st.plotly_chart(repair_history_chart(repairs),width='stretch')
