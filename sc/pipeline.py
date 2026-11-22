"""Сценарии: запись/VOD, лайв-нарезка, проверка кандидатов, ещё N клипов, очистка, история."""
import json, math, re, shutil, subprocess, threading, time
from pathlib import Path

from .core import (perf, OUT_ROOT, YTDLP, Cancelled, capture, dur_of, enforce_disk_limit, ffmpeg, hms, jload, jsave, notify,
                   popen_kw, run_proc, settings, slug, source_files, dir_size)
from . import sources as S
from .analysis import bounds, find_peaks, loudness, loudness_par, rerank, sequential, words_of
from .render import compilation, render_clip, segment
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
                "title": (self.info or {}).get("title"), "uploader": (self.info or {}).get("uploader")}


DEFAULTS = {"mode": "auto", "min_len": 60, "max_len": 90, "max_clips": 40, "min_score": 2.0, "layout": "blur", "cam": None,
            "subs": True, "model": None, "lang": None, "live_minutes": 0, "skip_start": 180, "seq_limit": 0,
            "speech_filter": True, "review": False, "censor": False, "censor_mode": "beep", "sub_style": {},
            "hook": False, "zoom": False, "watermark": False, "cover": False, "denoise": False, "compile": False, "compile_min": 8,
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
def run_job(job):
    try:
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
        job.state, job.err = "error", str(e)
        job.log("error", e)
    finally:
        if job.dir:
            job.extra["sources_bytes"] = sum(f.stat().st_size for f in source_files(job.dir) if f.exists())
            try: enforce_disk_limit(keep=job.dir)
            except Exception: pass


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
        if mode in ("best", "compile") and info.get("duration"):
            # чат качається одночасно зі звуком/відео — вони не залежать одне від одного
            chat_f = _POOL.submit(_chat, job, d, meta, info, 0, info["duration"], o, True)
        _download(job, d, meta, info["url"], full, 0, None, 2, 30)
        meta["duration"] = info.get("duration") or dur_of(meta["audio"])
    jsave(d / "meta.json", meta)
    n = meta["duration"]
    # 2) выбор
    if mode == "compile":  # один горизонтальний ролик 16:9 заданої довжини з найкращих моментів
        target = max(1.0, float(o.get("compile_min") or 8)) * 60
        o["max_clips"] = int(math.ceil(target / ((o["min_len"] + o["max_len"]) / 2) * 1.6)) + 2
    if mode in ("best", "compile"):
        if chat_f:
            if not chat_f.done(): job.step("download_chat", job.pct)
            chat = chat_f.result()
        else:
            chat = _chat(job, d, meta, info, 0, n, o)
        clips = _select(job, d, meta, o, 0, n, chat, used=history_used(meta["uploader"], meta["vid"]) if o["history"] else [])
    else:
        clips = _sequential(job, meta, o, n)
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


def _download(job, d, meta, url, full, a, b, p0, p1):
    fmt = "bv*[height<=1080]+ba/b[height<=1080]/b" if full else "Audio_Only/bestaudio/worst"
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
