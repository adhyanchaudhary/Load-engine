"""Fixed offline Python bootstrap and recipe execution inside managed containers."""
import io
import json
import re
import socket
import time
from guardrails.docker.manager import SandboxManager
from guardrails.docker.models import ApprovedImage,ImagePolicy
from guardrails.diagnostics.io import ArtifactError
from guardrails.diagnostics.security import redact
from guardrails.repair.models import StageResult
from guardrails.repair.policy import fingerprint

# This code is service-owned, not sourced from a repository or MCP parameter.
BOOTSTRAP='''import io,json,os,pathlib,subprocess,sys,tarfile
root=pathlib.Path('/tmp/guardrails-source')
root.mkdir(mode=0o700)
data=sys.stdin.buffer.read(3145729)
if len(data)>3145728: raise RuntimeError('source bound')
total=0
with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as archive:
 for index,member in enumerate(archive):
  p=pathlib.PurePosixPath(member.name)
  total+=member.size
  if index>=100 or total>2097152 or not member.isfile() or p.is_absolute() or '..' in p.parts: raise RuntimeError('unsafe source')
  target=root.joinpath(*p.parts)
  target.parent.mkdir(parents=True,exist_ok=True)
  target.write_bytes(archive.extractfile(member).read())
  target.chmod(0o444)
command=json.loads(sys.argv[1])
command[0]=sys.executable
result=subprocess.run(command,cwd=root,env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1'})
sys.exit(result.returncode)
'''


class Cancelled(Exception):pass


class CorrectnessVerifier:
    def __init__(self,manager,store,cancelled=None):
        self.base=manager;self.store=store;self.cancelled=cancelled

    def check_cancelled(self):
        if self.cancelled and self.cancelled.is_set():raise Cancelled('Verification cancelled')

    def sandbox_manager(self,session,project,command,*,application=False):
        recipe=project.recipe
        image=ApprovedImage(image_id=project.runtime_image.image_id,user=project.runtime_image.user,
            entrypoint=[recipe.python_executable,'-I','-c',BOOTSTRAP],command=[json.dumps(command)],
            application_port=recipe.port if application else None,application_identity=session.approved_project_id,
            application_version='repair-source-v1',commit_identifier=getattr(session,'execution_revision',session.baseline_revision),
            dataset_id=project.dataset_id,ephemeral_workspace_mb=recipe.workspace_mb,stdin_open=True,
            test_recipe_version=recipe.version,test_recipe_digest=fingerprint(recipe))
        policy=ImagePolicy(images={'repair-runtime':image},performance=self.base._load_policy().performance)
        manager=SandboxManager(self.base.registry.directory,policy=policy,client_factory=self.base.client_factory,clock=self.base.clock)
        manager.repair_session_id=session.session_id
        return manager

    def create_and_deliver(self,session,project,command,snapshot,*,application=False,revision=None):
        self.check_cancelled()
        startup_deadline=time.monotonic()+15
        manager=self.sandbox_manager(session,project,command,application=application)
        if revision:manager._policy.images['repair-runtime'].commit_identifier=revision
        created=manager.create('repair-runtime',cpus=project.application_cpus,memory_mb=project.application_memory_mb,
            pids_limit=project.application_pids_limit,timeout_seconds=300)
        if not created.ok:raise ArtifactError(created.error.message)
        sid=created.sandbox.sandbox_id
        session.managed_sandbox_ids.append(sid);self.store.save(session)
        with manager.client_factory() as client,manager.registry.transaction() as data:
            entry,container=manager._resolve(client,data,sid)
            sock=container.attach_socket(params={'stdin':1,'stream':1})
        try:
            if time.monotonic()>startup_deadline:raise ArtifactError('Recipe container startup timed out')
            self.check_cancelled()
            result=manager.start(sid)
            if not result.ok:raise ArtifactError(result.error.message)
            if time.monotonic()>startup_deadline:raise ArtifactError('Recipe container startup timed out')
            self.check_cancelled()
            transport=getattr(sock,'_sock',sock);transport.settimeout(max(1,min(10,startup_deadline-time.monotonic())))
            transport.sendall(snapshot);transport.shutdown(socket.SHUT_WR)
        finally:sock.close()
        return manager,sid

    def output(self,container,stderr=False):
        stream=container.logs(stdout=not stderr,stderr=stderr,stream=True,follow=False,tail=200)
        output=bytearray();deadline=time.monotonic()+5
        try:
            for chunk in stream:
                output.extend(chunk[:8192-len(output)])
                if len(output)>=8192 or time.monotonic()>deadline:break
        finally:
            if hasattr(stream,'close'):stream.close()
        return redact(output.decode('utf-8',errors='replace'))[:8192]

    def run(self,session,project,snapshot,stage):
        recipe=project.recipe
        result=StageResult(stage=stage,status='INCONCLUSIVE',recipe_version=recipe.version,recipe_digest=fingerprint(recipe))
        command=recipe.build_command if stage=='build' else recipe.test_command
        timeout=recipe.build_timeout_seconds if stage=='build' else recipe.test_timeout_seconds
        try:
            manager,sid=self.create_and_deliver(session,project,command,snapshot)
            deadline=time.monotonic()+timeout
            with manager.client_factory() as client:
                while True:
                    self.check_cancelled()
                    with manager.registry.transaction() as data:
                        entry,container=manager._resolve(client,data,sid)
                        manager._require_unexpired(entry)
                    state=container.attrs.get('State',{})
                    if state.get('Status') in ('exited','dead'):
                        result.exit_code=state.get('ExitCode')
                        result.stdout=self.output(container);result.stderr=self.output(container,True)
                        if state.get('OOMKilled') or result.exit_code in (137,143):
                            result.errors.append('Recipe execution was resource limited or killed')
                        elif result.exit_code is None:result.errors.append('Recipe exit status unavailable')
                        elif result.exit_code!=0:result.status='FAIL'
                        else:result.status='PASS'
                        break
                    if time.monotonic()>deadline:
                        result.errors.append('Recipe stage timed out');break
                    time.sleep(.05)
            if stage=='correctness' and result.status in ('PASS','FAIL'):
                # Trusted recipe must report nonzero tests. Output remains untrusted evidence.
                text=result.stdout+'\n'+result.stderr
                match=re.search(r'Ran (\d+) tests?',text)
                pytest_match=re.search(r'\b(\d+) passed\b',text)
                count=int(match.group(1)) if match else int(pytest_match.group(1)) if pytest_match else None
                result.tests_executed=count
                failures=re.findall(r'(?:failures|errors)=(\d+)',text)
                result.failures=sum(map(int,failures)) if failures else 0 if result.status=='PASS' and count is not None else None
                if result.status=='PASS' and (count is None or count<recipe.minimum_tests):
                    result.status='INCONCLUSIVE';result.errors.append('Trusted test recipe did not report the required nonzero test count')
        except Cancelled:raise
        except Exception:
            result.status='INCONCLUSIVE';result.errors.append('Isolated recipe runner unavailable or failed; no host execution fallback')
        return result
