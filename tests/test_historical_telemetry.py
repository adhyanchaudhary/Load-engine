from datetime import datetime,timezone
from unittest.mock import MagicMock
from guardrails.load_testing.telemetry import capture_logs
from test_thresholds import record


def test_historical_log_capture_bounded_redacted_and_closed():
    r=record();container=MagicMock()
    def chunks():
        yield b'2026-01-01T00:00:00Z timeout token=very-secret user@example.com\n'
        yield b'x'*20000
    stream=chunks();container.logs.return_value=stream
    capture_logs(container,r)
    assert r.historical_logs.untrusted and r.historical_logs.truncated
    assert len(r.historical_logs.text)<=16384
    assert 'very-secret' not in r.historical_logs.text and 'user@example' not in r.historical_logs.text
    assert stream.gi_frame is None
    args=container.logs.call_args.kwargs
    assert args['timestamps'] and not args['follow'] and args['tail']==200
    assert args['since']==r.started_at and args['until'].tzinfo is not None


def test_historical_collection_failure_stays_explicit():
    r=record();c=MagicMock();c.logs.side_effect=RuntimeError('private daemon information')
    capture_logs(c,r)
    assert r.historical_logs.error and 'private daemon' not in r.historical_logs.error
