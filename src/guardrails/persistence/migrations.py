"""Explicit, bounded, idempotent JSON import. Never edit or delete source artifacts."""
from uuid import uuid4
from guardrails.diagnostics.io import read_json
from guardrails.docker.models import RegistryData
from guardrails.persistence.database import utc
from guardrails.persistence.indexer import ArtifactIndexer


def import_artifacts(state_dir,limit=5000):
    if type(limit) is not int or not 1<=limit<=10000:raise ValueError('Import limit must be 1–10000')
    index=ArtifactIndexer(state_dir);start=utc();counts=dict(imported=0,unchanged=0,skipped=0,errors=0)
    registry=state_dir/'registry.json'
    if registry.exists():
        try:index.registry(RegistryData.model_validate(read_json(registry)))
        except ValueError:counts['errors']+=1
    paths=[]
    for directory,pattern in [('results','*.json'),('performance','*.json'),('diagnostics','*.json'),('repair','*.session.json'),('repair','*.candidate.json'),('repair','*.verification.json')]:
        for path in sorted((state_dir/directory).glob(pattern)):
            if path.name.endswith(('.ownership.json','.summary.json','.traces.json')):continue
            if len(paths)>=limit:break
            paths.append(path)
    for path in paths:
        try:counts[index.artifact(path).lower()]+=1
        except Exception:
            counts['errors']+=1;index.failure(path)
    with index.db.connect(write=True) as con:
        con.execute('INSERT INTO import_runs VALUES(?,?,?,?,?,?,?)',(str(uuid4()),start,utc(),counts['imported'],counts['unchanged'],counts['skipped'],counts['errors']))
    return counts
