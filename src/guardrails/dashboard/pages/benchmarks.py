from datetime import datetime,time,timedelta,timezone
import streamlit as st
from guardrails.dashboard import services as service_provider
from guardrails.dashboard.components.metrics import benchmark_metrics
from guardrails.dashboard.components.charts import latency_trend_chart,throughput_chart,error_rate_chart,cpu_usage_chart,memory_usage_chart,before_after_comparison
from guardrails.dashboard.components.tables import benchmark_table,exports
from guardrails.dashboard.components.status import show_status,error_message


def render():
    services=service_provider.get_services();st.title('Benchmark history')
    cols=st.columns(3)
    search=cols[0].text_input('Search test ID')
    status=cols[1].selectbox('Execution status',['All','completed','failed','cancelled','interrupted','running'])
    days=cols[2].selectbox('History window',['All time',7,30,90])
    more=st.columns(2)
    sandbox=more[0].text_input('Filter sandbox UUID (optional)')
    endpoint=more[1].text_input('Filter exact endpoint (optional)')
    populations=services.history.populations();keys=['All']+[p['workload_key'] for p in populations]
    population=st.selectbox('Workload filter',keys)
    custom_dates=st.checkbox('Use exact UTC date range')
    bounds=None
    if custom_dates:
        bounds=st.date_input('UTC date range',value=(datetime.now(timezone.utc).date()-timedelta(days=30),datetime.now(timezone.utc).date()))
    page=st.number_input('Page',min_value=1,max_value=100000,value=1,step=1)
    ascending=st.checkbox('Oldest first',False)
    since=None if days=='All time' else datetime.now(timezone.utc)-timedelta(days=days)
    until=None
    if bounds and len(bounds)==2:
        since=datetime.combine(bounds[0],time.min,tzinfo=timezone.utc);until=datetime.combine(bounds[1],time.max,tzinfo=timezone.utc)
    rows,total=services.history.benchmarks(search=search,status=None if status=='All' else status,sandbox_id=sandbox or None,
        endpoint=endpoint or None,since=since,until=until,workload_key=None if population=='All' else population,page=page-1,ascending=ascending)
    st.caption(f'{total} matching records · 25 per page')
    if not rows:st.info('No matching history. Adjust filters or import retained JSON artifacts.');return
    benchmark_table(rows);exports(rows)
    if population!='All':
        trend=list(reversed(rows)) if not ascending else rows
        st.plotly_chart(latency_trend_chart(trend),width='stretch')
        a,b=st.columns(2);a.plotly_chart(throughput_chart(trend),width='stretch');b.plotly_chart(error_rate_chart(trend),width='stretch')
    else:st.caption('Trend charts require one workload population; incompatible runs are not pooled.')
    tid=st.selectbox('Benchmark detail',[r['test_id'] for r in rows],key='history_test')
    row=services.history.benchmark(tid);benchmark_metrics(row);show_status(row['budget_status'],'Budget')
    st.caption('Historical provenance: '+row['provenance'])
    samples=services.history.samples(tid)
    a,b=st.columns(2);a.plotly_chart(cpu_usage_chart(samples),width='stretch');b.plotly_chart(memory_usage_chart(samples),width='stretch')
    with st.expander('Saved configuration'):st.json(__import__('json').loads(row['configuration_json']))
    if len(rows)>=2:
        st.subheader('Compare two recorded tests')
        with st.form('comparison-form'):
            baseline=st.selectbox('Baseline test',[r['test_id'] for r in rows],index=min(1,len(rows)-1))
            candidate=st.selectbox('Candidate test',[r['test_id'] for r in rows])
            compare=st.form_submit_button('Compare with compatibility checks')
        if compare:
            try:
                result=services.compare(baseline,candidate)
                show_status(result.regression_classification,'Regression')
                st.info('Compatibility: '+result.compatibility)
                if result.compatibility!='COMPATIBLE':st.warning('Strict regression conclusions are unavailable for these workloads/environments.')
                st.dataframe([d.model_dump() for d in result.metric_differences],hide_index=True)
                st.plotly_chart(before_after_comparison(services.history.benchmark(baseline),services.history.benchmark(candidate)),width='stretch')
                st.write(result.limitations+result.missing_compatibility_information+result.incompatibilities)
            except Exception as exc:error_message(exc)
