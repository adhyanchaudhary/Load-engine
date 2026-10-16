"""Versioned local timings, independent of any observability server or SDK."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from guardrails.diagnostics.models import Contract
from guardrails.diagnostics.io import ArtifactError, read_json
from guardrails.diagnostics.ownership import owned_receipt


class TimingEvent(Contract):
    event_id: str = Field(max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    kind: Literal['database', 'http', 'connection_pool']
    started_at: datetime
    ended_at: datetime

    @field_validator('started_at','ended_at')
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None:raise ValueError('Trace timestamps require explicit UTC offsets')
        return value

    @model_validator(mode='after')
    def ordered(self):
        if not 0 <= (self.ended_at-self.started_at).total_seconds() <= 3600:
            raise ValueError('Invalid timing interval')
        return self

    @property
    def duration_ms(self):
        return (self.ended_at-self.started_at).total_seconds()*1000


class TraceBundle(Contract):
    schema_version: Literal[1] = 1
    test_id: str
    owner_id: str
    clock_origin: Literal['UTC'] = 'UTC'
    events: list[TimingEvent] = Field(max_length=1000)

    @field_validator('test_id','owner_id')
    @classmethod
    def uuid_value(cls,value):return str(UUID(value))


class TraceAdapter:
    def __init__(self,store):self.store=store

    def load(self,test_id):
        receipt=owned_receipt(self.store,test_id)
        path=self.store._path(test_id,'.traces.json')
        if not path.exists():return None
        bundle=TraceBundle.model_validate(read_json(path,512*1024))
        if bundle.test_id!=str(UUID(test_id)) or bundle.owner_id!=receipt['owner_id']:
            raise ArtifactError('Trace ownership mismatch')
        return bundle

    def ingest(self,test_id,content: bytes):
        """Operator-side Python API, not an MCP filesystem or write capability."""
        receipt=owned_receipt(self.store,test_id)
        if len(content)>512*1024:raise ArtifactError('Trace bundle exceeds size bound')
        bundle=TraceBundle.model_validate_json(content)
        if bundle.test_id!=str(UUID(test_id)) or bundle.owner_id!=receipt['owner_id']:
            raise ArtifactError('Trace ownership mismatch')
        self.store.write(test_id,'.traces.json',bundle.model_dump_json().encode())
