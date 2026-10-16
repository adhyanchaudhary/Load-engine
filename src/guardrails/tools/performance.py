"""Four typed MCP tools; arbitrary hosts, scripts, and filesystem paths are absent."""
from threading import Event
import anyio

from guardrails.tools.sandbox import get_manager
from guardrails.load_testing.runner import LoadEngine
from guardrails.load_testing.models import PerformanceTestConfig, TestRecord, EnvironmentResult, BudgetResult
from guardrails.load_testing.thresholds import evaluate


async def _invoke(method, argument):
    """Keep Docker work off the MCP loop; cancellation waits for owned cleanup."""
    cancelled = Event()
    done = anyio.Event()
    results, errors = [], []

    async def worker():
        with anyio.CancelScope(shield=True):
            try:
                from guardrails.application import make_benchmark_service
                engine = make_benchmark_service(get_manager(),cancelled=cancelled,engine_factory=LoadEngine)
                results.append(await anyio.to_thread.run_sync(lambda: getattr(engine, method)(argument)))
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

    async with anyio.create_task_group() as group:
        group.start_soon(worker)
        try:
            await done.wait()
        except BaseException:
            cancelled.set()
            raise
    if errors:
        raise errors[0]
    return results[0]


async def prepare_test_environment(sandbox_id: str) -> EnvironmentResult:
    """Verify owned internal networking and HTTP readiness using approved local k6."""
    return await _invoke("prepare", sandbox_id)


async def run_performance_test(sandbox_id: str, endpoint_path: str, vus: int = 10,
        duration_seconds: int = 30, p95_latency_budget_ms: float = 250,
        error_rate_budget: float = 0.01) -> TestRecord:
    """Run one bounded GET benchmark against the prepared owned application only."""
    config = PerformanceTestConfig(sandbox_id=sandbox_id, endpoint_path=endpoint_path,
        vus=vus, duration_seconds=duration_seconds, p95_latency_budget_ms=p95_latency_budget_ms,
        error_rate_budget=error_rate_budget)
    return await _invoke("run", config)


def get_performance_results(test_id: str) -> TestRecord:
    """Read a persisted test by generated UUID; null metrics mean unavailable."""
    return LoadEngine(get_manager()).store.get(test_id)


def evaluate_performance_budget(test_id: str) -> BudgetResult:
    """Evaluate persisted measurements: PASS, FAIL, or INCONCLUSIVE."""
    return evaluate(get_performance_results(test_id))
