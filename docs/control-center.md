# Milestone 6 control center

## Shared execution

`ApplicationServices` supplies the existing SandboxManager, LoadEngine,
DiagnosticService and RepairManager to both MCP and Streamlit. JSON write hooks
project successful writes into SQLite. Existing tool return contracts remain intact.
No secondary diagnostic rules, load runner or Docker daemon is introduced.

`dashboard/app.py` uses `st.Page` and `st.navigation` for six pages: overview,
run test, benchmark history, diagnostics, repair history and sandbox manager.
Components contain native status/metrics, bounded tables/exports and Plotly charts.
The navy theme uses supported Streamlit configuration. Logs use bounded text/code
rendering; exports omit raw diagnostic log text and protect CSV formula cells.

## SQLite and retained artifacts

`$GUARDRAILS_STATE_DIR/performance.db` uses schema version 1 (`user_version` and
`schema_migrations`). Tables are sandboxes, benchmarks, resource_samples,
diagnostic_reports, repair_sessions, repair_candidates, repair_verifications, jobs,
audit_events, artifact_imports and import_runs. UUIDs and existing relationships
are preserved, with foreign keys and owner/time/workload indexes. Baseline-only
repair audit sessions with zero UUID references use SQL NULL, retaining their
original JSON. Unknown measurements remain NULL, never zero substitutes.

Each operation opens and closes its own connection, enables foreign keys and a
five-second busy timeout. WAL permits concurrent readers; writes use short
BEGIN IMMEDIATE transactions. Migration has a local file lock. Connections are
never cached or shared between threads. ISO 8601 timezone-aware times normalize
to UTC. SQL filters are parameterized and pagination/chart queries bounded.

The explicit `uv run python -m guardrails.persistence import` imports retained
performance, diagnostic and repair JSON in dependency order, without editing source
files. It defaults to at most 5,000 files (`--limit`, maximum 10,000). Digests and
upserts make retries idempotent; benchmark fingerprints include receipt contents
and raw-summary availability. Malformed, foreign or unresolved records roll back
individually and appear in import issues; originals remain intact. Experimental
`results/` JSON is skipped because it lacks the supported record contract.
Missing raw summaries remain unavailable references. Import counts are persisted.

JSON remains the compatibility and ownership evidence store; SQLite is the durable
query projection and authoritative job/audit store. An indexing failure preserves
JSON and is retryable by explicit import. Legacy benchmarks without an ownership
receipt are visibly unverified history; importing them does not authorize diagnostic
or repair operations. Existing operator attestation rules remain in force.

## Jobs and review safety

Two local worker threads execute allowlisted validated actions. Durable job IDs and
owner/request-key uniqueness deduplicate repeated submissions. Concurrent tests on
one sandbox are excluded. UI state retains selection and intent identifiers, while
job status comes from SQLite. Cancellation is explicit and cooperatively observed
by the existing engines. A two-second fragment polls status without rerender loops.

A worker lifetime file lock distinguishes running from abandoned work. Restart marks
orphaned jobs INTERRUPTED and attempts owned runner cleanup; it never silently
replays tests or claims computation survived a crash. Cancellation is also checked
through SQLite across local processes. Cleanup failures require operator inspection.
Do not run this database on a network filesystem or share it across hosts.

Sandbox actions accept registry-owned UUIDs and approved image aliases. Destruction
and repair finalization require explicit confirmation. Lifecycle outcomes and shared
application actions are audited; repair transitions retain their original audit trail.
ACCEPT_FOR_REVIEW delegates to RepairManager, including its persisted verification
and compatibility checks. No dashboard action edits, merges, pushes or deploys code.

## Analytics and limitations

Latency trends use endpoint/method/load and available environment metadata to define
compatible populations. Missing compatibility metadata isolates a run. Mixed-population
p95 medians and trends are suppressed. KPI pass rate counts only completed tests in
the selected window/population, with inconclusive outcomes excluded from the numerator.
Median p95 is a median of per-run percentiles, not a pooled request percentile.
History exports include only the bounded displayed page; resource plots retain gaps.
Historical container snapshots are labeled separately from explicit live inspections;
expired entries are not counted as active.

All six pages have AppTest coverage. The opt-in dashboard integration submits a real
form, runs approved Docker/k6 fixtures through the job manager, checks deduplication,
SQLite restart retrieval, diagnostics, exports and owned cleanup. Ordinary tests need
no Docker. Synthetic unit fixtures are isolated from real operator state.

Viewing history needs no running daemon or repair policy. Creation/testing needs
approved local application and k6 images, daemon support and adequate runner CPU.
Repair creation/verification needs the existing approved project policy and evidence.
No previous capability is simulated when unavailable. Browser screenshots are optional
verification; automated service and page tests remain the functional checks.

Default startup is `uv run streamlit run src/guardrails/dashboard/app.py` from the
repository root, listening on 127.0.0.1:8501. Localhost is not authentication. Remote
hosting requires authentication, authorization, transport security and stronger
operational isolation. These and deployment/CI/CD integration remain Milestone 7
work, not functionality implemented here.
