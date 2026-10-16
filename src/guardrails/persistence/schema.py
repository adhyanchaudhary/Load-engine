"""SQLite v1. Detailed artifacts remain available as versioned JSON."""
SCHEMA_VERSION=1
DDL='''
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sandboxes(
 sandbox_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, container_id TEXT, image_identifier TEXT,
 cpu_limit REAL, memory_limit_mb REAL, status TEXT NOT NULL, health TEXT, created_at TEXT, expires_at TEXT,
 last_observed_at TEXT, last_error TEXT, managed INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sandbox_owner ON sandboxes(owner_id,status);
CREATE TABLE IF NOT EXISTS benchmarks(
 test_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, sandbox_id TEXT NOT NULL REFERENCES sandboxes(sandbox_id),
 application_revision TEXT, started_at TEXT, completed_at TEXT, status TEXT NOT NULL, endpoint TEXT,
 method TEXT, vus INTEGER, duration_seconds INTEGER, p50_ms REAL, p95_ms REAL, p99_ms REAL,
 throughput_rps REAL, error_rate REAL, request_count INTEGER, budget_status TEXT,
 configuration_json TEXT NOT NULL, raw_summary_path TEXT, workload_key TEXT NOT NULL,
 provenance TEXT NOT NULL, record_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS benchmark_history ON benchmarks(owner_id,started_at DESC);
CREATE INDEX IF NOT EXISTS benchmark_workloads ON benchmarks(owner_id,workload_key,started_at);
CREATE TABLE IF NOT EXISTS resource_samples(
 sample_id INTEGER PRIMARY KEY, test_id TEXT NOT NULL REFERENCES benchmarks(test_id) ON DELETE CASCADE,
 sample_index INTEGER NOT NULL, timestamp TEXT, cpu_percent REAL, memory_bytes INTEGER, memory_limit_bytes INTEGER,
 UNIQUE(test_id,sample_index));
CREATE TABLE IF NOT EXISTS diagnostic_reports(
 report_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, test_id TEXT NOT NULL REFERENCES benchmarks(test_id),
 baseline_test_id TEXT REFERENCES benchmarks(test_id), generated_at TEXT NOT NULL, status TEXT NOT NULL,
 regression_status TEXT, report_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS diagnostic_history ON diagnostic_reports(owner_id,generated_at DESC);
CREATE TABLE IF NOT EXISTS repair_sessions(
 session_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
 diagnostic_report_id TEXT REFERENCES diagnostic_reports(report_id), baseline_test_id TEXT REFERENCES benchmarks(test_id),
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, accepted_candidate_id TEXT,
 record_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS repair_history ON repair_sessions(owner_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS repair_candidates(
 candidate_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES repair_sessions(session_id),
 commit_sha TEXT NOT NULL, source_digest TEXT NOT NULL, verification_status TEXT NOT NULL,
 correctness_status TEXT, benchmark_test_id TEXT REFERENCES benchmarks(test_id), improvement_percent REAL,
 created_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repair_verifications(
 verification_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES repair_sessions(session_id),
 candidate_id TEXT NOT NULL REFERENCES repair_candidates(candidate_id), verdict TEXT NOT NULL,
 started_at TEXT NOT NULL, ended_at TEXT, report_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 job_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, request_key TEXT NOT NULL, kind TEXT NOT NULL,
 payload_json TEXT NOT NULL, status TEXT NOT NULL, worker_id TEXT NOT NULL, exclusive_key TEXT,
 created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT, result_json TEXT, error TEXT,
 UNIQUE(owner_id,request_key));
CREATE INDEX IF NOT EXISTS active_jobs ON jobs(owner_id,status);
CREATE TABLE IF NOT EXISTS audit_events(
 event_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, timestamp TEXT NOT NULL, actor TEXT NOT NULL,
 action TEXT NOT NULL, target_id TEXT, outcome TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS audit_history ON audit_events(owner_id,timestamp DESC);
CREATE TABLE IF NOT EXISTS artifact_imports(
 source TEXT PRIMARY KEY, digest TEXT, status TEXT NOT NULL, detail TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_runs(
 run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT NOT NULL, imported INTEGER NOT NULL,
 unchanged INTEGER NOT NULL, skipped INTEGER NOT NULL, errors INTEGER NOT NULL);
'''
