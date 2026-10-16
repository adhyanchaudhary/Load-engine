"""Small bounded local worker pool. Durable IDs/status, cancellation, no crash replay."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event,Lock
from uuid import uuid4,UUID
from filelock import FileLock,Timeout
from guardrails.persistence.database import utc

ACTIVE=('QUEUED','RUNNING','CANCEL_REQUESTED')


class PersistentCancellation:
    def __init__(self,db,job_id):
        self.db=db;self.job_id=job_id;self.event=Event();self.last_check=0
    def set(self):self.event.set()
    def is_set(self):
        import time
        if self.event.is_set():return True
        if time.monotonic()-self.last_check>.25:
            self.last_check=time.monotonic()
            try:
                rows=self.db.rows('SELECT status FROM jobs WHERE job_id=?',(self.job_id,))
                if not rows or rows[0]['status']=='CANCEL_REQUESTED':self.event.set()
            except Exception:self.event.set()
        return self.event.is_set()


class JobManager:
    def __init__(self,services,concurrency=2):
        self.services=services;self.db=services.persistence;self.concurrency=concurrency
        self.worker_id=str(uuid4());self.events={};self.futures={};self.guard=Lock()
        self.worker_lock=FileLock(str(self.db.directory/f'worker-{self.worker_id}.lock'),timeout=0);self.worker_lock.acquire()
        self.pool=ThreadPoolExecutor(max_workers=concurrency,thread_name_prefix='guardrails-job')
        self.recover()

    def recover(self):
        rows=self.db.rows("SELECT DISTINCT worker_id FROM jobs WHERE owner_id=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",(self.services.owner_id,))
        interrupted=False
        for row in rows:
            wid=str(UUID(row['worker_id']))
            if wid==self.worker_id:continue
            lock=FileLock(str(self.db.directory/f'worker-{wid}.lock'),timeout=0)
            try:
                with lock:
                    with self.db.connect(write=True) as con:
                        con.execute("UPDATE jobs SET status='INTERRUPTED',ended_at=?,error=? WHERE owner_id=? AND worker_id=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",
                            (utc(),'Worker process ended. Job was not replayed; inspect results and owned cleanup.',self.services.owner_id,wid))
                    interrupted=True
            except Timeout:continue
        if interrupted:
            try:self.services.sandbox_manager.cleanup_abandoned_runners()
            except Exception:pass

    def submit(self,kind,payload,request_key):
        payload=self.services.validate_action(kind,payload)
        if len(request_key)>160:raise ValueError('Request key is too long')
        job_id=str(uuid4());owner=self.services.owner_id
        exclusive=('benchmark:'+payload['sandbox_id']) if kind=='benchmark' else None
        with self.guard:
            with self.db.connect(write=True) as con:
                existing=con.execute('SELECT job_id FROM jobs WHERE owner_id=? AND request_key=?',(owner,request_key)).fetchone()
                if existing:return existing['job_id']
                if exclusive:
                    active=con.execute("SELECT job_id FROM jobs WHERE owner_id=? AND exclusive_key=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",(owner,exclusive)).fetchone()
                    if active:return active['job_id']
                count=con.execute("SELECT COUNT(*) FROM jobs WHERE owner_id=? AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",(owner,)).fetchone()[0]
                if count>=self.concurrency:raise ValueError('Local job limit reached. Wait or cancel an active job.')
                con.execute('INSERT INTO jobs(job_id,owner_id,request_key,kind,payload_json,status,worker_id,exclusive_key,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (job_id,owner,request_key,kind,json.dumps(payload),'QUEUED',self.worker_id,exclusive,utc()))
            event=PersistentCancellation(self.db,job_id);self.events[job_id]=event
            self.futures[job_id]=self.pool.submit(self._run,job_id,kind,payload,event)
        return job_id

    def _run(self,job_id,kind,payload,event):
        with self.db.connect(write=True) as con:
            con.execute("UPDATE jobs SET status='RUNNING',started_at=? WHERE job_id=? AND status='QUEUED'",(utc(),job_id))
        result=None;error=None;status='SUCCEEDED'
        try:
            if event.is_set():status='CANCELLED'
            else:
                value=self.services.execute(kind,payload,cancelled=event,actor='dashboard')
                result=value.model_dump_json() if hasattr(value,'model_dump_json') else json.dumps(value)
                if getattr(value,'ok',True) is False or getattr(value,'status',None) in ('failed','interrupted'):
                    status='FAILED';error='Backend operation did not complete successfully. Open the result for details.'
        except Exception:
            status='FAILED';error='Backend operation failed. Check approvals, ownership, Docker readiness and retained artifacts.'
        finally:
            if event.is_set():status='CANCELLED'
            with self.db.connect(write=True) as con:
                con.execute('UPDATE jobs SET status=?,ended_at=?,result_json=?,error=? WHERE job_id=? AND owner_id=?',
                    (status,utc(),result,error,job_id,self.services.owner_id))
            with self.guard:
                self.events.pop(job_id,None);self.futures.pop(job_id,None)

    def get(self,job_id):
        rows=self.db.rows('SELECT * FROM jobs WHERE job_id=? AND owner_id=?',(str(UUID(job_id)),self.services.owner_id))
        if not rows:raise ValueError('Owned job not found')
        return rows[0]

    def cancel(self,job_id):
        job=self.get(job_id)
        if job['status'] not in ACTIVE:return job
        with self.guard:
            event=self.events.get(job_id)
            if event:event.set()
        # Another UI/MCP process may own this worker; the worker also polls the durable flag.
        with self.db.connect(write=True) as con:
            con.execute("UPDATE jobs SET status='CANCEL_REQUESTED' WHERE job_id=? AND owner_id=? AND status IN ('QUEUED','RUNNING')",(job_id,self.services.owner_id))
        self.services.audit('cancel_job',job_id,'REQUESTED','dashboard')
        return self.get(job_id)

    def close(self):
        with self.guard:
            for event in self.events.values():event.set()
        self.pool.shutdown(wait=True,cancel_futures=False)
        self.worker_lock.release()
