"""Build pinned CPU and NPU-vision llama.cpp binaries on Orion O6."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '57819b8d4b39d893408e51520dff3d47d1ebb757'


def run(args):
    subprocess.run([str(a) for a in args], check=True)


def build(kind, jobs):
    source = ROOT / '.deps' / ('llama-' + kind)
    if not source.exists():
        source.parent.mkdir(exist_ok=True)
        run(['git', 'clone', 'https://github.com/ggml-org/llama.cpp.git', source])
        run(['git', '-C', source, 'checkout', '--detach', COMMIT])
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != COMMIT:
        raise SystemExit('Wrong runtime revision: ' + actual)
    if kind == 'npu':
        pristine = subprocess.check_output(['git', '-C', str(source), 'show', COMMIT + ':tools/mtmd/clip.cpp']).decode()
        anchor = 'bool clip_image_batch_encode(clip_ctx * ctx, const int n_threads, const clip_image_f32_batch * imgs_c_ptr, float * vec) {'
        before = '    // if buffers are not allocated, we need to do a warmup run to allocate them'
        bridge = '''    const char * npu_socket = std::getenv("LABAGENT_NPU_VISION_SOCKET");
    if (npu_socket && *npu_socket && vec != nullptr && !imgs.is_audio) {
        return codex_clip_npu(ctx, *imgs.entries[0], vec, npu_socket);
    }

'''
        assert pristine.count(anchor) == 1 and pristine.count(before) == 1
        patched = pristine.replace(anchor, '#include "npu-vision-bridge.inc"\n\n' + anchor)
        patched = patched.replace(before, bridge + before)
        (source / 'tools/mtmd/clip.cpp').write_text(patched, encoding='utf-8')
        shutil.copyfile(ROOT / 'maintenance/o6_npu_vision_bridge.inc', source / 'tools/mtmd/npu-vision-bridge.inc')
    run(['cmake', '-S', source, '-B', source / 'build', '-DCMAKE_BUILD_TYPE=Release',
         '-DGGML_NATIVE=OFF', '-DGGML_VULKAN=OFF', '-DGGML_CPU_KLEIDIAI=ON',
         '-DGGML_CPU_ARM_ARCH=armv9-a+i8mm+dotprod+sve'])
    run(['cmake', '--build', source / 'build', '--target', 'llama-server', '-j' + str(jobs)])


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--kind', choices=['cpu', 'npu', 'both'], default='both')
    p.add_argument('--jobs', type=int, default=4)
    a = p.parse_args()
    for kind in (['cpu', 'npu'] if a.kind == 'both' else [a.kind]):
        build(kind, a.jobs)
