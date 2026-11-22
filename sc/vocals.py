"""Виділення вокалу з треку (щоб розпізнати слова пісні без музики).

Модель UVR MDX-Net «Voc_FT» (проєкт Ultimate Vocal Remover, TRvlvr/model_repo) — ONNX, ~66 МБ, качається один раз
у OUT_ROOT/_models. Запуск через onnxruntime (вже стоїть разом із faster-whisper). STFT/ISTFT — numpy.
Запасний варіант без моделі — центральний канал стерео + смуга голосу.
"""
import subprocess
import urllib.request

import numpy as np

from .core import OUT_ROOT, ffmpeg, ssl_ctx

MODEL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Voc_FT.onnx"
MODEL = OUT_ROOT / "_models" / "UVR-MDX-NET-Voc_FT.onnx"
SR, N_FFT, HOP, DIM_F, DIM_T, COMP = 44100, 7680, 1024, 3072, 256, 1.021


def _stereo(path, start, dur, sr=SR):
    out = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0, start):.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-ac", "2", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(out, np.float32).reshape(-1, 2).T.copy()


def ensure_model(job=None):
    if MODEL.exists() and MODEL.stat().st_size > 50e6: return MODEL
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    if job: job.log("vocal_model")
    tmp = MODEL.with_suffix(".part")
    with urllib.request.urlopen(urllib.request.Request(MODEL_URL, headers={"User-Agent": "StreamClipper"}), timeout=120, context=ssl_ctx()) as r, \
            open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    tmp.replace(MODEL)
    return MODEL


_WIN = np.hanning(N_FFT + 1)[:-1].astype(np.float32)  # періодичне вікно Ганна, як у torch.hann_window


def _stft(x):
    """x: (C, N) → (C, F, T) complex, center=True (reflect)."""
    p = N_FFT // 2
    x = np.pad(x, ((0, 0), (p, p)), mode="reflect")
    T = 1 + (x.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(T)[:, None]
    fr = x[:, idx] * _WIN
    return np.fft.rfft(fr, axis=-1).transpose(0, 2, 1)


def _istft(S, length):
    """(C, F, T) complex → (C, length)."""
    C, F, T = S.shape
    fr = np.fft.irfft(S.transpose(0, 2, 1), n=N_FFT, axis=-1).astype(np.float32) * _WIN
    n = N_FFT + HOP * (T - 1)
    out = np.zeros((C, n), np.float32); wsum = np.zeros(n, np.float32)
    for t in range(T):
        out[:, t * HOP:t * HOP + N_FFT] += fr[:, t]; wsum[t * HOP:t * HOP + N_FFT] += _WIN ** 2
    out /= np.maximum(wsum, 1e-8)
    p = N_FFT // 2
    return out[:, p:p + length]


def separate(path, start, dur, job=None):
    """-> вокал (mono float32, 16 кГц) для розпізнавання, або None."""
    import onnxruntime as ort
    mix = _stereo(path, start, dur)
    if mix.shape[1] < SR: return None
    sess = ort.InferenceSession(str(ensure_model(job)), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    n = mix.shape[1]; trim = N_FFT // 2
    chunk = HOP * (DIM_T - 1); gen = chunk - 2 * trim
    pad = gen - n % gen
    mp = np.concatenate([np.zeros((2, trim), np.float32), mix, np.zeros((2, pad + trim), np.float32)], 1)
    outs = []
    for i in range(0, n + pad, gen):
        if job is not None: job.check()
        w = mp[:, i:i + chunk]
        S = _stft(w)                                    # (2, F, T)
        spec = np.stack([S.real, S.imag], 1).reshape(4, S.shape[1], S.shape[2])[:, :DIM_F]
        y = sess.run(None, {name: spec[None].astype(np.float32)})[0][0]  # (4, DIM_F, T)
        full = np.zeros((4, N_FFT // 2 + 1, y.shape[2]), np.float32); full[:, :DIM_F] = y
        c = full.reshape(2, 2, full.shape[1], full.shape[2])
        wav = _istft(c[:, 0] + 1j * c[:, 1], chunk)
        outs.append(wav[:, trim:-trim])
    voc = np.concatenate(outs, 1)[:, :n] * COMP
    mono = voc.mean(0)
    return _resample16(mono)


def _resample16(x):
    """44.1 кГц → 16 кГц через ffmpeg (якісно, без scipy)."""
    p = subprocess.run([ffmpeg(), "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", "-ar", "16000", "-f", "f32le", "-"],
                       input=x.astype(np.float32).tobytes(), capture_output=True).stdout
    return np.frombuffer(p, np.float32).copy()


def center_vocals(path, start, dur):
    """Запасний варіант: середина стерео мінус бокові (вокал зазвичай по центру) + смуга 150–5000 Гц, 16 кГц."""
    out = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0, start):.3f}", "-t", f"{dur:.3f}", "-i", str(path),
                          "-af", "pan=mono|c0=0.5*c0+0.5*c1,highpass=f=150,lowpass=f=5000,dynaudnorm", "-ar", "16000", "-ac", "1",
                          "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(out, np.float32).copy()
