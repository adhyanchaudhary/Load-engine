"""No raw log text enters reports. Defensive sanitization precedes classification."""
import re
from guardrails.docker.security import sanitize_logs


def redact(text):
    text = sanitize_logs(text.encode('utf-8', errors='replace')[:16384], False).text
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[REDACTED EMAIL]', text)
    text = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[REDACTED IP]', text)
    text = re.sub(r'(https?://[^\s?#]+)[?#][^\s]*', r'\1[REDACTED PARAMETERS]', text)
    return text[:16384]


SIGNATURES = {
    'http_error': re.compile(r'\bHTTP(?:/\d(?:\.\d)?)?[^\n]{0,80}\b[45]\d\d\b|\bstatus[=: ]+[45]\d\d\b', re.I),
    'exception': re.compile(r'\b(?:Traceback|[A-Za-z]+Exception|[A-Za-z]+Error)\b'),
    'timeout': re.compile(r'\b(?:timed?\s*out|timeout|deadline exceeded)\b', re.I),
    'connection_failure': re.compile(r'\b(?:connection (?:refused|reset|failed)|cannot connect|ECONNREFUSED)\b', re.I),
}


def signatures(text):
    safe = redact(text)
    return {name: len(pattern.findall(safe)) for name, pattern in SIGNATURES.items()}
