import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

from guardrails.server import mcp

EXPECTED_TOOLS = {"health_check", "get_system_info", "validate_test_config", "check_docker", "create_sandbox", "start_sandbox",
                  "inspect_sandbox", "get_sandbox_logs", "destroy_sandbox"}
VALID = dict(cpus=1.0, memory_mb=512, vus=10, duration_seconds=30)


async def exercise_client(transport):
    async with asyncio.timeout(30):
        async with Client(transport) as client:
            discovery = await client.list_tools()
            assert {tool.name for tool in discovery.tools} == EXPECTED_TOOLS
            for tool in discovery.tools:
                assert tool.output_schema is not None
            health = await client.call_tool("health_check", {})
            assert not health.is_error
            assert health.structured_content["status"] == "healthy"
            system = await client.call_tool("get_system_info", {})
            assert not system.is_error
            assert set(system.structured_content) == {
                "operating_system", "python_version", "cpu_count", "available_memory_mb",
            }
            valid = await client.call_tool("validate_test_config", VALID)
            assert not valid.is_error
            assert valid.structured_content == {"valid": True, "config": VALID, "errors": []}
            invalid = await client.call_tool("validate_test_config", VALID | {"cpus": 0.0})
            assert not invalid.is_error
            assert invalid.structured_content["valid"] is False
            assert invalid.structured_content["errors"][0]["field"] == "cpus"
            malformed = await client.call_tool("validate_test_config", {"cpus": "not-a-number"})
            assert malformed.is_error
            # Rejection must not terminate the connection or server.
            again = await client.call_tool("health_check", {})
            assert again.structured_content["status"] == "healthy"


def test_in_memory_mcp():
    asyncio.run(exercise_client(mcp))


def test_stdio_mcp(tmp_path):
    state_dir = tmp_path / "registry"
    asyncio.run(exercise_client(StdioServerParameters(
        command=sys.executable,
        args=["-m", "guardrails.server"],
        cwd=Path(__file__).resolve().parents[1],
        env={"GUARDRAILS_STATE_DIR": str(state_dir)},
    )))
