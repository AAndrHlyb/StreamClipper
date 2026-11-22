"""Поиск моментов: громкость, чат, клипы зрителей, речь (под язык стримера), обучение на оценках."""
import math, os, re, sys, types
from collections import Counter

import numpy as np

from .core import perf, ffmpeg, run_proc, capture, hms, popen_kw, Cancelled, settings, save_settings

# ------------------------------------------------------------------ словари по языкам
NOISE = re.compile(r"(?i)^\s*(!.*|[\d\s.,%+\-xх]+|да|нет|ні|так|ку+|q|hi|hello|привет\w*|привіт\w*|прив|здаров\w*|бб+|bb+|пока|бувай|gn|f|ф|[1-5])\s*$")
GIVE = re.compile(r"(?i)розыгр|розіграш|giveaway|ps5|пс5|промокод|ставк|predict")
CATS = [(re.compile(r"(?i)\bclip|клип|кліп"), 3.0),
        (re.compile(r"(?i)ахах|хаха|ахпх|ххх|kekw|lul|lmao|omegalul|pepelaugh|ору|ржу|ржака|\){2,}|😂|🤣|xd\b"), 1.6),
        (re.compile(r"(?i)wtf|жесть|ебать|ахуе|охуе|пизд|что\?|шо\?|що\?|\?{2,}|omg|😱|💀|monka|aware|ебан|капец|жах"), 1.4),
        (re.compile(r"(?i)^w+$|pog|гг|gg|легенд|имба|🔥|красав|топ|клас"), 0.8),
        (re.compile(r"(?i)кринж|cringe|^фу+$|^l+$|позор|ганьба"), 0.8)]
LEX = {
    "ru": {"emo": r"блят|бля\b|сука|нахуй|нахер|ебать|ебан|пиздец|охуе|ахуе|жесть|какого|ааа+|ахах|хаха|боже|мама|не может быть|офиге|ничего себе|капец|вау|что\?",
           "promo": r"ссылк|описани|промокод|реклам|подпис|донат|спасибо за|розыгр|казино|партн[её]р|сабк|скидк"},
    "uk": {"emo": r"бляха|курва|дідько|жах|капець|оце так|нічого собі|ахах|хаха|боже|мамо|не може бути|вау|офіг|пиздець|бля\b|що\?|ааа+",
           "promo": r"посилан|опис[іи]|промокод|реклам|підпис|донат|дякую за|розіграш|казино|партнер|знижк"},
    "en": {"emo": r"wtf|omg|oh my god|what the|no way|holy|damn|shit|fuck|lol|haha|let'?s go|insane|crazy|wow|bro what",
           "promo": r"link in|description|promo code|sponsor|subscribe|donat|thank you for|giveaway|casino|discount|use code"},
}
LEX["other"] = {"emo": "|".join(v["emo"] for v in LEX.values()), "promo": "|".join(v["promo"] for v in LEX.values())}
PROFANITY = re.compile(r"(?i)(ху[йяеёию]|пизд|п[иі]зд|[её]б[аеиуы]|^еб|бля|сук[аиу]|сучк|муда|мудак|залуп|пид[оа]р|пидр|гандон|шлюх|курв|"
                       r"fuck|shit|bitch|cunt|dick|asshole|motherf|whore)")


def lex(lang):
    l = LEX.get((lang or "")[:2], LEX["other"])
    return re.compile("(?i)" + l["emo"]), re.compile("(?i)" + l["promo"])


# ------------------------------------------------------------------ громкость
def loudness(audio, n, job=None, start=0.0, dur=None):
    x = np.full(n, -70.0)
    rx = re.compile(r"t:\s*([\d.]+)\s+TARGET:\S+\s+LUFS\s+M:\s*(-?[\d.]+)")

    def on(line):
        m = rx.search(line)
        if m:
            i = int(float(m.group(1)))
            if i < n:
                x[i] = max(x[i], float(m.group(2)), -70.0)
                if job and i % 600 == 0: job.sub = f"{hms(start + i)}"
    cmd = [ffmpeg(), "-hide_banner", "-nostats"]
    if start: cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", audio]
    if dur: cmd += ["-t", f"{dur:.2f}"]
    run_proc(cmd + ["-vn", "-af", "ebur128", "-f", "null", "-"], job, on, check=False)
    return x


def loudness_par(audio, n, job=None, start=0.0, dur=None, parts=1):
    """Те саме, що loudness, але довгий запис ділиться на шматки, які рахуються одночасно."""
    dur = dur or n
    if parts <= 1 or dur < 1800: return loudness(audio, n, job, start, dur)
    from concurrent.futures import ThreadPoolExecutor
    step = math.ceil(dur / parts); warm = 5
    def one(i):
        a = i * step; b = min(dur, a + step)
        a0 = max(0, a - warm)  # кілька секунд розгону ebur128, їх відкидаємо
        y = loudness(audio, int(math.ceil(b - a0)) + 1, job, start + a0, b - a0)
        return a, a0, b, y
    x = np.full(n, -70.0)
    with ThreadPoolExecutor(parts) as ex:
        for a, a0, b, y in ex.map(one, range(parts)):
            lo, hi = int(a), min(n, int(math.ceil(b)) + 1)
            seg = y[lo - int(a0): lo - int(a0) + (hi - lo)]
            x[lo:lo + len(seg)] = np.maximum(x[lo:lo + len(seg)], seg)
    return x


def rmed(x, w, st=10):
    c = np.arange(0, len(x), st)
    return np.interp(np.arange(len(x)), c, [np.median(x[max(0, i - w // 2): i + w // 2 + 1]) for i in c])


def sm(x, k): return np.convolve(x, np.ones(k) / k, mode="same") if len(x) >= k else x


def rz(x, w, fl):
    d = x - rmed(x, w)
    s = max(np.median(np.abs(d - np.median(d))) * 1.4826, 0.5 * float(np.std(d)), fl)
    return np.clip(d / s, -5, 25)


def best_lag(cnt, az, maxlag=15):
    best, bl = -1.0, None
    x = (az - az.mean()) / (az.std() + 1e-6)
    for L in range(0, maxlag + 1):
        y = cnt[L:]; xx = x[:len(y)]
        if len(y) < 300: break
        c = float(np.mean(xx * (y - y.mean()) / (y.std() + 1e-6)))
        if c > best: best, bl = c, L
    return bl if best > 0.03 else None


def find_peaks(job, loud, chat, o, offset=0.0, vclips=(), used=(), keywords=()):
    """loud — массив громкости окна (секунды от offset); chat — сообщения с абсолютным t.
    Возвращает кандидатов с абсолютным временем пика и признаками."""
    n = len(loud)
    loud = loud.copy()
    sil = loud <= -60
    run = np.convolve(sil.astype(float), np.ones(5), mode="same") >= 5
    muted = np.convolve(run.astype(float), np.ones(121), mode="same") > 0
    if run.any():
        job.log("muted", int(run.sum()))
        loud = np.where(sil, np.median(loud[~sil]) if (~sil).any() else -30, loud)
    az = sm(rz(sm(loud, 3), 300, 1.0), 3)
    chat = [c for c in chat if c.get("m") and not NOISE.match(c["m"]) and not GIVE.search(c["m"]) and offset <= c["t"] < offset + n + 20]
    kw = [k.strip().lower() for k in keywords if k.strip()]
    delay, cz, rxz, kwz = 6.0, np.zeros(n), np.zeros(n), np.zeros(n)
    if len(chat) > 30:
        raw = np.zeros(n)
        for c in chat:
            i = int(c["t"] - offset)
            if 0 <= i < n: raw[i] += 1
        L = best_lag(sm(raw, 5), az)
        if L is not None: delay = float(L)
        cnt, rx, kh = np.zeros(n), np.zeros(n), np.zeros(n)
        for c in chat:
            i = int(c["t"] - offset - delay)
            if 0 <= i < n:
                cnt[i] += 1; rx[i] += max([w for r, w in CATS if r.search(c["m"])] or [0])
                if kw and any(k in c["m"].lower() for k in kw): kh[i] += 1
        cz, rxz = rz(sm(cnt, 10), 600, 0.3), rz(sm(rx, 10), 600, 0.3)
        kwz = sm(kh, 15) * 3
        sc = sm(0.35 * cz + 0.45 * rxz + 0.3 * az + kwz, 5)
        job.log("chat_n", len(chat), int(delay))
    else:
        sc = az.copy()
    vbonus = np.zeros(n)
    for off, du, views in vclips:  # клипы зрителей — самый надёжный сигнал
        i = int(off - offset + min(du, 30) * 0.6)
        if 0 <= i < n: vbonus[max(0, i - 3):i + 4] += 8 + min(6, math.log10(views + 1) * 2)
    sc = sc + vbonus
    s = sc.copy()
    if offset == 0: s[:min(o["skip_start"], n // 10)] = -1e9
    s[muted] = -1e9
    for a, b in used:  # уже нарезанное
        s[max(0, int(a - offset - 20)):max(0, int(b - offset + 20))] = -1e9
    gap = max(o["min_len"] + 15, 45)
    pk, lo = [], o["min_score"] * 0.6
    for i in np.argsort(-s):
        if s[i] < lo or len(pk) >= int(o["max_clips"] * 2): break
        if all(abs(i - q) >= gap for q in pk): pk.append(int(i))
    out = []
    for p in sorted(pk):
        msgs = [c["m"].strip().lower() for c in chat if p - 3 <= c["t"] - offset - delay <= p + 15]
        top = ", ".join(f"{t[:14]}×{k}" if k > 1 else t[:14] for t, k in Counter(msgs).most_common(3))
        out.append({"peak": round(p + offset, 2), "score": round(float(sc[p]), 2), "chat": top,
                    "f": {"chat": round(float(cz[p]), 2), "react": round(float(rxz[p]), 2), "audio": round(float(az[p]), 2),
                          "vclip": 1.0 if vbonus[p] > 0 else 0.0, "kw": round(float(kwz[p]), 2)}})
    keep = min(o["max_clips"], max(1, sum(1 for c in out if c["score"] >= o["min_score"])))
    return out, keep


# ------------------------------------------------------------------ whisper
_M = {}


_MLOCK = __import__("threading").Lock()


def whisper(o, job):
    pf = perf(); key = f'{o["model"]}|{pf["asr"]}|{pf["asr_threads"]}'  # зміна швидкості в налаштуваннях — нова модель
    with _MLOCK:  # паралельні потоки не повинні вантажити модель двічі
        if key not in _M:
            try: import av  # noqa
            except Exception:  # Smart App Control может блокировать PyAV — он не нужен
                for k in [k for k in sys.modules if k == "av" or k.startswith("av.")]: del sys.modules[k]
                sys.modules["av"] = types.ModuleType("av")
            job.log("model", o["model"])
            from faster_whisper import WhisperModel
            for k in [k for k in _M if "|" in k]: del _M[k]  # стара модель звільняє пам'ять
            _M[key] = WhisperModel(o["model"], device=os.environ.get("SC_DEVICE", "cpu"), compute_type="int8",
                                   cpu_threads=pf["asr_threads"], num_workers=pf["asr"])
        return _M[key]


def pcm(src, s, d, r=16000):
    import subprocess
    p = subprocess.run([ffmpeg(), "-v", "error", "-ss", str(max(0, s)), "-i", str(src), "-t", str(d), "-vn", "-ac", "1",
                        "-ar", str(r), "-f", "s16le", "-"], capture_output=True, **popen_kw())
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)


def words_of(src, start, dur, o, job, src_offset=0.0):
    """Слова с абсолютным временем. src_offset — время начала файла src в таймлайне стрима."""
    if _M.get("err"): return []
    try:
        mdl = whisper(o, job)
        audio = pcm(src, start - src_offset, dur) / 32768.0
        kw = dict(language=o.get("lang") or o.get("_lang") or None, word_timestamps=True, beam_size=1, condition_on_previous_text=False)
        try: segs, info = mdl.transcribe(audio, vad_filter=True, **kw); segs = list(segs)
        except Exception: segs, info = mdl.transcribe(audio, vad_filter=False, **kw); segs = list(segs)
        if not o.get("lang") and not o.get("_lang") and getattr(info, "language_probability", 0) > 0.6:
            o["_lang"] = info.language; job.log("lang", info.language)
        return [{"s": round(x.start + start, 2), "e": round(x.end + start, 2), "w": x.word.strip()}
                for g in segs for x in (g.words or []) if x.word.strip()]
    except Cancelled:
        raise
    except Exception as ex:
        _M["err"] = 1; job.log("subs_off", str(ex)[:200])
        return []


def _sent_end(ws, i): return i == len(ws) - 1 or ws[i]["w"][-1:] in ".?!…" or ws[i + 1]["s"] - ws[i]["e"] > 0.8
def _sent_start(ws, i): return i == 0 or ws[i - 1]["w"][-1:] in ".?!…" or ws[i]["s"] - ws[i - 1]["e"] > 0.7


def bounds(p, ws, lo, hi, mn, mx):
    """Границы клипа вокруг пика p по фразам; lo/hi — пределы доступного звука."""
    lead = min(30, max(8, 0.4 * mn))
    st = [w["s"] for i, w in enumerate(ws) if p - lead - 12 <= w["s"] <= p - 4 and _sent_start(ws, i)]
    s = min(st, key=lambda t: abs(t - (p - lead))) - 0.15 if st else p - lead
    s = max(lo, s)
    en = [w["e"] for i, w in enumerate(ws) if p + 3 <= w["e"] <= s + mx and _sent_end(ws, i)]
    e = (min(en) + 0.6) if en else p + 10
    while e - s < mn and en:
        nxt = [t for t in en if t + 0.6 > e]
        if not nxt: break
        e = min(nxt) + 0.6
    e = max(e, s + mn); e = min(e, s + mx, hi)
    return round(s, 2), round(e, 2)


FEATS = ["chat", "react", "audio", "vclip", "kw", "wps", "emo", "promo"]


def rerank(c, o, streamer=""):
    """Итоговая оценка: сигналы + анализ речи под язык + поправки из оценок пользователя."""
    dur = max(1.0, c["end"] - c["start"]); txt = " ".join(w["w"] for w in c["words"])
    emo_re, promo_re = lex(o.get("lang") or o.get("_lang"))
    wps = len(c["words"]) / dur
    emo = min(len(emo_re.findall(txt)) / dur * 10, 4)
    promo = len(promo_re.findall(txt))
    kwt = sum(txt.lower().count(k.lower()) for k in o.get("keywords") or [] if k.strip())
    c["f"].update(wps=round(min(wps, 3), 2), emo=round(emo, 2), promo=float(promo))
    f = c["score"] + 0.8 * min(wps, 3) + 0.7 * emo - 3 * promo + 2 * min(kwt, 3)
    if o.get("speech_filter", True) and c["f"].get("vclip", 0) == 0:
        if wps < 0.6 and emo < 0.5: f -= 4          # стример почти молчит
        if promo >= 2: f -= 4                        # реклама / донаты
    w = (settings().get("learn") or {}).get(streamer.lower(), {}).get("w") if streamer else None
    if w: f += sum(w.get(k, 0) * c["f"].get(k, 0) for k in FEATS)
    return round(f, 2)


def learn(streamer, feats, good):
    s = settings(); L = s.get("learn") or {}
    e = L.setdefault(streamer.lower(), {"w": {}, "n": 0})
    y = 1 if good else -1
    for k in FEATS:
        v = max(-3, min(3, float(feats.get(k, 0))))
        e["w"][k] = round(max(-2, min(2, e["w"].get(k, 0) + 0.12 * y * v)), 3)
    e["n"] += 1
    save_settings({"learn": L})
    return e


def sequential(ws, lo, hi, mn, mx):
    ends = [w["e"] for i, w in enumerate(ws) if _sent_end(ws, i)]
    out, s = [], lo
    while s < hi - 3:
        tgt = s + (mn + mx) / 2
        c = [t for t in ends if s + mn <= t <= s + mx]
        e = (min(c, key=lambda t: abs(t - tgt)) + 0.35) if c else min(s + mx, hi)
        if hi - e < mn * 0.5: e = hi
        out.append((round(s, 2), round(min(e, hi), 2))); s = e
    return out
