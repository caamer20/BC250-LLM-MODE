#!/usr/bin/python3
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
compiler=sys.argv[1];args=sys.argv[2:]
sources=[Path(a) for a in args if a.endswith('.comp.cpp')]
if len(sources)!=1 or sources[0].stat().st_size<=32*1024**2:
 os.execv(compiler,[compiler,*args])
source=sources[0];out=Path(args[args.index('-o')+1]);dep=Path(args[args.index('-MF')+1]);directory=Path(__file__).resolve().parent/'shader-chunks'/source.name;directory.mkdir(parents=True,exist_ok=True)
header=b'#include "ggml-vulkan-shaders.hpp"\n';chunks=[];current=bytearray(header+b'\n');names=[];original=hashlib.sha256();body_digest=hashlib.sha256();state='between';name=None;limit=8*1024**2
with source.open('rb') as stream:
 first=stream.readline();assert first==header;original.update(first)
 for line in stream:
  original.update(line);body_digest.update(line)
  if state=='between':
   if line.strip():
    match=re.fullmatch(rb'const uint64_t ([a-zA-Z0-9_]+)_len = ([0-9]+);\n',line);assert match,line[:100]
    name=match[1].decode();names.extend([name+'_len',name+'_data']);state='array'
  elif state=='array':
   assert re.fullmatch(rb'const unsigned char '+name.encode()+rb'_data\[[0-9]+\] = \{\n',line),line[:100];state='data'
  elif state=='data':
   if line.strip()==b'};':state='between'
   else:assert not line.strip() or re.fullmatch(rb'(?:0x[0-9a-f]+,)+\n',line),line[:100]
  current.extend(line)
  if state=='between' and len(current)>=limit:
   path=directory/(str(len(chunks))+'.cpp');path.write_bytes(current);chunks.append(path);current=bytearray(header+b'\n')
assert state=='between'
if len(current)>len(header)+1:
 path=directory/(str(len(chunks))+'.cpp');path.write_bytes(current);chunks.append(path)
rebuilt=hashlib.sha256()
for piece in chunks:
 data=piece.read_bytes();assert data.startswith(header+b'\n');rebuilt.update(data[len(header)+1:])
assert rebuilt.digest()==body_digest.digest()
assert names and len(names)==len(set(names)) and len(chunks)<64
objects=[]
for i,piece in enumerate(chunks):
 object_path=directory/(str(i)+'.o');piece_args=list(args)
 piece_args[piece_args.index(str(source))]=str(piece)
 piece_args[piece_args.index('-o')+1]=str(object_path)
 piece_args[piece_args.index('-MF')+1]=str(directory/(str(i)+'.d'))
 subprocess.run([compiler,*piece_args],check=True);objects.append(object_path)
subprocess.run(['/usr/bin/ld','-r','-o',str(out),*map(str,objects)],check=True)
symbols=subprocess.check_output(['/usr/bin/nm','-g','--defined-only',str(out)],text=True)
actual={line.split()[-1] for line in symbols.splitlines() if line.strip()}
assert actual==set(names),(len(actual),len(names))
dep.write_text(str(out)+': '+str(source)+' '+str(source.parent/'ggml-vulkan-shaders.hpp')+'\n')
report={'source':str(source),'source_sha256':original.hexdigest(),'reassembled_body_sha256':rebuilt.hexdigest(),'reassembled_body_byte_equality':True,'bytes':source.stat().st_size,'chunk_count':len(chunks),'symbols_verified':len(names),'chunk_source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in chunks},'object_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'method':'Verbatim complete constant definitions split at declaration boundaries, compiled sequentially with identical flags/header, linked with ld -r; exact exported symbol-set check.'}
(directory/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
print('Bounded shader compilation:',json.dumps({'source':source.name,'chunks':len(chunks),'symbols':len(names)}),flush=True)
