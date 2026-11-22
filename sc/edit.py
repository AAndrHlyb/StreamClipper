"""Едіт під музику: склейки в біт, ефекти по кадрах і кінетичний текст (за людиною, навколо неї, з голови).

Схема: музика → біти/удари/енергія → план кадрів → кожен кадр окремо через ffmpeg (кроп по обличчю, швидкість, грейд, зерно)
→ покадрова обробка в Python (наїзди, тряска, спалах, RGB-зсув, змаз, маска людини, текст) → ffmpeg-кодер + музика.
"""
import math, random, subprocess
from pathlib import Path

import numpy as np

from .core import APP_DIR, ffmpeg, run_proc, perf
from . import analysis as A
from .render import encoder_args, segment, _cv2

W, H, FPS = 1080, 1920, 30
FONT_BIG = APP_DIR / "fonts" / "Anton-Regular.ttf"
FONT_BOLD = APP_DIR / "fonts" / "Montserrat-ExtraBold.ttf"
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
                if t and (w.probability or 0) > 0.25 and w.end > w.start: out.append({"s": float(w.start), "e": float(w.end), "w": t})
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


# ------------------------------------------------------------------ план кадрів
def plan_shots(m, L, pool, rnd):
    beats = [b for b in m["beats"] if 0.05 < b < L - 0.15]
    fast = m["period"] < 0.42
    cuts = [0.0]
    for i, b in enumerate(beats):
        e = m["energy"][m["beats"].index(b)] if b in m["beats"] else 0.5
        step_ok = (b - cuts[-1]) >= (0.3 if e > 0.55 else 0.55) and (not fast or (b - cuts[-1]) >= 0.42 or e > 0.85)
        if step_ok or b - cuts[-1] > 1.6: cuts.append(b)
    cuts.append(L)
    shots, used, last = [], [], None
    ranked = sorted(range(len(pool)), key=lambda k: -pool[k].get("final", 0))
    offs = [0.0, -1.2, 1.0, -2.4, 2.2, -3.6, 3.4]
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]; d = b - a
        hard = any(abs(h - a) < 0.06 for h in m["hits"])
        drop = m["drop"] and abs(a - m["drop"]) < max(0.5, d)
        speed = 0.5 if (drop or (d >= 0.9 and rnd.random() < 0.3)) else (1.3 if d < 0.4 and rnd.random() < 0.25 else 1.0)
        pick = None
        for k in (ranked[:6] if (hard or drop) else ranked):
            if k == last and len(pool) > 1: continue
            for off in offs:
                t = pool[k]["peak"] + off
                if any(u[0] == k and abs(u[1] - t) < 1.6 for u in used): continue
                if pool[k].get("lo") is not None and not (pool[k]["lo"] <= t and t + d * speed <= pool[k]["hi"]): continue
                pick = (k, t); break
            if pick: break
        if not pick:  # усе використано — беремо випадковий з повтором подалі
            k = rnd.choice(ranked); pick = (k, pool[k]["peak"] + rnd.uniform(-3, 3))
        used.append(pick); last = pick[0]
        shots.append({"a": a, "b": b, "k": pick[0], "t": max(0.0, pick[1]), "speed": speed, "flash": hard and rnd.random() < 0.6,
                      "whip": (not hard) and rnd.random() < 0.18, "rgb": hard and rnd.random() < 0.5})
    return shots


# ------------------------------------------------------------------ кадр із джерела
def _sample(src, t, pre, job):
    """Маленький кадр (BGR) для пошуку обличчя."""
    cv2 = _cv2()
    out = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", str(src), "-frames:v", "1",
                          "-vf", (pre + "," if pre else "") + "scale=360:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR) if out else None


_CASC = {}


def _faces(img):
    cv2 = _cv2()
    if "f" not in _CASC: _CASC["f"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    return list(_CASC["f"].detectMultiScale(g, 1.1, 5, minSize=(int(img.shape[1] * 0.06),) * 2))


def render_shot(src, t, sh, o, job, out, cam=None):
    """Один кадр плану → 1080×1920, точна кількість кадрів. t — час початку у файлі src."""
    nfr = max(2, int(round((sh["b"] - sh["a"]) * FPS)))
    pre = f"crop=iw*{cam[2]:.4f}:ih*{cam[3]:.4f}:iw*{cam[0]:.4f}:ih*{cam[1]:.4f}" if cam else ""
    fx, face = 0.5, False
    img = _sample(src, t + (sh["b"] - sh["a"]) * sh["speed"] / 2, pre, job)
    if img is not None:
        h, w = img.shape[:2]; fs = _faces(img)
        if fs:
            face = True; x, y, fw, fh = max(fs, key=lambda f: f[2] * f[3])
            ow = min(1.0, h * 9 / 16 / w)
            if ow < 1: fx = min(1, max(0, ((x + fw / 2) / w - ow / 2) / (1 - ow)))
    grade = GRADES.get(o.get("edit_grade") or "warm", GRADES["warm"])
    vf = ",".join(x for x in [pre, f"crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)':x='(iw-ow)*{fx:.3f}':y='(ih-oh)*0.45'",
                              f"scale={W}:{H}:flags=lanczos", "setsar=1", f"setpts=(PTS-STARTPTS)/{sh['speed']}",
                              f"minterpolate=fps={FPS}:mi_mode=blend" if sh["speed"] < 1 else f"fps={FPS}",
                              grade, "noise=alls=6:allf=t", "vignette=PI/5", "format=yuv420p"] if x)
    run_proc([ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0, t):.3f}", "-t", f"{(sh['b'] - sh['a']) * sh['speed'] + 0.5:.3f}", "-i", str(src),
              "-vf", vf, "-frames:v", str(nfr), "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", str(out)], job)
    return {"file": str(out), "n": nfr, "face": face}


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


def draw_text(frame, ev, t, mask, head, rnd_cache):
    """Малює одну подію тексту в кадр."""
    st = ev["style"]; txt = ev["txt"].upper() if ev.get("caps", True) else ev["txt"]
    p = (t - ev["t0"]) / max(0.05, ev["t1"] - ev["t0"]); k = ease((t - ev["t0"]) / 0.16)
    fade = min(1.0, (ev["t1"] - t) / 0.08, (t - ev["t0"]) / 0.04 + 0.2)
    if fade <= 0: return
    acc, white = ev["color"], (245, 245, 245)
    person = mask is not None and head is not None
    hx = head["x"] if person else W / 2
    if st == "behind":
        sz = fit_size(txt, FONT_BIG, W * 0.94); spr = sprite(txt, sz, FONT_BIG, ev.get("fill", white))
        y = (head["cy"] + H * 0.10) if person else H * 0.42
        blend(frame, warp(spr, 1.18 - 0.18 * k), W / 2, y + 30 * (1 - k), mask, person, fade)
    elif st == "stack":
        sz = fit_size(txt, FONT_BIG, W * 0.86, hi=360)
        sol, out = sprite(txt, sz, FONT_BIG, acc), sprite(txt, sz, FONT_BIG, white, stroke=4, outline_only=True)
        y = (head["cy"] + H * 0.08) if person else H * 0.40; gap = sol.shape[0] * 0.86; dx = 60 * (p - 0.5)
        blend(frame, out, W / 2 - dx, y - gap, mask, person, fade * k)
        blend(frame, sol, W / 2, y, mask, person, fade)
        blend(frame, out, W / 2 + dx, y + gap, mask, person, fade * k)
    elif st == "pop":
        spr = sprite(txt, 118, FONT_BOLD, acc, stroke=9)
        y0 = max(H * 0.17, head["top"] - 30) if person else H * 0.3
        ang = rnd_cache.setdefault(id(ev), random.uniform(-14, 14))
        blend(frame, warp(spr, 0.5 + 0.6 * k, ang * k), hx + ang * 6 * p, y0 - 170 * ease(p * 1.2), None, False, fade)
    elif st == "orbit":  # слово дугою облітає голову: задня половина ховається за людиною
        chars = list(txt)
        sz = 124; hw = ((head["x1"] - head["x0"]) / 2) if person else W * 0.25
        rx, ry = min(W * 0.42, max(W * 0.22, hw * 0.75)), 64
        cx, cy = (hx, head["cy"]) if person else (W / 2, H * 0.4)
        step = min(0.36, 2 * math.pi / (len(chars) + 1))
        base = rnd_cache.setdefault(id(ev), random.uniform(0, 6.28)) + (t - ev["t0"]) * 1.5
        for i, ch in enumerate(chars):
            if ch == " ": continue
            th = base - i * step
            x, y, front = cx + rx * math.cos(th), cy + ry * math.sin(th), math.sin(th) > 0
            spr = sprite(ch, sz, FONT_BIG, acc if front else (210, 210, 210), stroke=3 if front else 0)
            blend(frame, warp(spr, 0.72 + 0.28 * (math.sin(th) + 1) / 2), x, y, mask, person and not front, fade * k)
    elif st == "side":
        sz = fit_size(txt, FONT_BIG, H * 0.62, hi=300)
        spr = warp(sprite(txt, sz, FONT_BIG, white, stroke=3, stroke_fill=acc), 1.0, 90)
        side = rnd_cache.setdefault(id(ev), random.choice([-1, 1]))
        x = (head["x0"] - 40 if side < 0 else head["x1"] + 40) if person else (W * 0.18 if side < 0 else W * 0.82)
        x = min(W - spr.shape[1] * 0.35, max(spr.shape[1] * 0.35, x))
        blend(frame, spr, x, H * 0.48 - 120 * (1 - k), mask, person, fade)
    else:  # type — по літері внизу
        n = max(1, int(len(txt) * min(1.0, (t - ev["t0"]) / 0.25)))
        spr = sprite(txt[:n], 96, FONT_BOLD, white, stroke=8)
        blend(frame, spr, W / 2, H * 0.74, None, False, fade)
        bar = np.zeros((14, int(spr.shape[1] * 0.9 * k) + 1, 4), np.uint8); bar[..., :3] = acc; bar[..., 3] = 255
        blend(frame, bar, W / 2, H * 0.74 + spr.shape[0] / 2 + 10, None, False, fade)


def style_events(ev, rnd, behind_ok):
    styles = (["behind"] * 3 + ["stack"] * 2 + ["orbit"] * 2 + ["pop"] * 2 + ["side", "type"]) if behind_ok else ["pop", "stack", "type", "behind"]
    last = None
    for k, e in enumerate(ev):
        s = rnd.choice([x for x in styles if x != last] or styles)
        if s == "orbit" and len(e["txt"]) > 10: s = "behind"
        e["style"] = s; last = s
        e["color"] = ACCENTS[k % len(ACCENTS)] if rnd.random() < 0.7 else ACCENTS[0]
        e["fill"] = (245, 245, 245) if rnd.random() < 0.7 else e["color"]
    return ev


# ------------------------------------------------------------------ головна функція
def make_edit(d, meta, pool, o, job, seed=0, name="edit_9x16.mp4"):
    cv2 = _cv2()
    d = Path(d); wk = d / "out" / "_e"; wk.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed or 1); random.seed(seed or 1)
    music = o.get("music"); start = float(o.get("music_start") or 0)
    mlen = music_dur(music) if music else 0
    L = float(min(max(5.0, float(o.get("edit_sec") or 15)), max(5.0, mlen - start - 0.2) if mlen else 60))
    job.step("edit_beats", 60)
    m = analyze_music(music, start, L)
    words = None
    if (o.get("edit_textmode") or "lyrics") == "lyrics":
        job.step("edit_lyrics", 62); words = lyrics(music, start, L, o, job)
    ev = text_events(m, o, L, rnd, words) if (o.get("edit_textmode") or "lyrics") != "none" else []
    shots = plan_shots(m, L, pool, rnd)
    job.log("edit_plan", len(shots), round(60 / max(0.2, m["period"])), len(ev))
    # джерела моментів (для стрімів — шматки з запасом)
    srcs = {}

    def src_of(k):
        if k not in srcs:
            c = pool[k]; s0 = max(0.0, c["peak"] - 6)
            src, off = segment(d, meta, s0, c["peak"] + 6, f"e{k}_{int(s0)}", job, pad=1)
            srcs[k] = (src, off, s0)
        return srcs[k]
    cam = o.get("cam") if o.get("layout") == "cam" else None
    job.step("edit_shots", 64)
    from .pipeline import _pmap
    done = [0]

    def one(i):
        sh = shots[i]; src, off, s0 = src_of(sh["k"])
        # для стрімів з вебкою: чергуємо крупний план вебки і гру
        r = render_shot(src, max(0.0, off + sh["t"] - s0), sh, o, job, wk / f"s{i:03d}.mp4", cam=cam if (cam and i % 3 != 2) else None)
        done[0] += 1; job.pct = 64 + 14 * done[0] / len(shots); job.sub = f"{done[0]}/{len(shots)}"
        return r
    parts = _pmap(one, list(range(len(shots))), max(1, perf()["render"] + 1))
    # ---------------- покадровий прохід
    job.step("edit_fx", 78)
    seg = Segmenter() if o.get("edit_behind", True) else None
    behind_ok = seg is not None and seg.net is not None and any(p["face"] for p in parts)
    ev = style_events(ev, rnd, behind_ok)
    out = d / "out" / name
    eargs = encoder_args({**o, "gpu": False})[1]  # кодер у пайпі — без ризику GPU-збою посередині
    nfr_total = sum(p["n"] for p in parts)
    cmd = [ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if music and not o.get("edit_mute"):
        cmd += ["-ss", f"{start:.3f}", "-t", f"{nfr_total / FPS:.3f}", "-i", str(music), "-map", "0:v", "-map", "1:a",
                "-af", f"afade=t=out:st={max(0, nfr_total / FPS - 0.6):.2f}:d=0.6", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += eargs + ["-movflags", "+faststart", str(out)]
    pe = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    hits = m["hits"]
    sb = sorted(zip(m["strength"], m["beats"]), reverse=True); strong = {b for _, b in sb[:max(1, len(sb) // 6)]}
    nick = meta.get("uploader") if o.get("watermark") else None
    wm = sprite(nick, 40, FONT_BOLD, (235, 235, 235), stroke=3) if nick else None
    rc, fi, prev_last = {}, 0, None
    try:
        for i, (sh, pr) in enumerate(zip(shots, parts)):
            job.check()
            pd = subprocess.Popen([ffmpeg(), "-v", "error", "-i", pr["file"], "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
            if seg: seg.reset()
            nxt = shots[i + 1] if i + 1 < len(shots) else None
            for j in range(pr["n"]):
                buf = pd.stdout.read(W * H * 3)
                if len(buf) < W * H * 3:
                    fr = prev_last.copy() if prev_last is not None else np.zeros((H, W, 3), np.uint8)
                else:
                    fr = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
                t = fi / FPS
                # наїзд на ударі + повільний наїзд у кадрі
                lh = max([h for h in hits if h <= t + 1e-3], default=-9)
                z = 1 + 0.13 * math.exp(-(t - lh) / 0.11) if t - lh < 0.6 else 1.0
                z *= 1 + 0.035 * j / max(1, pr["n"])
                sx = sy = 0.0
                if t - lh < 0.18 and lh in strong:
                    amp = 22 * math.exp(-(t - lh) / 0.06); sx, sy = rnd.uniform(-amp, amp), rnd.uniform(-amp, amp)
                if z > 1.001 or sx or sy:
                    M = cv2.getRotationMatrix2D((W / 2, H * 0.45), 0, z); M[0, 2] += sx; M[1, 2] += sy
                    fr = cv2.warpAffine(fr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
                # змаз-перехід (whip)
                if (sh["whip"] and j < 3) or (nxt and nxt["whip"] and j >= pr["n"] - 3):
                    fr = cv2.blur(fr, (61, 1))
                # RGB-зсув на сильних ударах
                if sh["rgb"] and j < 5:
                    dx = int(16 * (1 - j / 5)) or 1
                    b, g, r = cv2.split(fr); fr = cv2.merge([np.roll(b, dx, 1), g, np.roll(r, -dx, 1)])
                mask = seg(fr) if (seg and behind_ok and pr["face"]) else None
                head = head_of(mask) if mask is not None else None
                for e in ev:
                    if e["t0"] <= t < e["t1"]: draw_text(fr, e, t, mask if head else None, head, rc)
                if wm is not None: blend(fr, wm, W / 2, H * 0.80, None, False, 0.85)
                # спалах на склейці
                if sh["flash"] and j < 3:
                    a = (0.75, 0.4, 0.15)[j]; fr = cv2.addWeighted(fr, 1 - a, np.full_like(fr, 255), a, 0)
                if t > nfr_total / FPS - 0.4:
                    fr = (fr * max(0.0, (nfr_total / FPS - t) / 0.4)).astype(np.uint8)
                pe.stdin.write(fr.tobytes()); fi += 1; prev_last = fr
                if fi % 15 == 0: job.pct = 78 + 20 * fi / nfr_total; job.sub = f"{fi}/{nfr_total}"
            pd.stdout.close(); pd.wait()
        pe.stdin.close()
        if pe.wait() != 0: raise RuntimeError("ffmpeg: " + pe.stderr.read().decode("utf-8", "replace")[-300:])
    except BaseException:
        try: pe.kill()
        except Exception: pass
        raise
    for p in parts: Path(p["file"]).unlink(missing_ok=True)
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-ss", f"{min(1.5, nfr_total / FPS / 2):.2f}", "-i", str(out), "-frames:v", "1",
                    "-vf", "scale=360:-2", str(out.with_name(out.stem + "_preview.jpg"))], capture_output=True)
    return str(out), nfr_total / FPS
