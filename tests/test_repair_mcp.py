import asyncio
from mcp import Client
from guardrails.server import mcp
from guardrails.tools import repair as tools
from repair_fixtures import repair,fake_verification


def test_five_tools_and_verified_workflow(repair,monkeypatch):
    fake_verification(repair,monkeypatch)
    monkeypatch.setattr(tools,'get_repair_manager',lambda cancelled=None:repair.manager)
    async def scenario():
        async with Client(mcp) as client:
            names={t.name for t in (await client.list_tools()).tools}
            assert {'create_repair_session','register_repair_candidate','verify_repair_candidate','get_repair_status','finalize_repair_session'}<=names
            result=await client.call_tool('create_repair_session',{'diagnostic_report_id':repair.diagnostic.report_id,'approved_project_id':repair.project_id})
            sid=result.structured_content['session_id']
            c=await client.call_tool('register_repair_candidate',{'session_id':sid,'candidate_revision':repair.revision})
            cid=c.structured_content['candidate_id']
            verified=await client.call_tool('verify_repair_candidate',{'session_id':sid,'candidate_id':cid})
            assert verified.structured_content['verdict']=='VERIFIED'
            status=await client.call_tool('get_repair_status',{'session_id':sid})
            assert status.structured_content['attempts_remaining']==2
            final=await client.call_tool('finalize_repair_session',{'session_id':sid,'candidate_id':cid,'decision':'ACCEPT_FOR_REVIEW'})
            assert final.structured_content['status']=='COMPLETED'
    asyncio.run(scenario())


def test_repair_async_cancellation_waits_for_worker(monkeypatch):
    import threading
    import anyio
    entered=threading.Event();cleaned=threading.Event()
    class Manager:
        def __init__(self,cancelled):self.cancelled=cancelled
        def verify(self,*args):
            entered.set()
            assert self.cancelled.wait(3)
            cleaned.set()
    monkeypatch.setattr(tools,'get_repair_manager',lambda cancelled=None:Manager(cancelled))
    async def scenario():
        async with anyio.create_task_group() as group:
            group.start_soon(tools.verify_repair_candidate,'session','candidate')
            while not entered.is_set():await anyio.sleep(.01)
            group.cancel_scope.cancel()
        assert cleaned.is_set()
    asyncio.run(scenario())
