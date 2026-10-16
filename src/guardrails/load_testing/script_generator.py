"""Fixed template. Only JSON-serialized validated data enters JavaScript."""
import json
from guardrails.load_testing.models import PerformanceTestConfig


def generate_script(config: PerformanceTestConfig, port: int, *, probe: bool = False) -> str:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Invalid trusted application port")
    config = PerformanceTestConfig.model_validate(config.model_dump())
    options = {
        "scenarios": {"controlled": {"executor": "constant-vus", "vus": 1 if probe else config.vus,
            "duration": "1s" if probe else f"{config.duration_seconds}s", "gracefulStop": "2s"}},
        "summaryTrendStats": ["med", "p(50)", "p(95)", "p(99)"],
        "maxRedirects": 0, "discardResponseBodies": True,
        "noCookiesReset": False,
        "thresholds": {"http_req_failed": [f"rate<={0 if probe else config.error_rate_budget}"],
                       "http_req_duration": [f"p(95)<={config.p95_latency_budget_ms}"]},
    }
    if probe:
        options["scenarios"] = {"ready": {"executor": "shared-iterations", "vus": 1,
            "iterations": 1, "maxDuration": "7s", "gracefulStop": "1s"}}
    action = (
        "  let response;\n"
        "  for (let i = 0; i < 10; i++) {\n"
        "    response = http.get('http://' + __ENV.APP_IP + ':' + port + path, {timeout: '500ms', redirects: 0});\n"
        "    if (response.status >= 200 && response.status < 300) break;\n"
        "    sleep(0.1);\n  }\n"
        "  check(response, {ready: r => r.status >= 200 && r.status < 300});\n"
    ) if probe else (
        "  const response = http.get('http://' + __ENV.APP_IP + ':' + port + path, {timeout: '3s', redirects: 0});\n"
        "  responsesReceived.add(response.status > 0 ? 1 : 0);\n"
        "  sleep(0.01);\n"
    )
    # Literal IP is supplied separately by trusted Docker inspection; hostname
    # resolution cannot redirect traffic outside the application sandbox.
    return ("import http from 'k6/http';\nimport { sleep, check } from 'k6';\n" +
        "import { Counter } from 'k6/metrics';\nconst responsesReceived = new Counter('responses_received');\n" +
        "export const options = " + json.dumps(options, sort_keys=True) + ";\n" +
        "const path = " + json.dumps(config.endpoint_path) + ";\n" +
        f"const port = {port};\n" +
        "export default function () {\n"
        + action + "}\n"
        "export function handleSummary(data) { return {stdout: 'GUARDRAILS_SUMMARY=' + JSON.stringify(data) + '\\n'}; }\n")
