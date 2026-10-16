"""Plotly charts preserve missing values and keep latency populations separate."""
import pandas as pd
import plotly.graph_objects as go


def _chart(rows,fields,units,title,x='started_at'):
    frame=pd.DataFrame(rows);figure=go.Figure()
    for field,label in fields:
        if field in frame and x in frame:
            figure.add_scatter(x=frame[x],y=frame[field],mode='lines+markers',name=label,connectgaps=False)
    figure.update_layout(title=title,xaxis_title='Time (UTC)',yaxis_title=units,margin=dict(l=12,r=12,t=48,b=12),
        height=320,legend=dict(orientation='h'),paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)')
    return figure


def latency_trend_chart(rows):return _chart(rows,[('p50_ms','p50'),('p95_ms','p95'),('p99_ms','p99')],'Latency (ms)','Latency distribution over runs')
def throughput_chart(rows):return _chart(rows,[('throughput_rps','Throughput')],'Requests / second','Request throughput')
def error_rate_chart(rows):
    fig=_chart(rows,[('error_rate','HTTP failure fraction')],'HTTP failures (fraction)','HTTP failure rate')
    fig.update_yaxes(tickformat='.1%');return fig

def cpu_usage_chart(rows):return _chart(rows,[('cpu_percent','Docker CPU')],'CPU (%; 100% = one CPU)','Sampled application CPU',x='timestamp')
def memory_usage_chart(rows):
    data=[dict(r,memory_mib=r['memory_bytes']/1024**2 if r.get('memory_bytes') is not None else None,
        limit_mib=r['memory_limit_bytes']/1024**2 if r.get('memory_limit_bytes') is not None else None) for r in rows]
    return _chart(data,[('memory_mib','Sampled usage'),('limit_mib','Memory limit')],'Memory (MiB)','Sampled application memory',x='timestamp')

def before_after_comparison(baseline,candidate):
    fig=go.Figure()
    for row,label in ((baseline,'Baseline'),(candidate,'Candidate')):
        fig.add_bar(name=label,x=['p50','p95','p99'],y=[row.get(k) for k in ('p50_ms','p95_ms','p99_ms')])
    fig.update_layout(title='Observed before / after latency',yaxis_title='Latency (ms)',barmode='group',height=320)
    return fig

def repair_history_chart(rows):
    fig=go.Figure()
    frame=pd.DataFrame(rows)
    if 'status' in frame:
        counts=frame['status'].value_counts();fig.add_bar(x=counts.index,y=counts.values,name='Sessions')
    fig.update_layout(title='Repair session outcomes',yaxis_title='Sessions',height=280);return fig
