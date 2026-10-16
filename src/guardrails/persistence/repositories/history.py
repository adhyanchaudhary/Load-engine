from datetime import datetime,timezone
import json
from uuid import UUID
from guardrails.diagnostics.ownership import current_owner
from guardrails.persistence.database import utc


class HistoryRepository:
    def __init__(self,database):self.db=database
    @property
    def owner(self):return current_owner(self.db.directory)

    def benchmarks(self,*,search='',status=None,sandbox_id=None,endpoint=None,since=None,until=None,workload_key=None,page=0,page_size=25,ascending=False):
        if type(page) is not int or not 0<=page<=100000 or not 1<=page_size<=200:raise ValueError('Invalid pagination')
        where=['owner_id=?'];args=[self.owner]
        for column,value in (('status',status),('sandbox_id',sandbox_id),('endpoint',endpoint),('workload_key',workload_key)):
            if value is not None:where.append(column+'=?');args.append(value)
        if search:
            if len(search)>64:raise ValueError('Search is too long')
            where.append("test_id LIKE ? ESCAPE '\\'");args.append('%'+search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        if since:where.append('started_at>=?');args.append(utc(since))
        if until:where.append('started_at<=?');args.append(utc(until))
        clause=' AND '.join(where)
        count=self.db.rows('SELECT COUNT(*) AS total FROM benchmarks WHERE '+clause,args)[0]['total']
        rows=self.db.rows('SELECT * FROM benchmarks WHERE '+clause+' ORDER BY started_at '+('ASC' if ascending else 'DESC')+',test_id LIMIT ? OFFSET ?',args+[page_size,page*page_size])
        return rows,count

    def benchmark(self,test_id):
        rows=self.db.rows('SELECT * FROM benchmarks WHERE test_id=? AND owner_id=?',(str(UUID(test_id)),self.owner))
        if not rows:raise ValueError('Benchmark not found in owned history')
        return rows[0]

    def samples(self,test_id):
        self.benchmark(test_id)
        return self.db.rows('SELECT timestamp,cpu_percent,memory_bytes,memory_limit_bytes FROM resource_samples WHERE test_id=? ORDER BY sample_index LIMIT 150',(test_id,))

    def populations(self):
        return self.db.rows('SELECT workload_key,endpoint,vus,duration_seconds,COUNT(*) AS records FROM benchmarks WHERE owner_id=? GROUP BY workload_key,endpoint,vus,duration_seconds ORDER BY MAX(started_at) DESC LIMIT 200',(self.owner,))

    def overview(self,*,since=None,workload_key=None):
        where='owner_id=?';args=[self.owner]
        if since:where+=' AND started_at>=?';args.append(utc(since))
        if workload_key:where+=' AND workload_key=?';args.append(workload_key)
        totals=self.db.rows("SELECT COUNT(*) AS completed,SUM(CASE WHEN budget_status='PASS' THEN 1 ELSE 0 END) AS passed FROM benchmarks WHERE "+where+" AND status='completed'",args)[0]
        populations=self.db.rows('SELECT COUNT(DISTINCT workload_key) AS n FROM benchmarks WHERE '+where,args)[0]['n']
        median_value=None
        if populations<=1:
            measured_where=where+" AND status='completed' AND p95_ms IS NOT NULL"
            count=self.db.rows('SELECT COUNT(*) AS n FROM benchmarks WHERE '+measured_where,args)[0]['n']
            if count:
                middle=self.db.rows('SELECT p95_ms FROM benchmarks WHERE '+measured_where+' ORDER BY p95_ms LIMIT ? OFFSET ?',[*args,2 if count%2==0 else 1,(count-1)//2])
                median_value=sum(r['p95_ms'] for r in middle)/len(middle)
        verified=self.db.rows("SELECT COUNT(*) AS n FROM repair_candidates WHERE owner_id=? AND verification_status='VERIFIED'",(self.owner,))[0]['n']
        active=self.db.rows("SELECT COUNT(*) AS n FROM sandboxes WHERE owner_id=? AND managed=1 AND status='running' AND expires_at>?",(self.owner,utc()))[0]['n']
        return dict(completed=totals['completed'],pass_rate=totals['passed']/totals['completed'] if totals['completed'] else None,
            median_p95_ms=median_value,verified_candidates=verified,active_sandboxes=active)

    def sandboxes(self):
        rows=self.db.rows('SELECT * FROM sandboxes WHERE owner_id=? ORDER BY created_at DESC LIMIT 200',(self.owner,))
        for row in rows:
            row['last_known_status']=row['status']
            if row['managed'] and row['expires_at'] and row['expires_at']<=utc():row['status']='EXPIRED'
        return rows

    def diagnostics(self,test_id=None):
        args=[self.owner];where='owner_id=?'
        if test_id:where+=' AND test_id=?';args.append(str(UUID(test_id)))
        return self.db.rows('SELECT * FROM diagnostic_reports WHERE '+where+' ORDER BY generated_at DESC LIMIT 100',args)

    def repairs(self):return self.db.rows('SELECT * FROM repair_sessions WHERE owner_id=? ORDER BY updated_at DESC LIMIT 100',(self.owner,))
    def candidates(self,session_id):
        return self.db.rows('SELECT * FROM repair_candidates WHERE owner_id=? AND session_id=? ORDER BY created_at LIMIT 5',(self.owner,str(UUID(session_id))))
    def verifications(self,candidate_id):
        return self.db.rows('SELECT * FROM repair_verifications WHERE owner_id=? AND candidate_id=? ORDER BY started_at DESC LIMIT 5',(self.owner,str(UUID(candidate_id))))
    def jobs(self):return self.db.rows('SELECT * FROM jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT 50',(self.owner,))
    def audit(self):return self.db.rows('SELECT * FROM audit_events WHERE owner_id=? ORDER BY timestamp DESC LIMIT 100',(self.owner,))
    def import_issues(self):return self.db.rows("SELECT source,detail,updated_at FROM artifact_imports WHERE status='ERROR' ORDER BY updated_at DESC LIMIT 100")
