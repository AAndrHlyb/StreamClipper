# StreamClipper — локальный сервер + интерфейс в браузере.
import io, json, mimetypes, os, secrets, shutil, socket, subprocess, sys, threading, time, urllib.request, uuid, webbrowser, zipfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from sc import core as C, sources as S, pipeline as P, render as R, analysis as A
from sc.watcher import Queue, Watcher

PORT = int(os.environ.get("SC_PORT", "8765"))
UI = C.APP_DIR / "ui" / "index.html"
JOBS, INFOS, WATCH_JOBS = {}, {}, {}


def start_job(url, opts, info=None, kind="clip", watch_key=None):
    jid = uuid.uuid4().hex[:8]; j = P.Job(jid, url, opts, info, kind); JOBS[jid] = j
    if watch_key: WATCH_JOBS[watch_key] = j
    threading.Thread(target=P.run_job, args=(j,), daemon=True).start()
    return j


def active_live(key):
    j = WATCH_JOBS.get(key); return bool(j and j.state in ("running", "review"))


QUEUE = Queue(start_job)
WATCH = None


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"


def allowed(p):
    p = Path(p).resolve()
    roots = [C.OUT_ROOT.resolve()] + [Path(i["url"]).resolve().parent for i in INFOS.values() if i.get("platform") == "file"]
    return any(str(p).startswith(str(r)) for r in roots)


def open_folder(p):
    p = str(p)
    if C.IS_WIN: os.startfile(p)
    elif C.IS_MAC: subprocess.Popen(["open", p])
    else: subprocess.Popen(["xdg-open", p])


def library():
    out = []
    if C.OUT_ROOT.exists():
        for d in sorted(C.OUT_ROOT.iterdir(), reverse=True):
            res = C.jload(d / "results.json") if d.is_dir() else None
            if res:
                meta = C.jload(d / "meta.json", {}) or {}
                out.append({"dir": str(d), "title": (meta.get("info") or {}).get("title") or d.name, "src_dur": meta.get("duration") or 0,
                            "uploader": meta.get("uploader", ""), "results": res, "sources_deleted": bool(meta.get("sources_deleted")),
                            "sources_bytes": sum(f.stat().st_size for f in C.source_files(d))})
    return out[:40]


def restart():
    time.sleep(1.0)
    env = {k: v for k, v in os.environ.items() if k != "SC_LAN_ACTIVE"}; env["SC_NO_BROWSER"] = "1"  # вкладка сама перезавантажиться
    if C.IS_WIN:
        subprocess.Popen(["cmd", "/c", "start", "", str(C.APP_DIR / "Start-Windows.bat")], cwd=C.APP_DIR, env=env)
    elif C.IS_MAC:
        subprocess.Popen(["open", str(C.APP_DIR / "Start-Mac.command")])
    else:
        subprocess.Popen([sys.executable, str(C.APP_DIR / "app.py")], cwd=C.APP_DIR, env=env)
    os._exit(0)


def update_check():
    """Остання версія = файл VERSION у гілці main (реліз не обов'язковий)."""
    raw = f"https://raw.githubusercontent.com/{C.REPO}/main/VERSION"
    with urllib.request.urlopen(urllib.request.Request(raw, headers={"User-Agent": "StreamClipper", "Cache-Control": "no-cache"}),
                                timeout=20, context=C.ssl_ctx()) as r:
        tag = r.read().decode().strip().lstrip("v")
    return {"current": C.VERSION, "latest": tag, "newer": bool(tag) and _ver(tag) > _ver(C.VERSION),
            "url": f"https://codeload.github.com/{C.REPO}/zip/refs/heads/main"}


def _ver(v):
    try: return tuple(int(x) for x in v.split("."))
    except Exception: return (0,)


def update_apply():
    info = update_check()
    if not info["url"]: raise RuntimeError("no release")
    with urllib.request.urlopen(urllib.request.Request(info["url"], headers={"User-Agent": "StreamClipper"}), timeout=120, context=C.ssl_ctx()) as r:
        data = r.read()
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist(); root = os.path.commonprefix(names).split("/")[0] + "/" if all("/" in n for n in names) else ""
    old_req = (C.APP_DIR / "requirements.txt").read_text() if (C.APP_DIR / "requirements.txt").exists() else ""
    for n in names:
        rel = n[len(root):] if n.startswith(root) else n
        if not rel or rel.endswith("/") or rel.startswith(".venv"): continue
        dst = C.APP_DIR / rel; dst.parent.mkdir(parents=True, exist_ok=True)
        with z.open(n) as src, open(dst, "wb") as f: shutil.copyfileobj(src, f)
        if rel.endswith(".command"): os.chmod(dst, 0o755)
    if (C.APP_DIR / "requirements.txt").read_text() != old_req:
        (C.APP_DIR / ".venv" / "deps_ok.txt").unlink(missing_ok=True)
    threading.Thread(target=restart, daemon=True).start()
    return info


def qr_svg(text):
    try:
        import segno
        buf = io.BytesIO(); segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#ededed", light="#19191b"); return buf.getvalue()
    except Exception:
        return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        try: self.wfile.write(body)
        except ConnectionError: pass

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def authed(self, q):
        if self.client_address[0] in ("127.0.0.1", "::1"): return True
        tok = C.settings().get("token")
        ck = {k.strip(): v for k, v in (c.split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)}
        return bool(tok) and (q.get("t", [""])[0] == tok or ck.get("sc_t") == tok)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        try:
            if not self.authed(q): return self.send(403, "StreamClipper: open the QR link from the computer", "text/plain; charset=utf-8")
            if u.path in ("/", "/index.html"):
                extra = {"Set-Cookie": f"sc_t={q['t'][0]}; Path=/; SameSite=Strict; Max-Age=31536000"} if q.get("t") else None
                return self.send(200, UI.read_bytes(), "text/html; charset=utf-8", extra)
            if u.path == "/font":
                return self.send(200, C.FONT.read_bytes(), "font/ttf", {"Cache-Control": "max-age=86400"})
            if u.path.startswith("/ui/"):
                f = (UI.parent / u.path[4:]).resolve()
                if f.parent != UI.parent.resolve() or not f.is_file(): return self.send(404, {"error": "not found"})
                ct = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(f.suffix, "application/octet-stream")
                return self.send(200, f.read_bytes(), ct, {"Cache-Control": "no-cache"})
            if u.path == "/api/health":
                s = C.settings(); h = {"version": C.VERSION, "out": str(C.OUT_ROOT), "platform": sys.platform, "settings": {k: v for k, v in s.items() if k != "learn"}}
                try: h["ffmpeg"] = C.ffmpeg()
                except Exception as e: h["ffmpeg_err"] = str(e)
                return self.send(200, h)
            if u.path == "/api/hardware":
                return self.send(200, C.hardware())
            if u.path.startswith("/api/job/"):
                j = JOBS.get(u.path.rsplit("/", 1)[-1])
                return self.send(200, j.view()) if j else self.send(404, {"error": "no job"})
            if u.path == "/api/jobs":
                return self.send(200, [j.view() for j in JOBS.values() if j.state in ("running", "review")])
            if u.path == "/api/library": return self.send(200, library())
            if u.path == "/api/queue": return self.send(200, QUEUE.view())
            if u.path == "/api/watch":
                return self.send(200, {"list": C.settings().get("watch") or [], "status": WATCH.status if WATCH else {}})
            if u.path == "/api/update": return self.send(200, update_check())
            if u.path == "/api/lan":
                s = C.settings(); url = f"http://{lan_ip()}:{PORT}/?t={s.get('token')}"
                svg = qr_svg(url) if s.get("lan") else None
                return self.send(200, {"on": bool(s.get("lan")), "url": url if s.get("lan") else None, "svg": svg.decode() if svg else None,
                                       "active": bool(os.environ.get("SC_LAN_ACTIVE"))})
            if u.path == "/file":
                return self.file((q.get("p") or [""])[0], bool(q.get("dl")))
            if u.path == "/thumb":
                with urllib.request.urlopen(urllib.request.Request((q.get("u") or [""])[0], headers={"User-Agent": "Mozilla/5.0"}), timeout=20, context=C.ssl_ctx()) as r:
                    return self.send(200, r.read(), r.headers.get("Content-Type", "image/jpeg"), {"Cache-Control": "max-age=86400"})
            self.send(404, {"error": "not found"})
        except ConnectionError:
            pass
        except Exception as e:
            self.send(500, {"error": str(e)})

    def file(self, p, dl=False):
        if not p or not allowed(p) or not Path(p).is_file(): return self.send(404, {"error": "no file"})
        size = os.path.getsize(p); ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        rng = self.headers.get("Range"); start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b)); end = min(int(b) if a and b else end, size - 1)
        ln = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Length", str(ln))
        if dl: self.send_header("Content-Disposition", f'attachment; filename="{Path(p).name}"')
        if rng: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(start); left = ln
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk: break
                    self.wfile.write(chunk); left -= len(chunk)
        except ConnectionError:
            pass

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if not self.authed(parse_qs(u.query)): return self.send(403, {"error": "forbidden"})
            b = self.body(); p = u.path
            if p == "/api/probe":
                info = S.probe(b["url"], bool(b.get("live")))
                prof = (C.settings().get("profiles") or {}).get((info.get("uploader") or "").lower(), {})
                if info.get("kind") == "channel": return self.send(200, {"info": info, "profile": prof})
                pid = uuid.uuid4().hex[:8]; INFOS[pid] = info
                return self.send(200, {"pid": pid, "info": info, "profile": prof})
            if p == "/api/frame":
                info = INFOS[b["pid"]]; tmp = C.OUT_ROOT / "_frames"; tmp.mkdir(parents=True, exist_ok=True)
                dest = tmp / f"{b['pid']}_{int(b.get('t', 0))}.jpg"
                if not dest.exists(): S.frame_at(info, float(b.get("t", 0)), dest)
                return self.send(200, {"path": str(dest)})
            if p == "/api/detect_cam":
                info = INFOS[b["pid"]]; d = info.get("duration") or 0; tmp = C.OUT_ROOT / "_frames"; tmp.mkdir(parents=True, exist_ok=True)
                fr = []
                for k, t in enumerate([d * 0.25, d * 0.5, d * 0.75] if d else [0]):
                    dest = tmp / f"{b['pid']}_det{k}.jpg"
                    try:
                        if not dest.exists(): S.frame_at(info, t, dest)
                        fr.append(dest)
                    except Exception:
                        pass
                return self.send(200, {"cam": R.detect_cam(fr)})
            if p == "/api/start":
                info = INFOS.get(b.get("pid"))
                o = b.get("opts") or {}
                j = start_job(b.get("url") or (info or {}).get("url"), o, info)
                return self.send(200, {"job": j.id})
            if p in ("/api/cancel", "/api/stop"):
                j = JOBS.get(b["job"])
                if j:
                    if p == "/api/cancel": j.cancel = True
                    else: j.stop = True
                return self.send(200, {"ok": True})
            if p == "/api/review":
                j = JOBS[b["job"]]; j.extra["review_result"] = b.get("items") or []; j.review_ev.set(); j.review_ev = threading.Event()
                return self.send(200, {"ok": True})
            if p == "/api/rerender":
                if not allowed(b["dir"]): return self.send(403, {"error": "forbidden"})
                jid = uuid.uuid4().hex[:8]; j = P.Job(jid, "", {}); JOBS[jid] = j

                def go():
                    try:
                        j.step("render", 0, "1", "1"); r = P.rerender(b["dir"], int(b["n"]), b.get("title", ""), j)
                        j.results = [r]; j.state = "done"; j.stage = "done"
                    except Exception as e:
                        j.state, j.err = "error", str(e)
                threading.Thread(target=go, daemon=True).start()
                return self.send(200, {"job": jid})
            if p == "/api/more":
                if not allowed(b["dir"]): return self.send(403, {"error": "forbidden"})
                j = start_job(b["dir"], {"n": int(b.get("n") or 10), **(b.get("opts") or {})}, None, kind="more")
                return self.send(200, {"job": j.id})
            if p == "/api/cleanup":
                if not allowed(b["dir"]): return self.send(403, {"error": "forbidden"})
                return self.send(200, {"freed": C.delete_sources(b["dir"])})
            if p == "/api/rate":
                res = C.jload(Path(b["dir"]) / "results.json", []) or []
                r = next((x for x in res if x["n"] == int(b["n"])), None)
                meta = C.jload(Path(b["dir"]) / "meta.json", {}) or {}
                if r and meta.get("uploader"):
                    A.learn(meta["uploader"], r.get("f") or {}, bool(b.get("good")))
                    r["rated"] = 1 if b.get("good") else -1; C.jsave(Path(b["dir"]) / "results.json", res)
                return self.send(200, {"ok": True})
            if p == "/api/settings":
                upd = {k: v for k, v in b.items() if k in C.DEFAULT_SETTINGS}
                if upd.get("lan") and not C.settings().get("token"): upd["token"] = secrets.token_urlsafe(12)
                s = C.save_settings(upd)
                if "lan" in upd and bool(upd["lan"]) != bool(os.environ.get("SC_LAN_ACTIVE")) and b.get("restart"):
                    threading.Thread(target=restart, daemon=True).start()
                return self.send(200, {k: v for k, v in s.items() if k != "learn"})
            if p == "/api/profile":
                s = C.settings(); prof = s.get("profiles") or {}
                name = (b.get("name") or "").lower()
                if name: prof[name] = {**prof.get(name, {}), "opts": b.get("opts") or {}, **({"cam": b["opts"]["cam"]} if (b.get("opts") or {}).get("cam") else {})}
                C.save_settings({"profiles": prof}); return self.send(200, {"ok": True})
            if p == "/api/queue/add":
                QUEUE.add(b.get("urls") or [], b.get("opts") or {}); return self.send(200, QUEUE.view())
            if p == "/api/queue/remove":
                QUEUE.remove(b["id"]); return self.send(200, QUEUE.view())
            if p == "/api/watch/save":
                lst = []
                for w in b.get("list") or []:
                    url = (w.get("url") or "").strip()
                    pf = S.platform_of(url) if url else w.get("platform", "twitch")
                    name = S.channel_of(url) if url else w.get("name")
                    if not name: continue
                    lst.append({"platform": pf, "name": name, "url": url or None, "enabled": bool(w.get("enabled", True)),
                                "avatar": w.get("avatar"), "display": w.get("display") or name})
                C.save_settings({"watch": lst}); return self.send(200, {"list": lst})
            if p == "/api/update":
                return self.send(200, update_apply())
            if p == "/api/open":
                pth = b.get("path") or str(C.OUT_ROOT)
                if not allowed(pth) and Path(pth).resolve() != C.OUT_ROOT.resolve(): return self.send(403, {"error": "forbidden"})
                Path(pth).mkdir(parents=True, exist_ok=True); open_folder(pth); return self.send(200, {"ok": True})
            self.send(404, {"error": "not found"})
        except ConnectionError:
            pass
        except Exception as e:
            self.send(400, {"error": str(e)})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError): return
        super().handle_error(request, client_address)


def main():
    global WATCH
    C.OUT_ROOT.mkdir(parents=True, exist_ok=True)
    s = C.auto_setup(); C.set_quiet(s.get("quiet"))
    host = "0.0.0.0" if s.get("lan") else "127.0.0.1"
    url = f"http://127.0.0.1:{PORT}/"
    srv = None
    for _ in range(15):  # после обновления старый процесс может ещё держать порт
        try: srv = Server((host, PORT), H); break
        except OSError: time.sleep(1)
    if not srv:
        print("StreamClipper is already running."); webbrowser.open(url); return
    if s.get("lan"): os.environ["SC_LAN_ACTIVE"] = "1"
    WATCH = Watcher(lambda u, o, i, watch_key=None: start_job(u, o, i, watch_key=watch_key), active_live)
    print(f"StreamClipper {C.VERSION}: {url}\n{C.OUT_ROOT}\n(не закривай це вікно / не закрывай это окно)")
    if not os.environ.get("SC_NO_BROWSER"): threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__":
    main()
