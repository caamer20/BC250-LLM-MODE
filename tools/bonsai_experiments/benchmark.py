"""Synthetic same-model baseline/O2/FP16 comparison with monitored restoration."""
from pathlib import Path
import hashlib,json,os,random,signal,socket,subprocess,threading,time,urllib.request,statistics
from bc250_llm_mode.app import Application
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.prism_runtime import pinned_build_id,pinned_manifest
from bc250_llm_mode.runtime_policy import request_activity
from bc250_llm_mode.server import minimal_inference_probe

ROOT=Path('/var/tmp/bc250-crack-performance-20260918/benchmarks');ROOT.mkdir(mode=0o700,exist_ok=True)
REPORT=ROOT/'comparison.json';assert not REPORT.exists()
app=Application.compose(AppPaths.for_home());before=app.read_model()
assert before['current_model']=='bonsai2-27b-crack' and before['current_ctx']==8192
assert before['optimizations']['parallel_slots']==1 and before['runtime_component_id']==pinned_build_id()
assert app.operation_query.active_summary().active_count==0
assert app.runtime_lifecycle.status()['recovery_barrier']is None
assert request_activity(before['server_port'])['active']==0
row=next(r for r in app.model_library.entries()if r.alias=='bonsai2-27b-crack')
assert row.content_digest=='sha256:5a264c32944e90222d275b47239ca50b56a175e2edc97e5bdb99c59d7c8f4e15'
model=Path(row.path);assert model.stat().st_size==5946648928
container=before['container_name'];server=before['llama_cpp_path']+'/build/bin/llama-server'
actual=subprocess.check_output(['podman','exec',container,'sha256sum',server],text=True).split()[0]
assert actual==pinned_manifest()['binaries'][0]['sha256']
keys=['current_model','current_ctx','optimizations','runtime_component_id','runtime_source_commit','runtime_server_sha256','container_name','server_port']
fingerprint=lambda s:hashlib.sha256(json.dumps({k:s.get(k)for k in keys},sort_keys=True).encode()).hexdigest()
prior_fingerprint=fingerprint(before)
prior_unit=Path(subprocess.check_output(['systemctl','show',before['service_name'],'--property=FragmentPath','--value'],text=True).strip())
unit_bytes=prior_unit.read_bytes();boot=subprocess.check_output(['systemctl','get-default'],text=True).strip()
was_active=app.model_server.status(before,app.runner())['active'];assert was_active
with socket.socket()as sock:sock.bind(('127.0.0.1',18080))
gpu=next(p for p in Path('/sys/class/drm').glob('card*/device')if(p/'mem_info_vram_total').exists())
def read_num(path,divisor=1):
    try:return float(path.read_text())/divisor
    except (OSError,AttributeError,ValueError):return None
def resources():
    available=next(int(x.split()[1])/1024 for x in Path('/proc/meminfo').read_text().splitlines()if x.startswith('MemAvailable:'))
    temperatures=[int(p.read_text())/1000 for p in(gpu/'hwmon').glob('hwmon*/temp*_input')]
    rss=0
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            if(proc/'comm').read_text().strip()=='llama-server':rss+=int((proc/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1024**2
        except(OSError,ValueError,IndexError):pass
    return dict(gpu_clock_mhz=read_num(next((gpu/'hwmon').glob('hwmon*/freq1_input'),None),1e6),gpu_busy_percent=read_num(gpu/'gpu_busy_percent'),power_watts=read_num(next((gpu/'hwmon').glob('hwmon*/power1_average'),None),1e6),host_available_mib=available,temperature_c=max(temperatures),rss_mib=rss,vram_used_mib=int((gpu/'mem_info_vram_used').read_text())/1024**2,vram_total_mib=int((gpu/'mem_info_vram_total').read_text())/1024**2)

optimized=Path('/var/tmp/bc250-crack-performance-20260918/build-o2/bin/llama-server')
build=json.loads((optimized.parents[2]/'build-result.json').read_text())
assert build['exit_status']==0 and build['stop_reason'] is None
assert hashlib.file_digest(optimized.open('rb'),'sha256').hexdigest()==build['binaries']['llama-server']['sha256']
report={'model_sha256':row.content_digest,'context':8192,'slots':1,'synthetic_prompts_only':True,'production_configuration_sha256':prior_fingerprint,'variants':[],'limits':{'temperature_c':82,'host_available_floor_mib':512,'fast_vram_reserve_mib':768,'server_rss_ceiling_mib':2800,'per_variant_seconds':600},'gpu_controls_changed':False}
stop=threading.Event();stopped=False;process=None;pidfile=None
for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,lambda *_:stop.set())
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path,payload=None,port=18080,timeout=120):
    data=json.dumps(payload).encode() if payload is not None else None
    return opener.open(urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,headers={'Content-Type':'application/json'}),timeout=timeout)
def terminate_trial():
    global process
    if process is None or process.poll() is not None:return
    assert pidfile and pidfile.exists()
    pid=int(pidfile.read_text());assert pid>1
    code="import os,signal,sys,pathlib;pid=int(sys.argv[1]);p=pathlib.Path('/proc')/str(pid);args=(p/'cmdline').read_bytes().split(b'\\0') if p.exists() else [];assert not args or (b'18080' in args and b'crack-speed-trial' in args);os.kill(pid,int(sys.argv[2])) if args else None"
    for sig in (signal.SIGTERM,signal.SIGKILL):
        subprocess.run(['podman','exec',container,'python3','-c',code,str(pid),str(int(sig))],check=True,timeout=15)
        try:process.wait(timeout=15);break
        except subprocess.TimeoutExpired:continue
    assert process.poll() is not None
def check(prompt,expected,stream=False):
    payload=dict(model='crack-speed-trial',messages=[{'role':'user','content':prompt}],max_tokens=24,temperature=0,stream=stream)
    with request('/v1/chat/completions',payload) as response:
        if stream:
            answer='';done=False
            for index,line in enumerate(response):
                assert index<4096 and len(line)<65536
                if not line.startswith(b'data:'):continue
                raw=line[5:].strip()
                if raw==b'[DONE]':done=True;break
                for choice in json.loads(raw).get('choices',[]):answer+=choice.get('delta',{}).get('content')or''
                assert len(answer)<4096
            assert done
        else:answer=json.load(response)['choices'][0]['message'].get('content')or''
    assert answer.strip().rstrip('.!').casefold()==expected.casefold(), 'fixed-answer mismatch'
    return {'expected':expected,'passed':True,'stream':stream}
def probe(result,done):
    try:
        deadline=time.monotonic()+150
        while time.monotonic()<deadline and not stop.is_set():
            if process.poll() is not None:raise RuntimeError('server exited')
            try:
                with request('/health',timeout=3) as response:
                    if response.status==200:break
            except Exception:time.sleep(.5)
        else:raise TimeoutError('startup health')
        with request('/props') as response:props=json.load(response)
        assert props['total_slots']==1 and props['default_generation_settings']['n_ctx']==8192
        result['checks'].append(check('What is 2 + 2? Answer with only the digit.','4',True))
        result['checks'].append(check('What is the capital of France? Answer with only the city name.','Paris'))
        result['checks'].append(check('What is 17 minus 9? Answer with only the digit.','8'))
        for index in range(2):
            payload=dict(model='crack-speed-trial',messages=[{'role':'user','content':'Write a detailed factual explanation of how rain forms, covering evaporation, condensation, clouds, and precipitation. Use at least 400 words.'}],max_tokens=192,temperature=0,seed=93241,stream=False,cache_prompt=False)
            result['phase']='generation';start=time.monotonic()
            with request('/v1/chat/completions',payload,timeout=240) as response:answer=json.load(response)
            text=answer['choices'][0]['message'].get('content')or'';usage=answer.get('usage')or{};timings=answer.get('timings')or{}
            tokens=int(usage.get('completion_tokens')or 0)
            result['generation'].append({'repeat':index+1,'completion_tokens':tokens,'prompt_tokens':usage.get('prompt_tokens'),'seconds':round(time.monotonic()-start,3),'timings':timings,'output_sha256':hashlib.sha256(text.encode()).hexdigest(),'output_characters':len(text)})
            assert tokens>=128 and len(text)>150 and all(ord(c)>=32 or c in '\r\n\t' for c in text)
        result['checks'].append(check('What is 2 + 2? Answer with only the digit.','4',True))
        result['passed']=True
    except BaseException as error:result['error']=type(error).__name__+': '+str(error)[:160]
    finally:result['phase']='finished';done.set()

try:
    assert fingerprint(app.read_model())==prior_fingerprint
    result=app.model_server.stop(app.read_model(),app.runner());stopped=True;assert result['active'] is False
    subprocess.run(['podman','start',container],check=True,stdout=subprocess.DEVNULL)
    for label,binary,disable_f16 in [('baseline-o0-f32',server,True),('optimized-o2-f32','/run/host'+str(optimized),True),('optimized-o2-f16','/run/host'+str(optimized),False)]:
        if stop.is_set():break
        cool_end=time.monotonic()+180
        while resources()['temperature_c']>=65 and time.monotonic()<cool_end and not stop.is_set():time.sleep(1)
        assert resources()['temperature_c']<65,'cooldown did not finish'
        result={'name':label,'disable_f16':disable_f16,'binary_sha256':pinned_manifest()['binaries'][0]['sha256'] if label.startswith('baseline') else build['binaries']['llama-server']['sha256'],'checks':[],'generation':[],'samples':[],'phase':'startup','passed':False}
        report['variants'].append(result)
        pidfile=ROOT/(label+'.pid');assert not pidfile.exists()
        args=[binary,'-m',str(model),'--host','127.0.0.1','--port','18080','--alias','crack-speed-trial','--n-gpu-layers','99','--ctx-size','8192','--parallel','1','--batch-size','128','--ubatch-size','128','--threads','2','--threads-batch','2','--cache-type-k','q8_0','--cache-type-v','q8_0','--flash-attn','auto','--metrics','--cache-reuse','256','--defrag-thold','0.1','--load-mode','mmap','--reasoning','off','--no-context-shift']
        env={'GGML_VK_DISABLE_F16':'1'} if disable_f16 else {}
        code="import os,sys,json,pathlib;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));env={k:v for k,v in os.environ.items()if not k.startswith(('LLAMA_','GGML_'))};env.update(json.loads(sys.argv[2]));os.execvpe(sys.argv[3],sys.argv[3:],env)"
        start=time.monotonic();done=threading.Event();reason=None
        with (ROOT/(label+'.log')).open('w') as log:
            process=subprocess.Popen(['podman','exec','--user','root',container,'python3','-c',code,'/run/host'+str(pidfile),json.dumps(env),*args],stdout=log,stderr=subprocess.STDOUT)
            worker=threading.Thread(target=probe,args=(result,done),daemon=True);worker.start()
            while not done.is_set() and not stop.is_set():
                sample=resources();sample.update(seconds=round(time.monotonic()-start,2),phase=result['phase']);result['samples'].append(sample)
                if sample['temperature_c']>=82:reason='temperature_limit'
                elif sample['host_available_mib']<512:reason='host_memory_floor'
                elif sample['vram_total_mib']-sample['vram_used_mib']<768:reason='fast_vram_reserve'
                elif sample['rss_mib']>2800:reason='server_rss_ceiling'
                elif time.monotonic()-start>600:reason='wall_time_limit'
                if reason:break
                time.sleep(.5)
            terminate_trial();worker.join(timeout=10);assert not worker.is_alive()
        result['stop_reason']=reason or ('interrupted' if stop.is_set() else None)
        if result['stop_reason']:result['passed']=False
        result['elapsed_seconds']=round(time.monotonic()-start,2)
        generation_samples=[s for s in result['samples'] if s['phase']=='generation']
        summary={'samples':len(result['samples']),'peak_temperature_c':max(s['temperature_c'] for s in result['samples']),'minimum_host_available_mib':min(s['host_available_mib'] for s in result['samples'])}
        for field in ['gpu_clock_mhz','gpu_busy_percent','power_watts']:
            values=[s[field] for s in generation_samples if s[field] is not None]
            summary[field]={'min':min(values),'median':statistics.median(values),'max':max(values)} if values else None
        result['resource_summary']=summary
        (ROOT/(label+'.json')).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='samples'}),flush=True)
finally:
    terminate_trial()
    if stopped:
        assert fingerprint(app.read_model())==prior_fingerprint,'owner configuration changed'
        restored=app.model_server.start(app.read_model(),app.runner());assert restored.get('healthy')or restored.get('ok'),restored
        assert minimal_inference_probe(app.read_model(),timeout=20.0).get('ok') is True
    assert fingerprint(app.read_model())==prior_fingerprint
    assert prior_unit.read_bytes()==unit_bytes and subprocess.check_output(['systemctl','get-default'],text=True).strip()==boot
    with request('/health',port=8080,timeout=5) as response:assert response.status==200
    with socket.socket()as s:assert s.connect_ex(('127.0.0.1',18080))!=0
    report['production_restored']=True;report['temporary_listener_removed']=True
    REPORT.write_text(json.dumps({**report,'variants':[{k:v for k,v in r.items() if k!='samples'} for r in report['variants']]},indent=2)+'\n')
    print('PRODUCTION_RESTORED',flush=True)
