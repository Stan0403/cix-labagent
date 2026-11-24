"""Build a separate pinned llama.cpp worktree with an opt-in local vision bridge."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import time

WORK=Path.home()/'.local/state/codex-model-accel/20261117'
ORIGINAL=Path.home()/'llama.cpp'
SOURCE=Path.home()/'llama.cpp-codex-npu'
COMMIT='57819b8d4b39d893408e51520dff3d47d1ebb757'
status={'status':'building','commit':COMMIT,'steps':[]}
record=WORK/'npu-bridge-build.json'
record.write_text(json.dumps(status,indent=2))
with (WORK/'npu-bridge-build.log').open('w') as log:
    def run(command,timeout=600):
        started=time.monotonic()
        code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=timeout).returncode
        status['steps'].append({'command':command,'exit':code,'seconds':time.monotonic()-started})
        record.write_text(json.dumps(status,indent=2))
        if code: raise RuntimeError('Command failed; inspect build log')
    try:
        if not SOURCE.exists():
            run(['git','-C',str(ORIGINAL),'worktree','add','--detach',str(SOURCE),COMMIT])
        assert subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()==COMMIT
        clip=SOURCE/'tools/mtmd/clip.cpp'
        pristine=subprocess.check_output(['git','-C',str(SOURCE),'show',COMMIT+':tools/mtmd/clip.cpp']).decode()
        anchor='bool clip_image_batch_encode(clip_ctx * ctx, const int n_threads, const clip_image_f32_batch * imgs_c_ptr, float * vec) {'
        assert pristine.count(anchor)==1
        patched=pristine.replace(anchor,'#include "npu-vision-bridge.inc"\n\n'+anchor)
        before='    // if buffers are not allocated, we need to do a warmup run to allocate them'
        bridge='''    const char * npu_socket = std::getenv("LABAGENT_NPU_VISION_SOCKET");
    if (npu_socket && *npu_socket && vec != nullptr && !imgs.is_audio) {
        return codex_clip_npu(ctx, *imgs.entries[0], vec, npu_socket);
    }

'''
        assert patched.count(before)==1
        patched=patched.replace(before,bridge+before)
        before='        ggml_backend_tensor_get(embeddings, vec, 0, ggml_nbytes(embeddings));'
        assert patched.count(before)==1
        patched=patched.replace(before,before+'\n        if (!imgs.is_audio) { codex_clip_dump(ctx, *imgs.entries[0], vec); }')
        clip.write_text(patched)
        shutil.copy2(WORK/'npu-vision-bridge.inc',SOURCE/'tools/mtmd/npu-vision-bridge.inc')
        status['clip_sha256']=hashlib.sha256(clip.read_bytes()).hexdigest()
        build=SOURCE/'build'
        run(['cmake','-S',str(SOURCE),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DGGML_NATIVE=OFF',
             '-DGGML_VULKAN=OFF','-DGGML_CPU_KLEIDIAI=ON','-DGGML_CPU_ARM_ARCH=armv9-a+i8mm+dotprod+sve',
             '-DFETCHCONTENT_SOURCE_DIR_KLEIDIAI_DOWNLOAD='+str(ORIGINAL/'build/_deps/kleidiai_download-src')])
        run(['cmake','--build',str(build),'--target','llama-server','-j4'],timeout=1200)
        status['status']='passed'
    except Exception as error:
        status['status']='failed'; status['error']=repr(error)
        raise
    finally:
        record.write_text(json.dumps(status,indent=2))
