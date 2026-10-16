import csv
import io
import json
import streamlit as st


def safe_export_record(row):
    record=json.loads(row['record_json']) if 'record_json' in row else dict(row)
    if record.get('historical_logs'):
        record['historical_logs']['text']='[Raw log text omitted from export]'
    return record


def csv_export(rows):
    columns=['test_id','sandbox_id','started_at','status','endpoint','vus','duration_seconds','p50_ms','p95_ms','p99_ms','throughput_rps','error_rate','request_count','budget_status']
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=columns);writer.writeheader()
    for row in rows:
        values={key:row.get(key) for key in columns}
        for key,value in values.items():
            if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')):values[key]="'"+value
        writer.writerow(values)
    return out.getvalue()


def exports(rows):
    st.download_button('Export displayed page · CSV',csv_export(rows),file_name='benchmarks.csv',mime='text/csv')
    st.download_button('Export displayed page · JSON',json.dumps([safe_export_record(r) for r in rows],indent=2),file_name='benchmarks.json',mime='application/json')


def benchmark_table(rows):
    fields=('test_id','started_at','status','endpoint','vus','duration_seconds','p95_ms','throughput_rps','budget_status')
    st.dataframe([{k:r.get(k) for k in fields} for r in rows],hide_index=True,width='stretch')
