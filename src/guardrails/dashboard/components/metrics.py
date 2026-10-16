import streamlit as st


def number(value,unit='',digits=2):return 'Unavailable' if value is None else f'{value:,.{digits}f} {unit}'.strip()


def benchmark_metrics(row):
    cols=st.columns(4)
    cols[0].metric('p95 latency',number(row.get('p95_ms'),'ms'))
    cols[1].metric('p99 latency',number(row.get('p99_ms'),'ms'))
    cols[2].metric('Throughput',number(row.get('throughput_rps'),'req/s'))
    rate=row.get('error_rate')
    cols[3].metric('HTTP failures','Unavailable' if rate is None else f'{rate:.2%}')


def overview_metrics(data):
    cols=st.columns(4)
    cols[0].metric('Completed benchmarks',data['completed'])
    cols[1].metric('Budget pass rate','Unavailable' if data['pass_rate'] is None else f"{data['pass_rate']:.1%}")
    cols[2].metric('Median recorded p95',number(data['median_p95_ms'],'ms'))
    cols[3].metric('Verified candidates',data['verified_candidates'])
