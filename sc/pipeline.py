"""Сценарии: запись/VOD, лайв-нарезка, проверка кандидатов, ещё N клипов, очистка, история."""
import json, math, os, re, shutil, subprocess, threading, time
from pathlib import Path

from .core import (perf, OUT_ROOT, YTDLP, Cancelled, capture, dur_of, enforce_disk_limit, ffmpeg, hms, jload, jsave, notify,
                   popen_kw, run_proc, settings, slug, source_files, dir_size)
from . import sources as S
from .analysis import bounds, find_peaks, loudness, loudness_par, rerank, sequential, words_of
from .render import compilation, render_clip, segment
from . import render as R
from concurrent.futures import ThreadPoolExecutor

_POOL = ThreadPoolExecutor(8)  # фонові завдання (чат паралельно із завантаженням)


def _pmap(fn, items, workers):
    """map із заданою кількістю потоків; порядок результатів зберігається, помилка/скасування — одразу далі."""
    items = list(items)
    if workers <= 1 or len(items) <= 1: return [fn(x) for x in items]
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fn, x) for x in items]
        try:
            return [f.result() for f in futs]
        except BaseException:
            for f in futs: f.cancel()
            raise

HISTORY = OUT_ROOT / "history.json"


class Job:
    def __init__(self, jid, url, opts, info=None, kind="clip"):
        self.id, self.url, self.opts, self.info, self.kind = jid, url, opts or {}, info, kind
        self.stage, self.args, self.pct, self.sub = "prep", [], 0.0, ""
        self.state, self.err = "running", None
        self.logs, self.results, self.dir, self.cancel, self.stop = [], [], None, False, False
        self.candidates, self.review_ev, self.live, self.extra = None, threading.Event(), False, {}
        self.t0 = time.time()

    def log(self, key, *a):
        self.logs.append({"t": time.strftime("%H:%M:%S"), "k": key, "a": [str(x) for x in a]}); self.logs = self.logs[-300:]
        print(time.strftime("%H:%M:%S"), key, *a, flush=True)

    def step(self, key, pct=None, *a):
        self.stage, self.args, self.sub = key, [str(x) for x in a], ""
        if pct is not None: self.pct = pct
        self.log(key, *a)

    def check(self):
        if self.cancel: raise Cancelled()

    def view(self):
        return {"id": self.id, "stage": self.stage, "args": self.args, "sub": self.sub, "pct": round(self.pct, 1), "state": self.state,
                "err": self.err, "logs": self.logs[-14:], "results": self.results, "dir": str(self.dir or ""), "live": self.live,
                "elapsed": int(time.time() - self.t0), "src_dur": (self.info or {}).get("duration") or self.extra.get("src_dur") or 0,
                "candidates": self.candidates if self.state == "review" else None, "extra": self.extra, "url": self.url,
                "opts": {k: v for k, v in (self.opts or {}).items() if k.startswith(("edit", "music", "nick", "_music"))},
                "title": (self.info or {}).get("title"), "uploader": (self.info or {}).get("uploader")}


DEFAULTS = {"mode": "auto", "min_len": 60, "max_len": 90, "max_clips": 40, "min_score": 2.0, "layout": "blur", "cam": None,
            "subs": True, "model": None, "lang": None, "live_minutes": 0, "skip_start": 180, "seq_limit": 0,
            "speech_filter": True, "review": False, "censor": False, "censor_mode": "beep", "sub_style": {},
            "hook": False, "zoom": False, "watermark": False, "cover": False, "denoise": False, "compile": False, "compile_min": 8, "teaser_sec": 30, "music": None, "music_start": None, "edit_sec": 15, "edit_textmode": "lyrics", "edit_text": "", "edit_grade": "auto", "edit_style": "auto", "edit_ar": "4:3", "edit_mode": "normal", "edit_loop": True, "edit_vocal_ramp": True, "edit_behind": True, "edit_mute": False, "nick": "", "edit_delogo": True,
            "vclips": False, "keywords": [], "history": None, "gpu": None, "live_every": 8}


def opts_of(o):
    r = dict(DEFAULTS); r.update({k: v for k, v in (o or {}).items() if v is not None and v != ""})
    for k in ("min_len", "max_len", "min_score"): r[k] = float(r[k])
    for k in ("max_clips", "live_minutes", "skip_start", "seq_limit", "live_every"): r[k] = int(float(r[k]))
    if r["max_len"] < r["min_len"]: r["max_len"] = r["min_len"]
    if not r["model"]: r["model"] = settings().get("model") or "small"
    if r["history"] is None: r["history"] = settings().get("history", True)
    if isinstance(r["keywords"], str): r["keywords"] = [k for k in re.split(r"[,;\n]", r["keywords"]) if k.strip()]
    return r


# ------------------------------------------------------------------ история (без повторов)
def history_used(streamer, vid):
    h = jload(HISTORY, {}) or {}
    return [(x["s"], x["e"]) for x in h.get(streamer.lower(), []) if str(x.get("vid")) == str(vid)]


def history_add(streamer, vid, clips):
    if not streamer: return
    h = jload(HISTORY, {}) or {}
    L = h.setdefault(streamer.lower(), [])
    L += [{"vid": str(vid), "s": c["s"] if "s" in c else c["start"], "e": c["e"] if "e" in c else c["end"], "at": int(time.time())} for c in clips]
    h[streamer.lower()] = L[-5000:]; jsave(HISTORY, h)


def history_vods(streamer):
    return {x["vid"] for x in (jload(HISTORY, {}) or {}).get(streamer.lower(), [])}


# ------------------------------------------------------------------ основной вход
def check_fresh():
    """Код на диску новіший за запущений процес (оновили, але не перезапустили) — нові правки НЕ працюють. Кажемо прямо."""
    from . import core as C
    try: disk = (C.APP_DIR / "VERSION").read_text().strip()
    except Exception: return
    if disk and disk != C.VERSION:
        print(f"\n!!! Програму оновлено до {disk}, а працює {C.VERSION}. Закрий це вікно й запусти Start-Windows.bat / Start-Mac.command ще раз.\n", flush=True)
        raise RuntimeError(f"Програму оновлено до {disk}, а працює стара {C.VERSION} — закрий вікно програми й запусти її знову")


def run_job(job):
    try:
        check_fresh()
        if job.kind == "more": _more(job)
        else: _run(job)
        if job.state == "running": job.state = "done"
        job.stage, job.pct = "done", 100
        job.args = [str(len(job.results))]
        try: notify("StreamClipper", f"{len(job.results)} ✓ {(job.info or {}).get('title') or ''}"[:120])
        except Exception: pass
    except Cancelled:
        job.state, job.stage = "cancelled", "cancelled"
    except Exception as e:
        import traceback; traceback.print_exc()
        tb = traceback.extract_tb(e.__traceback__)
        tb = [f for f in tb if "StreamClipper" in f.filename or "/sc/" in f.filename.replace("\\", "/") or "\\sc\\" in f.filename] or tb
        where = f" [{Path(tb[-1].filename).name}:{tb[-1].lineno}]" if tb else ""
        msg = (str(e).strip().splitlines() or [type(e).__name__])[-1][:300]
        job.state, job.err = "error", msg + where
        job.log("error", job.err)
    finally:
        if job.dir:
            job.extra["sources_bytes"] = sum(f.stat().st_size for f in source_files(job.dir) if f.exists())
            try: enforce_disk_limit(keep=job.dir)
            except Exception: pass


def _find_sources(info, full):
    """Чи вже качали цей запис: попередня папка з тим самим відео, де вихідники ще на місці. -> dict або None."""
    vid = str(info.get("video_id") or info.get("url") or "")
    if not vid or info.get("platform") == "file": return None
    for md in sorted(OUT_ROOT.glob("*/meta.json"), reverse=True):
        m = jload(md, {}) or {}
        if str(m.get("vid")) != vid or m.get("sources_deleted"): continue
        a, v = m.get("audio"), m.get("video")
        if not a or not Path(a).exists(): continue
        if full and not (v and Path(v).exists()): continue
        chat = md.parent / "chat.jsonl"
        return {"audio": a, "video": v if v and Path(v).exists() else None, "dir": str(md.parent),
                "chat": str(chat) if chat.exists() and chat.stat().st_size > 0 else None}
    return None


def _new_dir(info):
    name = slug(f"{info.get('uploader') or ''}_{info.get('title') or ''}", 50)
    d = OUT_ROOT / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}"; (d / "out").mkdir(parents=True, exist_ok=True)
    return d


def _run(job):
    o = opts_of(job.opts); job.opts = o
    info = job.info or S.probe(job.url)
    job.info = info
    mode = o["mode"]
    if mode == "auto": mode = "best" if info["platform"] in ("twitch", "kick") or info.get("has_chat") else "seq"
    o["mode"] = mode
    d = _new_dir(info); job.dir = d
    meta = {"info": info, "opts": o, "url": info.get("url"), "uploader": info.get("uploader") or "", "platform": info["platform"],
            "vid": info.get("video_id") or info.get("url"), "audios": []}
    ffmpeg()
    if info.get("kind") in ("live", "live_vod"):
        job.live = True
        return _live(job, d, meta, o)
    # 1) источник
    if info["platform"] == "file":
        meta.update(video=info["url"], audio=info["url"], duration=info["duration"])
    chat_f = None
    if info["platform"] != "file":
        full = info["platform"] not in ("twitch",) or mode == "seq"
        prev = _find_sources(info, full)
        if prev and prev.get("chat"): meta["_reuse_chat"] = prev["chat"]
        if mode in ("best", "compile", "teaser", "edit") and info.get("duration"):
            # чат качається одночасно зі звуком/відео — вони не залежать одне від одного
            chat_f = _POOL.submit(_chat, job, d, meta, info, 0, info["duration"], o, True)
        if prev:  # уже скачано раніше — беремо звідти, не качаємо вдруге
            meta["audio"] = prev["audio"]
            if prev["video"]: meta["video"] = prev["video"]
            meta["reused_from"] = prev["dir"]
            try: os.utime(prev["dir"])  # свіжа дата — ліміт місця не видалить її першою
            except OSError: pass
            vs = Path(prev["dir"]) / "vscan.json"
            if vs.exists(): shutil.copy(vs, d / "vscan.json")
            job.log("reuse_src", Path(prev["dir"]).name)
        else:
            _download(job, d, meta, info["url"], full, 0, None, 2, 30)
        meta["duration"] = info.get("duration") or dur_of(meta["audio"])
    jsave(d / "meta.json", meta)
    n = meta["duration"]
    # 2) выбор
    if mode == "compile":  # один горизонтальний ролик 16:9 заданої довжини з найкращих моментів
        target = max(1.0, float(o.get("compile_min") or 8)) * 60
        o["max_clips"] = int(math.ceil(target / ((o["min_len"] + o["max_len"]) / 2) * 1.6)) + 2
    if mode == "edit":
        if not o.get("music") or not Path(o["music"]).exists(): raise RuntimeError("no_music")
        o["max_clips"] = 18; o["min_len"], o["max_len"] = 8, 20; o["peak_gap"] = 15
    if mode == "teaser":  # потрібно ~ціль/2.6 с фраз, беремо з запасом; вікна аналізу короткі — швидше
        target = min(90.0, max(10.0, float(o.get("teaser_sec") or 30)))
        o["max_clips"] = int(math.ceil(target / 2.6 * 1.5)) + 3; o["min_len"], o["max_len"] = 8, 20; o["peak_gap"] = 15
    if mode in ("best", "compile", "teaser", "edit"):
        if chat_f:
            if not chat_f.done(): job.step("download_chat", job.pct)
            chat = chat_f.result()
        else:
            chat = _chat(job, d, meta, info, 0, n, o)
        if mode == "edit":  # едіт під музику: текст береться з пісні, розпізнавати мову всього відео не потрібно
            clips = _select_fast(job, meta, o, 0, n, chat)
        else:
            clips = _select(job, d, meta, o, 0, n, chat, used=history_used(meta["uploader"], meta["vid"]) if o["history"] else [])
    else:
        clips = _sequential(job, meta, o, n)
    if mode == "teaser":
        return _teaser_only(job, d, meta, o, clips)
    if mode == "edit":
        return _edit_only(job, d, meta, o, clips)
    clips = _review(job, clips)
    if mode == "compile":
        return _compile_only(job, d, meta, o, clips)
    _render_all(job, d, meta, o, clips)


def _compile_only(job, d, meta, o, clips):
    if not clips: job.log("no_more"); return
    for k, c in enumerate(clips, 1): c["k"] = k
    jsave(d / "clips.json", [{k: v for k, v in c.items() if k != "_ws"} for c in clips])
    job.step("compile", 70)
    p = compilation(d, meta, clips, o, job, max_min=float(o.get("compile_min") or 8))
    if not p: raise RuntimeError("compile_err")
    job.extra["compilation"] = p; job.extra["compilation_dur"] = round(dur_of(p))
    meta["compilation"] = p; meta["compilation_dur"] = job.extra["compilation_dur"]; jsave(d / "meta.json", meta)
    if o["history"]: history_add(meta["uploader"], meta["vid"], clips)
    shutil.rmtree(d / "out" / "_w", ignore_errors=True); shutil.rmtree(d / "raw", ignore_errors=True)


def _phrase(c):
    """Найкраща коротка фраза (1.6–4 с) біля піку моменту, по межах слів."""
    p = c.get("peak") or (c["start"] + c["end"]) / 2
    ws = sorted([w for w in (c.get("_ws") or c.get("words") or []) if w.get("w")], key=lambda w: w["s"])
    i = next((k for k, w in enumerate(ws) if w["e"] >= p - 0.8 and w["s"] <= p + 2.5), None)
    if i is None: return max(0.0, p - 1.2), p + 1.8, []
    st = ws[i]["s"] - 0.08; j = i
    while j + 1 < len(ws) and ws[j + 1]["e"] - st <= 4.0 and ws[j + 1]["s"] - ws[j]["e"] < 0.45: j += 1
    en = ws[j]["e"] + 0.15
    if en - st < 1.6: en = st + 2.2
    return st, en, ws[i:j + 1]


def _teaser_only(job, d, meta, o, clips):
    if not clips: job.log("no_more"); return
    target = min(90.0, max(10.0, float(o.get("teaser_sec") or 30)))
    snips, tot = [], 0.0
    for c in sorted(clips, key=lambda c: -(c.get("final") or 0)):
        st, en, ws = _phrase(c)
        if any(abs(st - x["start"]) < 6 for x in snips): continue
        snips.append({"start": st, "end": en, "words": ws, "peak": (st + en) / 2, "final": c.get("final") or 0})
        tot += en - st
        if tot >= target: break
    top = max(snips, key=lambda x: x["final"])
    snips = sorted([x for x in snips if x is not top], key=lambda x: x["start"]) + [top]  # хронологія, найсильніше — в кінці
    for k, c in enumerate(snips, 1): c["k"] = k
    jsave(d / "clips.json", snips)
    job.step("teaser", 70)
    p = R.teaser(d, meta, snips, o, job)
    if not p: raise RuntimeError("compile_err")
    job.extra["teaser"] = p; job.extra["teaser_dur"] = round(dur_of(p))
    meta["teaser"] = p; meta["teaser_dur"] = job.extra["teaser_dur"]; jsave(d / "meta.json", meta)
    shutil.rmtree(d / "out" / "_w", ignore_errors=True); shutil.rmtree(d / "raw", ignore_errors=True)


def _edit_pool(meta, clips):
    """Моменти для едіту: найкращі + (для відео) рівномірні проби, щоб кадри були різні."""
    pool = []
    for c in clips:
        pk = c.get("peak") or (c["start"] + c["end"]) / 2
        pool.append({"peak": pk, "final": float(c.get("final") or 1), "lo": pk - 6, "hi": pk + 6})
    if meta.get("video"):
        n = meta.get("duration") or 0
        step = max(4.0, n / 40)
        for k in range(int(n // step)):
            t = 3 + k * step
            if t < n - 3 and all(abs(t - p["peak"]) > 4 for p in pool): pool.append({"peak": t, "final": 0.1, "lo": 0, "hi": n})
        for p in pool: p["lo"], p["hi"] = 0, n
    return pool


def _extra_sources(job, d, o):
    """Посилання на інші ролики (або шляхи до файлів) → качаємо відео в d/x_<hash>/ і додаємо як друге джерело кадрів."""
    import hashlib
    urls = [u.strip() for u in (o.get("edit_extra_urls") or []) if u and u.strip()]
    dirs = list(o.get("edit_extra") or [])
    for k, u in enumerate(urls[:5]):
        xd = d / ("x_" + hashlib.sha1(u.encode("utf-8")).hexdigest()[:8]); xd.mkdir(parents=True, exist_ok=True)
        xm = jload(xd / "meta.json", {}) or {}
        if not (xm.get("video") and Path(xm["video"]).exists()):
            try:
                if Path(u).exists():
                    xm = {"video": str(Path(u).resolve()), "info": {"title": Path(u).stem}}
                else:
                    job.log("edit_extra_dl", k + 1, len(urls))
                    xm = {"audios": [], "info": {"title": u}}
                    _download(job, xd, xm, u, True, 0, 0, 52 + 6 * k / len(urls), 52 + 6 * (k + 1) / len(urls))
                xm["duration"] = dur_of(xm["video"]) or 0
                jsave(xd / "meta.json", xm)
            except Cancelled:
                raise
            except Exception as e:
                job.log("edit_extra_err", u[:60], str(e)[:80]); continue
        if str(xd) not in dirs: dirs.append(str(xd))
    o["edit_extra"] = dirs


def _edit_only(job, d, meta, o, clips, seed=1, name="edit_9x16.mp4"):
    from . import edit as E
    if o.get("edit_extra_urls"): _extra_sources(job, d, o)
    pool = _edit_pool(meta, clips or []) if clips is not None else jload(d / "edit_pool.json", [])
    if not pool: raise RuntimeError("no_more")
    jsave(d / "edit_pool.json", pool)
    p, dur = E.make_edit(d, meta, pool, o, job, seed=seed, name=name)
    job.extra["edit"] = p; job.extra["edit_dur"] = round(dur)
    info = dict(job.extra.get("edit_info") or {})
    info["thumbs"] = _edit_thumbs(p, info.get("cuts") or [])
    keep = {k: o.get(k) for k in ("edit_extra", "edit_ref", "lyrics_fix", "edit_ban", "edit_pin") if o.get(k)}
    job.extra["edit_info"] = info
    meta.setdefault("edits", []).append({"file": p, "dur": round(dur), "info": info, "o": keep, "project": job.extra.get("edit_project")})
    meta["edit"] = p; meta["edit_dur"] = round(dur)
    jsave(d / "meta.json", meta)
    shutil.rmtree(d / "out" / "_e", ignore_errors=True)


def _edit_thumbs(p, cuts):
    """Превʼю кожного кадру едіту (середина шоту) — для заміни окремих кусків і лайків."""
    p = Path(p); td = p.with_name("_th_" + p.stem); shutil.rmtree(td, ignore_errors=True); td.mkdir(parents=True, exist_ok=True)
    out = []
    for k, (a, b) in enumerate(cuts):
        f = td / f"{k:03d}.jpg"
        subprocess.run([ffmpeg(), "-y", "-v", "error", "-ss", f"{(a + b) / 2:.2f}", "-i", str(p), "-frames:v", "1", "-vf", "scale=160:-2", "-q:v", "5", str(f)],
                       capture_output=True)
        out.append(str(f) if f.exists() else "")
    return out


def reedit(d, job, extra=None, same=False, count=1):
    """«Ще варіант»: той самий трек і моменти, інша розкладка кадрів і стилів тексту.
    same=True — той самий план (seed) з новими налаштуваннями: правка тексту, заміна окремих кадрів.
    count — кілька варіантів підряд."""
    d = Path(d); meta = jload(d / "meta.json", {}) or {}
    o = opts_of(meta.get("opts") or {})
    last = (meta.get("edits") or [{}])[-1]
    if extra:
        o.update({k: v for k, v in extra.items() if v is not None})
    job.opts = o; job.dir = d
    for c in range(max(1, min(5, int(count or 1)))):
        meta = jload(d / "meta.json", {}) or {}
        n = len(meta.get("edits", [])) + 1
        seed = (last.get("info") or {}).get("seed") if same else None
        job.step("edit_beats", 60, str(c + 1), str(count or 1))
        _edit_only(job, d, meta, dict(o), None, seed=seed or n * 7 + 3, name=f"edit_9x16_v{n}.mp4")


def _download(job, d, meta, url, full, a, b, p0, p1):
    hmax = 2160 if (job.opts or {}).get("mode") == "edit" else 1080  # для едіту кроп 9:16 — потрібно більше пікселів
    fmt = f"bv*[height<={hmax}]+ba/b[height<={hmax}]/b" if full else "Audio_Only/bestaudio/worst"
    job.step("download_video" if full else "download_audio", p0)

    def on(line):
        m = re.search(r"([\d.]+)%", line)
        if m and "[download]" in line:
            job.pct = p0 + (p1 - p0) * float(m.group(1)) / 100; job.sub = line.split("]", 1)[-1].strip()[:60]
    tag = "video" if full else "audio"
    if a or b: tag += f"_{int(a)}"
    cmd = [*YTDLP, "--newline", "--no-warnings", "--no-part", "-N", str(perf()["net"]), "-f", fmt, "--ffmpeg-location", ffmpeg(),
           "--merge-output-format", "mp4", "-o", str(d / f"{tag}.%(ext)s")]
    if a or b: cmd += ["--download-sections", f"*{int(a)}-{int(b) if b else 'inf'}"]
    run_proc(cmd + [url], job, on)
    f = next(p for p in sorted(d.glob(tag + ".*")) if p.suffix not in (".part", ".ytdl", ".json"))
    path = str(f.resolve())
    if a or b:
        meta["audios"].append({"path": path, "start": float(a), "end": float(b or a + dur_of(path))})
        if full: meta.setdefault("videos", []).append({"path": path, "start": float(a)})
    else:
        meta["audio"] = path
        if full: meta["video"] = path
    return path


def _chat(job, d, meta, info, a, b, o, background=False):
    rows = []
    old = meta.get("_reuse_chat")
    if old and a == 0 and Path(old).exists():  # чат цього запису вже качали раніше
        rows = [json.loads(x) for x in Path(old).read_text(encoding="utf-8").splitlines() if x.strip()]
        shutil.copy(old, d / "chat.jsonl"); job.log("reuse_chat", len(rows))
        return rows
    if info["platform"] == "twitch" and info.get("video_id"):
        if not background: job.step("download_chat", job.pct)
        rows = S.twitch_chat(info["video_id"], a, b, job)
    elif info["platform"] == "youtube" and info.get("has_chat") and a == 0:
        if not background: job.step("download_chat", job.pct)
        rows = S.yt_live_chat(info["url"], d, job)
    if rows:
        with open(d / "chat.jsonl", "a", encoding="utf-8") as f:
            for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def _select(job, d, meta, o, a, b, chat, used=(), audio=None, audio_off=0.0, p0=35, p1=65):
    """Анализ окна [a,b] таймлайна; возвращает отобранные клипы."""
    if audio is None: audio, audio_off = meta["audio"], meta.get("audio_offset", 0.0)
    used = list(used) + [tuple(x) for x in (meta.get("info") or {}).get("skip", [])]  # реклама (SponsorBlock) — не брати
    job.step("analyze", p0)
    pf = perf()
    loud = loudness_par(audio, int(math.ceil(b - a)) + 1, job, start=a - audio_off, dur=b - a, parts=pf["loud"])
    vcl = []
    if o.get("vclips") and meta["platform"] == "twitch" and meta["info"].get("video_id"):
        vcl = S.viewer_clips(meta["uploader"], meta["info"]["video_id"])
        job.log("vclips", len(vcl))
    cands, keep = find_peaks(job, loud, chat, o, offset=a, vclips=vcl, used=used, keywords=o.get("keywords") or [])
    job.log("cands", len(cands), keep)
    done = [0]; lock = threading.Lock()

    def one(c):
        job.check()
        p = c["peak"]; w0 = max(a, p - 45)
        ws = words_of(audio, w0, min(b, p + o["max_len"]) - w0, o, job, src_offset=audio_off)
        with lock:
            done[0] += 1; job.pct = p0 + (p1 - p0) * done[0] / max(1, len(cands)); job.stage, job.args = "speech", [str(done[0]), str(len(cands))]
        s, e = bounds(p, ws, a, b, o["min_len"], o["max_len"])
        if e - s < o["min_len"] * 0.6: return None  # обрізаний край запису (кінець відео / ще не записаний лайв)
        cl = {"start": s, "end": e, "words": [w for w in ws if s - 0.2 <= w["s"] < e], "_ws": ws, **c}
        cl["final"] = rerank(cl, o, meta["uploader"]); return cl
    if cands and not o.get("_lang") and not o.get("lang"): first = [one(cands[0])]; rest = cands[1:]  # перший визначає мову
    else: first, rest = [], cands
    out = [c for c in first + _pmap(one, rest, pf["asr"]) if c]
    out = sorted(sorted(out, key=lambda c: -c["final"])[:keep], key=lambda c: c["start"])
    job.extra["review_src"] = {"path": audio, "off": audio_off}
    return out


def _select_fast(job, meta, o, a, b, chat):
    """Без розпізнавання мови: лише гучність/чат → моменти, де щось відбувається (для едіту під музику)."""
    used = [tuple(x) for x in (meta.get("info") or {}).get("skip", [])]
    job.step("analyze", 35)
    audio, audio_off = meta["audio"], meta.get("audio_offset", 0.0)
    loud = loudness_par(audio, int(math.ceil(b - a)) + 1, job, start=a - audio_off, dur=b - a, parts=perf()["loud"])
    cands, keep = find_peaks(job, loud, chat, o, offset=a, used=used)
    job.log("cands", len(cands), keep)
    return [{"start": max(a, c["peak"] - 6), "end": min(b, c["peak"] + 6), "final": float(c.get("score") or 1), **c} for c in cands]


def _sequential(job, meta, o, n):
    lim = o["seq_limit"] or 10 ** 6
    end = min(n, o["max_len"] * lim * 1.2) if o["seq_limit"] else n
    ws = []
    if o["subs"]:
        starts = list(range(0, int(math.ceil(end)), 300)); done = [0]; lock = threading.Lock()

        def one(t):
            job.check(); r = words_of(meta["audio"], t, min(300, end - t), o, job)
            with lock:
                done[0] += 1; job.pct = 35 + 30 * done[0] / max(1, len(starts)); job.stage, job.args = "speech_seq", [hms(min(end, done[0] * 300)), hms(end)]
            return r
        if starts and not o.get("_lang") and not o.get("lang"): ws += one(starts.pop(0))
        for r in _pmap(one, starts, perf()["asr"]): ws += r
    parts = sequential(ws, 0, end, o["min_len"], o["max_len"])[:lim]
    job.log("parts", len(parts)); job.extra["review_src"] = {"path": meta["audio"], "off": 0.0}
    return [{"start": s, "end": e, "words": [w for w in ws if s - 0.2 <= w["s"] < e], "score": 0, "final": 0, "chat": "", "f": {}} for s, e in parts]


def _review(job, clips):
    if not job.opts.get("review") or not clips: return clips
    job.candidates = [{"i": i, "start": c["start"], "end": c["end"], "text": " ".join(w["w"] for w in c["words"])[:400],
                       "chat": c.get("chat", ""), "score": c.get("final"), "keep": True} for i, c in enumerate(clips)]
    job.extra["review_audio"] = None
    job.state = "review"; job.step("review", 65)
    while not job.review_ev.wait(1):
        job.check()
    job.state = "running"
    edits = job.extra.pop("review_result", None) or []
    out = []
    for ed in edits:
        if not ed.get("keep"): continue
        c = clips[int(ed["i"])]
        st, en = float(ed.get("start", c["start"])), float(ed.get("end", c["end"]))
        if en - st >= 5: c["start"], c["end"] = st, en
        c["words"] = [w for w in (c.get("_ws") or c["words"]) if c["start"] - 0.2 <= w["s"] < c["end"]]
        c["title"] = ed.get("title") or ""
        out.append(c)
    return out


def _render_all(job, d, meta, o, clips, start_k=None, p0=66, p1=98):
    for i, c in enumerate(clips, 1): c.setdefault("title", "")
    prev = jload(d / "clips.json", []) or []
    k0 = start_k or (max([c.get("k", 0) for c in prev] or [0]) + 1)
    for i, c in enumerate(clips): c["k"] = k0 + i
    jsave(d / "clips.json", prev + [{k: v for k, v in c.items() if k != "_ws"} for c in clips])
    tag = re.sub(r"\W", "", meta["uploader"]).lower()
    done = []; pf = perf(); lock = threading.Lock(); cnt = [0]
    # відео для наступних кліпів качається наперед, поки збираються попередні (мережа і процесор працюють одночасно)
    need_dl = not meta.get("video")
    pre = ThreadPoolExecutor(max(1, pf["prefetch"])) if need_dl else None
    segs = {c["k"]: pre.submit(segment, d, meta, c["start"], c["end"], f"c{c['k']}_{int(c['start'])}", job) for c in clips} if pre else {}

    def one(c):
        job.check()
        seg = segs[c["k"]].result() if segs else None
        r = render_clip(d, meta, c, o, job, c["k"], seg=seg)
        with lock:
            cnt[0] += 1; job.pct = p0 + (p1 - p0) * cnt[0] / max(1, len(clips)); job.stage, job.args = "render", [str(min(len(clips), cnt[0] + 1)), str(len(clips))]
            job.results.append(r); job.results.sort(key=lambda x: x["n"]); done.append(r)
            _captions(d, meta, job.results, tag); jsave(d / "results.json", job.results)
        return r
    job.stage, job.args = "render", ["1", str(len(clips))]
    try:
        _pmap(one, clips, pf["render"])
    finally:
        if pre:
            for f in segs.values(): f.cancel()
            pre.shutdown(wait=False)
    if o["history"]: history_add(meta["uploader"], meta["vid"], done)
    if o.get("compile") and len(job.results) >= 3 and not job.live:
        job.step("compile", 98)
        try:
            all_clips = [c for c in (jload(d / "clips.json", []) or [])]
            p = compilation(d, meta, all_clips, o, job)
            if p: job.extra["compilation"] = p
        except Cancelled:
            raise
        except Exception as e:
            job.log("compile_err", str(e)[:200])
    shutil.rmtree(d / "out" / "_w", ignore_errors=True)
    shutil.rmtree(d / "raw", ignore_errors=True)
    return done


def _captions(d, meta, results, tag):
    pf = meta.get("platform")
    lines = [f"{Path(r['file']).name}\n{(r.get('title') + ' ') if r.get('title') else ''}{('@' + meta['uploader']) if meta['uploader'] else ''} "
             f"{('#' + tag + ' ') if tag else ''}#{pf} #clips #fyp\n" for r in results]
    (Path(d) / "out" / "captions.txt").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ лайв
def _live(job, d, meta, o):
    info = meta["info"]; ch = info["uploader"]; pf = info["platform"]
    every = max(3, o["live_every"]) * 60
    limit = o["live_minutes"] * 60 if o["live_minutes"] else None
    t_start = time.time()
    used = history_used(ch, meta["vid"]) if o["history"] else []
    o["skip_start"] = 120
    if info["kind"] == "live_vod":
        # запись эфира доступна: сначала всё, что уже было, потом догоняем каждые N минут
        done_to = 0.0
        while True:
            job.check()
            try: cur = float(S.ytdlp_json(info["url"]).get("duration") or 0)
            except Exception: cur = done_to
            ended = not (S.channel_info(pf, ch) or {}).get("live")
            if cur - done_to >= min(every, 300) or (ended and cur > done_to + 30):
                a, b = max(0.0, done_to - 60 if done_to else 0.0), cur
                apath = _download(job, d, meta, info["url"], False, a, b, job.pct, job.pct)
                jsave(d / "meta.json", meta)
                chat = _chat(job, d, meta, info, a, b, o) if pf == "twitch" else []
                clips = _select(job, d, meta, o, a, b, chat, used=used + [(r["s"], r["e"]) for r in job.results], audio=apath, audio_off=a)
                clips = _review(job, clips)
                if clips: _render_all(job, d, meta, o, clips)
                done_to = b; job.extra["src_dur"] = b
            if ended or job.stop or (limit and time.time() - t_start > limit): break
            job.step("live_wait", job.pct, hms(done_to)); job.pct = 50
            for _ in range(every):
                if job.stop or job.cancel: break
                time.sleep(1)
            if job.cancel: raise Cancelled()
        return
    # записи нет: пишем эфир сами и режем по мере записи
    code, out, err = capture([*YTDLP, "-g", "--no-warnings", "-f", "best[height<=1080]/best", info.get("channel_url") or info["url"]], timeout=120)
    if code: raise RuntimeError(err[-300:])
    rec = d / "live.ts"; t0 = time.time()
    irc = S.IRC(ch, d / "chat.jsonl", t0) if pf == "twitch" else None
    cmd = [ffmpeg(), "-hide_banner", "-y", "-v", "error", "-i", out.split()[0], "-c", "copy"]
    if limit: cmd += ["-t", str(limit)]
    proc = subprocess.Popen([str(c) for c in cmd + [rec]], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_kw())
    meta.update(video=str(rec.resolve()), audio=str(rec.resolve()), duration=0)
    jsave(d / "meta.json", meta)
    done_to = 0.0
    try:
        while True:
            for _ in range(every):
                if job.stop or job.cancel or proc.poll() is not None: break
                job.stage, job.args = "live_rec", [hms(time.time() - t0)]
                time.sleep(1)
            if job.cancel: raise Cancelled()
            finished = proc.poll() is not None or job.stop
            if finished and proc.poll() is None:
                try: proc.stdin.write(b"q"); proc.stdin.flush(); proc.wait(10)
                except Exception: proc.kill()
            cur = dur_of(rec) - (0 if finished else 8)
            if cur - done_to > 90:
                a, b = max(0.0, done_to - 60 if done_to else 0.0), cur
                chat = [json.loads(l) for l in (d / "chat.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] if (d / "chat.jsonl").exists() else []
                clips = _select(job, d, meta, o, a, b, chat, used=[(r["s"], r["e"]) for r in job.results])
                clips = _review(job, clips)
                if clips: _render_all(job, d, meta, o, clips)
                done_to = b; job.extra["src_dur"] = b
            if finished: break
    finally:
        if proc.poll() is None: proc.kill()
        if irc: irc.close()
    meta["duration"] = dur_of(rec); jsave(d / "meta.json", meta)


# ------------------------------------------------------------------ ещё N клипов / пересборка
def _more(job):
    d = Path(job.url); meta = jload(d / "meta.json"); job.dir = d; job.info = meta["info"]
    if meta.get("sources_deleted"): raise RuntimeError("sources_deleted")
    o = {**meta["opts"], **{k: v for k, v in job.opts.items() if v is not None}}; o = opts_of(o)
    o["max_clips"] = int(job.opts.get("n") or 10); o["min_score"] = min(o["min_score"], 1.0)
    old = jload(d / "results.json", []) or []
    job.results = list(old)
    used = [(r["s"], r["e"]) for r in old if "s" in r]
    n = meta.get("duration") or dur_of(meta["audio"])
    chat = [json.loads(l) for l in (d / "chat.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] if (d / "chat.jsonl").exists() else []
    audio, aoff = meta.get("audio"), 0.0
    if not audio and meta.get("audios"):
        last = max(meta["audios"], key=lambda a: a["end"]); audio, aoff = last["path"], last["start"]
        n = last["end"]
    clips = _select(job, d, meta, o, aoff, n, chat, used=used, audio=audio, audio_off=aoff)
    if not clips: job.log("no_more"); return
    clips = _review(job, clips)
    _render_all(job, d, meta, o, clips)


def rerender(d, k, title, job):
    d = Path(d); meta = jload(d / "meta.json"); clips = jload(d / "clips.json"); o = opts_of(meta["opts"])
    if meta.get("sources_deleted"): raise RuntimeError("sources_deleted")
    c = next(x for x in clips if x.get("k") == k); c["title"] = title; jsave(d / "clips.json", clips)
    r = render_clip(d, meta, c, o, job, k)
    res = jload(d / "results.json", [])
    res = [x for x in res if x["n"] != k] + [r]; res.sort(key=lambda x: x["n"]); jsave(d / "results.json", res)
    _captions(d, meta, res, re.sub(r"\W", "", meta["uploader"]).lower())
    shutil.rmtree(d / "out" / "_w", ignore_errors=True)
    return r
