"""Five bounded repair MCP adapters. No patches, paths, shell commands or deployments."""
import os
from pathlib import Path
from threading import Event
import anyio
from guardrails.tools.sandbox import get_manager
from guardrails.repair.manager import RepairManager
from guardrails.repair.models import RepairSession,RepairCandidate,VerificationReport,RepairStatus
from typing import Literal


def get_repair_manager(cancelled=None):
    from guardrails.application import services_for
    return services_for(get_manager()).repair_manager(cancelled=cancelled)


def create_repair_session(diagnostic_report_id: str,approved_project_id: str,maximum_attempts: int=3) -> RepairSession:
    """Start a bounded owned session from a diagnostic report and approved source project."""
    return get_repair_manager().create(diagnostic_report_id,approved_project_id,maximum_attempts)


def register_repair_candidate(session_id: str,candidate_revision: str) -> RepairCandidate:
    """Register a full commit hash from the approved repository; never accept code or paths."""
    return get_repair_manager().register(session_id,candidate_revision)


async def verify_repair_candidate(session_id: str,candidate_id: str) -> VerificationReport:
    """Run the fixed isolated recipe and repeated owned benchmarks; cancellation waits for cleanup."""
    cancelled=Event();done=anyio.Event();results=[];errors=[]
    async def worker():
        with anyio.CancelScope(shield=True):
            try:results.append(await anyio.to_thread.run_sync(lambda:get_repair_manager(cancelled).verify(session_id,candidate_id)))
            except BaseException as exc:errors.append(exc)
            finally:done.set()
    async with anyio.create_task_group() as group:
        group.start_soon(worker)
        try:await done.wait()
        except BaseException:cancelled.set();raise
    if errors:raise errors[0]
    return results[0]


def get_repair_status(session_id: str) -> RepairStatus:
    """Read bounded candidates, verification evidence, attempts remaining and reasons."""
    return get_repair_manager().status(session_id)


def finalize_repair_session(session_id: str,candidate_id: str,decision: Literal['ACCEPT_FOR_REVIEW','REJECT']) -> RepairSession:
    """Finalize verified evidence for human review, or reject. Never merge, push or deploy."""
    return get_repair_manager().finalize(session_id,candidate_id,decision)
