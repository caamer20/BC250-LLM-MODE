import ctypes, hashlib, json, math, os, pathlib, struct, subprocess, time
BASE=pathlib.Path('/var/tmp/bc250-bonsai-experimental')
WORK=BASE/'crack-conversion'
SRC=BASE/'models/Bonsai-2-27B-PQ2_0-CRACK.gguf'
DST=BASE/'models/Bonsai-2-27B-PTQ1_0-CRACK-lossless.gguf'
EXPECTED='5b24ea3eebc3e0bccd05fb474eb88b10c57699d71a5db2f29485e3789a70d55d'
def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def parse(path):
    f=open(path,'rb');raw=bytearray()
    def read(n):
        assert 0<=n<=64*1024*1024 and len(raw)+n<=64*1024*1024
        b=f.read(n);assert len(b)==n;raw.extend(b);return b
    def num(fmt):return struct.unpack('<'+fmt,read(struct.calcsize('<'+fmt)))[0]
    def string():return read(num('Q')).decode('utf-8')
    def value(t):
        if t==8:return string()
        if t==9:
            ty=num('I');n=num('Q');assert n<=1000000
            for _ in range(n):value(ty)
            return None
        return num({0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}[t])
    assert read(4)==b'GGUF' and num('I')==3
    nt,nk=num('Q'),num('Q');assert nt==851 and nk<10000
    align=32;ft=None;meta={}
    for _ in range(nk):
        k=string();t=num('I');pos=len(raw);v=value(t)
        if k=='general.file_type':assert t==4 and v in (141,142);ft=pos
        if k=='general.alignment':align=v
        if k.startswith('prism.hadamard.'):meta[k]=v
    assert ft is not None and align==32 and meta.get('prism.hadamard.version')==1
    ts=[]
    for _ in range(nt):
        name=string();nd=num('I');assert 1<=nd<=4
        dims=[num('Q') for _ in range(nd)];n=math.prod(dims);tp=len(raw);ty=num('I');off=num('Q')
        assert ty in (0,30,142) and n>0
        if ty==142:assert n%128==0
        size=n*{0:4,30:2}.get(ty,0) if ty!=142 else n//128*34
        ts.append(dict(name=name,n=n,ty=ty,offset=off,size=size,pos=tp))
    start=(len(raw)+align-1)//align*align;read(start-len(raw));f.close()
    ranges=sorted((t['offset'],t['offset']+t['size'])for t in ts)
    assert all(a[1]<=b[0] for a,b in zip(ranges,ranges[1:]))
    assert start+ranges[-1][1]==path.stat().st_size
    assert sum(t['ty']==142 for t in ts)==402
    return raw,ts,start,ft,meta
def main():
    started=time.time();assert not DST.exists() and not DST.with_suffix('.partial').exists()
    assert sha(SRC)==EXPECTED
    raw,ts,start,ft,meta=parse(SRC)
    original_header=hashlib.sha256(raw).hexdigest()
    struct.pack_into('<I',raw,ft,143);offset=0
    for t in ts:
        offset=(offset+31)//32*32;t['new_offset']=offset
        t['new_size']=t['n']//128*28 if t['ty']==142 else t['size']
        struct.pack_into('<IQ',raw,t['pos'],143 if t['ty']==142 else t['ty'],offset)
        offset+=t['new_size']
    assert os.statvfs(BASE).f_bavail*os.statvfs(BASE).f_frsize>start+offset+10*1024**3
    lib=ctypes.CDLL(str(WORK/'repack.so'))
    for name in ('repack','verify'):
        fn=getattr(lib,name);fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t];fn.restype=ctypes.c_int
    # Exhaust all 3^5 packed patterns, plus varied scales, then check rejection.
    import random
    rng=random.Random(918);samples=[]
    for v in range(243):
        codes=[rng.randrange(3) for _ in range(128)];x=v
        for i in range(5):codes[i*16]=x%3;x//=3
        samples.append(struct.pack('<H',v)+bytes(sum(codes[i+j]<<(2*j) for j in range(4)) for i in range(0,128,4)))
    sample=b''.join(samples);out=ctypes.create_string_buffer(28*len(samples))
    assert lib.repack(sample,out,len(samples))==0 and lib.verify(sample,out,len(samples))==0
    bad=b'\x00\x3c\xff'+bytes(31);assert lib.repack(bad,out,1)==3
    partial=DST.with_suffix('.partial');blocks=0
    with open(SRC,'rb')as src,open(partial,'xb')as dst:
        dst.write(raw)
        for index,t in enumerate(ts):
            src.seek(start+t['offset']);dst.seek(start+t['new_offset']);remain=t['size']
            while remain:
                count=min(remain,34*32768 if t['ty']==142 else 1024*1024)
                data=src.read(count);assert len(data)==count;remain-=count
                if t['ty']==142:
                    nb=count//34;buf=ctypes.create_string_buffer(nb*28)
                    rc=lib.repack(data,buf,nb)
                    if rc:raise ValueError(f'Non-ternary/non-finite block: {t["name"]}, code {rc}')
                    assert lib.verify(data,buf,nb)==0
                    dst.write(buf.raw);blocks+=nb
                else:dst.write(data)
            if index%50==0:print(json.dumps({'phase':'convert','tensor':index,'total':851}),flush=True)
        dst.flush();os.fsync(dst.fileno())
    assert partial.stat().st_size==start+offset
    # Re-read stored output and source: verify every code and original scale,
    # and compare all F32/BF16 bytes (including transforms) without conversion.
    with open(SRC,'rb')as src,open(partial,'rb')as dst:
        assert dst.read(start)==raw
        for t in ts:
            src.seek(start+t['offset']);dst.seek(start+t['new_offset']);remain=t['size']
            while remain:
                count=min(remain,34*32768 if t['ty']==142 else 1024*1024);a=src.read(count);remain-=count
                if t['ty']==142:
                    b=dst.read(count//34*28);assert len(b)==count//34*28 and lib.verify(a,b,count//34)==0
                else:assert a==dst.read(count)
    assert sha(SRC)==EXPECTED
    digest=sha(partial);os.rename(partial,DST)
    report=dict(source=str(SRC),source_sha256=EXPECTED,output=str(DST),output_sha256=digest,output_bytes=DST.stat().st_size,tensors=851,repacked_tensors=402,unchanged_tensors=449,verified_blocks=blocks,verified_weights=blocks*128,all_codes_and_scale_bytes_equal=True,all_other_tensor_bytes_equal=True,metadata_preserved_except_general_file_type=True,original_header_sha256=original_header,hadamard=meta,prism_commit='5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6',seconds=time.time()-started,loaded=False,application_registered=False)
    (WORK/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
if __name__=='__main__':main()
