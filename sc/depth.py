"""Карта глибини кадру (Depth Anything V2 Small, Apache-2.0, ONNX ~99 МБ — качається один раз у _models).
Потрібна для «3D-камери»: близьке й далеке рухаються з різною швидкістю (паралакс по шарах глибини)."""
import ssl
import threading
import urllib.request

import numpy as np

from .core import OUT_ROOT

MODEL = OUT_ROOT / "_models" / "depth_anything_v2_vits.onnx"
MODEL_URL = "https://github.com/fabio-sim/Depth-Anything-ONNX/releases/download/v2.0.0/depth_anything_v2_vits.onnx"
_S = {}
_LOCK = threading.Lock()


def _ssl():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def ensure_model(job=None):
    if MODEL.exists() and MODEL.stat().st_size > 50e6: return MODEL
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    if job: job.log("depth_model")
    tmp = MODEL.with_suffix(".part")
    with urllib.request.urlopen(urllib.request.Request(MODEL_URL, headers={"User-Agent": "StreamClipper"}), timeout=120,
                                context=_ssl()) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    tmp.replace(MODEL)
    return MODEL


def _session(job=None):
    with _LOCK:
        if "s" not in _S:
            import onnxruntime as ort
            ensure_model(job)
            so = ort.SessionOptions(); so.intra_op_num_threads = 4
            _S["s"] = ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"])
        return _S["s"]


def depth(img, job=None, size=518):
    """img BGR → глибина 0..1 (1 — найближче), розмір як у img (зменшений). None, якщо модель недоступна."""
    import cv2
    try:
        s = _session(job)
    except Exception as e:
        if job: job.log("vocal_fallback", f"depth: {str(e)[:60]}")
        _S["s"] = None
        return None
    if s is None: return None
    h, w = img.shape[:2]
    x = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)[..., ::-1].astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
    with _LOCK:
        d = s.run(None, {s.get_inputs()[0].name: x})[0][0]
    d = cv2.resize(d.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    return np.clip((d - lo) / (hi - lo + 1e-6), 0, 1)
