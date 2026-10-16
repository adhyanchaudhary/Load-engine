import asyncio
from uuid import uuid4
import anyio
from mcp import Client
from guardrails.server import mcp
from guardrails.tools import performance
from guardrails.load_testing.runner import LoadEngine
from test_load_runner import workload
from test_sandbox import engine
from test_metrics_parser import summary


def test_four_tools_and_persisted_results(workload,monkeypatch):
    e,m,c,a,sid=workload
    monkeypatch.setattr(performance,'get_manager',lambda:m)
    monkeypatch.setattr(LoadEngine,'_execute',lambda *args:(summary(),0,False))
    async def scenario():
        async with Client(mcp) as client:
            found={t.name for t in (await client.list_tools()).tools}
            assert {'prepare_test_environment','run_performance_test','get_performance_results','evaluate_performance_budget'}<=found
            r=await client.call_tool('run_performance_test',{'sandbox_id':sid,'endpoint_path':'/','duration_seconds':1})
            assert not r.is_error
            data=r.structured_content
            assert data['budget']['status']=='PASS'
            loaded=await client.call_tool('get_performance_results',{'test_id':data['test_id']})
            assert loaded.structured_content['metrics']['request_count']==100
            budget=await client.call_tool('evaluate_performance_budget',{'test_id':data['test_id']})
            assert budget.structured_content['status']=='PASS'
            bad=await client.call_tool('run_performance_test',{'sandbox_id':sid,'endpoint_path':'http://evil/'})
            assert bad.is_error
            unknown=await client.call_tool('prepare_test_environment',{'sandbox_id':str(uuid4())})
            assert unknown.structured_content['readiness_status']=='unavailable'
    asyncio.run(scenario())


def test_async_cancellation_waits_for_worker_cleanup(monkeypatch):
    import threading
    entered=threading.Event();cleaned=threading.Event()
    class Engine:
        def __init__(self,manager,cancelled):self.cancelled=cancelled
        def prepare(self,arg):
            entered.set()
            assert self.cancelled.wait(3)
            cleaned.set()
    monkeypatch.setattr(performance,'LoadEngine',Engine)
    monkeypatch.setattr(performance,'get_manager',lambda:None)
    async def scenario():
        async with anyio.create_task_group() as group:
            group.start_soon(performance.prepare_test_environment,'sandbox')
            while not entered.is_set():await anyio.sleep(.01)
            group.cancel_scope.cancel()
        assert cleaned.is_set()
    asyncio.run(scenario())
