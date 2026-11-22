"""Бібліотека образів (looks) і переходів для едіту.

Ідеї — з відкритих наборів: lowdevco/Video-editing-effects (MIT: прожектор, тунель, Ч/Б + слово за спиною,
повільний наїзд з розмитим фоном), HeyGen HyperFrames (переходи: iris, shutter, whip, echo-trail),
iart-ai motion-design (ритм, рампи). Код — свій, на numpy/OpenCV.
"""
import math

import numpy as np

W, H = 1080, 1920


def _cv2():
    from .render import _cv2 as c
    return c()


def ease_out(x): x = min(1.0, max(0.0, x)); return 1 - (1 - x) ** 3
def ease_io(x): x = min(1.0, max(0.0, x)); return 3 * x * x - 2 * x * x * x


def full_mask(mask):
    """Маска (H/2, W/2) → (H, W, 1) float32."""
    cv2 = _cv2()
    return cv2.resize(mask, (W, H), interpolation=cv2.INTER_LINEAR)[..., None]


_VIG = {}


def vignette(fr, s):
    if "v" not in _VIG:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = ((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.62)) ** 2
        _VIG["v"] = np.clip(1 - d, 0, 1)[..., None]
    return (fr * (1 - s + s * _VIG["v"])).astype(np.uint8)


# ------------------------------------------------------------------ образи кадру (діють увесь шот або його частину)
# st — словник стану шоту (зберігається між кадрами); lt — час від початку шоту; d — тривалість шоту
def look_spotlight(fr, m, head, lt, d, st, acc):
    """Світло гасне, людина лишається освітленою."""
    if m is None: return vignette(fr, 0.6)
    k = ease_out(lt / 0.25)
    M = full_mask(m)
    dark = 1 - k * 0.72
    out = fr.astype(np.float32) * (dark * (1 - M) + M * (1 + 0.05 * k))
    return np.clip(out, 0, 255).astype(np.uint8)


def look_colorpop(fr, m, head, lt, d, st, acc):
    """Фон чорно-білий, людина в кольорі."""
    cv2 = _cv2()
    g = cv2.cvtColor(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    if m is None: return g
    k = ease_out(lt / 0.15); M = full_mask(m) * 1.0
    a = 1 - k * (1 - M)
    return (fr * a + g * (1 - a) * 0.85).astype(np.uint8)


def look_bwred(fr, m, head, lt, d, st, acc):
    """Ч/Б, затемнений фон — під велике червоне слово за спиною (текст дає стиль behind)."""
    cv2 = _cv2()
    g = cv2.cvtColor(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR).astype(np.float32)
    g = np.clip((g - 128) * 1.25 + 128, 0, 255)
    if m is not None:
        M = full_mask(m); g = g * (M + (1 - M) * 0.6)
    return vignette(g.astype(np.uint8), 0.45)


def look_bokeh(fr, m, head, lt, d, st, acc):
    """Повільний наїзд + фон «тане» в розфокус."""
    cv2 = _cv2()
    p = ease_io(lt / max(0.3, d))
    s = 2 + 16 * p
    bl = cv2.GaussianBlur(fr, (0, 0), s)
    if m is None: return vignette(bl if p > 0.6 else fr, 0.25 * p)
    M = full_mask(m)
    return vignette((fr * M + bl * (1 - M)).astype(np.uint8), 0.3 * p)


def look_outline(fr, m, head, lt, d, st, acc):
    """Світний контур навколо людини."""
    if m is None: return fr
    cv2 = _cv2()
    mb = (cv2.resize(m, (W, H)) > 0.5).astype(np.uint8)
    thick = 10 + int(6 * math.sin(lt * 9) ** 2)
    edge = cv2.dilate(mb, np.ones((thick, thick), np.uint8)) - mb
    glow = cv2.GaussianBlur(edge.astype(np.float32), (0, 0), 14)
    k = ease_out(lt / 0.2)
    out = fr.astype(np.float32)
    col = np.array(acc, np.float32)
    out = out * (1 - (edge[..., None] * k)) + col * edge[..., None] * k
    out = out + col * (np.clip(glow * 2.2, 0, 1)[..., None] * 0.8 * k)
    return np.clip(out, 0, 255).astype(np.uint8)


def look_echo(fr, m, head, lt, d, st, acc):
    """Шлейф-привиди людини (onion skin) — видно рух."""
    if m is None: return fr
    M = full_mask(m)
    layer = (fr * M).astype(np.uint8)
    hist = st.setdefault("echo", [])
    out = fr.astype(np.float32)
    for i, (L, MM) in enumerate(reversed(hist[-4:])):
        a = 0.42 * (1 - i / 4)
        tint = np.array(acc, np.float32) * 0.35
        out = out * (1 - MM * a) + (L * 0.65 + tint) * MM * a
    out = out * (1 - M) + fr * M
    hist.append((layer.astype(np.float32), M))
    if len(hist) > 8: hist.pop(0)
    return np.clip(out, 0, 255).astype(np.uint8)


def look_tunnel(fr, m, head, lt, d, st, acc):
    """Кадр тунелем провалюється сам у себе й обертається; людина лишається попереду."""
    cv2 = _cv2()
    k = ease_out(lt / 0.35)
    cx, cy = (head["x"], head["cy"]) if head else (W / 2, H * 0.45)
    out = fr.astype(np.float32); r = 0.62; z = (lt * 0.9) % 1.0
    ones = np.ones((H, W), np.float32)
    for lev in range(1, 6):
        s = r ** (lev - z * k)
        if s < 0.04: break
        Mx = cv2.getRotationMatrix2D((cx, cy), (lev - z) * 10 * k, s)
        L = cv2.warpAffine(fr, Mx, (W, H), flags=cv2.INTER_LINEAR).astype(np.float32)
        a = cv2.warpAffine(ones, Mx, (W, H))[..., None] * k
        out = out * (1 - a) + L * a
        bd = cv2.warpAffine(_border(), Mx, (W, H))[..., None] * k
        out = out * (1 - bd) + 255 * bd
    if m is not None:
        M = full_mask(m) * k; out = out * (1 - M) + fr * M
    return out.astype(np.uint8)


_B = {}


def _border():
    if "b" not in _B:
        b = np.zeros((H, W), np.float32); t = 9; b[:t] = b[-t:] = 1; b[:, :t] = b[:, -t:] = 1; _B["b"] = b
    return _B["b"]


def look_mirror(fr, m, head, lt, d, st, acc):
    """Дзеркало: ліва половина відбивається праворуч."""
    out = fr.copy(); out[:, W // 2:] = fr[:, :W // 2][:, ::-1]
    return out


def look_letterbox(fr, m, head, lt, d, st, acc):
    """Кіношні смуги, що наїжджають."""
    k = ease_out(lt / 0.3); bh = int(H * 0.11 * k)
    out = fr.copy()
    if bh: out[:bh] = 0; out[H - bh:] = 0
    return out


def look_popframe(fr, m, head, lt, d, st, acc):
    """Фон стискається в картку з рамкою, а людина «вистрибує» з неї в повний розмір."""
    if m is None: return fr
    cv2 = _cv2()
    k = ease_out(lt / 0.3); s = 1 - 0.2 * k
    M = full_mask(m)
    bg = cv2.GaussianBlur(fr, (0, 0), 22).astype(np.float32) * 0.45
    cw, chh = int(W * s), int(H * s)
    card = cv2.resize(fr, (cw, chh), interpolation=cv2.INTER_AREA).astype(np.float32)
    x0, y0 = (W - cw) // 2, int((H - chh) * 0.55)
    bw = 10
    bg[y0 - bw:y0 + chh + bw, x0 - bw:x0 + cw + bw] = 245
    bg[y0:y0 + chh, x0:x0 + cw] = card
    return (bg * (1 - M) + fr * M).astype(np.uint8)


LOOKS = {"spotlight": look_spotlight, "colorpop": look_colorpop, "bwred": look_bwred, "bokeh": look_bokeh, "outline": look_outline,
         "echo": look_echo, "tunnel": look_tunnel, "mirror": look_mirror, "letterbox": look_letterbox, "popframe": look_popframe}
PERSON_LOOKS = {"spotlight", "colorpop", "bwred", "bokeh", "outline", "echo", "popframe", "tunnel"}


# ------------------------------------------------------------------ виїзд людини на своє місце (cutout)
def cutout_enter(fr, m, lt, st, direction=1, dur=0.32):
    """Перші ~0.3 с шоту: фон розмитий і темний, людина вилітає з-за краю екрана на своє місце зі шлейфом."""
    if m is None or lt >= dur + 0.12: return fr
    cv2 = _cv2()
    M = full_mask(m)
    q = min(1.0, lt / dur); e = 1 - (1 - q) ** 4
    off = (1 - e) * W * 1.05 * direction
    vel = 4 * (1 - q) ** 3  # швидкість для змазу
    bgk = min(1.0, max(0.0, (lt - dur) / 0.12))
    bg = cv2.GaussianBlur(fr, (0, 0), 16).astype(np.float32) * 0.55
    bg = bg * (1 - bgk) + fr.astype(np.float32) * bgk
    M2 = M[..., 0]
    person = (fr * M).astype(np.float32)
    out = bg * 1.0
    for g in range(3, -1, -1):  # шлейф: 3 привиди позаду + сама людина
        o = off + g * 90 * direction * vel
        Mx = np.float32([[1, 0, o], [0, 1, 0]])
        L = cv2.warpAffine(person, Mx, (W, H)); A = cv2.warpAffine(M2, Mx, (W, H))
        if vel > 0.4:
            kx = int(9 + 50 * vel) | 1; L = cv2.blur(L, (kx, 1)); A = cv2.blur(A, (kx, 1))
        f = 1.0 if g == 0 else 0.35 * (1 - g / 4)
        out = out * (1 - A[..., None] * f) + L * f
    return np.clip(out, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ переходи, яким потрібен попередній кадр
def tr_iris(prev, fr, q, cx=W / 2, cy=H * 0.45):
    """Коло розкриває новий кадр поверх старого."""
    if prev is None: return fr
    cv2 = _cv2()
    r = int(math.hypot(W, H) * ease_out(q))
    mk = np.zeros((H, W), np.uint8); cv2.circle(mk, (int(cx), int(cy)), max(1, r), 255, -1)
    mk = cv2.GaussianBlur(mk, (0, 0), 6).astype(np.float32)[..., None] / 255
    return (prev * (1 - mk) + fr * mk).astype(np.uint8)


def tr_shutter(prev, fr, q, n=8):
    """Жалюзі: смуги нового кадру висуваються по черзі."""
    if prev is None: return fr
    out = prev.copy(); sh = H // n
    for i in range(n):
        qq = min(1.0, max(0.0, q * 1.6 - i * 0.6 / n))
        w = int(W * ease_out(qq))
        if w <= 0: continue
        y0, y1 = i * sh, (i + 1) * sh if i < n - 1 else H
        if i % 2: out[y0:y1, :w] = fr[y0:y1, :w]
        else: out[y0:y1, W - w:] = fr[y0:y1, W - w:]
    return out


def tr_pixel(fr, q):
    """Піксельне розсипання (q 1 → 0 — кадр «збирається»)."""
    cv2 = _cv2()
    b = int(2 + 60 * q)
    if b <= 2: return fr
    s = cv2.resize(fr, (max(1, W // b), max(1, H // b)), interpolation=cv2.INTER_AREA)
    return cv2.resize(s, (W, H), interpolation=cv2.INTER_NEAREST)


def tr_spin(fr, q, sign=1):
    """Оберт із наїздом (кадр «прокручується» через склейку)."""
    cv2 = _cv2()
    if q <= 0.01: return fr
    M = cv2.getRotationMatrix2D((W / 2, H / 2), sign * 38 * q ** 2, 1 + 0.35 * q ** 2)
    out = cv2.warpAffine(fr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return cv2.addWeighted(out, 0.6, cv2.blur(out, (25, 25)), 0.4, 0) if q > 0.3 else out


# ------------------------------------------------------------------ стилі едіту (набір правил, з якого кожна генерація бере свій варіант)
STYLES = {
    "hype":    {"trans": ["zoom", "glitch", "whip", "spin"], "looks": ["outline", "echo", "mirror", "colorpop", None, None],
                "grade": ["punch", "warm"], "text": ["slam", "slam", "stack", "rgb", "behind", "pop", "cascade"], "cutout": 0.35, "flash": 3},
    "cinema":  {"trans": ["zoom", "iris", "whip"], "looks": ["letterbox", "bokeh", "spotlight", None, None],
                "grade": ["warm", "cold"], "text": ["waterfall", "behind", "wipe", "split", "behind"], "cutout": 0.2, "flash": 1},
    "neon":    {"trans": ["glitch", "pixel", "zoom"], "looks": ["outline", "spotlight", "echo", "tunnel", None],
                "grade": ["cold", "punch"], "text": ["rgb", "orbit", "behind", "slam", "side"], "cutout": 0.3, "flash": 2},
    "pop":     {"trans": ["whip", "shutter", "zoom", "iris"], "looks": ["colorpop", "popframe", "mirror", None, None],
                "grade": ["punch", "warm"], "text": ["stack", "pop", "cascade", "slam", "orbit"], "cutout": 0.45, "flash": 2},
    "noir":    {"trans": ["zoom", "shutter", "glitch"], "looks": ["bwred", "bwred", "spotlight", "bokeh", None],
                "grade": ["none"], "text": ["behind", "behind", "slam", "split", "waterfall"], "cutout": 0.25, "flash": 2},
}
