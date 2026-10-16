"""Official MCP SDK v2 registration and local stdio entry point."""
import logging
import os
import sys

from mcp.server import MCPServer

from guardrails.config import SERVICE_NAME
from guardrails.tools.health import get_system_info, health_check
from guardrails.tools.validation import validate_test_config
from guardrails.tools.sandbox import (
    check_docker, create_sandbox, start_sandbox, inspect_sandbox,
    get_sandbox_logs, destroy_sandbox, get_manager,
)

from guardrails.tools.performance import (
    prepare_test_environment, run_performance_test,
    get_performance_results, evaluate_performance_budget,
)

from guardrails.tools.diagnostics import (
    analyze_performance, compare_performance_tests, get_diagnostic_report, get_diagnostic_context,
)

from guardrails.tools.repair import (
    create_repair_session, register_repair_candidate, verify_repair_candidate, get_repair_status,
    finalize_repair_session, get_repair_manager,
)

mcp = MCPServer(SERVICE_NAME)
for tool in (create_repair_session, register_repair_candidate, verify_repair_candidate, get_repair_status, finalize_repair_session):
    mcp.tool()(tool)
for tool in (analyze_performance, compare_performance_tests, get_diagnostic_report, get_diagnostic_context):
    mcp.tool()(tool)
for tool in (prepare_test_environment, run_performance_test, get_performance_results, evaluate_performance_budget):
    mcp.tool()(tool)
mcp.tool()(health_check)
mcp.tool()(get_system_info)
mcp.tool()(validate_test_config)
for tool in (check_docker, create_sandbox, start_sandbox, inspect_sandbox, get_sandbox_logs, destroy_sandbox):
    mcp.tool()(tool)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    cleanup = get_manager().cleanup_expired()
    if cleanup.errors or cleanup.remaining_expired:
        logging.warning("Startup cleanup incomplete: %s errors, %s expired records remain",
                        len(cleanup.errors), cleanup.remaining_expired)
    recovery = get_manager().cleanup_abandoned_runners()
    if recovery.errors:
        logging.warning("Abandoned runner cleanup incomplete: %s errors", len(recovery.errors))
    if os.environ.get('GUARDRAILS_REPAIR_POLICY_FILE'):
        try:
            if get_repair_manager().recover():logging.warning('Repair resources remain after recovery')
        except Exception:logging.warning('Repair recovery incomplete; run the repair recover CLI')
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
