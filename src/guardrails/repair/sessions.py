"""Explicit state transitions and compact persisted audit events."""
from datetime import datetime,timezone


def now():return datetime.now(timezone.utc)


TRANSITIONS={
    'CREATED':{'AWAITING_CANDIDATE','FAILED'},
    'AWAITING_CANDIDATE':{'VERIFYING','COMPLETED','FAILED','CANCELLED'},
    'VERIFYING':{'VERIFIED','REJECTED','FAILED','CANCELLED'},
    'VERIFIED':{'COMPLETED','FAILED'},
    'REJECTED':{'AWAITING_CANDIDATE','COMPLETED','FAILED','CANCELLED'},
    'FAILED':set(),'CANCELLED':set(),'COMPLETED':set(),
}


def transition(session,state,reason=None):
    if state not in TRANSITIONS[session.status]:raise ValueError('Invalid repair-session state transition')
    session.status=state;session.updated_at=now();session.failure_reason=reason
    session.audit.append({'state':state,'timestamp':session.updated_at.isoformat(),'reason':reason})
