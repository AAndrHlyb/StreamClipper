"""Едіт під музику: склейки в біт, ефекти по кадрах і кінетичний текст (за людиною, навколо неї, з голови).

Схема: музика → біти/удари/енергія → план кадрів → кожен кадр окремо через ffmpeg (кроп по обличчю, швидкість, грейд, зерно)
→ покадрова обробка в Python (наїзди, тряска, спалах, RGB-зсув, змаз, маска людини, текст) → ffmpeg-кодер + музика.
"""
import math, random, subprocess
from pathlib import Path

import numpy as np

from .core import APP_DIR, ffmpeg, run_proc, perf
from . import analysis as A
from .render import segment, _cv2
from .core import src_fps
from . import edit_fx as FX

W, H, FPS = 1080, 1920, 30
FONT_BIG = APP_DIR / "fonts" / "Anton-Regular.ttf"
FONT_BOLD = APP_DIR / "fonts" / "Montserrat-ExtraBold.ttf"
FONT_CYR = APP_DIR / "fonts" / "Oswald-Bold.ttf"  # Anton не має кирилиці — для неї Oswald Bold


def _font(font, txt):
    if font == FONT_BIG and any(ord(c) > 0x24F for c in txt): return FONT_CYR
    return font
SEG_MODEL = APP_DIR / "models" / "human_segmentation_pphumanseg_2023mar.onnx"
GRADES = {
    "warm": "eq=contrast=1.08:saturation=1.28:gamma=0.97,colorbalance=rs=.06:gs=.01:bs=-.06:rm=.04:bm=-.04",
    "cold": "eq=contrast=1.1:saturation=1.12,colorbalance=rs=-.05:bs=.07:rm=-.03:bm=.05",
    "punch": "eq=contrast=1.25:saturation=1.38:brightness=-0.02",
    "none": "",
}
ACCENTS = [(0, 229, 255), (60, 255, 110), (255, 90, 255), (60, 60, 255), (255, 200, 60)]  # BGR: жовтий, зелений, рожевий, червоний, блакитний


# ------------------------------------------------------------------ музика
def music_pcm(path, start, dur, sr=22050):
    out = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768.0


def music_dur(path):
    from .core import dur_of
    return dur_of(path) or 0.0


def analyze_music(path, start, dur):
    """-> dict(beats=[с], hits=[с], strength=[0..1 на біт], energy=[0..1 на біт], period=с, drop=с)."""
    sr, hop, n = 22050, 512, 1024
    y = music_pcm(path, start, dur + 1, sr)
    if len(y) < sr: raise RuntimeError("music_short")
    frames = 1 + (len(y) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(frames)[:, None]
    S = np.abs(np.fft.rfft(y[idx] * np.hanning(n).astype(np.float32), axis=1))
    L = np.log1p(10 * S)
    flux = np.concatenate([[0], np.maximum(0, np.diff(L, axis=0)).sum(1)])
    env = np.maximum(flux - np.convolve(flux, np.ones(16) / 16, "same"), 0)
    env = env / (env.std() + 1e-9)
    fps = sr / hop
    rms = np.sqrt((y[idx] ** 2).mean(1)); rms = rms / (rms.max() + 1e-9)
    # темп: автокореляція з перевагою ~120 BPM
    ac = np.correlate(env, env, "full")[len(env) - 1:]
    lags = np.arange(len(ac)); bpm = 60 * fps / np.maximum(lags, 1)
    wgt = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1) / 120) / 0.9) ** 2) * ((bpm >= 70) & (bpm <= 185))
    period = int(np.argmax(ac * wgt)) or int(fps / 2)
    # біти: динамічне програмування (Ellis)
    N = len(env); cum = env.copy(); back = -np.ones(N, int)
    for t in range(N):
        lo, hi = max(0, t - 2 * period), t - max(1, period // 2)
        if hi <= lo: continue
        prev = np.arange(lo, hi)
        sc = cum[prev] - 100.0 * np.log((t - prev) / period) ** 2
        k = int(np.argmax(sc)); cum[t] = env[t] + sc[k]; back[t] = prev[k]
    tail = np.arange(max(0, N - period), N)
    t = int(tail[np.argmax(cum[tail])]) if len(tail) else N - 1
    bt = []
    while t >= 0: bt.append(t); t = back[t]
    bt = np.array(sorted(bt))
    beats = bt / fps
    strength = np.array([env[max(0, b - 2):b + 3].max() for b in bt]); strength = strength / (strength.max() + 1e-9)
    energy = np.array([rms[max(0, b - period // 2):b + period // 2 + 1].mean() for b in bt]); energy = energy / (energy.max() + 1e-9)
    hits = [float(beats[i]) for i in range(len(bt)) if strength[i] >= np.percentile(strength, 72)]
    # дроп: найбільший стрибок енергії
    sm = np.convolve(rms, np.ones(int(fps)) / fps, "same"); jump = sm[int(fps):] - sm[:-int(fps)] if len(sm) > fps else np.zeros(1)
    drop = float((np.argmax(jump) + fps) / fps) if len(jump) else 0.0
    keep = beats < dur
    return {"beats": beats[keep].tolist(), "strength": strength[keep].tolist(), "energy": energy[keep].tolist(),
            "hits": [h for h in hits if h < dur], "period": period / fps, "drop": drop if drop < dur - 1 else 0.0}


def best_segment(path, L, total):
    """Найенергійніший відрізок треку довжини L (приспів / дроп), початок — на сильну долю."""
    sr, hop = 11025, 512
    y = music_pcm(path, 0, total, sr)
    if len(y) < sr * (L + 2): return 0.0
    fr = len(y) // hop
    rms = np.sqrt((y[:fr * hop].reshape(fr, hop) ** 2).mean(1))
    fps = sr / hop; win = int(L * fps)
    cs = np.concatenate([[0], np.cumsum(rms)]); en = (cs[win:] - cs[:-win]) / win
    # трохи цінуємо стрибок енергії на початку (вхід у приспів)
    lead = int(1.5 * fps); jump = np.zeros_like(en)
    for i in range(lead, len(en)): jump[i] = rms[i:i + lead].mean() - rms[i - lead:i].mean()
    sc = en / (en.max() + 1e-9) + 0.6 * np.maximum(jump, 0) / (np.abs(jump).max() + 1e-9)
    sc[: int(3 * fps)] *= 0.7  # не з самого початку треку
    st = float(np.argmax(sc) / fps)
    return max(0.0, min(st, total - L - 0.2))


def lyrics(path, start, dur, o, job):
    """Слова пісні з таймінгом (розпізнавання мовлення по треку)."""
    try:
        mdl = A.whisper(o, job)
        y = music_pcm(path, start, dur, 16000)
        segs, _ = mdl.transcribe(y, word_timestamps=True, beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                 language=o.get("lang") or None)
        out = []
        for s in segs:
            for w in (s.words or []):
                t = w.word.strip().strip(",.!?…\"«»")
                if t and (w.probability or 0) > 0.12 and w.end > w.start: out.append({"s": float(w.start), "e": float(w.end), "w": t})
        return out
    except Exception:
        return []


def text_events(m, o, L, rnd, words=None):
    """-> [{t0,t1,txt}] — 1–2 слова на подію, прив'язка до бітів."""
    beats = m["beats"]; ev = []
    snap = lambda t: min(beats, key=lambda b: abs(b - t)) if beats and min(abs(b - t) for b in beats) < 0.12 else t
    if words:
        i = 0
        while i < len(words):
            grp = [words[i]]
            if i + 1 < len(words) and len(words[i]["w"]) + len(words[i + 1]["w"]) <= 9 and words[i + 1]["s"] - words[i]["e"] < 0.15:
                grp.append(words[i + 1])
            t0 = snap(grp[0]["s"]); ev.append({"t0": t0, "t1": 0, "txt": " ".join(g["w"] for g in grp)}); i += len(grp)
    else:
        toks = [t for t in (o.get("edit_text") or "").replace("\n", " ").split(" ") if t.strip()]
        if not toks: return []
        step = 1 if m["period"] > 0.42 else 2
        bs = [b for b in beats if b > 0.25][::step]
        for k, b in enumerate(bs):
            ev.append({"t0": b, "t1": 0, "txt": toks[k % len(toks)]})
    ev = [e for e in ev if e["t0"] < L - 0.2]
    for k, e in enumerate(ev):
        nxt = ev[k + 1]["t0"] if k + 1 < len(ev) else L
        e["t1"] = min(nxt, e["t0"] + 1.3, L)
        if e["t1"] - e["t0"] < 0.18: e["t1"] = e["t0"] + 0.18
    return ev


# ------------------------------------------------------------------ кадр із джерела
def _sample(src, t, pre, job):
    """Маленький кадр (BGR) для пошуку обличчя."""
    cv2 = _cv2()
    out = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", str(src), "-frames:v", "1",
                          "-vf", (pre + "," if pre else "") + "scale=360:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR) if out else None


_CASC = {}
_SRCH = {}


def _src_wh(src):
    """Розмір відео джерела (кеш)."""
    if src not in _SRCH:
        import re as _re
        r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(src)], capture_output=True, text=True, errors="replace").stderr
        m = _re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r)
        _SRCH[src] = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return _SRCH[src]


def _src_h(src): return _src_wh(src)[1]


def find_logos(src, dur, job=None):
    """Статичні написи/логотипи джерела (водяні знаки): краї, що стоять на місці в усіх кадрах. -> [(x,y,w,h) частки]."""
    cv2 = _cv2(); edges = []
    for k in range(14):
        t = dur * (0.04 + 0.92 * k / 13)
        out = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=640:-2",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_GRAYSCALE) if out else None
        if img is not None: edges.append(cv2.Canny(img, 40, 120) > 0)
    if len(edges) < 6: return []
    h, w = edges[0].shape
    edges = [e for e in edges if e.shape == (h, w)]
    st = (np.mean(edges, 0) >= 0.5).astype(np.uint8) * 255  # частина кадрів — спалахи/плашки без знаку
    st[:3, :] = st[-3:, :] = 0; st[:, :3] = st[:, -3:] = 0
    st = cv2.dilate(st, np.ones((9, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(st)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 60 or bw * bh > 0.03 * w * h or bh > 0.15 * h or bw > 0.6 * w: continue
        boxes.append((max(0.0, (x - 4) / w), max(0.0, (y - 4) / h), min(1.0, (bw + 8) / w), min(1.0, (bh + 8) / h)))
    return boxes[:4]


def delogo_vf(src, boxes):
    """ffmpeg delogo для знайдених боксів у пікселях джерела."""
    sw, shh = _src_wh(src)
    if not boxes or not sw: return ""
    out = []
    for x, y, w, h in boxes:
        X, Y = max(1, int(x * sw)), max(1, int(y * shh)); Wd, Hd = int(w * sw), int(h * shh)
        Wd, Hd = min(Wd, sw - X - 2), min(Hd, shh - Y - 2)
        if Wd > 4 and Hd > 4: out.append(f"delogo=x={X}:y={Y}:w={Wd}:h={Hd}")
    return ",".join(out)


YUNET = APP_DIR / "models" / "face_detection_yunet_2023mar.onnx"


def _yunet(img):
    """YuNet (OpenCV Zoo) — набагато точніший за Haar: профіль, окуляри, погане світло."""
    cv2 = _cv2()
    if "y" not in _CASC:
        try: _CASC["y"] = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 50) if YUNET.exists() else None
        except Exception: _CASC["y"] = None
    det = _CASC["y"]
    if det is None: return None
    h, w = img.shape[:2]; det.setInputSize((w, h))
    _, f = det.detect(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    return [] if f is None else [tuple(int(v) for v in r[:4]) for r in f]


def _faces(img):
    y = _yunet(img)
    if y is not None: return y
    return _faces_haar(img)


def _faces_haar(img):
    cv2 = _cv2()
    if "f" not in _CASC: _CASC["f"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    return list(_CASC["f"].detectMultiScale(g, 1.1, 5, minSize=(int(img.shape[1] * 0.06),) * 2))


# ------------------------------------------------------------------ маска людини
class Segmenter:
    def __init__(self):
        cv2 = _cv2(); self.cv2 = cv2
        self.net = cv2.dnn.readNet(str(SEG_MODEL)) if SEG_MODEL.exists() else None
        self.prev = None

    def reset(self): self.prev = None

    def __call__(self, frame):
        """-> маска float32 (H/2, W/2) 0..1 або None, якщо людини немає."""
        if self.net is None: return None
        cv2 = self.cv2
        x = cv2.resize(frame, (192, 192), interpolation=cv2.INTER_AREA)
        x = (cv2.cvtColor(x, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0 - 0.5) / 0.5
        self.net.setInput(x.transpose(2, 0, 1)[None]); m = self.net.forward()[0, 1]
        if self.prev is not None: m = 0.55 * m + 0.45 * self.prev
        self.prev = m
        area = float((m > 0.5).mean())
        if area < 0.04 or area > 0.9 or float(m[m > 0.5].mean() if area else 0) < 0.75: return None
        m = cv2.resize(m, (W // 2, H // 2), interpolation=cv2.INTER_CUBIC)
        m = np.clip((m - 0.35) / 0.3, 0, 1)
        return cv2.GaussianBlur(m, (0, 0), 2.5)


def head_of(mask):
    """Верх голови й центр голови в координатах кадру."""
    if mask is None: return None
    ys, xs = np.nonzero(mask > 0.5)
    if not len(ys): return None
    top = ys.min(); band = xs[ys < top + mask.shape[0] * 0.08]
    cx = float(band.mean()) if len(band) else float(xs.mean())
    return {"x": cx * 2, "top": top * 2.0, "cy": (top + mask.shape[0] * 0.07) * 2.0,
            "x0": xs.min() * 2.0, "x1": xs.max() * 2.0, "bottom": ys.max() * 2.0}


# ------------------------------------------------------------------ текст
_SPR = {}


def sprite(txt, size, font, fill, stroke=0, stroke_fill=(0, 0, 0), outline_only=False):
    """RGBA-спрайт (numpy, BGR+A) з кешем."""
    font = _font(font, txt)
    key = (txt, size, str(font), fill, stroke, stroke_fill, outline_only)
    if key in _SPR: return _SPR[key]
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(str(font), size)
    bb = f.getbbox(txt, stroke_width=stroke)
    w, h = bb[2] - bb[0] + 8, bb[3] - bb[1] + 8
    im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
    rgb = (fill[2], fill[1], fill[0]); srgb = (stroke_fill[2], stroke_fill[1], stroke_fill[0])
    if outline_only:
        dr.text((4 - bb[0], 4 - bb[1]), txt, font=f, fill=(0, 0, 0, 0), stroke_width=max(3, stroke), stroke_fill=rgb + (255,))
        # прибрати заливку всередині: малюємо текст прозорим поверх
        mask = Image.new("L", im.size, 0); ImageDraw.Draw(mask).text((4 - bb[0], 4 - bb[1]), txt, font=f, fill=255)
        a = np.array(im); a[..., 3] = np.where(np.array(mask) > 128, 0, a[..., 3]); im = Image.fromarray(a)
    else:
        dr.text((4 - bb[0], 4 - bb[1]), txt, font=f, fill=rgb + (255,), stroke_width=stroke, stroke_fill=srgb + (255,))
    a = np.array(im)[..., [2, 1, 0, 3]].copy()
    if len(_SPR) > 400: _SPR.clear()
    _SPR[key] = a; return a


def fit_size(txt, font, width, lo=60, hi=520):
    from PIL import ImageFont
    font = _font(font, txt)
    s = hi
    while s > lo:
        bb = ImageFont.truetype(str(font), s).getbbox(txt)
        if bb[2] - bb[0] <= width: return s
        s = int(s * 0.9)
    return lo


def warp(spr, scale=1.0, angle=0.0):
    if abs(scale - 1) < 1e-3 and abs(angle) < 0.05: return spr
    cv2 = _cv2(); h, w = spr.shape[:2]
    nw, nh = int(w * scale * 1.3 + 4), int(h * scale * 1.3 + 4)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale); M[0, 2] += nw / 2 - w / 2; M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(spr, M, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))


def blend(frame, spr, cx, cy, mask=None, behind=False, alpha=1.0):
    h, w = spr.shape[:2]; x0, y0 = int(cx - w / 2), int(cy - h / 2)
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0: return
    s = spr[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    a = s[..., 3:4].astype(np.float32) / 255.0 * alpha
    if behind and mask is not None:
        cv2 = _cv2()
        mm = cv2.resize(mask[fy0 // 2:(fy1 + 1) // 2, fx0 // 2:(fx1 + 1) // 2], (fx1 - fx0, fy1 - fy0), interpolation=cv2.INTER_LINEAR)
        a = a * (1 - mm[..., None])
    reg = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    frame[fy0:fy1, fx0:fx1] = (reg * (1 - a) + s[..., :3].astype(np.float32) * a).astype(np.uint8)


def ease(x): x = min(1.0, max(0.0, x)); return 1 - (1 - x) ** 3


# ------------------------------------------------------------------ аналіз картинки джерела
def scan_video(src, dur, skip=(), job=None):
    """Ключові кадри відео → оцінка «цікавості» картинки: обличчя, різкість, колір, яскравість.
    Один швидкий прохід (декодуються лише ключові кадри). -> [{t, score, face}]"""
    import threading, re as _re
    cv2 = _cv2()
    w, h = 320, 180
    p = subprocess.Popen([ffmpeg(), "-v", "info", "-hide_banner", "-skip_frame", "nokey", "-i", str(src), "-an", "-vf", f"scale={w}:{h},showinfo",
                          "-vsync", "vfr", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    times = []

    def rd():
        for line in p.stderr:
            m = _re.search(rb"pts_time:([\d.]+)", line)
            if m: times.append(float(m.group(1)))
    th = threading.Thread(target=rd, daemon=True); th.start()
    feats, k = [], 0
    while True:
        buf = p.stdout.read(w * h * 3)
        if len(buf) < w * h * 3: break
        if job is not None: job.check()
        img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        fs = _faces_small(g, img)
        face = max((f[2] * f[3] for f in fs), default=0) / (w * h)
        sharp = float(cv2.Laplacian(g, cv2.CV_32F).var())
        sat = float(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1].mean())
        bright = float(g.mean())
        feats.append((k, face, sharp, sat, bright)); k += 1
    p.wait(); th.join(timeout=2)
    if not feats: return []
    sh = np.log1p([f[2] for f in feats]); sh = (sh - sh.mean()) / (sh.std() + 1e-6)
    sa = np.array([f[3] for f in feats]); sa = (sa - sa.mean()) / (sa.std() + 1e-6)
    out = []
    for i, (k, face, _, _, bright) in enumerate(feats):
        t = times[k] if k < len(times) else k * 2.0
        if t < dur * 0.02 or t > dur * 0.985: continue
        if any(a - 1 <= t <= b + 1 for a, b in skip): continue  # реклама
        sc = 3.0 * min(1.0, face * 25) + 0.7 * sh[i] + 0.35 * sa[i] - (2.5 if bright < 38 else 0) - (1.0 if bright > 235 else 0)
        out.append({"t": t, "score": float(sc), "face": face > 0})
    return out


def _faces_small(g, img=None):
    cv2 = _cv2()
    if img is not None:
        y = _yunet(img)
        if y is not None: return y
    if "f" not in _CASC: _CASC["f"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return list(_CASC["f"].detectMultiScale(cv2.equalizeHist(g), 1.1, 4, minSize=(14, 14)))


# ------------------------------------------------------------------ план кадрів
def plan_shots(m, L, pool, rnd, video_pool=None, style=None):
    """Склейки в біт за драматургією: вступ (довші кадри) → розгін → кульмінація на дропі (кожна доля) → фінал.
    Переходи: 2–3 фірмові на весь ролик; спалах — лише на найсильніших піках."""
    beats = [b for b in m["beats"] if 0.05 < b < L - 0.15]
    drop = m["drop"] or (L * 0.45)
    resolve = L - 1.2
    cuts = [0.0]
    for b in beats:
        i = m["beats"].index(b); e = m["energy"][i]
        if b >= resolve: break
        if b < min(drop, L * 0.25): need = 0.9          # вступ: дихаємо
        elif b < drop: need = 0.55 if e < 0.7 else 0.42  # розгін
        else: need = 0.27 if e > 0.55 else 0.45          # кульмінація
        if b - cuts[-1] >= need - 0.03 or b - cuts[-1] > 1.6: cuts.append(b)
    cuts.append(L)
    n = len(cuts) - 1
    style = style or FX.STYLES["hype"]
    tr = style["trans"][:]; rnd.shuffle(tr); sig_a, sig_b = tr[0], (tr[1] if len(tr) > 1 else tr[0])  # 2 фірмові переходи на ролик
    max_flash = style.get("flash", 3)
    strengths = m["strength"]; thr = sorted(strengths)[int(len(strengths) * 0.85)] if strengths else 1
    # вибір моментів: найкращі з відстанню між ними → у хронологічному порядку (історія), найсильніший — на дроп
    picks = []
    if video_pool:
        for c in sorted(video_pool, key=lambda x: -x["score"]):
            if all(abs(c["t"] - p["t"]) > 3.0 for p in picks): picks.append(c)
            if len(picks) >= n: break
        while len(picks) < n: picks.append(rnd.choice(video_pool))
        best = max(picks, key=lambda x: x["score"])
        picks = sorted([p for p in picks if p is not best], key=lambda x: x["t"])
        di = min(range(n), key=lambda i: abs(cuts[i] - drop))
        picks.insert(min(di, len(picks)), best)
        while len(picks) < n: picks.append(dict(rnd.choice(video_pool)))  # мало кадрів у джерелі — повтори в кінці
    flashes = 0; shots = []
    for i in range(n):
        a, b = cuts[i], cuts[i + 1]; d = b - a
        bi = min(range(len(m["beats"])), key=lambda k: abs(m["beats"][k] - a)) if m["beats"] else 0
        st = strengths[bi] if strengths else 0.5
        is_drop = abs(a - drop) < 0.2 or (i > 0 and cuts[i - 1] < drop <= a)
        before_drop = i + 1 < n and cuts[i + 1] <= drop < cuts[i + 1] + 0.2
        # швидкість: перед дропом — сповільнення, що «приземляється» на удар; у кульмінації — інколи прискорення
        if before_drop and d >= 0.4: speed, ramp = 1.6, "in"
        elif is_drop: speed, ramp = 1.0, "none"
        elif a > drop and d >= 0.5 and rnd.random() < 0.35: speed, ramp = 1.7, rnd.choice(["in", "out"])
        else: speed, ramp = (0.85 if a < drop else 1.15), "none"
        if i == 0: fx = "none"
        elif is_drop and flashes < max_flash: fx, flashes = "flash", flashes + 1
        elif st >= thr and flashes < max_flash and a > drop: fx, flashes = "flash", flashes + 1
        elif st >= thr * 0.8: fx = sig_a
        elif i % 4 == 0: fx = sig_b
        else: fx = "cut"
        prev_look = shots[-1]["look"] if shots else None
        look = rnd.choice(style["looks"]) if (rnd.random() < 0.5 or (is_drop and a > 0)) else None
        if look == prev_look: look = None
        cut_in = (d >= 0.55 and i > 0 and fx in ("cut", "none", sig_b) and rnd.random() < style.get("cutout", 0.3))
        if video_pool:
            k, t = -1, max(0.0, picks[i]["t"] - 0.15)
        else:
            ranked = sorted(range(len(pool)), key=lambda j: -pool[j].get("final", 0))
            k = ranked[(0 if is_drop else i) % len(ranked)]; t = pool[k]["peak"] + rnd.uniform(-3, 2)
        shots.append({"a": a, "b": b, "k": k, "t": t, "speed": speed, "ramp": ramp, "hard": st >= thr, "drop": is_drop,
                      "fx": fx, "resolve": a >= resolve - 0.01, "look": look, "cutin": cut_in, "dir": rnd.choice([-1, 1])})
    return shots


# ------------------------------------------------------------------ кадр із джерела
def render_shot(src, t, sh, o, job, out, cam=None, logos=None):
    """Кадр плану → 1080×1920. Повертає кількість ВИХІДНИХ кадрів n і кількість кадрів у файлі nsrc (для рампи швидкості)."""
    nfr = max(2, int(round((sh["b"] - sh["a"]) * FPS)))
    span = (sh["b"] - sh["a"]) * sh["speed"]
    nsrc = max(nfr, int(round(span * FPS)))
    pre = f"crop=iw*{cam[2]:.4f}:ih*{cam[3]:.4f}:iw*{cam[0]:.4f}:ih*{cam[1]:.4f}" if cam else ""
    fx, face = 0.5, False
    img = _sample(src, t + span / 2, pre, job)
    if img is not None:
        hh, ww = img.shape[:2]; fs = _faces(img)
        if fs:
            face = True; x, y, fw, fh = max(fs, key=lambda f: f[2] * f[3])
            ow = min(1.0, hh * 9 / 16 / ww)
            if ow < 1: fx = min(1, max(0, ((x + fw / 2) / ww - ow / 2) / (1 - ow)))
    grade = GRADES.get(o.get("edit_grade") or "warm", GRADES["warm"])
    sfps = src_fps(src) or 30
    sh0 = _src_h(src); up = (H / sh0 / (cam[3] if cam else 1.0)) if sh0 else 1.0
    slow = sh["speed"] < 1 or sh["ramp"] != "none"
    vf = ",".join(x for x in [
        delogo_vf(src, logos), pre, f"crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)':x='(iw-ow)*{fx:.3f}':y='(ih-oh)*0.45'",
        "hqdn3d=1.2:1.2:2.5:2.5" if up > 1.3 else "",
        f"scale={W}:{H}:flags=lanczos+accurate_rnd+full_chroma_int", "setsar=1",
        f"cas={min(0.85, 0.35 + 0.25 * max(0.0, up - 1)):.2f}",
        f"setpts=(PTS-STARTPTS)/{sh['speed'] if sh['ramp'] == 'none' else 1}",
        f"minterpolate=fps={FPS}:mi_mode=blend" if (slow and sfps < 45 and sh["speed"] < 1) else f"fps={FPS}",
        grade, "noise=alls=2:allf=t", "vignette=PI/5", "format=yuv420p"] if x)
    nget = nsrc if sh["ramp"] != "none" else nfr
    run_proc([ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0, t):.3f}", "-t", f"{span + 0.6:.3f}", "-i", str(src),
              "-vf", vf, "-frames:v", str(nget), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "8", str(out)], job)
    return {"file": str(out), "n": nfr, "nsrc": nget, "face": face}


def ramp_index(j, n, nsrc, kind):
    """Велосіті: швидко → повільно (in) або повільно → швидко (out)."""
    x = j / max(1, n - 1)
    if kind == "in": y = 1 - (1 - x) ** 2.6
    elif kind == "out": y = x ** 2.6
    else: y = x
    return min(nsrc - 1, int(round(y * (nsrc - 1))))


# ------------------------------------------------------------------ текст: події зі слів стрімера (якщо пісня без слів)
def speech_events(shots, meta, o, job, L, rnd):
    ev = []
    audio = meta.get("audio") or meta.get("video")
    if not audio: return ev
    off = meta.get("audio_offset", 0.0)
    for sh in shots:
        if sh["b"] - sh["a"] < 0.3 or rnd.random() < 0.25: continue
        try:
            ws = A.words_of(audio, sh["t"], 2.5, o, job, src_offset=off) if meta.get("video") else []
        except Exception:
            ws = []
        good = [w["w"].strip(",.!?…\"«»") for w in ws if len(w["w"].strip(",.!?…\"«»")) >= 3]
        if not good: continue
        txt = " ".join(sorted(good, key=len, reverse=True)[:1 if rnd.random() < 0.6 else 2])
        ev.append({"t0": sh["a"], "t1": min(L, sh["b"] + (0.2 if sh["b"] - sh["a"] < 0.6 else 0)), "txt": txt})
    return ev


# ------------------------------------------------------------------ текст: стилі
def glow(spr, color, r=14):
    cv2 = _cv2(); a = spr[..., 3]
    pad = r * 2
    big = np.zeros((a.shape[0] + 2 * pad, a.shape[1] + 2 * pad), np.uint8); big[pad:-pad, pad:-pad] = a
    g = cv2.GaussianBlur(big, (0, 0), r)
    out = np.zeros(big.shape + (4,), np.uint8); out[..., :3] = color; out[..., 3] = np.clip(g.astype(np.int32) * 2, 0, 255)
    return out


def tint(spr, color):
    o = spr.copy(); o[..., :3] = color; return o


def back_ease(x, s=1.9):
    x = min(1.0, max(0.0, x)) - 1; return 1 + x * x * ((s + 1) * x + s)


def draw_text(frame, ev, t, mask, head, rc, beat_pulse):
    st = ev["style"]; txt = ev["txt"].upper()
    dt = t - ev["t0"]; p = dt / max(0.05, ev["t1"] - ev["t0"])
    k = back_ease(dt / 0.22); k0 = min(1.0, dt / 0.06)
    fade = max(0.0, min(1.0, (ev["t1"] - t) / 0.07, k0 + 0.1))
    if fade <= 0: return
    acc, white = ev["color"], (246, 246, 246)
    person = mask is not None and head is not None
    hx = head["x"] if person else W / 2
    cy = (head["cy"] + H * 0.10) if person else H * 0.42
    pulse = 1 + 0.06 * beat_pulse
    if st == "behind":
        sz = fit_size(txt, FONT_BIG, W * 0.96, hi=560); spr = sprite(txt, sz, FONT_BIG, ev["fill"])
        s = (1.35 - 0.35 * k) * pulse
        blend(frame, warp(glow(spr, acc, 18), s), W / 2, cy, mask, person, 0.55 * fade)
        blend(frame, warp(spr, s), W / 2, cy + 40 * (1 - min(1, k)), mask, person, fade)
    elif st == "stack":
        sz = fit_size(txt, FONT_BIG, W * 0.9, hi=380)
        sol, out = sprite(txt, sz, FONT_BIG, acc), sprite(txt, sz, FONT_BIG, white, stroke=4, outline_only=True)
        gap = sol.shape[0] * 0.84; dx = 90 * (p - 0.5)
        for n, (spr, ox) in enumerate([(out, -dx), (sol, 0), (out, dx), (out, -dx * 0.5)][:3]):
            yy = cy + (n - 1) * gap
            blend(frame, warp(spr, pulse if n == 1 else 1), W / 2 + ox, yy + 60 * (1 - min(1, k)) * (n - 1), mask, person, fade * (1 if n == 1 else min(1, k)))
    elif st == "split":  # верхня половина з одного боку, нижня — з іншого
        sz = fit_size(txt, FONT_BIG, W * 0.94, hi=520); spr = sprite(txt, sz, FONT_BIG, white)
        hh = spr.shape[0] // 2; e = ease(dt / 0.25)
        blend(frame, spr[:hh], W / 2 - (1 - e) * W, cy - hh / 2, mask, person, fade)
        blend(frame, tint(spr[hh:], acc), W / 2 + (1 - e) * W, cy + hh / 2, mask, person, fade)
    elif st == "wipe":  # контур, заливка біжить зліва направо
        sz = fit_size(txt, FONT_BIG, W * 0.94, hi=520)
        out, sol = sprite(txt, sz, FONT_BIG, white, stroke=5, outline_only=True), sprite(txt, sz, FONT_BIG, acc)
        cut = int(sol.shape[1] * min(1.0, p * 1.4)); part = sol.copy(); part[:, cut:, 3] = 0
        blend(frame, warp(out, pulse), W / 2, cy, mask, person, fade)
        blend(frame, warp(part, pulse), W / 2, cy, mask, person, fade)
    elif st == "cascade":  # літери падають по черзі з відскоком
        sz = fit_size(txt, FONT_BIG, W * 0.9, hi=300)
        from PIL import ImageFont
        f = ImageFont.truetype(str(_font(FONT_BIG, txt)), sz); total = f.getlength(txt); x = W / 2 - total / 2
        for i, ch in enumerate(txt):
            cw = f.getlength(ch)
            if ch != " ":
                q = back_ease((dt - i * 0.035) / 0.2)
                if q > 0:
                    col = acc if i % 2 else white
                    blend(frame, warp(sprite(ch, sz, FONT_BIG, col, stroke=3), max(0.05, q)), x + cw / 2, H * 0.30 - 160 * (1 - q), None, False, fade)
            x += cw
    elif st == "pop":
        spr = sprite(txt, 130, FONT_BOLD, acc, stroke=10)
        y0 = max(H * 0.17, head["top"] - 30) if person else H * 0.3
        ang = rc.setdefault(id(ev), random.uniform(-14, 14))
        pos = (hx + ang * 6 * p, y0 - 190 * ease(p * 1.2))
        blend(frame, warp(tint(spr, (60, 60, 255)), 0.5 + 0.7 * min(1, k), ang), pos[0] - 8, pos[1], None, False, 0.5 * fade)
        blend(frame, warp(spr, 0.5 + 0.7 * min(1, k), ang), pos[0], pos[1], None, False, fade)
    elif st == "orbit":
        chars = list(txt); sz = 132
        hw = ((head["x1"] - head["x0"]) / 2) if person else W * 0.25
        rx, ry = min(W * 0.44, max(W * 0.24, hw * 0.8)), 70
        ox, oy = (hx, head["cy"]) if person else (W / 2, H * 0.4)
        step = min(0.36, 2 * math.pi / (len(chars) + 1))
        base = rc.setdefault(id(ev), random.uniform(0, 6.28)) + dt * 1.7
        for i, ch in enumerate(chars):
            if ch == " ": continue
            th = base - i * step; front = math.sin(th) > 0
            spr = sprite(ch, sz, FONT_BIG, acc if front else (215, 215, 215), stroke=3 if front else 0)
            blend(frame, warp(spr, 0.7 + 0.3 * (math.sin(th) + 1) / 2), ox + rx * math.cos(th), oy + ry * math.sin(th), mask,
                  person and not front, fade * min(1, k))
    elif st == "side":
        sz = fit_size(txt, FONT_BIG, H * 0.66, hi=320)
        spr = warp(sprite(txt, sz, FONT_BIG, white, stroke=3, stroke_fill=acc), 1.0, 90)
        side = rc.setdefault(id(ev), random.choice([-1, 1]))
        x = (head["x0"] - 30 if side < 0 else head["x1"] + 30) if person else (W * 0.18 if side < 0 else W * 0.82)
        x = min(W - spr.shape[1] * 0.4, max(spr.shape[1] * 0.4, x))
        blend(frame, spr, x, H * 0.48 - 220 * (1 - min(1, k)), mask, person, fade)
    elif st == "slam":  # слово «падає» на екран з величезного розміру й приземляється з поштовхом
        sz = fit_size(txt, FONT_BIG, W * 0.92, hi=480); spr = sprite(txt, sz, FONT_BIG, ev["fill"], stroke=4, stroke_fill=(10, 10, 10))
        q = min(1.0, dt / 0.16); sc = 2.4 - 1.4 * (1 - (1 - q) ** 4)
        land = max(0.0, dt - 0.16); shake = 14 * math.exp(-land / 0.06) if dt >= 0.16 else 0
        jx = rc.setdefault(("sx", id(ev)), random.choice([-1, 1])) * shake
        blend(frame, warp(glow(spr, acc, 16), sc * pulse), W / 2 + jx, cy, mask, person, 0.5 * fade * q)
        blend(frame, warp(spr, sc * pulse), W / 2 + jx, cy, mask, person, fade * min(1.0, q * 1.5))
    elif st == "waterfall":  # слова піднімаються знизу по черзі (stagger ≤ 0.4 с)
        words = txt.split()
        sz = fit_size(max(words, key=len), FONT_BIG, W * 0.86, hi=300)
        lh = sz * 0.95; y0 = cy - (len(words) - 1) * lh / 2
        for i, wd in enumerate(words):
            q = (dt - i * min(0.12, 0.4 / max(1, len(words)))) / 0.3
            if q <= 0: continue
            e = 1 - (1 - min(1.0, q)) ** 4
            spr = sprite(wd, sz, FONT_BIG, acc if i % 2 else white)
            blend(frame, spr, W / 2, y0 + i * lh + 120 * (1 - e), mask, person, fade * min(1.0, q * 2))
    else:  # rgb — велике слово з кольоровими «привидами»
        sz = fit_size(txt, FONT_BIG, W * 0.85, hi=420); spr = sprite(txt, sz, FONT_BIG, white)
        jit = 14 * (1 - min(1, dt / 0.3)) + 6 * beat_pulse
        blend(frame, warp(tint(spr, (255, 220, 0)), pulse), W / 2 + jit, H * 0.36, None, False, 0.6 * fade)
        blend(frame, warp(tint(spr, (60, 40, 255)), pulse), W / 2 - jit, H * 0.36, None, False, 0.6 * fade)
        blend(frame, warp(spr, pulse), W / 2, H * 0.36, None, False, fade)


def style_events(ev, rnd, behind_ok, prefer=None):
    pool = (["behind", "behind", "slam", "slam", "stack", "orbit", "orbit", "pop", "split", "wipe", "side", "cascade", "rgb"] if behind_ok
            else ["slam", "slam", "stack", "split", "wipe", "cascade", "rgb", "pop", "behind"])
    last = []
    for k, e in enumerate(ev):
        src = prefer if (prefer and rnd.random() < 0.65) else pool
        if not behind_ok: src = [x for x in src if x not in ("orbit", "side")] or pool
        opts = [x for x in src if x not in last[-2:]] or src
        s = rnd.choice(opts)
        if s in ("orbit", "cascade") and len(e["txt"]) > 9: s = "behind"
        if len(e["txt"].split()) >= 2 and rnd.random() < 0.5: s = "waterfall"
        e["style"] = s; last.append(s)
        e["color"] = ACCENTS[(k * 2 + rnd.randrange(2)) % len(ACCENTS)]
        e["fill"] = (246, 246, 246) if rnd.random() < 0.65 else e["color"]
    return ev


# ------------------------------------------------------------------ ефекти кадру
def zoom_frame(fr, z, ang=0.0, dx=0.0, dy=0.0, cx=None, cy=None):
    cv2 = _cv2()
    M = cv2.getRotationMatrix2D((cx or W / 2, cy or H * 0.45), ang, z); M[0, 2] += dx; M[1, 2] += dy
    return cv2.warpAffine(fr, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def zoom_blur(fr, strength):
    acc = fr.astype(np.float32)
    for i in range(1, 4):
        acc += zoom_frame(fr, 1 + strength * i / 3).astype(np.float32)
    return (acc / 4).astype(np.uint8)


def glitch(fr, rnd, amt):
    cv2 = _cv2(); out = fr.copy()
    for _ in range(int(6 * amt) + 2):
        y = rnd.randrange(0, H - 40); h = rnd.randrange(20, 160); s = rnd.randrange(-int(90 * amt) - 1, int(90 * amt) + 2)
        out[y:y + h] = np.roll(out[y:y + h], s, axis=1)
    b, g, r = cv2.split(out); d = int(18 * amt) + 1
    return cv2.merge([np.roll(b, d, 1), g, np.roll(r, -d, 1)])


def leak_layer(rnd):
    """Кольоровий світловий відблиск (light leak) — один на кліп, рухається."""
    cv2 = _cv2(); lw, lh = W // 4, H // 4
    yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
    cx, cy = rnd.uniform(0, lw), rnd.uniform(0, lh * 0.5); r = rnd.uniform(lw * 0.5, lw)
    m = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
    col = rnd.choice([(40, 120, 255), (60, 60, 255), (200, 80, 255), (30, 200, 255)])
    img = np.zeros((lh, lw, 3), np.float32)
    for c in range(3): img[..., c] = m * col[c]
    return cv2.resize(img, (W, H), interpolation=cv2.INTER_LINEAR)


# ------------------------------------------------------------------ головна функція
def make_edit(d, meta, pool, o, job, seed=0, name="edit_9x16.mp4"):
    import json as _json
    cv2 = _cv2()
    d = Path(d); wk = d / "out" / "_e"; wk.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed or 1); random.seed(seed or 1)
    music = o.get("music"); mlen = music_dur(music) if music else 0
    want = max(5.0, float(o.get("edit_sec") or 15))
    if o.get("music_start") in (None, "", "auto"):  # авто: найсильніший шматок треку (приспів / дроп)
        start = best_segment(music, min(want, max(5.0, mlen - 0.5)), mlen) if mlen > want + 2 else 0.0
        job.log("edit_music_start", round(start, 1))
    else:
        start = float(o.get("music_start") or 0)
    L = float(min(want, max(5.0, mlen - start - 0.2) if mlen else 60))
    o["_music_start"] = start
    job.step("edit_beats", 58)
    m = analyze_music(music, start, L)
    # ---- картинка джерела: найцікавіші кадри (обличчя, різкість, колір), без реклами
    vpool = None
    if meta.get("video"):
        cache = d / "vscan.json"
        if cache.exists():
            vpool = _json.loads(cache.read_text())
        else:
            job.step("edit_scan", 60)
            vpool = scan_video(meta["video"], meta.get("duration") or 0, skip=(meta.get("info") or {}).get("skip") or [], job=job)
            cache.write_text(_json.dumps(vpool))
        for v in vpool:  # моменти, де щось відбувається за звуком/чатом, — трохи вище
            if any(abs(v["t"] - c["peak"]) < 4 for c in pool if c.get("final", 0) > 0.5): v["score"] += 0.6
        if len(vpool) < 8: vpool = None
    sname = o.get("edit_style") if o.get("edit_style") in FX.STYLES else rnd.choice(list(FX.STYLES))
    style = FX.STYLES[sname]
    o = dict(o)
    if (o.get("edit_grade") or "auto") == "auto": o["edit_grade"] = rnd.choice(style["grade"])
    job.log("edit_style", sname)
    shots = plan_shots(m, L, pool, rnd, vpool, style)
    # ---- текст
    tm = o.get("edit_textmode") or "lyrics"
    ev = []
    if tm == "lyrics":
        job.step("edit_lyrics", 62); words = lyrics(music, start, L, o, job)
        if len(words) >= max(3, L / 4): ev = text_events(m, o, L, rnd, words)
        else: job.log("edit_nolyrics")
    elif tm == "custom":
        ev = text_events(m, o, L, rnd, None)
    if tm != "none" and len(ev) < L / 2.2:  # мало слів — додаємо фрази стрімера з цих кадрів
        extra = speech_events(shots, meta, o, job, L, rnd)
        ev = sorted(ev + [e for e in extra if all(not (x["t0"] - 0.1 < e["t0"] < x["t1"]) for x in ev)], key=lambda e: e["t0"])
        if len(ev) < L / 3 and ev:  # усе ще мало — повторюємо найкращі слова на сильних долях
            pool_w = [e["txt"] for e in ev]
            free = [s for s in shots if s["hard"] and all(not (x["t0"] - 0.05 < s["a"] < x["t1"]) for x in ev)]
            for s in free: ev.append({"t0": s["a"], "t1": min(L, s["b"]), "txt": rnd.choice(pool_w)})
            ev.sort(key=lambda e: e["t0"])
    if tm != "none" and not ev:  # останній запас: слова з назви відео в такт
        import re as _re
        title = (meta.get("info") or {}).get("title") or ""
        toks = [w for w in _re.findall(r"[\w'’]+", title) if len(w) >= 3][:8] or ([o.get("nick")] if o.get("nick") else [])
        if toks: ev = text_events(m, {"edit_text": " ".join(toks)}, L, rnd, None)
    for k, e in enumerate(ev):
        if k + 1 < len(ev): e["t1"] = min(e["t1"], ev[k + 1]["t0"])
    ev = [e for e in ev if e["t1"] - e["t0"] >= 0.15]
    job.log("edit_plan", len(shots), round(60 / max(0.2, m["period"])), len(ev))
    # ---- джерела кадрів
    srcs = {}

    def src_of(k):
        if k not in srcs:
            c = pool[k]; s0 = max(0.0, c["peak"] - 6)
            srcs[k] = (*segment(d, meta, s0, c["peak"] + 6, f"e{k}_{int(s0)}", job, pad=1), s0)
        return srcs[k]
    cam = o.get("cam") if o.get("layout") == "cam" else None
    logos = []
    if o.get("edit_delogo", True) and meta.get("video"):
        try:
            logos = find_logos(meta["video"], meta.get("duration") or 60)
            if logos: job.log("edit_delogo", len(logos))
        except Exception:
            logos = []
    job.step("edit_shots", 64)
    from .pipeline import _pmap
    done = [0]

    def one(i):
        sh = shots[i]
        if sh["k"] < 0: src, ft = meta["video"], sh["t"] - meta.get("video_offset", 0.0)
        else:
            src, off, s0 = src_of(sh["k"]); ft = max(0.0, off + sh["t"] - s0)
        r = render_shot(src, ft, sh, o, job, wk / f"s{i:03d}.mp4", cam=cam if (cam and i % 3 != 2) else None,
                        logos=logos if meta.get("video") else None)
        done[0] += 1; job.pct = 64 + 14 * done[0] / len(shots); job.sub = f"{done[0]}/{len(shots)}"
        return r
    parts = _pmap(one, list(range(len(shots))), max(1, perf()["render"] + 1))
    # ---------------- покадровий прохід
    job.step("edit_fx", 78)
    seg = Segmenter() if o.get("edit_behind", True) else None
    behind_ok = seg is not None and seg.net is not None
    ev = style_events(ev, rnd, behind_ok, style.get("text"))
    out = d / "out" / name
    eargs = ["-c:v", "libx264", "-preset", "medium", "-crf", "15", "-tune", "film", "-profile:v", "high", "-level", "4.2",
             "-maxrate", "30M", "-bufsize", "60M", "-pix_fmt", "yuv420p", "-x264-params", "aq-mode=3"]
    nfr_total = sum(p["n"] for p in parts); T = nfr_total / FPS
    cmd = [ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if music and not o.get("edit_mute"):
        cmd += ["-ss", f"{start:.3f}", "-t", f"{T:.3f}", "-i", str(music), "-map", "0:v", "-map", "1:a",
                "-af", f"afade=t=out:st={max(0, T - 0.6):.2f}:d=0.6", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += eargs + ["-movflags", "+faststart", str(out)]
    pe = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    beats, hits = m["beats"], m["hits"]
    sb = sorted(zip(m["strength"], m["beats"]), reverse=True); strong = {b for _, b in sb[:max(1, len(sb) // 5)]}
    nick = (o.get("nick") or "").strip().lstrip("@")
    wm = sprite(nick, 50, FONT_BOLD, (240, 240, 240), stroke=2, stroke_fill=(30, 30, 30)) if nick else None
    endc = sprite(nick.upper(), fit_size(nick.upper(), FONT_BIG, W * 0.7, hi=220), FONT_BIG, (245, 245, 245)) if nick else None
    rc, fi, prev = {}, 0, None
    try:
        for i, (sh, pr) in enumerate(zip(shots, parts)):
            job.check()
            pd = subprocess.Popen([ffmpeg(), "-v", "error", "-i", pr["file"], "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
            if seg: seg.reset()
            nxt = shots[i + 1] if i + 1 < len(shots) else None
            leak = leak_layer(rnd) if rnd.random() < 0.2 else None
            sst = {}; ev_acc = ACCENTS[i % len(ACCENTS)]
            cur_idx, cur = -1, None
            for j in range(pr["n"]):
                want = ramp_index(j, pr["n"], pr["nsrc"], sh["ramp"]) if sh["ramp"] != "none" else j
                while cur_idx < want:
                    buf = pd.stdout.read(W * H * 3)
                    if len(buf) < W * H * 3: break
                    cur = np.frombuffer(buf, np.uint8).reshape(H, W, 3); cur_idx += 1
                fr = cur.copy() if cur is not None else (prev.copy() if prev is not None else np.zeros((H, W, 3), np.uint8))
                t = fi / FPS
                # ---- рух камери: пульс на кожну долю, удар, нахил, тряска, легке погойдування
                lb = max([b for b in beats if b <= t + 1e-3], default=-9)
                lh = max([h for h in hits if h <= t + 1e-3], default=-9)
                bp = math.exp(-(t - lb) / 0.09) if t - lb < 0.4 else 0.0
                hp = math.exp(-(t - lh) / 0.12) if t - lh < 0.6 else 0.0
                z = 1 + 0.05 * bp + 0.15 * hp + (0.02 * j / max(1, pr["n"]) if not sh.get("resolve") else 0.06 * j / max(1, pr["n"]))
                kick = rc.setdefault(("k", lh), rnd.choice([-1, 1]))
                ang = 2.2 * kick * hp
                dx = dy = 0.0
                if lh in strong and t - lh < 0.2:
                    amp = 26 * math.exp(-(t - lh) / 0.07); dx, dy = rnd.uniform(-amp, amp), rnd.uniform(-amp, amp)
                # ---- переходи з узгодженою швидкістю: рух не зупиняється на склейці
                fx, nfx = sh["fx"], (nxt["fx"] if nxt else "cut")
                tail = pr["n"] - 1 - j  # кадрів до кінця шоту
                blur = 0.0; hblur = 0
                if nfx == "zoom" and tail < 4:           # вихід: розгін наїзду
                    q = (4 - tail) / 4; z *= 1 + 0.32 * q ** 2.2; blur = 0.10 * q
                if fx == "zoom" and j < 5:                # вхід: продовжує наїзд і гальмує
                    q = 1 - j / 5; z *= 1 - 0.2 * q ** 2.2; blur = max(blur, 0.08 * q)
                if nfx == "whip" and tail < 3:            # вихід: розгін вліво
                    q = (3 - tail) / 3; dx -= W * 0.22 * q ** 2.2; hblur = int(30 + 70 * q)
                if fx == "whip" and j < 4:                # вхід: справа, гальмує
                    q = 1 - j / 4; dx += W * 0.22 * q ** 2.2; hblur = max(hblur, int(30 + 70 * q))
                if fx == "flash" and j < 4: z *= 1 + 0.12 * (1 - j / 4)
                fr = zoom_frame(fr, z, ang, dx, dy)
                if blur > 0.01: fr = zoom_blur(fr, blur)
                if hblur: fr = cv2.blur(fr, (hblur | 1, 1))
                if (fx == "glitch" and j < 3) or (nfx == "glitch" and tail < 2): fr = glitch(fr, rnd, 0.9 if j < 3 else 0.6)
                if nfx == "pixel" and tail < 3: fr = FX.tr_pixel(fr, (3 - tail) / 3)
                if fx == "pixel" and j < 3: fr = FX.tr_pixel(fr, 1 - j / 3)
                if nfx == "spin" and tail < 3: fr = FX.tr_spin(fr, (3 - tail) / 3, 1)
                if fx == "spin" and j < 3: fr = FX.tr_spin(fr, 1 - j / 3, -1)
                if fx == "iris" and j < 6: fr = FX.tr_iris(prev, fr, (j + 1) / 6)
                if fx == "shutter" and j < 6: fr = FX.tr_shutter(prev, fr, (j + 1) / 6)
                if leak is not None:
                    a = 0.32 * (0.5 + 0.5 * math.sin(t * 3 + i))
                    fr = np.clip(fr.astype(np.float32) + leak * a, 0, 255).astype(np.uint8)
                # ---- людина і текст
                mask = seg(fr) if seg else None
                head = head_of(mask) if mask is not None else None
                lt = j / FPS; dshot = pr["n"] / FPS
                if sh.get("look"):
                    try: fr = FX.LOOKS[sh["look"]](fr, mask if head else None, head, lt, dshot, sst, ev_acc)
                    except Exception: pass
                if sh.get("cutin") and head is not None and lt < 0.5:
                    fr = FX.cutout_enter(fr, mask, lt, sst, sh.get("dir", 1))
                for e in ev:
                    if e["t0"] <= t < e["t1"]: draw_text(fr, e, t, mask if head else None, head, rc, bp)
                if wm is not None and t < T - 0.9: blend(fr, wm, W / 2, H * 0.80, None, False, 0.92)
                if fx == "flash" and j < 3:
                    a = (0.85, 0.45, 0.15)[j]; fr = cv2.addWeighted(fr, 1 - a, np.full_like(fr, 255), a, 0)
                if endc is not None and t >= T - 0.9:
                    q = (t - (T - 0.9)) / 0.9
                    fr = (fr * max(0.0, 1 - q * 2.2)).astype(np.uint8)
                    blend(fr, warp(endc, 1.12 - 0.12 * ease(q * 2)), W / 2, H / 2, None, False, min(1.0, q * 4))
                elif t > T - 0.4:
                    fr = (fr * max(0.0, (T - t) / 0.4)).astype(np.uint8)
                pe.stdin.write(fr.tobytes()); fi += 1; prev = fr
                if fi % 15 == 0: job.pct = 78 + 20 * fi / nfr_total; job.sub = f"{fi}/{nfr_total}"
            pd.stdout.close(); pd.wait()
        pe.stdin.close()
        if pe.wait() != 0: raise RuntimeError("ffmpeg: " + pe.stderr.read().decode("utf-8", "replace")[-300:])
    except BaseException:
        try: pe.kill()
        except Exception: pass
        raise
    for p in parts: Path(p["file"]).unlink(missing_ok=True)
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-ss", f"{min(1.5, T / 2):.2f}", "-i", str(out), "-frames:v", "1",
                    "-vf", "scale=360:-2", str(out.with_name(out.stem + "_preview.jpg"))], capture_output=True)
    return str(out), T
