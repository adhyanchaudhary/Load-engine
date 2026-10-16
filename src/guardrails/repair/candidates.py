"""Read Git objects without hooks, checkouts, repository scripts, filters or remotes."""
import hashlib
import io
import os
from pathlib import Path,PurePosixPath
import re
import selectors
import subprocess
import tarfile
import time
from guardrails.diagnostics.io import ArtifactError

MAX_SOURCE=2*1024*1024


def digest(data):return hashlib.sha256(data).hexdigest()


def matches(path,patterns):return any(path==p or p.endswith('/') and path.startswith(p) for p in patterns)


def git(root,*args,limit=MAX_SOURCE,timeout=5):
    command=['git','--no-replace-objects','--no-optional-locks','-c','core.hooksPath=/dev/null',
             '-c','core.fsmonitor=false','-c','protocol.allow=never',*args]
    env={'PATH':os.defpath,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'GIT_TERMINAL_PROMPT':'0'}
    process=subprocess.Popen(command,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    output=bytearray();deadline=time.monotonic()+timeout
    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.monotonic()>deadline:raise ArtifactError('Git inspection timed out')
            for key,_ in selector.select(.05):
                chunk=os.read(key.fd,65536)
                if not chunk:selector.unregister(key.fileobj);break
                output.extend(chunk)
                if len(output)>limit:raise ArtifactError('Git output exceeded size bound')
        if process.wait(timeout=1)!=0:raise ArtifactError('Revision or repository inspection failed')
        return bytes(output)
    finally:
        selector.close()
        if process.poll() is None:process.kill()
        process.wait(timeout=2)
        process.stdout.close()


class CandidateResolver:
    def __init__(self,project):
        self.project=project
        self.deadline=time.monotonic()+20
        supplied=Path(project.workspace)
        if not supplied.is_absolute() or supplied.is_symlink() or any(p.is_symlink() for p in supplied.parents):
            raise ArtifactError('Approved repository must be an absolute nonsymlink path')
        self.root=supplied.resolve()
        if not (self.root/'.git').is_dir() or (self.root/'.git').is_symlink():
            raise ArtifactError('MVP requires a standalone local Git repository')
        metadata=self.root/'.git'
        if any((metadata/name).is_symlink() for name in ('objects','refs','config','index')) or (metadata/'objects/info/alternates').exists():
            raise ArtifactError('External Git object stores or metadata symlinks are unsupported')
        if any((metadata/name).exists() for name in ('MERGE_HEAD','REBASE_HEAD','CHERRY_PICK_HEAD','rebase-merge','rebase-apply','info/grafts')):
            raise ArtifactError('Repository has an unfinished or rewritten-history state')
        if Path(self._git('rev-parse','--show-toplevel').decode().strip()).resolve()!=self.root:
            raise ArtifactError('Approved repository identity mismatch')
        if self._git('rev-parse','--is-shallow-repository').strip()!=b'false':raise ArtifactError('Shallow repositories are unsupported')
        if self._git('status','--porcelain','--untracked-files=normal','--ignore-submodules=all'):
            raise ArtifactError('Commit or stash workspace edits before candidate registration')

    def _git(self,*args,**kwargs):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise ArtifactError('Source inspection exceeded its stage deadline')
        return git(self.root,*args,timeout=min(5,remaining),**kwargs)

    def resolve(self,revision):
        if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{40}',revision):
            raise ArtifactError('Use a full immutable 40-character commit hash, never a branch or path')
        actual=self._git('rev-parse','--verify','--end-of-options',revision+'^{commit}').decode().strip()
        if actual!=revision:raise ArtifactError('Commit identity mismatch')
        return actual

    def files(self,revision):
        revision=self.resolve(revision)
        entries=self._git('ls-tree','-rlz','--full-tree',revision).split(b'\0')
        files={};total=0
        for entry in entries:
            if not entry:continue
            head,rawpath=entry.split(b'\t',1);mode,kind,oid,size=head.split()
            path=rawpath.decode('utf-8',errors='strict');p=PurePosixPath(path)
            if (mode not in (b'100644',b'100755') or kind!=b'blob' or p.is_absolute() or
                any(x in ('.','..','.git','.env') for x in p.parts) or '\\' in path or
                not re.fullmatch(r'[A-Za-z0-9_./-]+',path)):
                raise ArtifactError('Unsafe path, symlink, submodule or secret-file name in source snapshot')
            length=int(size);total+=length
            if length>512*1024 or total>MAX_SOURCE or len(files)>=100:raise ArtifactError('Source snapshot exceeds MVP file/size bounds')
            files[path]=self._git('cat-file','blob',oid.decode(),limit=512*1024)
        return files

    def archive(self,revision):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w') as tar:
            for path,content in sorted(self.files(revision).items()):
                member=tarfile.TarInfo(path);member.size=len(content);member.mode=0o444;member.mtime=0
                tar.addfile(member,io.BytesIO(content))
        return output.getvalue()

    def candidate(self,revision):
        revision=self.resolve(revision);baseline=self.resolve(self.project.baseline_revision)
        if revision==baseline:raise ArtifactError('Candidate must differ from baseline')
        self._git('merge-base','--is-ancestor',baseline,revision)
        before=self.files(baseline);after=self.files(revision)
        changed=sorted(p for p in before.keys()|after.keys() if before.get(p)!=after.get(p))
        if not changed:raise ArtifactError('Candidate changes no source files')
        if any(not matches(p,self.project.allowed_changes) or matches(p,self.project.protected_paths) for p in changed):
            raise ArtifactError('Candidate modifies a protected or unapproved file')
        return revision,changed,self.archive(revision)
