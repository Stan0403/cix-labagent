"""Local CIX SenseVoice adapter following the 26_Q2 fixed-shape preprocessing contract.

Reference: cix/ai_model_hub_26_Q2/models/Audio/Speech_Recognition/onnx_sensevoice.
No Torch or downloaded Python code is executed; only the selected model and tokenizer are loaded.
"""
import io
import re
import threading
import time
from pathlib import Path


class AudioError(ValueError):
    pass


def decode_audio(raw: bytes, max_seconds=60):
    import av
    import numpy as np

    if not raw:
        raise AudioError("音频为空")
    pieces, count = [], 0
    limit = int(max_seconds * 16000)
    try:
        with av.open(io.BytesIO(raw)) as container:
            if not container.streams.audio:
                raise AudioError("文件中没有音轨")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    samples = converted.to_ndarray().reshape(-1).astype(np.float32)
                    count += samples.size
                    if count > limit:
                        raise AudioError(f"录音超过 {max_seconds} 秒，请分段录制")
                    pieces.append(samples)
            for converted in resampler.resample(None):
                samples = converted.to_ndarray().reshape(-1).astype(np.float32)
                count += samples.size
                if count > limit:
                    raise AudioError(f"录音超过 {max_seconds} 秒，请分段录制")
                pieces.append(samples)
    except AudioError:
        raise
    except Exception as exc:
        raise AudioError("无法解码音频，请使用有效的 WAV/MP3/WebM 文件") from exc
    if not pieces or count < 3200:
        raise AudioError("音频太短，至少需要 0.2 秒")
    waveform = np.concatenate(pieces)
    if not np.isfinite(waveform).all():
        raise AudioError("音频含无效样本")
    return waveform


def read_cmvn(path: Path):
    import numpy as np

    lines = path.read_text(encoding="utf-8").splitlines()
    arrays = {}
    for i, line in enumerate(lines[:-1]):
        if line.strip().startswith(("<AddShift>", "<Rescale>")):
            values = lines[i + 1]
            if "[" not in values or "]" not in values:
                raise AudioError("CMVN 文件格式错误")
            arrays[line.split()[0]] = np.fromstring(values.split("[", 1)[1].split("]", 1)[0], sep=" ")
    if set(arrays) != {"<AddShift>", "<Rescale>"}:
        raise AudioError("CMVN 缺少均值或缩放")
    if any(a.shape != (560,) or not np.isfinite(a).all() for a in arrays.values()):
        raise AudioError("CMVN 应为 560 维有效数值")
    return arrays["<AddShift>"], arrays["<Rescale>"]


def stack_lfr(feats, m=7, n=6):
    import numpy as np

    if feats.ndim != 2 or len(feats) == 0:
        raise AudioError("没有足够的声学特征")
    count = (len(feats) + n - 1) // n
    padded = np.vstack([np.repeat(feats[:1], (m - 1) // 2, axis=0), feats])
    rows = []
    for i in range(count):
        window = padded[i * n:i * n + m]
        if len(window) < m:
            window = np.vstack([window, np.repeat(padded[-1:], m - len(window), axis=0)])
        rows.append(window.reshape(-1))
    return np.asarray(rows, dtype=np.float32)


def ctc_decode(logits, length, tokenizer):
    import numpy as np

    logits = np.asarray(logits)
    lengths = np.asarray(length).reshape(-1)
    if logits.size != 256 * 25055 or not np.isfinite(logits).all():
        raise AudioError("NPU 返回的语音张量无效")
    if lengths.size != 1 or not np.isfinite(lengths).all():
        raise AudioError("NPU 返回的输出长度无效")
    length = int(lengths[0])
    if not 0 <= length <= 256:
        raise AudioError("NPU 返回的输出长度无效")
    if not length:
        return ""
    ids = logits.reshape(256, 25055)[:length].argmax(axis=-1)
    ids = ids[np.r_[True, ids[1:] != ids[:-1]]]
    ids = ids[ids != 0].tolist()
    text = tokenizer.decode(ids)
    return re.sub(r"<\|[^|]*\|>", "", text).strip()


class SenseVoiceCIX:
    def __init__(self, directory: Path, engine_factory=None):
        import kaldi_native_fbank as knf
        import sentencepiece as spm
        import yaml

        self.directory = directory.resolve()
        self.lock = threading.Lock()
        required = ["sense_voice_mask256.cix", "model/config.yaml", "model/am.mvn",
                    "model/chn_jpn_yue_eng_ko_spectok.bpe.model"]
        if any(not (self.directory / f).is_file() for f in required):
            raise AudioError("SenseVoice 文件不完整，请按下载清单核对")
        config = yaml.safe_load((self.directory / "model/config.yaml").read_text(encoding="utf-8"))
        frontend = config["frontend_conf"]
        expected = {"fs": 16000, "n_mels": 80, "frame_length": 25, "frame_shift": 10, "lfr_m": 7, "lfr_n": 6}
        if any(frontend.get(k) != v for k, v in expected.items()):
            raise AudioError("此适配器只支持 26_Q2 SenseVoice mask256 的前处理配置")
        self.knf = knf
        self.options = knf.FbankOptions()
        self.options.frame_opts.samp_freq = 16000
        self.options.frame_opts.dither = 1.0  # Default in the CIX WavFrontend.
        self.options.frame_opts.window_type = frontend.get("window", "hamming")
        self.options.frame_opts.frame_shift_ms = 10
        self.options.frame_opts.frame_length_ms = 25
        self.options.frame_opts.snip_edges = True
        self.options.mel_opts.num_bins = 80
        self.options.mel_opts.debug_mel = False
        self.options.energy_floor = 0
        self.shift, self.scale = read_cmvn(self.directory / "model/am.mvn")
        self.tokenizer = spm.SentencePieceProcessor(
            model_file=str(self.directory / "model/chn_jpn_yue_eng_ko_spectok.bpe.model"))
        if engine_factory is None:
            from NOE_Engine import EngineInfer

            engine_factory = EngineInfer
        self.engine = engine_factory(str(self.directory / "sense_voice_mask256.cix"))
        self.backend = "CIX NPU / libnoe"

    def features(self, waveform):
        import numpy as np

        fbank = self.knf.OnlineFbank(self.options)
        fbank.accept_waveform(16000, (waveform * 32768).tolist())
        frames = fbank.num_frames_ready
        if not frames:
            raise AudioError("没有足够的音频帧")
        feats = np.stack([fbank.get_frame(i) for i in range(frames)]).astype(np.float32)
        feats = stack_lfr(feats)
        feats = ((feats + self.shift) * self.scale).astype(np.float32)
        length = len(feats)
        if length > 252:
            raise AudioError("语音片段超出 NPU 固定输入长度")
        speech = np.pad(feats, ((0, 252 - length), (0, 0)))[None].astype(np.float32)
        mask = (np.arange(256)[None] < length)
        return speech, mask

    def transcribe(self, waveform, language="zh"):
        import numpy as np

        language_ids = {"auto": 0, "zh": 3, "en": 4, "yue": 7, "ja": 11, "ko": 12}
        if language not in language_ids:
            raise AudioError("不支持该语言")
        if not self.lock.acquire(blocking=False):
            raise AudioError("语音服务正在处理另一段录音，请稍后重试")
        started = time.perf_counter()
        try:
            chunks = []
            step = 12 * 16000
            for offset in range(0, len(waveform), step):
                chunk = waveform[offset:offset + step]
                if len(chunk) < 400:
                    continue
                if float(np.max(np.abs(chunk))) < 1e-6:
                    chunks.append("")
                    continue
                speech, mask = self.features(chunk)
                outputs = self.engine.forward([speech, mask, np.array([language_ids[language]], np.int32),
                                               np.array([14], np.int32)])
                if len(outputs) != 2:
                    raise AudioError("NPU 语音输出数量异常")
                chunks.append(ctc_decode(outputs[0], outputs[1], self.tokenizer))
            return {"text": "".join(chunks), "model": "sensevoice-small-cix", "backend": self.backend,
                    "audio_seconds": round(len(waveform) / 16000, 3),
                    "elapsed_seconds": round(time.perf_counter() - started, 3), "segments": len(chunks),
                    "note": "长录音按 12 秒分段，切分点可能影响识别；转写需人工核对"}
        finally:
            self.lock.release()

    def close(self):
        with self.lock:
            self.engine.clean()
