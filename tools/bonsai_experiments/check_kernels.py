"""Bounded optimized PTQ and Hadamard checks against CPU reference."""
import hashlib,json,os,re,subprocess,time
from pathlib import Path
root=Path('/run/host/var/tmp/bc250-crack-performance-20260918')
exe=root/'build-o2/bin/test-backend-ops'
build=json.loads((root/'build-result.json').read_text());assert build['exit_status']==0 and not build['stop_reason']
assert hashlib.file_digest(exe.open('rb'),'sha256').hexdigest()==build['binaries']['test-backend-ops']['sha256']
checks=[('ptq-small','MUL_MAT',r'^type_a=ptq1_0,type_b=(f32|f16),m=16,n=(1|2|8),k=256,bs=\[1,1\],nr=\[1,1\],per=\[0,1,2,3\],k_v=0,o=1,src_overlap=0$',5),('hadamard-small','MUL_MAT_HADAMARD',r'^blk=1024,width=5120,n_tokens=1,type_x=f32$',1)]
results=[]
for disable in [True,False]:
 for name,op,pattern,expected in checks:
  env={k:v for k,v in os.environ.items() if not k.startswith(('GGML_','LLAMA_'))}
  if disable:env['GGML_VK_DISABLE_F16']='1'
  start=time.monotonic();label=name+('-f32' if disable else '-f16')
  argv=[str(exe),'test','-b','Vulkan0','-o',op,'-p',pattern,'-j','1']
  proc=subprocess.run(argv,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
  log=proc.stdout;(root/(label+'.log')).write_text(log)
  clean=re.sub(r'\x1b\[[0-9;]*m','',log)
  passed=[line.strip() for line in clean.splitlines() if line.strip().startswith(op+'(') and line.strip().endswith(': OK')]
  skipped=[line.strip() for line in log.splitlines() if 'not supported' in line.lower() or 'skipped' in line.lower()]
  result={'name':label,'disable_f16':disable,'exit_status':proc.returncode,'seconds':round(time.monotonic()-start,3),'expected_ok_count':expected,'ok_lines':passed,'skipped_lines':skipped,'passed':proc.returncode==0 and len(passed)==expected,'log_sha256':hashlib.sha256(log.encode()).hexdigest(),'device_lines':[line for line in log.splitlines() if 'ggml_vulkan:' in line and 'fp16:' in line]}
  results.append(result);print(json.dumps(result),flush=True)
(root/'kernel-checks.json').write_text(json.dumps({'binary_sha256':build['binaries']['test-backend-ops']['sha256'],'checks':results,'scope':'Small fixed PTQ and signed Hadamard checks, not full-model or sustained qualification'},indent=2)+'\n')
raise SystemExit(0 if all(r['passed'] for r in results) else 1)
