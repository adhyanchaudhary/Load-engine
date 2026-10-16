import asyncio
from uuid import uuid4
from mcp import Client
from guardrails.server import mcp
from guardrails.tools import diagnostics
from diagnostic_fixtures import artifacts


def test_four_diagnostic_tools(artifacts,monkeypatch):
    monkeypatch.setattr(diagnostics,'get_service',lambda:artifacts.service)
    baseline=artifacts.make();candidate=artifacts.make('cpu',offset=60)
    async def scenario():
        async with Client(mcp) as client:
            found={t.name for t in (await client.list_tools()).tools}
            assert {'analyze_performance','compare_performance_tests','get_diagnostic_report','get_diagnostic_context'}<=found
            summary=await client.call_tool('analyze_performance',{'test_id':candidate.test_id,'baseline_test_id':baseline.test_id})
            assert not summary.is_error
            rid=summary.structured_content['report_id']
            report=await client.call_tool('get_diagnostic_report',{'report_id':rid})
            assert report.structured_content['evidence']
            context=await client.call_tool('get_diagnostic_context',{'report_id':rid,'max_characters':1000})
            assert context.structured_content['characters']<=1000
            compare=await client.call_tool('compare_performance_tests',{'baseline_test_id':baseline.test_id,'candidate_test_id':candidate.test_id})
            assert compare.structured_content['regression_classification']=='REGRESSION'
            bad=await client.call_tool('analyze_performance',{'test_id':str(uuid4())})
            assert bad.is_error
            assert not (await client.call_tool('health_check',{})).is_error
    asyncio.run(scenario())
