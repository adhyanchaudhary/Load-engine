"""Streamlit's entry to the same services used by MCP; no alternative engines."""
from guardrails.application import get_application_services


def get_services():return get_application_services()
