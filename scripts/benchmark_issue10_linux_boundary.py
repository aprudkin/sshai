#!/usr/bin/env python3
"""Generate trusted-controller, no-model Linux SSH boundary bodies.

These bodies are for system OpenSSH owned by a trusted controller, never an
agent-accessible unrestricted SSH credential or shell. They do not implement a
broker, authorize deployment, qualify a study slot, or prove complete confinement.
Runtime manifests enumerate individual reviewed files, including dynamic-loader,
Python-library and setpriv dependencies; no directory mounts or discovery occur.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
from typing import Any

SCHEMA = "sshai-issue10-linux-boundary-1"
MAX_FILES = 4096
MAX_BYTES = 256 * 1024 * 1024
MAX_BODY = 64 * 1024
_PATH = re.compile(r"/[A-Za-z0-9_./-]+\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _path(value: Any) -> str:
    if (not isinstance(value, str) or len(value) > 1024 or not _PATH.fullmatch(value)
            or value == "/" or any(x in ("", ".", "..") for x in value[1:].split("/"))):
        raise ValueError("require bounded absolute lexical paths without traversal")
    return value


def _files(value: Any, relative: bool = False) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_FILES:
        raise ValueError("require a bounded nonempty explicit file manifest")
    names = set()
    total = 0
    for row in value:
        keys = {"path", "sha256", "size"} if relative else {"source", "path", "sha256", "size", "executable"}
        if not isinstance(row, dict) or set(row) != keys:
            raise ValueError("invalid file manifest row")
        name = row["path"]
        _path("/" + name if relative and isinstance(name, str) else name)
        if relative and name.startswith("/"):
            raise ValueError("fixture paths must be relative")
        if name in names or any(name.startswith(n + "/") or n.startswith(name + "/") for n in names):
            raise ValueError("duplicate or overlapping file destinations")
        names.add(name)
        if not isinstance(row["sha256"], str) or not _HEX.fullmatch(row["sha256"]):
            raise ValueError("invalid digest")
        if type(row["size"]) is not int or not 0 <= row["size"] <= MAX_BYTES:
            raise ValueError("invalid file size")
        total += row["size"]
        if not relative:
            _path(row["source"])
            if type(row["executable"]) is not bool:
                raise ValueError("executable must be boolean")
            if any(name == p or name.startswith(p + "/") for p in ("/fixture", "/scratch", "/proc", "/dev", "/tmp")):
                raise ValueError("runtime destination overlaps task or virtual filesystems")
    if total > MAX_BYTES:
        raise ValueError("file population exceeds byte limit")


def validate_plan(plan: Any) -> dict[str, Any]:
    """Validate public structure; physical paths and all hashes are checked remotely."""
    keys = {"schema", "root", "nonce", "uid", "gid", "fixture_source", "fixture_files",
            "runtime_files", "tools", "bash", "setpriv", "python"}
    if not isinstance(plan, dict) or set(plan) != keys or plan["schema"] != SCHEMA:
        raise ValueError("invalid boundary plan")
    root = _path(plan["root"])
    if (not re.fullmatch(r"[0-9a-f]{32}", str(plan["nonce"]))
            or root.rsplit("/", 1)[-1] != "issue10-linux-" + plan["nonce"]):
        raise ValueError("root must have the exact nonce-bound task-owned name")
    source = _path(plan["fixture_source"])
    if source == root or source.startswith(root + "/") or root.startswith(source + "/"):
        raise ValueError("fixture source must be disjoint from owned root")
    for name in ("uid", "gid"):
        if type(plan[name]) is not int or not 10000 <= plan[name] <= 65534:
            raise ValueError("require explicit unprivileged numeric uid/gid")
    _files(plan["fixture_files"], relative=True)
    _files(plan["runtime_files"])
    runtime = {r["path"]: r for r in plan["runtime_files"]}
    for name in ("bash", "setpriv", "python"):
        path = _path(plan[name])
        if path not in runtime or not runtime[path]["executable"]:
            raise ValueError("confined interpreter/tool must be a pinned runtime executable")
    if not isinstance(plan["tools"], dict) or set(plan["tools"]) != {"python", "mount", "unshare"}:
        raise ValueError("require exact controller tool pins")
    for row in plan["tools"].values():
        if (not isinstance(row, dict) or set(row) != {"path", "sha256"}
                or not isinstance(row["sha256"], str) or not _HEX.fullmatch(row["sha256"])):
            raise ValueError("invalid controller tool pin")
        _path(row["path"])
    return json.loads(json.dumps(plan))


# Self-contained remote code. Only generated controller constants reach this
# privileged code; diagnostic bytes enter Bash stdin after chroot/setpriv.
_REMOTE = r'''
import base64, contextlib, errno, fcntl, hashlib, json, os, pathlib, resource
import selectors, shutil, signal, socket, stat, subprocess, sys, time
P = pathlib.Path
MAX_BYTES = 268435456
_GLOBAL_PROC = ('meminfo','stat','diskstats','version','sys')

def physical(path, kind=None):
    p = P(path)
    for part in (p, *p.parents):
        st = part.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise ValueError("nonphysical path")
    st = p.stat()
    if kind == "file" and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
        raise ValueError("require singly linked regular file")
    if kind == "dir" and not stat.S_ISDIR(st.st_mode):
        raise ValueError("require physical directory")
    return p

def digest(path, size=None):
    p = physical(path, "file")
    st = p.stat()
    if st.st_size > MAX_BYTES or (size is not None and st.st_size != size):
        raise ValueError("file size mismatch")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()

def check_tools(plan):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("boundary requires the trusted Linux root controller")
    for row in plan["tools"].values():
        p = physical(row["path"], "file")
        if p.stat().st_uid != 0 or p.stat().st_mode & 0o022 or not os.access(p, os.X_OK):
            raise ValueError("controller tool is not root-controlled")
        if digest(p) != row["sha256"]:
            raise ValueError("controller tool changed")

def identity(plan):
    return {"schema": plan["schema"], "plan_sha256": hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}

def secure_parent(plan):
    p = physical(P(plan["root"]).parent, "dir")
    s = p.stat()
    if s.st_uid != 0 or s.st_mode & 0o022:
        raise ValueError("owned-root parent must be root-owned and not group/world writable")
    return p

@contextlib.contextmanager
def owned(plan):
    secure_parent(plan)
    root = physical(plan["root"], "dir")
    if root.stat().st_uid != 0 or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("owned root permissions changed")
    marker = physical(root / "owner.json", "file")
    if marker.stat().st_uid != 0 or stat.S_IMODE(marker.stat().st_mode) != 0o600:
        raise ValueError("owner marker permissions changed")
    with marker.open("rb") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if f.read(4097) != json.dumps(identity(plan), sort_keys=True).encode():
            raise ValueError("exact-owned root marker mismatch")
        yield root

def copy_file(source, target, row):
    if digest(source, row["size"]) != row["sha256"]:
        raise ValueError("source pin mismatch")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with physical(source, "file").open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst, 65536)
    target.chmod(0o555 if row.get("executable", False) else 0o444)
    if digest(target, row["size"]) != row["sha256"]:
        raise ValueError("copied file pin mismatch")

def prepare(plan, entry_files):
    if set(entry_files) != {'entry.py','entry.sh'}:
        raise ValueError('require exact trusted entry files')
    parent = secure_parent(plan)
    fixture = physical(plan["fixture_source"], "dir")
    expected = {r["path"] for r in plan["fixture_files"]}
    actual = set()
    entries = 0
    for directory, dirs, files in os.walk(fixture, followlinks=False):
        entries += len(dirs) + len(files)
        if entries > 20000:
            raise ValueError("fixture inventory overflow")
        for name in dirs:
            physical(P(directory) / name, "dir")
        for name in files:
            p = physical(P(directory) / name, "file")
            actual.add(p.relative_to(fixture).as_posix())
            if len(actual) > 4096:
                raise ValueError("fixture inventory overflow")
    if actual != expected:
        raise ValueError("fixture inventory differs from explicit manifest")
    # Exclusive creation: failed preparations are retained, never silently cleaned/reused.
    root = P(plan["root"])
    root.mkdir(mode=0o700)
    marker = root / "owner.json"
    with marker.open("xb") as f:
        f.write(json.dumps(identity(plan), sort_keys=True).encode())
    marker.chmod(0o600)
    image = root / "rootfs"
    image.mkdir(mode=0o755)
    for name in ("fixture", "scratch", "proc", "dev", "tmp"):
        (image / name).mkdir(mode=0o755)
    for row in plan["runtime_files"]:
        copy_file(row["source"], image / row["path"].lstrip("/"), row)
    for row in plan["fixture_files"]:
        copy_file(fixture / row["path"], image / "fixture" / row["path"], row)
    scratch = image / "scratch"
    scratch.chmod(0o700)
    os.chown(scratch, plan["uid"], plan["gid"])
    for name in ("tmp", "home"):
        p = scratch / name
        p.mkdir(mode=0o700)
        os.chown(p, plan["uid"], plan["gid"])
    entry_hashes = {}
    for name, row in entry_files.items():
        data = base64.b64decode(row['content'],validate=True)
        if len(data)>2097152 or hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ValueError('trusted entry pin mismatch')
        with (root/name).open('xb') as f: f.write(data)
        (root/name).chmod(0o555 if name=='entry.sh' else 0o444)
        entry_hashes[name] = row['sha256']
    # ready is published only after complete population; partial roots cannot run.
    with (root / "ready").open("xb") as f:
        f.write(json.dumps({**identity(plan),'entry_hashes':entry_hashes},sort_keys=True).encode())
    (root / "ready").chmod(0o600)
    return {"status": "prepared", "entry_hashes":entry_hashes, **identity(plan)}

def verify_image(plan, root):
    ready = physical(root / "ready", "file")
    if ready.stat().st_uid!=0 or stat.S_IMODE(ready.stat().st_mode)!=0o600 or ready.stat().st_size>4096:
        raise ValueError('unsafe readiness marker')
    state=json.loads(ready.read_bytes())
    if set(state)!={'schema','plan_sha256','entry_hashes'} or any(state[k]!=v for k,v in identity(plan).items()):
        raise ValueError("root is not ready")
    if set(state['entry_hashes'])!={'entry.py','entry.sh'}:
        raise ValueError('trusted entry inventory changed')
    for name, expected in state['entry_hashes'].items():
        p=physical(root/name,'file')
        if p.stat().st_uid!=0 or p.stat().st_mode & 0o222 or digest(p)!=expected:
            raise ValueError('trusted entry changed')
    image = physical(root / "rootfs", "dir")
    for row in plan["runtime_files"]:
        p = physical(image / row["path"].lstrip("/"), "file")
        if p.stat().st_uid != 0 or p.stat().st_mode & 0o222 or digest(p, row["size"]) != row["sha256"]:
            raise ValueError("runtime image changed")
    for row in plan["fixture_files"]:
        p = physical(image / "fixture" / row["path"], "file")
        if p.stat().st_uid != 0 or p.stat().st_mode & 0o222 or digest(p, row["size"]) != row["sha256"]:
            raise ValueError("fixture image changed")
    # Task cannot replace mountpoints: ancestors are root-owned/non-writable.
    for name in ("", "fixture", "proc", "dev", "tmp"):
        p = physical(image / name, "dir")
        if p.stat().st_uid != 0 or p.stat().st_mode & 0o022:
            raise ValueError("image directory is not root-controlled")
    s = physical(image / "scratch", "dir").stat()
    if s.st_uid != plan["uid"] or s.st_gid != plan["gid"] or stat.S_IMODE(s.st_mode) != 0o700:
        raise ValueError("scratch identity changed")
    return image

# This code runs only inside freshly created namespaces. Mounts never occur in
# the host namespace. Exec replaces namespace PID 1, so its exit kills descendants.
_STAGE = r"""
import ctypes, json, os, resource, socket, stat, subprocess, sys
p=json.loads(sys.argv[1]); image=p['root']+'/rootfs'; mount=p['tools']['mount']['path']
status_fd=p['status_fd']; saved_stderr=os.dup(2)
os.set_inheritable(saved_stderr,True); os.set_inheritable(status_fd,True)
null_fd=os.open('/dev/null',os.O_WRONLY); os.dup2(null_fd,2); os.close(null_fd)
# Never mutate the parent/global UTS namespace, even if a launcher fails to isolate it.
parent_uts=p.get('parent_uts')
if (not isinstance(parent_uts,list) or len(parent_uts)!=2
        or any(type(value) is not int or value<0 for value in parent_uts)):
 raise ValueError('missing parent UTS identity')
current_uts=os.stat('/proc/self/ns/uts')
if [current_uts.st_dev,current_uts.st_ino]==parent_uts:
 raise ValueError('UTS namespace was not isolated')
socket.sethostname(b'issue10-target')
libc=ctypes.CDLL(None,use_errno=True)
set_domainname=libc.setdomainname
set_domainname.argtypes=(ctypes.c_char_p,ctypes.c_size_t); set_domainname.restype=ctypes.c_int
synthetic_domain=b'issue10.invalid'
if len(synthetic_domain)>64 or set_domainname(synthetic_domain,len(synthetic_domain))!=0:
 raise OSError(ctypes.get_errno(),'synthetic UTS domain setup failed')
env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'}
def m(*args): subprocess.run([mount,*args],check=True,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
m('--make-rprivate','/')
m('--bind',image,image)
m('--bind',image+'/scratch',image+'/scratch')
m('-o','remount,bind,ro,nosuid,nodev',image)
m('-t','proc','-o','nosuid,nodev,noexec,subset=pid','proc',image+'/proc')
m('-t','tmpfs','-o','nosuid,noexec,size=1m,mode=755','tmpfs',image+'/dev')
for name,major,minor in [('null',1,3),('zero',1,5),('random',1,8),('urandom',1,9)]:
 os.mknod(image+'/dev/'+name,stat.S_IFCHR|0o666,os.makedev(major,minor)); os.chmod(image+'/dev/'+name,0o666)
m('-o','remount,ro,nosuid,noexec',image+'/dev')
os.chroot(image); os.chdir('/scratch')
fd_limit=resource.getrlimit(resource.RLIMIT_NOFILE)[0]
if fd_limit==resource.RLIM_INFINITY or not 3<=fd_limit<=16777216:
 raise ValueError('unsupported descriptor bound')
start=3
for keep in sorted({status_fd,saved_stderr}):
 if not 3<=keep<fd_limit: raise ValueError('unsupported lifecycle descriptor')
 os.closerange(start,keep); start=keep+1
os.closerange(start,fd_limit)
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_FSIZE,(16777216,16777216))
resource.setrlimit(resource.RLIMIT_NOFILE,(256,256))
# Inherited hard/soft per-process load ceilings, not cgroup aggregate quotas.
# RLIMIT_NPROC counts processes/threads for the real UID across PID namespaces.
MAX_ADDRESS_SPACE=512*1024*1024
MAX_UID_PROCESSES=128
MAX_PROCESS_CPU_SECONDS=60
resource.setrlimit(resource.RLIMIT_AS,(MAX_ADDRESS_SPACE,MAX_ADDRESS_SPACE))
resource.setrlimit(resource.RLIMIT_NPROC,(MAX_UID_PROCESSES,MAX_UID_PROCESSES))
resource.setrlimit(resource.RLIMIT_CPU,(MAX_PROCESS_CPU_SECONDS,MAX_PROCESS_CPU_SECONDS))
env={'PATH':'/usr/bin:/bin','HOME':'/scratch/home','TMPDIR':'/scratch/tmp','LANG':'C','LC_ALL':'C'}
# The command decoder itself runs only after setpriv; no untrusted shell parser
# sees command bytes while privileged. Its stdin is the unchanged SSH stream.
decoder=("import base64,os,sys\nstatus_fd=int(sys.argv[3]); saved_stderr=int(sys.argv[4])\ntry:\n"
 " cmd=base64.b64decode(sys.argv[1],validate=True)\n"
 " if not 0<len(cmd)<=65536 or 0 in cmd: raise ValueError('invalid remote command')\n"
 " os.dup2(saved_stderr,2); os.close(saved_stderr)\n"
 " os.write(status_fd,b'READY\\n'); os.set_inheritable(status_fd,False)\n"
 " os.execve(sys.argv[2],[sys.argv[2],'--noprofile','--norc','-c',cmd],dict(os.environ))\n"
 "except Exception:\n os.write(status_fd,b'FAIL\\n'); os._exit(125)\n")
argv=[p['setpriv'],'--reuid='+str(p['uid']),'--regid='+str(p['gid']),'--clear-groups',
 '--bounding-set=-all','--inh-caps=-all','--ambient-caps=-all','--no-new-privs','--',
 p['python'],'-I','-B','-c',decoder,p['command_b64'],p['bash'],str(status_fd),str(saved_stderr)]
os.execve(argv[0],argv,env)
"""
# Even interpreter/exec/bootstrap failures never enter model output. The private
# lifecycle pipe closes on successful Bash exec; setpriv/decoder failures cannot
# be mistaken for a diagnostic that legitimately exits 125.
_STAGE=("import json,os,sys\nstatus_fd=json.loads(sys.argv[1])['status_fd']\ntry:\n exec("
        +repr(_STAGE)+")\nexcept Exception:\n try: os.write(status_fd,b'FAIL\\n')\n except OSError: pass\n os._exit(125)\n")

def stop(proc):
    if proc.poll() is not None:
        return
    # Reap an already-exiting producer before signalling its process group.
    try:
        proc.wait(timeout=0.05)
        return
    except subprocess.TimeoutExpired:
        pass
    def send(sig):
        try: os.killpg(proc.pid, sig)
        except ProcessLookupError: pass
        except PermissionError:
            # A producer can exit between poll and killpg; only tolerate an
            # independently reaped exit, never an unkillable running process.
            proc.wait(timeout=0.1)
    send(signal.SIGTERM)
    try: proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        send(signal.SIGKILL)
        proc.wait(timeout=3)

def parent_death(parent_pid):
    # Keep the namespace launcher tied to its trusted SSH controller even if
    # the controller is killed before its Python finally block can execute.
    import ctypes
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,int(signal.SIGKILL),0,0,0)!=0 or os.getppid()!=parent_pid:
        os._exit(125)

def launch(plan, body, seconds, limit, capture=False, command_b64=None):
    if command_b64 is None:
        command_b64=base64.b64encode(('exec '+plan['bash']+' --noprofile --norc -s').encode()).decode()
    stage_plan={k:plan[k] for k in ('root','uid','gid','tools','bash','setpriv','python')}
    stage_plan['command_b64']=command_b64
    stage_plan['parent_uts']=None
    if sys.platform=='linux':
        parent_uts=os.stat('/proc/self/ns/uts')
        stage_plan['parent_uts']=[parent_uts.st_dev,parent_uts.st_ino]
    command = [plan['tools']['unshare']['path'], '--mount', '--pid', '--net', '--ipc', '--uts',
               '--fork', '--kill-child=KILL', plan['tools']['python']['path'], '-I', '-B', '-c',
               _STAGE, json.dumps(stage_plan, sort_keys=True)]
    clean = {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'}
    # Convenience/qualification bodies use a private control file. entry_main
    # forwards the original SSH stdin descriptor without pre-reading or decoding.
    with contextlib.ExitStack() as stack:
        import tempfile
        if body is None:
            source=sys.stdin.buffer
        else:
            source = stack.enter_context(tempfile.TemporaryFile(dir=plan['root']))
            source.write(body); source.seek(0)
        parent_pid=os.getpid()
        death_hook=(lambda: parent_death(parent_pid)) if sys.platform=='linux' else None
        status_read,status_write=os.pipe()
        status_reader=stack.enter_context(os.fdopen(status_read,'rb',buffering=0))
        status_writer=stack.enter_context(os.fdopen(status_write,'wb',buffering=0))
        stage_plan['status_fd']=status_write; command[-1]=json.dumps(stage_plan,sort_keys=True)
        proc = subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=clean, close_fds=True, pass_fds=(status_write,), start_new_session=True, preexec_fn=death_hook)
        status_writer.close()
        selector = stack.enter_context(selectors.DefaultSelector())
        os.set_blocking(status_read,False); selector.register(status_reader,selectors.EVENT_READ,'lifecycle')
        for stream, name in ((proc.stdout,'stdout'),(proc.stderr,'stderr')):
            stack.callback(stream.close)
            os.set_blocking(stream.fileno(),False); selector.register(stream,selectors.EVENT_READ,name)
        counts={'stdout':0,'stderr':0}; data={'stdout':bytearray(),'stderr':bytearray()}
        pending={'stdout':bytearray(),'stderr':bytearray()}; lifecycle=bytearray(); ready=False
        def forward(name,block):
            stream=sys.stdout.buffer if name=='stdout' else sys.stderr.buffer
            stream.write(block); stream.flush()
        deadline=time.monotonic()+seconds
        try:
            while selector.get_map():
                left=deadline-time.monotonic()
                if left <= 0: raise ValueError('boundary process timeout')
                for key,_ in selector.select(min(left,0.2)):
                    block=os.read(key.fd,65536); name=key.data
                    if name=='lifecycle':
                        if block:
                            lifecycle.extend(block)
                            if len(lifecycle)>64: raise ValueError('invalid boundary lifecycle')
                        else:
                            selector.unregister(key.fileobj)
                            if lifecycle!=b'READY\n': raise ValueError('privileged boundary bootstrap failed')
                            ready=True
                            if not capture:
                                for stream,buffer in pending.items():
                                    forward(stream,buffer); buffer.clear()
                        continue
                    if not block: selector.unregister(key.fileobj); continue
                    counts[name]+=len(block)
                    if sum(counts.values()) > limit: raise ValueError('boundary output overflow')
                    if capture: data[name].extend(block)
                    elif ready: forward(name,block)
                    else: pending[name].extend(block)
            rc=proc.wait(timeout=max(0.01,deadline-time.monotonic()))
        finally:
            stop(proc)
    return rc,{k:bytes(v) for k,v in data.items()},counts

_PROBE = r"""
import ctypes, errno, hashlib, json, os, pathlib, resource, socket, subprocess, sys
s=json.load(sys.stdin); out={}
def checked_domainname():
 libc=ctypes.CDLL(None,use_errno=True); get_domainname=libc.getdomainname
 get_domainname.argtypes=(ctypes.POINTER(ctypes.c_char),ctypes.c_size_t); get_domainname.restype=ctypes.c_int
 buffer=ctypes.create_string_buffer(65)
 if get_domainname(buffer,len(buffer))!=0:
  raise OSError(ctypes.get_errno(),'synthetic UTS domain check failed')
 return buffer.value
out['synthetic_hostname']=socket.gethostname()=='issue10-target' and os.uname().nodename=='issue10-target'
out['synthetic_domainname']=checked_domainname()==b'issue10.invalid'
out['resource_limits']=all(resource.getrlimit(kind)==(value,value) for kind,value in [
 (resource.RLIMIT_AS,536870912),(resource.RLIMIT_NPROC,128),(resource.RLIMIT_CPU,60),
 (resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,16777216),(resource.RLIMIT_NOFILE,256)])
for row in s['fixture_files']:
 try: out['fixture:'+row['path']]=hashlib.sha256(pathlib.Path('/fixture',row['path']).read_bytes()).hexdigest()==row['sha256']
 except OSError: out['fixture:'+row['path']]=False
out['identity']=os.getuid()==s['uid'] and os.getgid()==s['gid'] and os.getgroups()==[]
status=pathlib.Path('/proc/self/status').read_text()
out['privileges']=all(x in status for x in ['CapEff:\t0000000000000000','CapBnd:\t0000000000000000','NoNewPrivs:\t1'])
def denied(path,write=False):
 try:
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL if write else os.O_RDONLY,0o600)
 except OSError as e: return e.errno in (errno.ENOENT,errno.EACCES,errno.EPERM,errno.EROFS,errno.ENOTDIR)
 else: os.close(fd); return False
for name in ('meminfo','stat','diskstats','version','sys'):
 out['global_proc:'+name]=denied('/proc/'+name)
for row in s['canaries']: out['outside:'+row['name']]=denied(row['path'])
first=s['canaries'][0]['path']
out['traversal']=denied('/fixture/../../'+first.lstrip('/'))
link=pathlib.Path('/scratch/.qualification-link'); link.symlink_to(first)
try: out['symlink']=denied(str(link))
finally: link.unlink()
try:
 f=pathlib.Path('/scratch/.qualification-write'); f.write_text('synthetic-write'); out['scratch']=f.read_text()=='synthetic-write'; f.unlink()
except OSError: out['scratch']=False
try:
 fd=os.open('/fixture/'+s['fixture_files'][0]['path'],os.O_WRONLY)
except OSError as e: out['fixture_write_denied']=e.errno in (errno.EACCES,errno.EPERM,errno.EROFS)
else: os.close(fd); out['fixture_write_denied']=False
out['fixture_create_denied']=denied('/fixture/.qualification-create',True)
out['outside_write_denied']=denied(first+'.write',True)
child='import os,sys,errno\ntry: os.open(sys.argv[1],os.O_RDONLY)\nexcept OSError as e: sys.exit(0 if e.errno in (errno.ENOENT,errno.EACCES,errno.EPERM,errno.ENOTDIR) else 2)\nelse: sys.exit(3)'
r=subprocess.run([s['bash'],'--noprofile','--norc','-c','exec "$1" -I -B -c "$2" "$3"','probe',s['python'],child,first],timeout=5,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
out['descendant']=r.returncode==0
sock=socket.socket(); sock.settimeout(2)
try: sock.connect(('127.0.0.1',s['port']))
except OSError as e: out['network']=e.errno in (errno.ECONNREFUSED,errno.ENETUNREACH,errno.EHOSTUNREACH,errno.EACCES,errno.EPERM)
else: out['network']=False
finally: sock.close()
print(json.dumps(out,sort_keys=True))
"""

def qualify(plan, canaries, seconds, limit):
    # Controller checks only caller-declared synthetic canary bytes, never production files.
    for row in canaries:
        if digest(row['path'],row['size']) != row['sha256']:
            raise ValueError('synthetic negative canary mismatch')
    # Establish that the negative proc targets exist and are controller-openable.
    # Open/close only: never consume any live-global proc bytes.
    for name in _GLOBAL_PROC:
        fd=os.open('/proc/'+name,os.O_RDONLY|os.O_NOFOLLOW)
        os.close(fd)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); listener.listen(1)
        spec={k:plan[k] for k in ('fixture_files','uid','gid','bash','python')}
        spec.update(canaries=canaries,port=listener.getsockname()[1])
        import shlex
        encoded=json.dumps(spec,sort_keys=True)
        delimiter='ISSUE10_PROBE_'+hashlib.sha256(encoded.encode()).hexdigest()
        body=('exec '+shlex.quote(plan['python'])+' -I -B -c '+shlex.quote(_PROBE)
              +" <<'"+delimiter+"'\n"+encoded+'\n'+delimiter+'\n').encode()
        rc,data,counts=launch(plan,body,seconds,limit,True)
    if rc != 0 or data['stderr']:
        raise ValueError('qualification lifecycle failed')
    checks=json.loads(data['stdout'])
    expected={'fixture:'+r['path'] for r in plan['fixture_files']} | {'outside:'+r['name'] for r in canaries} | {'global_proc:'+name for name in _GLOBAL_PROC} | {'identity','privileges','resource_limits','synthetic_hostname','synthetic_domainname','traversal','symlink','scratch','fixture_write_denied','fixture_create_denied','outside_write_denied','descendant','network'}
    if not isinstance(checks,dict) or set(checks)!=expected or not all(x is True for x in checks.values()):
        raise ValueError('named access qualification failed')
    return {**identity(plan),'schema':'sshai-issue10-linux-access-1','checks':checks,'output_bytes':counts,
            'limitations':['Named synthetic canaries, not complete confinement or OS tracing.',
                          'Reviewed runtime files remain readable; transport/broker exclusion is external.',
                          'Per-process address-space/CPU limits are not aggregate cgroup quotas; NPROC is real-UID-wide.',
                          'No model, finality, usage or diagnostic-routing qualification.']}

def inspect_image(plan, image):
    # Read only current fixture files/metadata. Never enumerate scratch or other roots.
    expected={row['path'] for row in plan['fixture_files']}
    actual=set(); exact=True; entries=0
    fixture=physical(image/'fixture','dir')
    for directory,dirs,files in os.walk(fixture,followlinks=False):
        entries+=len(dirs)+len(files)
        if entries>20000: raise ValueError('fixture inspection inventory overflow')
        for name in list(dirs):
            st=(P(directory)/name).lstat()
            if not stat.S_ISDIR(st.st_mode):
                exact=False; dirs.remove(name)
        for name in files:
            p=P(directory)/name; st=p.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:
                exact=False
            actual.add(p.relative_to(fixture).as_posix())
            if len(actual)>4096: raise ValueError('fixture inspection file overflow')
    integrity={row['path']:digest(fixture/row['path'],row['size'])==row['sha256']
               for row in plan['fixture_files']}
    if not all(integrity.values()): raise ValueError('fixture inspection pin mismatch')
    return {**identity(plan),'fixture_integrity':integrity,
            'fixture_inventory_exact':exact and actual==expected}

def cleanup(plan, root):
    # Explicit separate action only. Refuse any surviving mount before fd-safe removal.
    for line in P('/proc/self/mountinfo').read_text().splitlines():
        raw=line.split()[4]
        mount=raw.replace('\\040',' ').replace('\\011','\t').replace('\\012','\n').replace('\\134','\\')
        if mount==str(root) or mount.startswith(str(root)+'/'):
            raise ValueError('refuse cleanup of mounted root')
    count=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        count+=len(dirs)+len(files)
        if count>20000: raise ValueError('cleanup inventory overflow')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('fd-safe cleanup unavailable')
    parentfd=os.open(root.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: shutil.rmtree(root.name,dir_fd=parentfd)
    finally: os.close(parentfd)
    return {'status':'removed',**identity(plan)}

def entry_main(plan):
    import re
    if (len(sys.argv)!=2 or not 4<=len(sys.argv[1])<=87384 or len(sys.argv[1])%4
            or not re.fullmatch(r'[A-Za-z0-9+/]+={0,2}',sys.argv[1])):
        raise ValueError('require one bounded base64 remote-command argument')
    check_tools(plan)
    with owned(plan) as root:
        verify_image(plan,root)
        rc,_,_=launch(plan,None,600,1048576,command_b64=sys.argv[1])
    sys.exit(rc if 0<=rc<=255 else 125)

def main(request):
    os.umask(0o022)
    plan=request['plan']; check_tools(plan)
    if request['action']=='prepare': result=prepare(plan,request['entry_files'])
    else:
        with owned(plan) as root:
            if request['action']=='cleanup': result=cleanup(plan,root)
            else:
                image=verify_image(plan,root)
                if request['action']=='inspect': result=inspect_image(plan,image)
                elif request['action']=='qualify': result=qualify(plan,request['canaries'],request['seconds'],request['limit'])
                elif request['action']=='run':
                    rc,_,_=launch(plan,base64.b64decode(request['body'],validate=True),request['seconds'],request['limit'])
                    sys.exit(rc if 0<=rc<=255 else 125)
                else: raise ValueError('unsupported action')
    print(json.dumps(result,sort_keys=True))
'''


def _emit(plan: dict[str, Any], action: str, **fields: Any) -> str:
    plan = validate_plan(plan)
    request = json.dumps({"plan": plan, "action": action, **fields}, sort_keys=True)
    script = _REMOTE + "\nmain(json.loads(base64.b64decode(" + repr(base64.b64encode(request.encode()).decode()) + ")))\n"
    if len(script.encode()) > 2 * 1024 * 1024:
        raise ValueError("generated controller body exceeds bound")
    delimiter = "ISSUE10_" + hashlib.sha256(script.encode()).hexdigest()
    return ("exec " + shlex.quote(plan["tools"]["python"]["path"]) + " -I -B - <<'" + delimiter
            + "'\n" + script + delimiter + "\n")


def _limits(seconds: int, output_limit: int) -> None:
    if type(seconds) is not int or not 1 <= seconds <= 600:
        raise ValueError("timeout must be 1..600 seconds")
    if type(output_limit) is not int or not 1024 <= output_limit <= 8 * 1024 * 1024:
        raise ValueError("output limit must be 1 KiB..8 MiB")


def entry_files(plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Return exact entry bytes (base64) and hashes for the controller manifest."""
    plan = validate_plan(plan)
    python = (_REMOTE + '\ntry:\n entry_main(' + repr(plan)
              + ")\nexcept Exception:\n sys.stderr.write('issue10 boundary failure\\n')\n sys.exit(125)\n").encode()
    shell = ('#!/bin/sh\nexec ' + shlex.quote(plan['tools']['python']['path'])
             + ' -I -B ' + shlex.quote(plan['root'] + '/entry.py') + ' "$@"\n').encode()
    return {name: {'content': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest()}
            for name, data in [('entry.py', python), ('entry.sh', shell)]}


def entry_command(plan: dict[str, Any], remote_command: bytes) -> str:
    """Fixed physical entry + bounded base64 argv; OpenSSH stdin stays untouched.

    The trusted broker supplies this command to the actual system SSH invocation.
    Do not expose that broker's unrestricted SSH credentials to model tools.
    """
    plan = validate_plan(plan)
    if (not isinstance(remote_command, bytes) or not 1 <= len(remote_command) <= MAX_BODY
            or b'\0' in remote_command):
        raise ValueError('require bounded remote shell-command bytes without NUL')
    return shlex.quote(plan['root'] + '/entry.sh') + ' ' + shlex.quote(base64.b64encode(remote_command).decode())


def preparation_body(plan: dict[str, Any]) -> str:
    return _emit(plan, "prepare", entry_files=entry_files(plan))


def run_body(plan: dict[str, Any], diagnostic: bytes, *, seconds: int = 600,
             output_limit: int = 1024 * 1024) -> str:
    """Return a privileged-controller body, NOT a command for the model to execute."""
    _limits(seconds, output_limit)
    if not isinstance(diagnostic, bytes) or not 1 <= len(diagnostic) <= MAX_BODY or b"\0" in diagnostic:
        raise ValueError("diagnostic must be nonempty bounded Bash bytes without NUL")
    return _emit(plan, "run", body=base64.b64encode(diagnostic).decode(), seconds=seconds, limit=output_limit)


def qualification_body(plan: dict[str, Any], canaries: list[dict[str, Any]], *,
                       seconds: int = 30, output_limit: int = 1024 * 1024) -> str:
    _limits(seconds, output_limit)
    if not isinstance(canaries, list) or not 2 <= len(canaries) <= 16:
        raise ValueError("require bounded sibling and other-case synthetic canaries")
    names = set()
    root = validate_plan(plan)["root"]
    for row in canaries:
        if (not isinstance(row, dict) or set(row) != {"name", "path", "size", "sha256"}
                or not isinstance(row["name"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", row["name"])):
            raise ValueError("invalid named synthetic canary")
        if row["name"] in names:
            raise ValueError("duplicate canary name")
        names.add(row["name"])
        path = _path(row["path"])
        if path == root or path.startswith(root + "/"):
            raise ValueError("negative canary must be outside task root")
        if (not isinstance(row["sha256"], str) or not _HEX.fullmatch(row["sha256"])
                or type(row["size"]) is not int or not 0 <= row["size"] <= 4096):
            raise ValueError("invalid synthetic canary pin or byte bound")
    if not {"sibling", "other_case"} <= names:
        raise ValueError("require sibling and other_case canary categories")
    return _emit(plan, "qualify", canaries=canaries, seconds=seconds, limit=output_limit)


def inspection_body(plan: dict[str, Any]) -> str:
    """Controller-only locked image/pin and exact fixture census; no scratch probes."""
    return _emit(plan, "inspect")


def cleanup_body(plan: dict[str, Any]) -> str:
    """Explicit exact-owned cleanup; never called automatically on failure."""
    return _emit(plan, "cleanup")
