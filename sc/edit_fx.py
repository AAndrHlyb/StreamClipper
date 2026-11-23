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




# ------------------------------------------------------------------ нові образи (за референсами: стікер, світіння, тепловізор, змаз пікселів, лінза)
def look_sticker(fr, m, head, lt, d, st, acc):
    """Стоп-кадр-«наліпка»: кадр завмирає фотокарткою з білою рамкою й тінню над розмитим живим фоном,
    злегка повернутий; в кінці шоту картка «відлітає» назад у кадр."""
    cv2 = _cv2()
    if "frz" not in st: st["frz"] = fr.copy(); st["ang"] = float(np.random.uniform(-7, 7))
    k = ease_out(lt / 0.22); out_k = ease_io(max(0.0, (lt - (d - 0.18)) / 0.18))
    s = 1 - 0.3 * k * (1 - out_k)
    if s > 0.995: return fr
    bg = (cv2.GaussianBlur(fr, (0, 0), 18).astype(np.float32) * (1 - 0.35 * k)).astype(np.uint8)
    card = st["frz"]; b = 16
    framed = cv2.copyMakeBorder(card, b * 3, b * 3, b * 3, b * 3, cv2.BORDER_CONSTANT, value=(246, 246, 246))
    fh, fw = framed.shape[:2]
    M = cv2.getRotationMatrix2D((fw / 2, fh / 2), st["ang"] * k * (1 - out_k), s * min(W / fw, H / fh))
    M[0, 2] += W / 2 - fw / 2; M[1, 2] += H / 2 - fh / 2
    warped = cv2.warpAffine(framed, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    mk = cv2.warpAffine(np.full((fh, fw), 255, np.uint8), M, (W, H))
    sh = cv2.GaussianBlur(np.roll(mk, (24, 18), (0, 1)), (0, 0), 18).astype(np.float32)[..., None] / 255 * 0.6
    out = bg.astype(np.float32) * (1 - sh)
    a = mk.astype(np.float32)[..., None] / 255
    return (out * (1 - a) + warped * a).astype(np.uint8)


def look_glow(fr, m, head, lt, d, st, acc):
    """М'яке світіння світлих місць (bloom) + легкий підйом тіней — «дорога» картинка."""
    cv2 = _cv2()
    f = fr.astype(np.float32)
    hi = np.clip(f - 150, 0, 255) * 1.6
    small = cv2.resize(hi, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    bl = cv2.resize(cv2.GaussianBlur(small, (0, 0), 9), (W, H), interpolation=cv2.INTER_LINEAR)
    k = ease_out(lt / 0.2)
    return np.clip(f * (1 - 0.06 * k) + 10 * k + bl * 0.75 * k, 0, 255).astype(np.uint8)


def look_thermal(fr, m, head, lt, d, st, acc):
    """Тепловізор/соляризація фону (помаранчево-фіолетова карта), людина — у звичайних кольорах."""
    cv2 = _cv2()
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    th = cv2.applyColorMap(cv2.equalizeHist(g), cv2.COLORMAP_INFERNO)
    k = ease_out(lt / 0.12)
    if m is None: return cv2.addWeighted(fr, 1 - 0.8 * k, th, 0.8 * k, 0)
    M = full_mask(m) * k
    bgm = 1 - M
    return (fr * (1 - bgm * 0.85) + th * bgm * 0.85).astype(np.uint8)


def look_smear(fr, m, head, lt, d, st, acc):
    """Пікселі розтягуються від однієї колонки до краю (pixel smear), межа рухається — як у динамічних едітах."""
    cv2 = _cv2()
    if "sx" not in st: st["sx"] = float(np.random.uniform(0.22, 0.34)); st["side"] = int(np.random.choice([-1, 1]))
    x = int(W * (st["sx"] + 0.08 * math.sin(lt * 5)))
    out = fr.copy()
    if st["side"] < 0:
        col = cv2.GaussianBlur(fr[:, x:x + 1], (1, 0), sigmaX=1, sigmaY=3); out[:, :x] = col
    else:
        x = W - x; col = cv2.GaussianBlur(fr[:, x - 1:x], (1, 0), sigmaX=1, sigmaY=3); out[:, x:] = col
    if m is not None:  # людина лишається поверх розмазаного фону
        M = full_mask(m); out = (out * (1 - M) + fr * M).astype(np.uint8)
    return out


_BULGE = {}


def look_bulge(fr, m, head, lt, d, st, acc):
    """Лінза-«риб'яче око» на обличчі: голова роздувається на удар і повертається (смішний акцент)."""
    cv2 = _cv2()
    if head is None: return fr
    cx, cy = int(head["x"]) // 40 * 40, int(head["cy"] + 60) // 40 * 40
    q = math.sin(math.pi * min(1.0, lt / 0.45)) if lt < 0.45 else 0.0
    if q < 0.02: return fr
    r = int(max(180, (head["x1"] - head["x0"]) * 0.55))
    key = (cx, cy, r)
    if key not in _BULGE:
        if len(_BULGE) > 8: _BULGE.clear()
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        dx, dy = xx - cx, yy - cy; dist = np.sqrt(dx * dx + dy * dy) / r
        _BULGE[key] = (dx, dy, dist)
    dx, dy, dist = _BULGE[key]
    f = np.where(dist < 1, np.power(np.clip(dist, 1e-3, 1), 0.6 * q), 1.0).astype(np.float32)
    mx = (cx + dx * f).astype(np.float32); my = (cy + dy * f).astype(np.float32)
    return cv2.remap(fr, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


LOOKS = {"spotlight": look_spotlight, "colorpop": look_colorpop, "bwred": look_bwred, "bokeh": look_bokeh, "outline": look_outline,
         "echo": look_echo, "tunnel": look_tunnel, "mirror": look_mirror, "letterbox": look_letterbox, "popframe": look_popframe,
         "sticker": look_sticker, "glow": look_glow, "thermal": look_thermal, "smear": look_smear, "bulge": look_bulge}
PERSON_LOOKS = {"spotlight", "colorpop", "bwred", "bokeh", "outline", "echo", "popframe", "tunnel", "bulge"}


def look_vhs(fr, m, head, lt, d, st, acc):
    """Плівка VHS: розмиті кольори, рядки розгортки, шум, легке «плавання» рядка."""
    cv2 = _cv2()
    out = fr.astype(np.float32)
    ycc = cv2.cvtColor(fr, cv2.COLOR_BGR2YCrCb)
    ycc[..., 1] = cv2.blur(ycc[..., 1], (21, 1)); ycc[..., 2] = cv2.blur(ycc[..., 2], (21, 1))  # кольори «течуть»
    ycc[..., 1] = np.roll(ycc[..., 1], 6, 1)
    out = cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR).astype(np.float32)
    out[::4] *= 0.82                                                   # рядки розгортки
    out += np.random.normal(0, 7, (H // 4, W // 4, 1)).repeat(4, 0).repeat(4, 1)
    y0 = int((lt * 900) % H); out[y0:y0 + 24] = np.roll(out[y0:y0 + 24], 18, 1)  # смуга трекінгу
    out = out * np.array([0.96, 1.0, 1.06], np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def hit_chroma(fr, q):
    """Удар лінзою: кадр «вибухає» від центру (бочка) + сильне RGB-розшарування, затухає за 3–4 кадри."""
    cv2 = _cv2()
    if q <= 0: return fr
    s = 1 + 0.08 * q
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
    z = cv2.warpAffine(fr, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    d = int(26 * q)
    b, g, r = cv2.split(z)
    Mb = cv2.getRotationMatrix2D((W / 2, H / 2), 0, 1 + 0.025 * q); Mr = cv2.getRotationMatrix2D((W / 2, H / 2), 0, 1 - 0.02 * q)
    b = np.roll(cv2.warpAffine(b, Mb, (W, H), borderMode=cv2.BORDER_REFLECT), d, 1)
    r = np.roll(cv2.warpAffine(r, Mr, (W, H), borderMode=cv2.BORDER_REFLECT), -d, 1)
    return cv2.merge([b, g, r])


LOOKS["vhs"] = look_vhs


# ------------------------------------------------------------------ акценти на удар (2–4 кадри)
def hit_silhouette(fr, m, q, color=(255, 255, 255)):
    """Людина спалахує білим силуетом із сяйвом, фон темнішає (як у референсах на сильну долю)."""
    cv2 = _cv2()
    if m is None or q <= 0: return fr
    M = full_mask(m)
    glow = cv2.GaussianBlur(M[..., 0], (0, 0), 22)[..., None]
    col = np.array(color, np.float32)[None, None, :]
    out = fr.astype(np.float32) * (1 - 0.75 * q)
    out = out + glow * col * 0.8 * q
    out = out * (1 - M * q) + col * M * q
    return np.clip(out, 0, 255).astype(np.uint8)


def hit_invert(fr, q):
    """Інверсія кольорів на удар (негатив-спалах)."""
    if q <= 0: return fr
    inv = 255 - fr
    return (fr * (1 - q) + inv * q).astype(np.uint8)


def blackout(fr, m, lt, d, color):
    """Пауза перед дропом: чорний екран; у другій половині з темряви проступає неоновий контур людини."""
    cv2 = _cv2()
    out = np.zeros_like(fr)
    if m is None or lt < d * 0.45: return out
    q = min(1.0, (lt - d * 0.45) / max(0.08, d * 0.4))
    mm = (cv2.resize(m, (W, H)) > 0.5).astype(np.uint8) * 255
    edge = cv2.morphologyEx(mm, cv2.MORPH_GRADIENT, np.ones((9, 9), np.uint8)).astype(np.float32) / 255
    g = cv2.GaussianBlur(edge, (0, 0), 14)
    col = np.array(color, np.float32)[None, None, :]
    return np.clip((edge[..., None] * 1.0 + g[..., None] * 1.6) * col * q, 0, 255).astype(np.uint8)


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


def slide_curve(j, n):
    """Хід «наїзду» кадру: швидко на ~65 %, коротка пауза (два кадри видно разом), потім дотискає до кінця."""
    x = (j + 1) / n
    if x < 0.4: return 0.65 * ease_out(x / 0.4), 1.0
    if x < 0.7: return 0.65 + 0.05 * (x - 0.4) / 0.3, 0.0
    return 0.7 + 0.3 * ease_io((x - 0.7) / 0.3), 0.6


def tr_slide(prev, fr, q, direction="up", vel=1.0):
    """Новий кадр наїжджає на попередній з краю (частина одного кадру поверх іншого), край змазаний рухом,
    під ним — тінь; старий кадр трохи відступає й темнішає."""
    if prev is None: return fr
    cv2 = _cv2()
    q = min(1.0, max(0.0, q))
    vert = direction in ("up", "down")
    L = H if vert else W
    off = int(round((1 - q) * L))
    if off <= 0: return fr
    sgn = 1 if direction in ("up", "left") else -1  # up: входить знизу
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, 1 + 0.04 * q)
    old = cv2.warpAffine(prev, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    old = (old.astype(np.float32) * (1 - 0.35 * q)).astype(np.uint8)
    new = fr
    if vel > 0.05:
        kz = int(9 + 70 * vel) | 1
        new = cv2.blur(fr, (1, kz) if vert else (kz, 1))
    out = old.copy()
    sh = 26
    if vert:
        if sgn > 0:  # знизу вгору
            out[off:] = new[:H - off]
            if off - sh > 0: out[off - sh:off] = (out[off - sh:off] * np.linspace(1, 0.45, sh)[:, None, None]).astype(np.uint8)
        else:
            out[:H - off] = new[off:]
            e = H - off
            if e + sh < H: out[e:e + sh] = (out[e:e + sh] * np.linspace(0.45, 1, sh)[:, None, None]).astype(np.uint8)
    else:
        if sgn > 0:  # справа наліво
            out[:, off:] = new[:, :W - off]
        else:
            out[:, :W - off] = new[:, off:]
    return out


def tr_splitin(prev, fr, q, vel=1.0):
    """Дві половини нового кадру заїжджають з протилежних боків (верх — зліва, низ — справа)."""
    if prev is None: return fr
    cv2 = _cv2()
    q = min(1.0, max(0.0, q)); off = int(round((1 - q) * W))
    if off <= 0: return fr
    new = cv2.blur(fr, (int(9 + 60 * vel) | 1, 1)) if vel > 0.05 else fr
    out = (prev.astype(np.float32) * (1 - 0.3 * q)).astype(np.uint8)
    hh = H // 2
    out[:hh, :W - off] = new[:hh, off:]
    out[hh:, off:] = new[hh:, :W - off]
    return out


def tr_strips(prev, fr, q, vel=1.0, n=3, horiz=True):
    """Новий кадр заїжджає смугами (3 шт.) з різних боків по черзі — кожна смуга трохи запізнюється, край змазаний."""
    if prev is None: return fr
    cv2 = _cv2()
    out = (prev.astype(np.float32) * (1 - 0.3 * min(1.0, q))).astype(np.uint8)
    new = cv2.blur(fr, (int(9 + 60 * vel) | 1, 1) if horiz else (1, int(9 + 60 * vel) | 1)) if vel > 0.05 else fr
    for k in range(n):
        qk = min(1.0, max(0.0, (q - k * 0.12) / (1 - 0.24)))
        if qk <= 0: continue
        if horiz:
            y0, y1 = k * H // n, (k + 1) * H // n
            off = int(round((1 - qk) * W))
            if k % 2 == 0: out[y0:y1, off:] = new[y0:y1, :W - off]
            else: out[y0:y1, :W - off] = new[y0:y1, off:]
        else:
            x0, x1 = k * W // n, (k + 1) * W // n
            off = int(round((1 - qk) * H))
            if k % 2 == 0: out[off:, x0:x1] = new[:H - off, x0:x1]
            else: out[:H - off, x0:x1] = new[off:, x0:x1]
    return out


# ------------------------------------------------------------------ стилі едіту (набір правил, з якого кожна генерація бере свій варіант)
STYLES = {
    "hype":    {"trans": ["zoom", "glitch", "whip", "spin", "slide", "strips"], "looks": ["outline", "echo", "mirror", "colorpop", None, None],
                "grade": ["punch", "warm"], "text": ["slam", "slam", "stack", "rgb", "behind", "deep", "pop", "rise"], "cutout": 0.35, "flash": 3},
    "cinema":  {"trans": ["zoom", "iris", "whip", "slide", "strips"], "looks": ["letterbox", "bokeh", "spotlight", None, None],
                "grade": ["warm", "cold"], "text": ["waterfall", "behind", "rise", "wipe", "split", "behind"], "cutout": 0.2, "flash": 1},
    "neon":    {"trans": ["glitch", "pixel", "zoom", "slide", "strips"], "looks": ["outline", "spotlight", "echo", "tunnel", None],
                "grade": ["cold", "punch"], "text": ["rgb", "orbit", "behind", "slam", "deep", "rise"], "cutout": 0.3, "flash": 2},
    "pop":     {"trans": ["whip", "shutter", "zoom", "iris", "slide", "splitin", "strips"], "looks": ["colorpop", "popframe", "mirror", None, None],
                "grade": ["punch", "warm"], "text": ["stack", "pop", "deep", "slam", "orbit", "rise"], "cutout": 0.45, "flash": 2},
    "noir":    {"trans": ["zoom", "shutter", "glitch", "slide", "strips"], "looks": ["bwred", "bwred", "spotlight", "bokeh", None],
                "grade": ["none"], "text": ["behind", "rise", "slam", "deep", "waterfall"], "cutout": 0.25, "flash": 2},
}


# ------------------------------------------------------------------ «режисер»: кожному кадру — найкращий ефект під музику й картинку
# c — контекст шоту: sec (intro/build/pre/drop/peak/resolve), en (енергія), br (яскравість звуку), bass, busy (щільність ударів),
# st (сила долі), d (тривалість), face (є обличчя), fs (розмір обличчя), fbr (яскравість кадру 0..1), fsa (насиченість, z)
_ON = lambda c, *secs: 1.0 if c["sec"] in secs else 0.0
LOOK_RULES = {
    None:        lambda c: 0.35 + 0.5 * _ON(c, "intro", "resolve") + (0.5 if c["d"] < 0.35 else 0) - 0.5 * _ON(c, "drop"),  # чиста картинка — норма, образ — акцент
    "spotlight": lambda c: 0.8 * c["face"] + 0.5 * _ON(c, "build", "pre") + 0.3 * (1 - c["fbr"]),
    "colorpop":  lambda c: 0.7 * c["face"] + 0.4 * max(0.0, c["fsa"]) + 0.3 * _ON(c, "peak"),
    "bwred":     lambda c: 0.6 * c["face"] + 0.5 * (1 - c["br"]) + 0.3 * _ON(c, "pre", "drop"),
    "bokeh":     lambda c: 0.7 * c["face"] + 0.6 * _ON(c, "intro", "build") + 0.3 * (1 - c["en"]),
    "outline":   lambda c: 0.8 * c["face"] + 0.5 * c["br"] + 0.4 * c["en"],
    "echo":      lambda c: 0.6 * c["face"] + 0.6 * c["busy"] + 0.3 * _ON(c, "peak"),
    "tunnel":    lambda c: 0.6 * (1 - c["face"]) + 0.5 * c["en"] + 0.4 * _ON(c, "drop", "peak"),
    "mirror":    lambda c: 0.7 * (1 - c["face"]) + 0.3 * _ON(c, "build"),
    "letterbox": lambda c: 0.7 * _ON(c, "intro", "resolve") + 0.4 * (1 - c["en"]),
    "popframe":  lambda c: 0.6 * c["face"] + 0.4 * _ON(c, "build") + 0.2 * (c["d"] > 0.6),
    "sticker":   lambda c: 0.6 * (c["d"] >= 0.7) + 0.5 * _ON(c, "intro", "build", "resolve") + 0.2 * c["face"] - 0.8 * (c["d"] < 0.5),
    "glow":      lambda c: 0.7 * c["br"] + 0.4 * c["fbr"] + 0.3 * _ON(c, "intro", "resolve"),
    "thermal":   lambda c: 0.5 * c["face"] + 0.7 * c["bass"] * c["en"] + 0.4 * _ON(c, "drop", "peak"),
    "smear":     lambda c: 0.4 * (c["fs"] < 0.08) + 0.5 * c["busy"] + 0.3 * _ON(c, "build", "peak"),
    "vhs":       lambda c: 0.5 * (1 - c["br"]) + 0.3 * _ON(c, "intro", "build") + 0.3 * (c["fsa"] < 0),
    "bulge":     lambda c: (0.9 if c["fs"] > 0.03 else -1) * (0.7 if c["sec"] in ("drop", "peak") else 0.2) + 0.3 * c["st"],
}
TRANS_RULES = {
    "cut":     lambda c: 0.55 + 0.4 * _ON(c, "peak") * c["busy"],
    "zoom":    lambda c: 0.45 + 0.8 * _ON(c, "drop") + 0.3 * c["en"] + 0.3 * _ON(c, "build"),
    "whip":    lambda c: 0.35 + 0.6 * c["busy"] + 0.2 * _ON(c, "peak"),
    "glitch":  lambda c: 0.6 * c["br"] + 0.4 * c["busy"],
    "spin":    lambda c: 0.5 * _ON(c, "drop", "peak") + 0.3 * c["st"],
    "pixel":   lambda c: 0.4 * c["br"] + 0.3 * _ON(c, "build"),
    "iris":    lambda c: 0.6 * _ON(c, "intro", "resolve", "build") + 0.3 * (1 - c["en"]),
    "shutter": lambda c: 0.5 * _ON(c, "build") + 0.3 * c["busy"],
    "slide":   lambda c: 0.9 + 0.35 * _ON(c, "build", "peak", "drop") + 0.3 * (c["d"] >= 0.5),  # наїзд кадру на кадр — фірмовий
    "splitin": lambda c: 0.6 + 0.35 * _ON(c, "peak") + 0.2 * c["busy"] + 0.2 * (c["d"] >= 0.5),
    "strips":  lambda c: 0.55 + 0.35 * _ON(c, "peak", "drop") + 0.25 * c["busy"] + 0.2 * (c["d"] >= 0.5),
    "dip":     lambda c: 0.9 * _ON(c, "intro", "resolve") * (1 - c["en"]) - 0.6 * _ON(c, "peak", "drop"),
}
HIT_RULES = {  # акцент на початку шоту на сильну долю
    None:         lambda c: 0.5,
    "flash":      lambda c: 0.45 + 0.4 * _ON(c, "drop") + 0.2 * c["br"],
    "silhouette": lambda c: (0.7 if c["face"] else -2) + 0.4 * c["br"] + 0.3 * _ON(c, "drop"),
    "invert":     lambda c: 0.35 + 0.5 * c["bass"] + 0.2 * c["busy"],
    "chroma":     lambda c: 0.45 + 0.4 * c["bass"] + 0.3 * c["en"],
}
MOVE_RULES = {  # рух «камери» всередині шоту
    "push":  lambda c: 0.5 + 0.5 * c["face"] + 0.4 * _ON(c, "intro", "build", "pre"),
    "pull":  lambda c: 0.3 + 0.6 * _ON(c, "resolve", "intro") + 0.2 * (1 - c["en"]),
    "pan":   lambda c: 0.3 + 0.5 * (1 - c["face"]) + 0.3 * _ON(c, "build"),
    "drift": lambda c: 0.3 + 0.5 * _ON(c, "peak") + 0.3 * c["busy"],
    "punch": lambda c: 0.2 + 0.9 * _ON(c, "drop") + 0.4 * c["st"] * _ON(c, "peak"),
}


def choose(rules, c, rnd, prefer=(), used=None, prev=None, bonus=0.55, noise=0.4, person_only=()):
    """Найкращий варіант за правилами + перевага стилю + штраф за повтори + трохи випадковості (кожен едіт інший)."""
    used = used if used is not None else {}
    best, bs = None, -9e9
    for k, f in rules.items():
        try: sc = float(f(c))
        except Exception: continue
        if k in prefer: sc += bonus
        if k is not None:
            sc -= 0.6 * used.get(k, 0)
            if k == prev: sc -= 1.5
            if k in person_only and not c["face"]: sc -= 1.2
        sc += rnd.uniform(0, noise)
        if sc > bs: best, bs = k, sc
    if best is not None: used[best] = used.get(best, 0) + 1
    return best


# ------------------------------------------------------------------ віртуальна камера (як у моушн-дизайні та іграх)
class CamRig:
    """Жива камера для всього едіту:
    • «травма» (trauma) як у GDC-доповіді про camera shake: удари додають травму, вона згасає; тряска = травма² × гладкий шум
      (не випадкові стрибки кадр-у-кадр, а плавне «дихання», що різко посилюється на ударі);
    • пружина зуму з недодемпфуванням: на кожну долю кадр «підстрибує» з перельотом і повертається (overshoot);
    • ручна камера: ледь помітний дрейф і нахил весь час — кадр ніколи не «мертвий»;
    • слідкування за людиною: при наближенні кадр зсувається так, щоб людина лишалась у композиції."""

    def __init__(self, rnd, energy=1.0):
        self.ph = [rnd.uniform(0, 100) for _ in range(12)]
        self.tr = 0.0; self.zx = 0.0; self.zv = 0.0; self.fx = 0.0; self.fy = 0.0
        self.energy = energy; self.prev = None

    def _n(self, t, ch):  # гладкий шум −1..1 (3 октави синусів з випадковими фазами)
        p = self.ph[ch * 3:ch * 3 + 3]
        return (math.sin(t * 1.0 + p[0]) * 0.5 + math.sin(t * 2.3 + p[1]) * 0.3 + math.sin(t * 4.7 + p[2]) * 0.2)

    def step(self, t, dt, beat=0.0, hit=0.0, head=None, z=1.0):
        e = self.energy
        self.tr = max(0.0, self.tr - 1.9 * dt)
        if hit: self.tr = min(1.0, self.tr + 0.6 * hit)
        if beat: self.tr = min(1.0, self.tr + 0.1 * beat)
        # пружина зуму: k=300, c=15 → ζ≈0.43 (перелітає і повертається за ~0.25 с)
        if beat: self.zv += (1.0 + 0.8 * beat) * e
        if hit: self.zv += 5.0 * hit * e
        a = -300 * self.zx - 15 * self.zv
        self.zv += a * dt; self.zx += self.zv * dt
        sh = self.tr ** 2 * e
        dx = sh * 38 * self._n(t * 13, 0) + 5 * self._n(t * 0.7, 1)
        dy = sh * 30 * self._n(t * 13, 2) + 4 * self._n(t * 0.6, 3)
        rot = sh * 3.2 * self._n(t * 9, 1) + 0.35 * self._n(t * 0.5, 2)
        zz = 1 + 0.75 * self.zx  # ~3 % на долю, ~10 % на удар, з перельотом
        # слідкування: зсув до людини, але не більше, ніж дозволяє запас від наближення
        if head is not None:
            room = max(0.0, (z * zz - 1)) * W / 2
            tx = max(-room, min(room, (W / 2 - head["x"]) * 0.5))
            ty = max(-room * H / W, min(room * H / W, (H * 0.42 - head["cy"]) * 0.3))
            self.fx += (tx - self.fx) * 0.12; self.fy += (ty - self.fy) * 0.12
        else:
            self.fx *= 0.9; self.fy *= 0.9
        return zz, dx + self.fx, dy + self.fy, rot


def motion_blur(fr, vx, vy):
    """Змаз руху вздовж швидкості камери (як motion blur у After Effects): кілька зсунутих копій уздовж вектора."""
    cv2 = _cv2()
    sp = math.hypot(vx, vy)
    if sp < 7: return fr
    n = 4 if sp < 25 else 6
    acc = fr.astype(np.float32)
    for k in range(1, n):
        f = k / n * 0.8
        M = np.float32([[1, 0, -vx * f], [0, 1, -vy * f]])
        acc += cv2.warpAffine(fr, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    return (acc / n).astype(np.uint8)


def look_parallax(fr, m, head, lt, d, st, acc):
    """3D-паралакс (2.5D): людину вирізаємо, фон «латаємо» і їдемо камерою — людина наближається швидше за фон,
    фон трохи зсувається вбік. Як «3D zoom» у CapCut: передній план і фон рухаються з різною швидкістю."""
    cv2 = _cv2()
    if m is None: return fr
    if "bg" not in st:  # фон без людини (inpaint на ¼ роздільності — швидко), рахуємо раз на шот
        small = cv2.resize(fr, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
        mk = (cv2.resize(m, (W // 4, H // 4)) > 0.3).astype(np.uint8) * 255
        mk = cv2.dilate(mk, np.ones((9, 9), np.uint8))
        bg = cv2.inpaint(small, mk, 5, cv2.INPAINT_TELEA)
        st["bg"] = cv2.resize(cv2.GaussianBlur(bg, (0, 0), 2), (W, H), interpolation=cv2.INTER_CUBIC)
        st["dir"] = 1 if (head is None or head["x"] < W / 2) else -1
    p = ease_io(min(1.0, lt / max(0.3, d)))
    zb, zf = 1.0 + 0.04 * p, 1.0 + 0.13 * p
    cx = head["x"] if head else W / 2; cy = head["bottom"] if head else H * 0.8
    Mb = cv2.getRotationMatrix2D((W / 2, H / 2), 0, zb); Mb[0, 2] += st["dir"] * 40 * p
    bg = cv2.warpAffine(st["bg"], Mb, (W, H), borderMode=cv2.BORDER_REFLECT)
    Mf = cv2.getRotationMatrix2D((cx, min(H - 1, cy)), 0, zf); Mf[0, 2] -= st["dir"] * 25 * p
    M = full_mask(m)[..., 0]
    fg = cv2.warpAffine(fr, Mf, (W, H), borderMode=cv2.BORDER_REFLECT)
    mf = cv2.warpAffine(M, Mf, (W, H))[..., None]
    return (bg * (1 - mf) + fg * mf).astype(np.uint8)


LOOKS["parallax"] = look_parallax
PERSON_LOOKS.add("parallax")
LOOK_RULES["parallax"] = lambda c: (1.1 if c["face"] else -2) * (0.8 if c["sec"] in ("intro", "build", "resolve") else 0.35) + 0.3 * (c["d"] >= 0.8)


# ------------------------------------------------------------------ з референсу: людина в чужій сцені, людина в темряві, світлові смуги
def look_transfer(fr, m, head, lt, d, st, acc):
    """Людина з нового кадру «вклеєна» у попередню сцену (колаж, як у референсі): ~0.35 с, з білим обідком,
    потім — звичайний кадр."""
    cv2 = _cv2()
    bg = st.get("prev_bg")
    if m is None or bg is None or lt > 0.4: return fr
    M = full_mask(m)
    edge = cv2.GaussianBlur(M[..., 0], (0, 0), 6)[..., None]
    rim = np.clip(edge - M, 0, 1) * 1.8
    b = cv2.GaussianBlur(bg, (0, 0), 3).astype(np.float32) * 0.85
    out = b * (1 - M) + fr.astype(np.float32) * M + 255 * rim
    k = 1.0 if lt < 0.3 else 1 - (lt - 0.3) / 0.1
    return np.clip(out * k + fr.astype(np.float32) * (1 - k), 0, 255).astype(np.uint8)


def look_isolate(fr, m, head, lt, d, st, acc):
    """Людина в темряві: фон іде майже в чорне, по контуру — кольорове світло (rim light)."""
    cv2 = _cv2()
    if m is None: return fr
    M = full_mask(m)
    k = ease_out(min(1.0, lt / 0.12))
    g = cv2.GaussianBlur(M[..., 0], (0, 0), 14)[..., None]
    rim = np.clip(g - M, 0, 1) * np.array(acc, np.float32)[None, None, :] * 2.4
    out = fr.astype(np.float32) * (M + (1 - M) * (1 - 0.88 * k)) + rim * k
    return np.clip(out, 0, 255).astype(np.uint8)


def hit_streak(fr, q, acc, seed=0):
    """Світлові смуги (анаморфні відблиски) через кадр на ударі, гаснуть за кілька кадрів."""
    cv2 = _cv2()
    if q <= 0: return fr
    r = np.random.RandomState(seed)
    layer = np.zeros((H // 4, W // 4, 3), np.float32)
    for _ in range(r.randint(2, 5)):
        y = r.randint(H // 16, H // 4 - H // 16); th = r.randint(1, 3)
        col = np.array(acc if r.rand() < 0.6 else (255, 255, 255), np.float32)
        layer[y:y + th, :] = col
    layer = cv2.GaussianBlur(layer, (0, 0), sigmaX=2, sigmaY=1.2) * 2.2 + cv2.GaussianBlur(layer, (0, 0), sigmaX=20, sigmaY=6) * 3.0
    layer = cv2.resize(layer, (W, H), interpolation=cv2.INTER_LINEAR)
    return np.clip(fr.astype(np.float32) + layer * q, 0, 255).astype(np.uint8)


LOOKS["transfer"] = look_transfer
LOOKS["isolate"] = look_isolate
PERSON_LOOKS.update({"transfer", "isolate"})
LOOK_RULES["transfer"] = lambda c: (1.0 if c["face"] else -2) * (0.9 if c["sec"] in ("build", "peak", "drop") else 0.3) + 0.2 * c["busy"]
LOOK_RULES["isolate"] = lambda c: (0.9 if c["face"] else -2) * (0.8 if c["sec"] in ("pre", "drop", "peak") else 0.3) + 0.3 * (1 - c["fbr"])
HIT_RULES["streak"] = lambda c: 0.5 + 0.4 * c["br"] + 0.3 * _ON(c, "drop", "peak")
TRANS_RULES["crash"] = lambda c: 0.55 + 0.6 * _ON(c, "drop") + 0.3 * _ON(c, "peak") + 0.2 * c["st"]
for _s in STYLES.values():
    if "crash" not in _s["trans"]: _s["trans"].append("crash")


# ------------------------------------------------------------------ 3D-камера по карті глибини (шари рухаються з різною швидкістю)
def look_depth3d(fr, m, head, lt, d, st, acc):
    """«3D Ken Burns»: кадр розкладається за глибиною (Depth Anything), камера летить уперед і трохи вбік —
    близьке наближається й зсувається сильніше за далеке, з'являється об'єм. Глибина оновлюється кожні 8 кадрів."""
    cv2 = _cv2()
    st["k"] = st.get("k", -1) + 1
    if st.get("dep") is None or st["k"] % 8 == 0:
        from .depth import depth as _depth
        small = cv2.resize(fr, (W // 3, H // 3), interpolation=cv2.INTER_AREA)
        dm = _depth(small, None)
        if dm is None: return LOOKS["parallax"](fr, m, head, lt, d, st, acc) if m is not None else fr
        dm = cv2.GaussianBlur(dm, (0, 0), 3)
        st["dep"] = cv2.resize(dm, (W, H), interpolation=cv2.INTER_LINEAR)
        if "dir" not in st: st["dir"] = float(np.random.choice([-1, 1]))
        if "grid" not in st:
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32); st["grid"] = (xx, yy)
    dep = st["dep"]; xx, yy = st["grid"]
    p = ease_io(min(1.0, lt / max(0.4, d)))
    cx = head["x"] if head else W / 2; cy = head["cy"] if head else H / 2
    zoom = 0.02 + 0.11 * dep            # далеке майже стоїть, близьке наїжджає
    shift = st["dir"] * W * 0.035 * (dep - 0.35)  # орбіта: близьке й далеке зсуваються в різні боки
    sx = 1 / (1 + zoom * p)
    mx = cx + (xx - cx) * sx - shift * p
    my = cy + (yy - cy) * sx
    return cv2.remap(fr, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


LOOKS["depth3d"] = look_depth3d
LOOK_RULES["depth3d"] = lambda c: 0.7 + 0.5 * _ON(c, "intro", "build", "resolve") + 0.3 * (c["d"] >= 0.7) - 0.8 * (c["d"] < 0.45)


# ------------------------------------------------------------------ «поворот куба» (Box Turn): кадри — грані куба, що провертається
def tr_cube(prev, fr, q, direction=1):
    cv2 = _cv2()
    if prev is None: return fr
    q = min(1.0, max(0.0, q)); a = q * math.pi / 2
    out = np.zeros_like(fr)
    def face(img, ang, side):
        c = math.cos(ang); s_ = math.sin(ang)
        w2 = W * c
        if w2 < 2: return None
        x0 = (W - w2) / 2 + side * (W / 2) * s_ * direction
        dz = 0.18 * s_
        src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
        if side * direction > 0:
            dst = np.float32([[x0, H * dz / 2], [x0 + w2, 0], [x0 + w2, H], [x0, H - H * dz / 2]])
        else:
            dst = np.float32([[x0, 0], [x0 + w2, H * dz / 2], [x0 + w2, H - H * dz / 2], [x0, H]])
        return cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (W, H))
    for img, ang, side in ((prev, a, -1), (fr, math.pi / 2 - a, 1)):
        f = face(img, ang, side)
        if f is not None:
            mk = f.sum(axis=2) > 0
            out[mk] = (f[mk] * (0.55 + 0.45 * math.cos(ang))).astype(np.uint8)
    return out


for _s in STYLES.values():
    if "cube" not in _s["trans"] and _s is not STYLES.get("noir"): _s["trans"].append("cube")
TRANS_RULES["cube"] = lambda c: 0.45 + 0.35 * _ON(c, "build", "peak") + 0.2 * (c["d"] >= 0.5)


# ------------------------------------------------------------------ плівковий «опік» і комікс-растр
def hit_burn(fr, q, seed=0):
    """Опік плівки на склейці: теплі помаранчево-червоні плями, що розгоряються від краю й гаснуть, з мерехтінням."""
    cv2 = _cv2()
    if q <= 0: return fr
    r = np.random.RandomState(seed)
    n = np.zeros((H // 16, W // 16), np.float32)
    for _ in range(5):
        cx, cy = r.randint(0, W // 16), r.choice([r.randint(0, H // 48 + 1), H // 16 - 1 - r.randint(0, H // 48 + 1)])
        cv2.circle(n, (int(cx), int(cy)), int(r.randint(3, 9)), 1.0, -1)
    n = cv2.GaussianBlur(n, (0, 0), 4)
    n = cv2.resize(n, (W, H), interpolation=cv2.INTER_CUBIC)[..., None]
    col = np.array([30, 110, 255], np.float32)[None, None, :]  # BGR помаранчевий
    hot = np.clip(n * 2.2 - 0.6, 0, 1)
    out = fr.astype(np.float32) + n * col * 1.6 * q + hot * 255 * q
    return np.clip(out, 0, 255).astype(np.uint8)


def look_halftone(fr, m, head, lt, d, st, acc):
    """Комікс-растр: фон — крапки (розмір за яскравістю) кольором акценту на папері, людина — у кольорі з білим обідком."""
    cv2 = _cv2()
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    cell = 12
    gs = cv2.resize(g, (W // cell, H // cell), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    paper = np.full((H, W, 3), (232, 240, 246), np.uint8)
    col = tuple(int(c * 0.75) for c in acc)
    for yy in range(gs.shape[0]):
        for xx in range(gs.shape[1]):
            rr = (1 - min(1.0, gs[yy, xx] * 1.6)) ** 1.4 * cell * 0.62
            if rr > 0.8: cv2.circle(paper, (xx * cell + cell // 2, yy * cell + cell // 2), int(rr), col, -1, cv2.LINE_AA)
    if m is None: return paper
    M = full_mask(m)
    rim = np.clip(cv2.GaussianBlur(M[..., 0], (0, 0), 4)[..., None] - M, 0, 1) * 2
    out = paper * (1 - M) + fr * M + 255 * rim
    return np.clip(out, 0, 255).astype(np.uint8)


LOOKS["halftone"] = look_halftone
LOOK_RULES["halftone"] = lambda c: 0.35 + 0.35 * c["face"] + 0.3 * _ON(c, "build", "peak") + 0.2 * c["busy"]
HIT_RULES["burn"] = lambda c: 0.4 + 0.3 * _ON(c, "intro", "build", "drop") + 0.2 * (1 - c["br"])

