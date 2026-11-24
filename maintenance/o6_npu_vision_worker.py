"""Private Unix-socket CIX encoder worker for the fixed-grid experimental bridge."""
from pathlib import Path
import argparse
import json
import os
import signal
import socket
import struct
import time
import numpy as np
from NOE_Engine import EngineInfer

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
MAGIC=0x51334e50
stopping=False

def receive(connection,size):
    chunks=[]
    while size:
        data=connection.recv(min(size,1<<20))
        if not data: raise ConnectionError('Truncated encoder request')
        chunks.append(data); size-=len(data)
    return b''.join(chunks)

def stop(*args):
    global stopping
    stopping=True

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--socket',default=str(WORK/'qwen3vl-npu.sock'))
    args=parser.parse_args()
    path=Path(args.socket)
    assert path.parent==WORK and len(str(path).encode())<108
    if path.exists():
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
            try: probe.connect(str(path))
            except ConnectionRefusedError: path.unlink()
            else: raise RuntimeError('Encoder worker already active')
    engine=EngineInfer(str(WORK/'npu-vision-candidate/vision.cix'))
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
            server.bind(str(path)); os.chmod(path,0o600); server.listen(2); server.settimeout(1)
            (WORK/'npu-worker-ready.json').write_text(json.dumps({'pid':os.getpid(),'socket':str(path)}))
            while not stopping:
                try: connection,_=server.accept()
                except socket.timeout: continue
                with connection:
                    connection.settimeout(30)
                    try:
                        magic,width,height,count=struct.unpack('<4I',receive(connection,16))
                        assert (magic,width,height,count)==(MAGIC,320,320,320*320*3)
                        pixels=np.frombuffer(receive(connection,count*4),dtype='<f4').reshape(320,320,3)
                        assert np.isfinite(pixels).all()
                        chw=pixels.transpose(2,0,1)
                        patches=np.repeat(chw[None],2,axis=0).reshape(1,2,3,10,2,16,10,2,16)
                        patches=patches.transpose(0,3,6,4,7,2,1,5,8).reshape(400,1536).copy()
                        started=time.monotonic()
                        values=engine.forward([patches])
                        assert len(values)==4
                        encoded=np.concatenate([np.asarray(v).reshape(100,2560) for v in values],axis=1).astype('<f4')
                        assert np.isfinite(encoded).all()
                        elapsed=time.monotonic()-started
                        connection.sendall(struct.pack('<2I',MAGIC,encoded.size)+encoded.tobytes())
                        with (WORK/'npu-worker-requests.jsonl').open('a') as log:
                            log.write(json.dumps({'seconds':elapsed,'width':width,'height':height})+'\n')
                    except Exception as error:
                        print('Encoder request rejected: '+repr(error),flush=True)
                        try: connection.sendall(struct.pack('<2I',MAGIC,0))
                        except OSError: pass
    finally:
        engine.clean()
        path.unlink(missing_ok=True)
