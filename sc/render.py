"""Сборка клипа: макеты, качество, стили субтитров, цензура, хук, автозум, ник, обложка, компиляция, GPU."""
import math, re, shutil
from pathlib import Path

import numpy as np

from .core import perf, dur_of, FONT, YTDLP, ffmpeg, run_proc, capture, settings, gpu_encoder, slug, hms, src_fps
from .analysis import PROFANITY, pcm

# ------------------------------------------------------------------ стили субтитров
PRESETS = {  # primary, active, outline, back, border_style
    "classic": ("&H00FFFFFF", "&H0000E5FF", "&H00000000", "&H78000000", 1),
    "green":   ("&H00FFFFFF", "&H0066FF4C", "&H00000000", "&H78000000", 1),
    "minimal": ("&H00FFFFFF", "&H00FFFFFF", "&H40000000", "&H00000000", 1),
    "box":     ("&H00FFFFFF", "&H0000E5FF", "&H99000000", "&H99000000", 3),
}
SIZES = {"s": 66, "m": 82, "l": 98}


def sub_style(o):
    st = o.get("sub_style") or {}
    return {"preset": st.get("preset", "classic"), "size": st.get("size", "m"), "pos": st.get("pos", "auto"),
            "caps": st.get("caps", True), "words": int(st.get("words", 3)), "font": st.get("font", "Montserrat ExtraBold")}


MARG = {"blur": (560, 330), "cam": (1180, 40), "full": (420, 230), "land": (70, 40)}
RES = {"blur": (1080, 1920), "cam": (1080, 1920), "full": (1080, 1920), "land": (1920, 1080)}


def ts(t): t = max(0.0, t); return f"{int(t//3600)}:{int(t%3600//60):02d}:{t%60:05.2f}"
def esc(s): return s.replace("\\", "").replace("{", "(").replace("}", ")")


def censor_word(w):
    if not PROFANITY.search(w): return w
    core = re.sub(r"[^\w]", "", w)
    return w.replace(core, core[:1] + "*" * max(1, len(core) - 1)) if core else w


def ass(ws, title, dur, lay, o, nick=""):
    st = sub_style(o); W, H = RES[lay]
    prim, act, outl, back, bs = PRESETS.get(st["preset"], PRESETS["classic"])
    size = SIZES.get(st["size"], 82) * (0.8 if lay == "land" else 1)
    cv, tv = MARG[lay]
    align = 2
    if st["pos"] == "middle": align, cv = 5, 0
    elif st["pos"] == "top": align, cv = 8, 260 if lay != "cam" else 680
    font = st["font"]
    h = ("[Script Info]\nScriptType: v4.00+\n" f"PlayResX: {W}\nPlayResY: {H}\n" "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
         "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
         "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
         f"Style: C,{font},{size:.0f},{prim},{prim},{outl},{back},{0 if 'ExtraBold' in font else -1},0,0,0,100,100,0,0,{bs},{7 if bs == 1 else 14},{3 if bs == 1 else 0},{align},90,150,{cv},1\n"
         f"Style: T,{font},{72 if lay != 'land' else 56},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,20,0,8,90,150,{tv},1\n"
         f"Style: N,{font},{34 if lay != 'land' else 30},&H55FFFFFF,&H55FFFFFF,&H90000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,9,40,40,{40 if lay != 'cam' else 600},1\n\n"
         "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    if title: ev.append(f"Dialogue: 2,{ts(0)},{ts(dur if lay == 'blur' else min(dur, 4))},T,,0,0,0,,{esc(title)}")
    if nick: ev.append(f"Dialogue: 1,{ts(0)},{ts(dur)},N,,0,0,0,,{esc(nick)}")
    gs, cur, mw = [], [], max(1, min(3, st["words"]))
    for w in ws:
        t = w["w"]
        if o.get("censor"): t = censor_word(t)
        t = esc(t.upper() if st["caps"] else t)
        if cur and (len(cur) >= mw or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!…" or sum(len(x["t"]) for x in cur) + len(t) > 20):
            gs.append(cur); cur = []
        cur.append({**w, "t": t})
    if cur: gs.append(cur)
    big = "" if st["preset"] == "box" else "\\fscx112\\fscy112"
    a_on = "{\\c" + act + "&" + big + "}" if st["preset"] != "minimal" else "{\\fscx110\\fscy110\\alpha&H00&}"
    a_off = "{\\c" + prim + "&\\fscx100\\fscy100}" if st["preset"] != "minimal" else "{\\fscx100\\fscy100\\alpha&H50&}"
    for gi, g in enumerate(gs):
        nx = gs[gi + 1][0]["s"] if gi + 1 < len(gs) else dur
        for k, w in enumerate(g):
            s0 = w["s"]; en = max(g[k + 1]["s"] if k + 1 < len(g) else min(nx, w["e"] + 0.35), s0 + 0.08)
            pre = "" if st["preset"] != "minimal" else "{\\alpha&H50&}"
            txt = pre + " ".join(a_on + x["t"] + a_off if j == k else x["t"] for j, x in enumerate(g))
            ev.append(f"Dialogue: 0,{ts(s0)},{ts(en)},C,,0,0,0,,{txt}")
    return h + "\n".join(ev) + "\n"


# ------------------------------------------------------------------ видео-фильтры
def vf(lay, cam, quality=True):
    L = ":flags=lanczos" if quality else ""
    if lay == "land":
        return f"scale=1920:1080:force_original_aspect_ratio=decrease{L},pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
    if lay == "blur":
        return (f"split[a][b];[a]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,boxblur=16:2,"
                f"scale=1080:1920,eq=brightness=-0.10[bg];[b]scale=1080:-2{L}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    if lay == "full": return f"crop=trunc(ih*9/16/2)*2:ih,scale=1080:1920{L}"
    x, y, w, h = cam
    camf = (",hqdn3d=1.5:1.5:4:4,unsharp=5:5:0.7:5:5:0" if quality else "")
    return (f"split[a][b];[a]crop=trunc(iw*{w}/2)*2:trunc(ih*{h}/2)*2:iw*{x}:ih*{y},scale=1080:640:force_original_aspect_ratio=increase{L},"
            f"crop=1080:640{camf}[c];[b]crop=trunc(ih*27/32/2)*2:ih,scale=1080:1280{L}[g];[c][g]vstack")


def encoder_args(o):
    s = settings(); enc = gpu_encoder() if (o.get("gpu") if o.get("gpu") is not None else s.get("gpu")) else None
    common = ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if enc == "h264_nvenc": return enc, ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0", "-maxrate", "16M", "-bufsize", "32M", "-profile:v", "high"] + common
    if enc == "h264_qsv": return enc, ["-c:v", enc, "-global_quality", "20", "-look_ahead", "1"] + common
    if enc == "h264_amf": return enc, ["-c:v", enc, "-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "21"] + common
    if enc == "h264_videotoolbox": return enc, ["-c:v", enc, "-b:v", "14M", "-maxrate", "18M", "-profile:v", "high"] + common
    thr = ["-threads", str(max(1, (__import__("os").cpu_count() or 4) // 2))] if s.get("quiet") else []
    return "libx264", ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-maxrate", "14M", "-bufsize", "28M",
                       "-profile:v", "high", "-level", "4.2"] + thr + common


# ------------------------------------------------------------------ источник фрагмента
def align(raw, audio, g, audio_off=0.0, se=10, pr=20, r=8000):
    x = pcm(raw, 0, pr, r); s0 = max(audio_off, g - se); y = pcm(audio, s0 - audio_off, pr + 2 * se, r)
    if len(x) < r or len(y) < len(x) or np.abs(x).max() < 50: return g
    nf = 1 << int(math.ceil(math.log2(len(x) + len(y))))
    c = np.fft.irfft(np.fft.rfft(y, nf) * np.conj(np.fft.rfft(x, nf)), nf)[: len(y) - len(x) + 1]
    return s0 + int(np.argmax(c)) / r


def audio_for(meta, t):
    for a in meta.get("audios") or []:
        if a["start"] - 1 <= t <= a["end"] + 1: return a["path"], a["start"]
    return meta.get("audio"), meta.get("audio_offset", 0.0)


def segment(d, meta, s, e, tag, job, pad=4):
    """-> (файл, позиция начала клипа в нём)."""
    if meta.get("video"): return meta["video"], s - meta.get("video_offset", 0.0)
    raw = Path(d) / "raw" / f"{tag}.mp4"; raw.parent.mkdir(exist_ok=True); a0 = max(0, s - pad)
    if not raw.exists():
        run_proc([*YTDLP, "-q", "--no-progress", "--no-warnings", "-f", "bv*[height<=1080]+ba/b[height<=1080]/best", "-N", str(perf()["net"]),
                  "--ffmpeg-location", ffmpeg(), "--download-sections", f"*{a0:.0f}-{e + pad:.0f}",
                  "--merge-output-format", "mp4", "-o", str(raw), meta["url"]], job)
    ap, aoff = audio_for(meta, s)
    return str(raw), max(0.0, s - align(raw, ap, a0, aoff))


# ------------------------------------------------------------------ звук: цензура
def censor_af(ws, mode="beep"):
    """Тихий писк (~-24 дБ) или приглушение на матерных словах. ws — время относительно клипа."""
    iv = [(max(0, w["s"] - 0.03), w["e"] + 0.03) for w in ws if PROFANITY.search(w["w"])]
    if not iv: return None, None
    cond = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in iv)
    duck = f"volume=volume='if({cond},0.06,1)':eval=frame"
    beep = None if mode == "mute" else f"sine=f=880:sample_rate=48000,volume=volume='if({cond},0.07,0)':eval=frame"
    return duck, beep


def render_clip(d, meta, c, o, job, k, lay=None, out_name=None, hook=None, zoom=None, seg=None):
    """Собирает один клип. c: start/end (абс.), words, title, peak."""
    d = Path(d); lay = lay or o["layout"]
    hook = o.get("hook") if hook is None else hook
    zoom = o.get("zoom") if zoom is None else zoom
    s, e = c["start"], c["end"]; dur = e - s; title = c.get("title") or ""
    src, off = seg or segment(d, meta, s, e, f"c{k}_{int(s)}", job)
    W, H = RES[lay]
    words = [{"s": w["s"] - s, "e": w["e"] - s, "w": w["w"]} for w in c.get("words", []) if 0 <= w["s"] - s < dur] if o.get("subs", True) else []
    pk = min(max(0.5, (c.get("peak") or s + dur * 0.4) - s), dur - 0.5)
    hl = 1.6 if hook and dur > 8 else 0.0
    hs = max(0.0, pk - 0.9)
    sub_words = ([{**w, "s": w["s"] - hs, "e": w["e"] - hs} for w in words if hs <= w["s"] < hs + hl] if hl else []) + \
                [{**w, "s": w["s"] + hl, "e": w["e"] + hl} for w in words]
    total = dur + hl
    wk = d / "out" / "_w"; wk.mkdir(parents=True, exist_ok=True)
    if FONT.exists() and not (wk / FONT.name).exists(): shutil.copy(FONT, wk / FONT.name)
    nick = ""
    if o.get("watermark") and (o.get("nick") or meta.get("uploader")):
        nick = ("@" + o["nick"].strip().lstrip("@")) if o.get("nick") else {"twitch": "twitch.tv/", "kick": "kick.com/"}.get(meta.get("platform"), "") + meta["uploader"]
    (wk / f"s{k}.ass").write_text(ass(sub_words, title, total, lay, o, nick), encoding="utf-8")
    fps = min(60, max(24, round(src_fps(src)))) if not meta.get("_fps") else meta["_fps"]
    # --- граф
    g = []
    # хук — окремий вхід того ж файлу (split+trim буферизував би десятки секунд кадрів у RAM)
    if hl:
        g.append("[0:v]setpts=PTS-STARTPTS[hv];[1:v]setpts=PTS-STARTPTS[mv];[hv][mv]concat=n=2:v=1:a=0[vin]")
        g.append("[0:a]asetpts=PTS-STARTPTS[ha];[1:a]asetpts=PTS-STARTPTS[ma];[ha][ma]concat=n=2:v=0:a=1[ain]")
    else:
        g.append("[0:v]null[vin]"); g.append("[0:a]anull[ain]")
    g.append(f"[vin]{vf(lay, o.get('cam') or [0, 0, 0.25, 0.25])}[lv]")
    last = "lv"
    if zoom:
        zp = pk + hl
        Z = f"(1+0.10*exp(-pow((t-{zp:.2f})/0.9,2)))"
        g.append(f"[lv]scale=w='trunc({W}*{Z}/2)*2':h='trunc({H}*{Z}/2)*2':eval=frame:flags=bicubic,crop={W}:{H}[zv]"); last = "zv"
    g.append(f"[{last}]ass=s{k}.ass:fontsdir=.,fps={fps},format=yuv420p,setsar=1[v]")
    af = f"afade=t=in:d=0.05,afade=t=out:st={max(0, total - 0.35):.2f}:d=0.35"
    if o.get("denoise"): af = "afftdn=nf=-25," + af
    if o.get("censor") and words:
        duck, beep = censor_af(sub_words, o.get("censor_mode", "beep"))
        if duck:
            g.append(f"[ain]{duck}[dk]")
            if beep: g.append(f"{beep},atrim=0:{total:.3f}[bp];[dk][bp]amix=inputs=2:normalize=0:duration=first[cz]")
            else: g.append("[dk]anull[cz]")
            g.append(f"[cz]{af},loudnorm=I=-14:TP=-1.5:LRA=11[a]")
        else:
            g.append(f"[ain]{af},loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        g.append(f"[ain]{af},loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    name = d / "out" / (out_name or f"{k:02d}_{slug(title) if title else 'clip'}.mp4")
    if not out_name:
        for old in (d / "out").glob(f"{k:02d}_*.mp4"): old.unlink()
    enc, eargs = encoder_args(o)
    inp = (["-ss", f"{max(0, off + hs):.3f}", "-t", f"{hl:.3f}", "-i", Path(src).resolve()] if hl else []) + \
          ["-ss", f"{max(0, off):.3f}", "-t", f"{dur:.3f}", "-i", Path(src).resolve()]
    base = [ffmpeg(), "-y", "-v", "error", *inp, "-filter_complex", ";".join(g), "-map", "[v]", "-map", "[a]"]
    tail = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", name.resolve()]
    try:
        run_proc(base + eargs + tail, job, cwd=wk)
    except Exception:
        if enc == "libx264": raise
        job.log("gpu_fallback", enc)
        run_proc(base + encoder_args({**o, "gpu": False})[1] + tail, job, cwd=wk)
    pv = name.with_name(name.stem + "_preview.jpg")
    pt = (sub_words[len(sub_words) // 2]["s"] + 0.1) if sub_words else total * 0.45
    run_proc([ffmpeg(), "-y", "-v", "error", "-ss", f"{pt:.2f}", "-i", name, "-frames:v", "1", "-vf", "scale=360:-2", pv], job, check=False)
    cover = None
    if o.get("cover") and lay != "land":
        cover = name.with_name(name.stem + "_cover.jpg")
        (wk / f"c{k}.ass").write_text(cover_ass(title or (meta.get("uploader") or ""), lay), encoding="utf-8")
        run_proc([ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0, off + pk):.2f}", "-i", Path(src).resolve(), "-frames:v", "1",
                  "-filter_complex", f"[0:v]{vf(lay, o.get('cam') or [0, 0, .25, .25])},ass=c{k}.ass:fontsdir=.[v]", "-map", "[v]",
                  "-q:v", "2", cover.resolve()], job, cwd=wk, check=False)
    if not meta.get("video") and not o.get("keep_raw") and Path(src).parent.name == "raw": Path(src).unlink(missing_ok=True)
    return {"n": k, "file": str(name), "preview": str(pv), "cover": str(cover) if cover and cover.exists() else None,
            "start": hms(s), "s": s, "e": e, "dur": round(dur), "title": title, "text": " ".join(w["w"] for w in c.get("words", []))[:160],
            "score": c.get("final"), "f": c.get("f", {})}


def cover_ass(title, lay):
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: K,Montserrat ExtraBold,104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,9,4,5,80,80,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,0:00:10.00,K,,0,0,0,,{{\\c&H0000E5FF&}}{esc(title.upper())}\n")


def compilation(d, meta, clips, o, job, max_min=8):
    """Один горизонтальный ролик 16:9 из лучших моментов (для YouTube)."""
    d = Path(d); best = sorted(clips, key=lambda c: -(c.get("final") or 0))
    pick, tot = [], 0
    for c in best:
        if tot + (c["end"] - c["start"]) > max_min * 60: continue
        pick.append(c); tot += c["end"] - c["start"]
    pick.sort(key=lambda c: c["start"])
    if not pick: return None
    parts = []
    for i, c in enumerate(pick, 1):
        job.check(); job.sub = f"{i}/{len(pick)}"
        if job.stage == "compile": job.pct = max(job.pct, 70 + 28 * (i - 1) / len(pick))
        r = render_clip(d, meta, {**c, "title": ""}, {**o, "cover": False}, job, 900 + i, lay="land",
                        out_name=f"_part{i:02d}.mp4", hook=False, zoom=False)
        parts.append(Path(r["file"])); Path(r["preview"]).unlink(missing_ok=True)
    lst = d / "out" / "_w" / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    out = d / "out" / "compilation_16x9.mp4"
    run_proc([ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out], job)
    for p in parts: p.unlink(missing_ok=True)
    return str(out)


def teaser(d, meta, snips, o, job):
    """Короткий вертикальний тизер: фрази по 2–4 с, склейки з білим спалахом/наближенням, сповільнення останньої (кульмінації)."""
    d = Path(d); parts = []
    for i, c in enumerate(snips, 1):
        job.check(); job.sub = f"{i}/{len(snips)}"; job.pct = max(job.pct, 70 + 22 * (i - 1) / len(snips))
        r = render_clip(d, meta, {**c, "title": ""}, {**o, "cover": False, "watermark": o.get("watermark") and i == len(snips)}, job, 800 + i,
                        out_name=f"_t{i:02d}.mp4", hook=False, zoom=True)
        Path(r["preview"]).unlink(missing_ok=True); parts.append(Path(r["file"]))
    if not parts: return None
    slow = 0.6 if len(parts) >= 3 else 1.0  # кульмінація в кінці — сповільнено
    T = 0.12; trans = ["fadewhite", "zoomin", "fadewhite", "slideup", "fadewhite", "circleopen"]
    durs = []
    for k, p in enumerate(parts):
        dd = dur_of(p) or 2.0
        durs.append(dd / slow if k == len(parts) - 1 else dd)
    g = []
    for k in range(len(parts)):
        last = k == len(parts) - 1
        g.append(f"[{k}:v]{'setpts=PTS/' + str(slow) + ',' if last and slow < 1 else ''}fps=30,settb=AVTB,format=yuv420p,setsar=1[v{k}]")
        g.append(f"[{k}:a]{'atempo=' + str(slow) + ',' if last and slow < 1 else ''}aresample=48000,asetpts=PTS-STARTPTS[a{k}]")
    vcur, acur, off = "v0", "a0", 0.0
    for k in range(1, len(parts)):
        off += durs[k - 1] - T
        g.append(f"[{vcur}][v{k}]xfade=transition={trans[(k - 1) % len(trans)]}:duration={T}:offset={off:.3f}[x{k}]")
        g.append(f"[{acur}][a{k}]acrossfade=d={T}[y{k}]")
        vcur, acur = f"x{k}", f"y{k}"
    total = sum(durs) - T * (len(parts) - 1)
    g.append(f"[{vcur}]fade=t=out:st={max(0, total - 0.3):.3f}:d=0.3[v];[{acur}]afade=t=out:st={max(0, total - 0.3):.3f}:d=0.3[a]")
    out = d / "out" / "teaser_9x16.mp4"
    inputs = []
    for p in parts: inputs += ["-i", p.resolve()]
    enc, eargs = encoder_args(o)
    base = [ffmpeg(), "-y", "-v", "error", *inputs, "-filter_complex", ";".join(g), "-map", "[v]", "-map", "[a]"]
    tail = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out.resolve()]
    try:
        run_proc(base + eargs + tail, job)
    except Exception:
        if enc == "libx264": raise
        run_proc(base + encoder_args({**o, "gpu": False})[1] + tail, job)
    for p in parts: p.unlink(missing_ok=True)
    run_proc([ffmpeg(), "-y", "-v", "error", "-ss", f"{total * 0.5:.2f}", "-i", out, "-frames:v", "1", "-vf", "scale=360:-2",
              out.with_name("teaser_9x16_preview.jpg")], job, check=False)
    return str(out)


# ------------------------------------------------------------------ автопоиск вебки
def _cv2():
    try:
        import cv2
        if (Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml").exists(): return cv2
    except Exception:
        pass
    import subprocess, sys  # OpenCV 5 прибрав каскади — потрібна 4.x
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "opencv-python-headless>=4.8,<5"], capture_output=True)
    for m in [k for k in sys.modules if k == "cv2" or k.startswith("cv2.")]: del sys.modules[m]
    import cv2
    if not (Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml").exists(): raise RuntimeError("OpenCV: no face models")
    return cv2


def detect_cam(frames):
    """Шукає вебку: обличчя (OpenCV Haar, анфас і профіль) + постійні краї рамки по кількох кадрах. -> [x,y,w,h] або None."""
    cv2 = _cv2()
    casc = [cv2.CascadeClassifier(cv2.data.haarcascades + n) for n in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")]
    imgs = [i for i in (cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_COLOR) for f in frames) if i is not None]  # imread не читає кирилицю в шляху
    if not imgs: return None
    H, W = imgs[0].shape[:2]; imgs = [cv2.resize(i, (W, H)) for i in imgs]
    hits = []
    for img in imgs:
        g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        for c in casc:
            for fl in (False, True):
                gg = cv2.flip(g, 1) if fl else g
                for (x, y, w, h) in c.detectMultiScale(gg, 1.1, 4, minSize=(int(W * 0.025),) * 2):
                    if fl: x = W - x - w
                    if w > W * 0.3: continue
                    hits.append((x / W, y / H, w / W, h / H))
    if not hits: return None
    near = lambda a, b: abs(a[0] - b[0]) < 0.05 and abs(a[1] - b[1]) < 0.06
    best = max(hits, key=lambda a: sum(near(a, b) for b in hits))
    grp = [b for b in hits if near(best, b)]
    fx, fy, fw, fh = [float(np.median(v)) for v in zip(*grp)]
    # постійні краї рамки вебки: середнє градієнтів по кадрах
    gx = np.mean([np.abs(cv2.Sobel(cv2.cvtColor(i, cv2.COLOR_BGR2GRAY).astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)) for i in imgs], 0)
    gy = np.mean([np.abs(cv2.Sobel(cv2.cvtColor(i, cv2.COLOR_BGR2GRAY).astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)) for i in imgs], 0)
    thr = max(30.0, float(np.percentile(np.maximum(gx, gy), 90)))
    X0, X1, Y0, Y1 = int(fx * W), int((fx + fw) * W), int(fy * H), int((fy + fh) * H)
    cy0, cy1 = max(0, Y0 - (Y1 - Y0)), min(H, Y1 + (Y1 - Y0)); cx0, cx1 = max(0, X0 - (X1 - X0)), min(W, X1 + (X1 - X0))
    def line(prof_fn, rng):
        best, sc = None, 0.55
        for p in rng:
            s = prof_fn(p)
            if s > sc: return p  # найближча до обличчя суцільна лінія
        return best
    rowf = lambda y: float(np.mean(np.max(gy[max(0, y - 1):y + 2, cx0:cx1], 0) > thr))
    span = int(W * 0.35)
    t = line(rowf, range(Y0 - 2, max(0, Y0 - span), -1)); b = line(rowf, range(Y1 + 2, min(H - 1, Y1 + span)))
    # вертикальні краї перевіряємо по всій висоті рамки: справжня межа вебки суцільна, край крісла чи стіни — ні
    ry0, ry1 = (t if t is not None else cy0) + 3, (b if b is not None else cy1) - 3
    if ry1 - ry0 < 10: ry0, ry1 = cy0, cy1
    colf = lambda x: float(np.mean(np.max(gx[ry0:ry1, max(0, x - 1):x + 2], 1) > thr)) * 0.55 / 0.8
    l = line(colf, range(X0 - 2, max(0, X0 - span), -1)); r = line(colf, range(X1 + 2, min(W - 1, X1 + span)))
    L = l if l is not None else 0; R = r if r is not None else W; T = t if t is not None else 0; B = b if b is not None else H
    ok = (R - L) < W * 0.5 and (B - T) < H * 0.6 and (R - L) > fw * W * 1.5 and (B - T) > fh * H * 1.3
    if ok and ((l is not None or L == 0) or (r is not None)) and (t is not None or b is not None):
        return [round(L / W, 3), round(T / H, 3), round((R - L) / W, 3), round((B - T) / H, 3)]
    cx, cy = fx + fw / 2, fy + fh / 2
    cw = min(0.45, fw * 3.2); ch = min(0.5, cw * (640 / 1080) * (16 / 9))
    X, Y = min(max(0, cx - cw / 2), 1 - cw), min(max(0, cy - ch * 0.45), 1 - ch)
    return [round(X, 3), round(Y, 3), round(cw, 3), round(ch, 3)]
