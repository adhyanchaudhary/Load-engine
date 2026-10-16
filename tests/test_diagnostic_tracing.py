import json
from datetime import timedelta
import pytest
from guardrails.diagnostics.tracing import TraceAdapter
from diagnostic_fixtures import artifacts


@pytest.mark.parametrize('mutation',['credentials','naive','wrong_owner','oversize','backwards'])
def test_strict_local_trace_format(artifacts,mutation):
    r=artifacts.make()
    data=dict(schema_version=1,test_id=r.test_id,owner_id=artifacts.owner,events=[dict(event_id='db1',kind='database',
        started_at=r.started_at.isoformat(),ended_at=(r.started_at+timedelta(seconds=1)).isoformat())])
    if mutation=='credentials':data['events'][0]['query']='select secret'
    if mutation=='naive':data['events'][0]['started_at']='2026-01-01T00:00:00'
    if mutation=='wrong_owner':data['owner_id']='00000000-0000-0000-0000-000000000000'
    if mutation=='oversize':data['events']*=1001
    if mutation=='backwards':data['events'][0]['ended_at']=(r.started_at-timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):TraceAdapter(artifacts.store).ingest(r.test_id,json.dumps(data).encode())
