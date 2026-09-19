"""Build a separate, resource-monitored Prism O2 candidate in the existing guest."""
import hashlib,json,os,resource,signal,subprocess,time
from pathlib import Path

BASE=Path('/run/host/var/tmp/bc250-bonsai-experimental')
ROOT=Path('/run/host/var/tmp/bc250-crack-performance-20260918')
ROOT.mkdir(mode=0o700,exist_ok=True)
BUILD=ROOT/'build-o2'
assert not (ROOT/'build-result.json').exists()
assert subprocess.check_output(['git','-C',str(BASE/'prism-source'),'rev-parse','HEAD'],text=True).strip()=='5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6'
assert not subprocess.check_output(['git','-C',str(BASE/'prism-source'),'status','--porcelain'],text=True).strip()
TOOLS=ROOT/'build-tools';TOOLS.mkdir(exist_ok=True)
wrapper=TOOLS/'bounded-cxx.py';wrapper.write_bytes((BASE/'build-tools/bounded-cxx.py').read_bytes());wrapper.chmod(0o755)
env=dict(os.environ,LD_LIBRARY_PATH=str(BASE/'compiler-root/usr/lib64'),TMPDIR=str(ROOT/'tmp'),CMAKE_BUILD_PARALLEL_LEVEL='1')
(ROOT/'tmp').mkdir(exist_ok=True)
args=['cmake','-S',str(BASE/'prism-source'),'-B',str(BUILD),'-G','Unix Makefiles','-DCMAKE_BUILD_TYPE=Release','-DBUILD_SHARED_LIBS=OFF','-DGGML_VULKAN=ON','-DGGML_NATIVE=OFF','-DGGML_OPENMP=OFF','-DLLAMA_CURL=OFF','-DLLAMA_BUILD_TESTS=ON','-DVulkan_GLSLC_EXECUTABLE='+str(BASE/'build-tools/glslc'),'-DCMAKE_C_COMPILER='+str(BASE/'compiler-root/usr/bin/clang'),'-DCMAKE_CXX_COMPILER='+str(BASE/'compiler-root/usr/bin/clang++'),'-DCMAKE_CXX_FLAGS_RELEASE=-O2 -DNDEBUG','-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG','-DCMAKE_CXX_COMPILER_LAUNCHER='+str(wrapper)]
targets=['llama-server','llama-cli','llama-quantize','test-backend-ops']
recipe={'source_commit':'5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6','configure_argv':args,'targets':targets,'jobs':1,'vendor_source_modified':False,'shader_wrapper_sha256':hashlib.sha256(wrapper.read_bytes()).hexdigest(),'limits':{'rss_mib':2048,'available_mib':512,'seconds':5400,'address_space_mib':4096}}
(ROOT/'build-recipe.json').write_text(json.dumps(recipe,indent=2)+'\n')
stop=False
def interrupted(*_):
 global stop
 stop=True
for sig in [signal.SIGHUP,signal.SIGTERM,signal.SIGINT]:signal.signal(sig,interrupted)
def limit():resource.setrlimit(resource.RLIMIT_AS,(4096*1024**2,4096*1024**2))
def usage(pid):
 rows={}
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   stat=(p/'stat').read_text().rsplit(')',1)[1].split();rows[int(p.name)]=(int(stat[1]),int(stat[21])*os.sysconf('SC_PAGE_SIZE')/1024**2)
  except (OSError,ValueError,IndexError):pass
 selected={pid}
 while True:
  new=selected|{p for p,(parent,rss) in rows.items() if parent in selected}
  if new==selected:break
  selected=new
 available=next(int(x.split()[1])/1024 for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
 return sum(rows.get(p,(0,0))[1] for p in selected),available
start=time.monotonic();peak=0;low=1e9;reason=None;status=0
for phase,command in [('configure',args),('build',['cmake','--build',str(BUILD),'--parallel','1','--target',*targets])]:
 with (ROOT/(phase+'.log')).open('w') as log:
  child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=limit)
  (ROOT/'build-process.json').write_text(json.dumps({'pid':child.pid,'phase':phase,'argv':command}))
  while child.poll() is None:
   rss,available=usage(child.pid);peak=max(peak,rss);low=min(low,available)
   if stop:reason='interrupted'
   elif rss>2048:reason='build_rss_limit'
   elif available<512:reason='host_available_floor'
   elif time.monotonic()-start>5400:reason='wall_time_limit'
   if reason:
    os.killpg(child.pid,signal.SIGTERM)
    try:child.wait(timeout=10)
    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    break
   time.sleep(.5)
  status=child.wait()
 if status or reason:break
binaries={}
if status==0 and not reason:
 for target in targets:
  p=BUILD/'bin'/target;h=hashlib.file_digest(p.open('rb'),'sha256').hexdigest();binaries[target]={'sha256':h,'bytes':p.stat().st_size}
result={'exit_status':status,'stop_reason':reason,'elapsed_seconds':round(time.monotonic()-start,2),'peak_build_rss_mib':peak,'minimum_host_available_mib':low,'binaries':binaries,'recipe_sha256':hashlib.sha256((ROOT/'build-recipe.json').read_bytes()).hexdigest(),'active_runtime_changed':False}
(ROOT/'build-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
raise SystemExit(0 if status==0 and not reason else 1)
