"""Owned, bounded atomic JSON and immutable source archives in application-controlled paths."""
from uuid import UUID
from filelock import FileLock
from guardrails.load_testing.storage import ResultStore
from guardrails.diagnostics.io import read_json,read_bytes,ArtifactError
from guardrails.diagnostics.ownership import current_owner
from guardrails.repair.models import RepairSession,RepairCandidate,VerificationReport


class RepairStore:
    def __init__(self,state_dir):
        self.state_dir=state_dir;self.directory=state_dir/'repair'
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.files=ResultStore(state_dir);self.files.directory=self.directory

    def lock(self,session_id):return FileLock(str(self.directory/(str(UUID(session_id))+'.lock')),timeout=0)

    def save(self,value):
        identifier=getattr(value,'session_id')
        if isinstance(value,RepairCandidate):identifier=value.candidate_id;suffix='.candidate.json'
        elif isinstance(value,VerificationReport):identifier=value.verification_id;suffix='.verification.json'
        else:suffix='.session.json'
        raw=value.model_dump_json(indent=2).encode()
        if len(raw)>1024*1024:raise ArtifactError('Repair artifact exceeds size bound')
        self.files.write(identifier,suffix,raw)

    def get(self,identifier,kind):
        identifier=str(UUID(identifier))
        model,suffix,field={'session':(RepairSession,'.session.json','session_id'),
            'candidate':(RepairCandidate,'.candidate.json','candidate_id'),
            'verification':(VerificationReport,'.verification.json','verification_id')}[kind]
        try:
            value=model.model_validate(read_json(self.files._path(identifier,suffix),1024*1024))
            if getattr(value,field)!=identifier or value.owner_id!=current_owner(self.state_dir):raise ValueError()
            return value
        except ValueError:raise ArtifactError('Repair artifact missing, malformed or not owned') from None

    def snapshot(self,identifier,data):
        if len(data)>3*1024*1024:raise ArtifactError('Snapshot too large')
        path=self.files._path(identifier,'.source.tar')
        if path.exists():raise ArtifactError('Immutable snapshot already exists')
        self.files.write(identifier,'.source.tar',data)

    def read_snapshot(self,identifier):return read_bytes(self.files._path(identifier,'.source.tar'),3*1024*1024)
