"""Общие утилиты: процессы, ffmpeg, железо, настройки, уведомления, диск."""
import ctypes, json, os, platform, queue, re, shutil, ssl, subprocess, sys, threading, time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
FONT = APP_DIR / "fonts" / "Montserrat-ExtraBold.ttf"
IS_WIN, IS_MAC = sys.platform.startswith("win"), sys.platform == "darwin"
OUT_ROOT = Path(os.environ.get("SC_OUT") or (Path.home() / ("Movies" if IS_MAC else "Videos") / "StreamClipper"))
YTDLP = [sys.executable, "-m", "yt_dlp"]
VERSION = (APP_DIR / "VERSION").read_text().strip() if (APP_DIR / "VERSION").exists() else "dev"
REPO = "AAndrHlyb/StreamClipper"

_QUIET = {"on": False}


def popen_kw():
    kw = {}
    if IS_WIN:
        kw["creationflags"] = 0x08000000 | (0x00004000 if _QUIET["on"] else 0)  # NO_WINDOW | BELOW_NORMAL
    elif _QUIET["on"]:
        kw["preexec_fn"] = lambda: os.nice(10)
    return kw


def set_quiet(on):
    _QUIET["on"] = bool(on)


class Cancelled(Exception):
    pass


def run_proc(cmd, job=None, on_line=None, cwd=None, check=True):
    cmd = [str(c) for c in cmd]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, **popen_kw())
    q, tail = queue.Queue(), []

    def rd():
        for raw in iter(p.stdout.readline, b""): q.put(raw)
        q.put(None)
    threading.Thread(target=rd, daemon=True).start()
    while True:
        try: raw = q.get(timeout=0.3)
        except queue.Empty:
            if job and job.cancel: p.kill(); raise Cancelled()
            continue
        if raw is None: break
        line = raw.decode("utf-8", "replace").rstrip()
        tail.append(line)
        if len(tail) > 40: tail.pop(0)
        if on_line: on_line(line)
    p.wait()
    if job and job.cancel: raise Cancelled()
    if check and p.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name} (код {p.returncode}): " + " | ".join(l for l in tail[-6:] if l.strip()))
    return tail


def capture(cmd, timeout=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, timeout=timeout, **popen_kw())
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


_FF = {}


def ffmpeg():
    if "p" not in _FF:
        cands = []
        try:
            import imageio_ffmpeg
            cands.append(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:
            pass
        if shutil.which("ffmpeg"): cands.append(shutil.which("ffmpeg"))
        for c in cands:
            try:
                code, out, _ = capture([c, "-hide_banner", "-filters"], timeout=20)
                if code == 0 and " ass " in out and "ebur128" in out:
                    _FF["p"] = c; break
            except Exception:
                continue
        if "p" not in _FF:
            raise RuntimeError("ffmpeg not found / blocked. Windows: winget install Gyan.FFmpeg; Mac: brew install ffmpeg")
    return _FF["p"]


def dur_of(p):
    _, out, err = capture([ffmpeg(), "-hide_banner", "-i", str(p)], timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out + err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def src_fps(p):
    _, out, err = capture([ffmpeg(), "-hide_banner", "-i", str(p)], timeout=60)
    m = re.search(r"([\d.]+) fps", out + err)
    return float(m.group(1)) if m else 30.0


def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_json(url, data=None, headers=None, timeout=30):
    import urllib.request
    h = {"User-Agent": "Mozilla/5.0 StreamClipper", **(headers or {})}
    body = json.dumps(data).encode() if data is not None else None
    if body is not None: h.setdefault("Content-Type", "application/json")
    rq = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(rq, timeout=timeout, context=ssl_ctx()) as r: return json.load(r)


def hms(t):
    t = int(t or 0); return f"{t//3600}:{t%3600//60:02d}:{t%60:02d}"


def slug(s, n=40):
    return (re.sub(r"[^\w]+", "_", s or "", flags=re.U).strip("_")[:n]) or "clip"


def jload(p, default=None):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception: return default


def jsave(p, o):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(json.dumps(o, ensure_ascii=False, indent=0), encoding="utf-8"); tmp.replace(p)


# ---------------------------------------------------------------- настройки
SETTINGS_F = OUT_ROOT / "settings.json"
_SLOCK = threading.Lock()
DEFAULT_SETTINGS = {
    "lang": "uk", "model": None, "gpu": False, "quiet": False, "disk_limit_gb": None, "lan": False,
    "token": None, "history": True, "speed": "auto", "nick": "", "profiles": {}, "watch": [], "learn": {}, "auto_done": False,
}
KNOWN_CAMS = {"evelone2004": [0, 0.58, 0.222, 0.222], "buster": [0, 0, 0.26, 0.26]}


def settings():
    with _SLOCK:
        s = {**DEFAULT_SETTINGS, **(jload(SETTINGS_F, {}) or {})}
    for k, v in KNOWN_CAMS.items():
        s["profiles"].setdefault(k, {}).setdefault("cam", v)
    return s


def save_settings(upd):
    with _SLOCK:
        s = {**DEFAULT_SETTINGS, **(jload(SETTINGS_F, {}) or {}), **upd}
        jsave(SETTINGS_F, s)
    set_quiet(s.get("quiet"))
    return s


# ---------------------------------------------------------------- железо
def ram_gb():
    try:
        if IS_WIN:
            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tot", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av2", ctypes.c_ulonglong), ("ex", ctypes.c_ulonglong)]
            m = MS(); m.l = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)); return m.tot / 2**30
        if IS_MAC:
            return int(capture(["sysctl", "-n", "hw.memsize"])[1].strip()) / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except Exception:
        return 8.0


_GPU = {}


def gpu_encoder():
    """Первый рабочий аппаратный H.264-кодировщик (проверяется реальным кодированием)."""
    if "v" in _GPU: return _GPU["v"]
    _GPU["v"] = None
    for enc in (["h264_videotoolbox"] if IS_MAC else ["h264_nvenc", "h264_qsv", "h264_amf"]):
        try:
            code, _, _ = capture([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=0.5", "-c:v", enc, "-f", "null", "-"], timeout=40)
            if code == 0: _GPU["v"] = enc; break
        except Exception:
            continue
    return _GPU["v"]


def hardware():
    cores = os.cpu_count() or 4
    ram = ram_gb()
    model = "medium" if cores >= 12 and ram >= 16 else ("small" if cores >= 6 and ram >= 8 else "base")
    free = shutil.disk_usage(OUT_ROOT if OUT_ROOT.exists() else Path.home()).free / 2**30
    return {"cores": cores, "ram": round(ram, 1), "gpu": gpu_encoder(), "model": model,
            "free_gb": round(free), "disk_suggest": int(max(10, min(100, free * 0.2))), "speed_auto": speed_auto(cores, ram), "cpu": platform.processor() or platform.machine()}


# ---------------------------------------------------------------- швидкість: скільки робити паралельно
SPEED = {  # net — потоки завантаження, loud — паралельні шматки аналізу гучності, asr — паралельне розпізнавання,
           # render — кліпів одночасно, prefetch — скільки відео наперед качати під час рендеру
    "eco":    {"net": 4,  "loud": 1, "asr": 1, "render": 1, "prefetch": 1},
    "normal": {"net": 8,  "loud": 2, "asr": 1, "render": 1, "prefetch": 2},
    "max":    {"net": 12, "loud": 4, "asr": 2, "render": 2, "prefetch": 3},
}
TIERS = ["eco", "normal", "max"]


def speed_auto(cores=None, ram=None):
    cores = cores or os.cpu_count() or 4; ram = ram if ram is not None else ram_gb()
    if cores <= 4 or ram < 7.5: return "eco"
    if cores >= 10 and ram >= 15: return "max"
    return "normal"


def perf():
    """План паралельності з урахуванням вибору користувача і тихого режиму."""
    s = settings(); auto = speed_auto()
    tier = s.get("speed") if s.get("speed") in SPEED else auto
    if s.get("quiet"): tier = TIERS[max(0, TIERS.index(tier) - 1)]
    p = dict(SPEED[tier]); p["tier"] = tier; p["auto"] = auto
    cores = os.cpu_count() or 4
    p["asr_threads"] = max(1, (cores // (2 if s.get("quiet") else 1)) // p["asr"])
    return p


def auto_setup():
    s = settings()
    if s.get("auto_done"): return s
    h = hardware()
    return save_settings({"auto_done": True, "model": h["model"], "gpu": bool(h["gpu"]), "disk_limit_gb": h["disk_suggest"]})


# ---------------------------------------------------------------- уведомления ОС
def notify(title, text):
    try:
        if IS_MAC:
            subprocess.Popen(["osascript", "-e", f'display notification {json.dumps(text)} with title {json.dumps(title)}'])
        elif IS_WIN:
            ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
                  "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                  f"$t=$x.GetElementsByTagName('text');$t.Item(0).AppendChild($x.CreateTextNode({json.dumps(title)}))>$null;"
                  f"$t.Item(1).AppendChild($x.CreateTextNode({json.dumps(text)}))>$null;"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('StreamClipper').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps], **popen_kw())
    except Exception:
        pass


# ---------------------------------------------------------------- диск
SOURCE_GLOBS = ["audio.*", "video.*", "live*.ts", "chat.jsonl", "raw/*", "live/*"]


def dir_size(d):
    return sum(f.stat().st_size for f in Path(d).rglob("*") if f.is_file())


def source_files(d):
    out = []
    for g in SOURCE_GLOBS: out += [f for f in Path(d).glob(g) if f.is_file()]
    return out


def delete_sources(d):
    n = 0
    for f in source_files(d):
        n += f.stat().st_size; f.unlink(missing_ok=True)
    for sub in ("raw", "live"): shutil.rmtree(Path(d) / sub, ignore_errors=True)
    m = jload(Path(d) / "meta.json", {}) or {}
    m["sources_deleted"] = True; jsave(Path(d) / "meta.json", m)
    return n


def enforce_disk_limit(keep=None):
    lim = settings().get("disk_limit_gb")
    if not lim or not OUT_ROOT.exists(): return 0
    dirs = sorted([d for d in OUT_ROOT.iterdir() if d.is_dir() and not d.name.startswith("_")], key=lambda d: d.stat().st_mtime)
    total = sum(dir_size(d) for d in dirs); freed = 0
    prot = set()
    if keep:  # поточна папка і та, з якої вона позичила вихідники
        prot.add(Path(keep)); rf = (jload(Path(keep) / "meta.json", {}) or {}).get("reused_from")
        if rf: prot.add(Path(rf))
    for d in dirs:
        if total <= lim * 2**30: break
        if Path(d) in prot: continue
        n = delete_sources(d); total -= n; freed += n
    return freed
