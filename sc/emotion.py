"""Емоція на обличчі: FER+ (ONNX Model Zoo, MIT, ~34 МБ, качається один раз у _models).
8 класів: нейтральне, радість, здивування, сум, злість, огида, страх, зневага. Для едіту важливо «наскільки виразне обличчя»:
сміх, крик, здивування — кращі кадри для дропу. Якщо модель недоступна — повертає None (працює запасна евристика)."""
import threading
import urllib.request

import numpy as np

from .core import OUT_ROOT
from .depth import _ssl

MODEL = OUT_ROOT / "_models" / "emotion-ferplus-8.onnx"
MODEL_URL = "https://media.githubusercontent.com/media/onnx/models/main/validated/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx"
_S = {}
_LOCK = threading.Lock()
# вага «виразності» кожного класу: нейтральне 0, радість/здивування — найцінніші для едіту
WEIGHTS = np.array([0.0, 1.0, 1.0, 0.35, 0.8, 0.5, 0.6, 0.3], np.float32)


def _session(job=None):
    with _LOCK:
        if "s" in _S: return _S["s"]
        try:
            if not (MODEL.exists() and MODEL.stat().st_size > 20e6):
                MODEL.parent.mkdir(parents=True, exist_ok=True)
                if job: job.log("emotion_model")
                tmp = MODEL.with_suffix(".part")
                with urllib.request.urlopen(urllib.request.Request(MODEL_URL, headers={"User-Agent": "StreamClipper"}), timeout=120,
                                            context=_ssl()) as r, open(tmp, "wb") as f:
                    while True:
                        b = r.read(1 << 20)
                        if not b: break
                        f.write(b)
                if tmp.stat().st_size < 20e6: raise RuntimeError("bad download")
                tmp.replace(MODEL)
            import onnxruntime as ort
            so = ort.SessionOptions(); so.intra_op_num_threads = 2
            _S["s"] = ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"])
        except Exception as e:
            print("emotion model:", e)
            _S["s"] = None
        return _S["s"]


def expressive(gray_face, job=None):
    """Сіре обличчя (будь-якого розміру) → виразність 0..1 або None (моделі нема)."""
    import cv2
    s = _session(job)
    if s is None or gray_face is None or not gray_face.size: return None
    x = cv2.resize(gray_face, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)[None, None]
    try:
        out = s.run(None, {s.get_inputs()[0].name: x})[0][0]
    except Exception:
        return None
    p = np.exp(out - out.max()); p /= p.sum()
    return float(np.clip((p * WEIGHTS).sum() * 1.3, 0, 1))
