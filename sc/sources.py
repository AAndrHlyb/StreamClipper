"""Источники: Twitch, YouTube, Kick, файлы. Списки стримов, эфир, чат, клипы зрителей, кадры."""
import json, os, random, re, socket, threading, time
from pathlib import Path

from .core import YTDLP, capture, ffmpeg, dur_of, http_json, Cancelled

CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
CHAT_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def gql(body):
    return http_json("https://gql.twitch.tv/gql", body, {"Client-Id": CID})


def platform_of(url):
    if os.path.exists(url): return "file"
    u = url.lower()
    if "twitch.tv" in u: return "twitch"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "kick.com" in u: return "kick"
    return "other"


def channel_of(url):
    """Имя канала из ссылки на канал (не на запись), иначе None."""
    pf = platform_of(url)
    if pf == "twitch":
        m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)/?(?:\?.*)?$", url)
        if m and m.group(1).lower() not in ("videos", "directory", "settings") and "clip" not in url: return m.group(1)
    if pf == "kick":
        m = re.search(r"kick\.com/([A-Za-z0-9_\-]+)/?(?:\?.*)?$", url)
        if m and m.group(1).lower() not in ("video", "categories"): return m.group(1)
    return None


# ------------------------------------------------------------------ Twitch
def twitch_user(ch):
    try:
        u = (gql({"query": 'query{user(login:"%s"){displayName profileImageURL(width:70) stream{id title archiveVideo{id} game{name}}}}' % ch.lower()})
             .get("data") or {}).get("user")
        if not u: return None
        st = u.get("stream")
        return {"name": u.get("displayName") or ch, "avatar": u.get("profileImageURL"), "live": bool(st),
                "vod": ((st or {}).get("archiveVideo") or {}).get("id"), "title": (st or {}).get("title") or "",
                "game": ((st or {}).get("game") or {}).get("name") or ""}
    except Exception:
        code, out, _ = capture([*YTDLP, "-J", "--no-warnings", f"https://www.twitch.tv/{ch}"], timeout=60)
        try: j = json.loads(out)
        except Exception: return {"name": ch, "avatar": None, "live": False, "vod": None, "title": ""}
        return {"name": ch, "avatar": None, "live": bool(j.get("is_live")), "vod": None, "title": j.get("description") or ""}


def twitch_videos(ch, pages=4):
    out, after = [], None
    try:
        for _ in range(pages):
            q = ('query{user(login:"%s"){videos(first:100,sort:TIME%s){edges{cursor node{id title lengthSeconds createdAt viewCount '
                 'broadcastType status previewThumbnailURL(width:320,height:180) game{name}}} pageInfo{hasNextPage}}}}') % (
                ch.lower(), (',after:"%s"' % after) if after else "")
            v = ((gql({"query": q}).get("data") or {}).get("user") or {}).get("videos") or {}
            ed = v.get("edges") or []
            for e in ed:
                n = e["node"]
                out.append({"id": n["id"], "url": f"https://www.twitch.tv/videos/{n['id']}", "title": n.get("title") or "",
                            "duration": n.get("lengthSeconds") or 0, "created": n.get("createdAt"), "views": n.get("viewCount") or 0,
                            "type": (n.get("broadcastType") or "ARCHIVE").lower(), "thumb": n.get("previewThumbnailURL"),
                            "game": (n.get("game") or {}).get("name") or "", "recording": n.get("status") == "RECORDING"})
            if not ed or not (v.get("pageInfo") or {}).get("hasNextPage"): break
            after = ed[-1]["cursor"]
        if out: return out
    except Exception:
        pass
    code, o, _ = capture([*YTDLP, "--flat-playlist", "-J", "--no-warnings", f"https://www.twitch.tv/{ch}/videos?filter=all&sort=time"], timeout=180)
    try: ents = json.loads(o).get("entries") or []
    except Exception: ents = []
    for e in ents:
        vid = re.sub(r"\D", "", str(e.get("id") or e.get("url") or ""))
        ts_ = e.get("timestamp")
        out.append({"id": vid, "url": f"https://www.twitch.tv/videos/{vid}", "title": e.get("title") or "", "duration": e.get("duration") or 0,
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_)) if ts_ else None, "views": e.get("view_count") or 0,
                    "type": "archive", "thumb": (e.get("thumbnails") or [{}])[-1].get("url"), "game": ""})
    return out


def viewer_clips(ch, vid):
    """Клипы, которые зрители сделали из этой записи: [(offset_sec, duration, views)]."""
    found = {}
    for period in ("LAST_WEEK", "LAST_MONTH", "ALL_TIME"):
        try:
            q = ('query{user(login:"%s"){clips(first:100,criteria:{period:%s,sort:VIEWS_DESC}){edges{node{id viewCount durationSeconds '
                 'videoOffsetSeconds video{id}}}}}}') % (ch.lower(), period)
            for e in (((gql({"query": q}).get("data") or {}).get("user") or {}).get("clips") or {}).get("edges") or []:
                n = e["node"]
                if (n.get("video") or {}).get("id") == str(vid) and n.get("videoOffsetSeconds") is not None:
                    found[n["id"]] = (float(n["videoOffsetSeconds"]), float(n.get("durationSeconds") or 30), int(n.get("viewCount") or 0))
        except Exception:
            continue
    return sorted(found.values())


def _chat_range(vid, t0, t1, out, lock, deadline, job):
    t, seen = t0, set()
    while t < t1 and time.time() < deadline and not (job and job.cancel):
        r = None
        for k in range(5):
            try:
                r = gql([{"operationName": "VideoCommentsByOffsetOrCursor", "variables": {"videoID": str(vid), "contentOffsetSeconds": int(t)},
                          "extensions": {"persistedQuery": {"version": 1, "sha256Hash": CHAT_HASH}}}])[0]
                break
            except Exception:
                time.sleep(1 + 2 * k)
        ed = ((((r or {}).get("data") or {}).get("video") or {}).get("comments") or {}).get("edges") or []
        if not ed: t += 30; continue
        rows, last = [], t
        for e in ed:
            nd = e["node"]; ct = nd["contentOffsetSeconds"]
            if ct >= t1: last = t1; break
            key = nd.get("id") or (ct, str(nd.get("message"))[:40])
            if key in seen: continue
            seen.add(key); last = max(last, ct)
            rows.append({"t": ct, "m": "".join((x or {}).get("text", "") for x in (nd.get("message") or {}).get("fragments") or [])})
        with lock: out.extend(rows)
        t = last + 1 if last >= t else t + 30


def twitch_chat(vid, t0, t1, job, workers=8, limit_min=40):
    out, lock = [], threading.Lock()
    deadline, step = time.time() + limit_min * 60, max(1.0, float(t1 - t0) / workers)
    ths = [threading.Thread(target=_chat_range, args=(vid, t0 + i * step, t0 + (i + 1) * step, out, lock, deadline, job), daemon=True)
           for i in range(workers)]
    for th in ths: th.start()
    while any(th.is_alive() for th in ths):
        time.sleep(2)
        if job:
            job.check(); job.sub = f"{len(out)}"
    out.sort(key=lambda r: r["t"])
    return out


class IRC:
    """Чат идущего эфира Twitch (анонимно)."""
    def __init__(self, ch, path, t0):
        self.ch, self.path, self.t0, self.stop = ch.lower(), Path(path), t0, threading.Event()
        self.th = threading.Thread(target=self.run, daemon=True); self.th.start()

    def run(self):
        while not self.stop.is_set():
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.ch}\r\n".encode())
                s.settimeout(5); buf = b""
                with open(self.path, "a", encoding="utf-8") as f:
                    while not self.stop.is_set():
                        try: data = s.recv(8192)
                        except socket.timeout: continue
                        if not data: break
                        buf += data; *lines, buf = buf.split(b"\r\n")
                        for l in lines:
                            l = l.decode(errors="ignore")
                            if l.startswith("PING"): s.sendall(b"PONG :tmi.twitch.tv\r\n")
                            elif " PRIVMSG " in l:
                                msg = l.split(" PRIVMSG ", 1)[1].split(" :", 1)[-1]
                                f.write(json.dumps({"t": round(time.time() - self.t0, 2), "m": msg}, ensure_ascii=False) + "\n"); f.flush()
            except Exception:
                time.sleep(5)

    def close(self):
        self.stop.set()


# ------------------------------------------------------------------ YouTube / Kick
def yt_live_chat(url, d, job=None):
    """Повтор чата записи YouTube-трансляции (если есть)."""
    try:
        capture([*YTDLP, "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-warnings", "-o", str(Path(d) / "ytchat"), url], timeout=1800)
        f = next(Path(d).glob("ytchat*.live_chat.json"), None)
        if not f: return []
        out = []
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                a = json.loads(line)["replayChatItemAction"]
                t = int(a["videoOffsetTimeMsec"]) / 1000
                for act in a.get("actions", []):
                    r = ((act.get("addChatItemAction") or {}).get("item") or {}).get("liveChatTextMessageRenderer")
                    if r: out.append({"t": t, "m": "".join(x.get("text", "") for x in r.get("message", {}).get("runs", []))})
            except Exception:
                continue
        f.unlink(missing_ok=True)
        return out
    except Exception:
        return []


def kick_channel(slug):
    try:
        j = http_json(f"https://kick.com/api/v2/channels/{slug}", timeout=20)
        ls = j.get("livestream") or {}
        return {"name": (j.get("user") or {}).get("username") or slug, "avatar": (j.get("user") or {}).get("profile_pic"),
                "live": bool(ls.get("is_live")), "title": ls.get("session_title") or "", "vod": None}
    except Exception:
        code, out, _ = capture([*YTDLP, "-J", "--no-warnings", f"https://kick.com/{slug}"], timeout=60)
        try: j = json.loads(out)
        except Exception: return {"name": slug, "avatar": None, "live": False, "title": "", "vod": None}
        return {"name": slug, "avatar": None, "live": bool(j.get("is_live")), "title": j.get("title") or "", "vod": None}


def kick_videos(slug):
    try:
        out = []
        for v in http_json(f"https://kick.com/api/v2/channels/{slug}/videos", timeout=20) or []:
            vid = (v.get("video") or {}).get("uuid")
            if not vid: continue
            out.append({"id": vid, "url": f"https://kick.com/{slug}/videos/{vid}", "title": v.get("session_title") or "",
                        "duration": int((v.get("duration") or 0) / 1000), "created": (v.get("start_time") or v.get("created_at") or "").replace(" ", "T"),
                        "views": v.get("viewer_count") or v.get("views") or 0, "type": "archive", "thumb": (v.get("thumbnail") or {}).get("src"),
                        "game": ", ".join(c.get("name", "") for c in v.get("categories") or [])})
        return out
    except Exception:
        return []


def channel_info(platform, name):
    if platform == "kick": return kick_channel(name)
    return twitch_user(name)


# ------------------------------------------------------------------ разбор ссылки
def ytdlp_json(url):
    code, out, err = capture([*YTDLP, "-J", "--no-warnings", "--no-playlist", url], timeout=120)
    if code: raise RuntimeError((err.strip().splitlines() or ["?"])[-1][:300])
    return json.loads(out)


def probe(url, live=False):
    url = url.strip().strip('"')
    pf = platform_of(url)
    if pf == "file":
        p = Path(url).resolve()
        return {"platform": "file", "kind": "file", "url": str(p), "title": p.stem, "uploader": "", "duration": dur_of(p),
                "thumbnail": None, "has_chat": False}
    ch = channel_of(url)
    if ch:
        ci = channel_info(pf, ch)
        if not live:
            vids = kick_videos(ch) if pf == "kick" else twitch_videos(ch)
            return {"platform": pf, "kind": "channel", "url": url, "uploader": ch, "title": ci.get("name") or ch,
                    "avatar": ci.get("avatar"), "live": ci if ci.get("live") else None, "videos": vids}
        if not ci.get("live"): raise RuntimeError("offline")
        info = {"platform": pf, "uploader": ch, "live_title": ci.get("title"), "has_chat": pf == "twitch"}
        if ci.get("vod"):
            vurl = f"https://www.twitch.tv/videos/{ci['vod']}"
            j = ytdlp_json(vurl)
            info.update(kind="live_vod", url=vurl, video_id=ci["vod"], title=ci.get("title") or j.get("title"),
                        duration=j.get("duration"), thumbnail=j.get("thumbnail"), channel_url=url)
        else:
            thumb = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{ch.lower()}-640x360.jpg" if pf == "twitch" else None
            info.update(kind="live", url=url, title=ci.get("title") or ch, duration=None, thumbnail=thumb, channel_url=url)
        return info
    j = ytdlp_json(url)
    info = {"platform": pf, "url": j.get("webpage_url") or url, "title": j.get("title"), "uploader": j.get("uploader") or j.get("channel") or "",
            "duration": j.get("duration"), "thumbnail": j.get("thumbnail"), "has_chat": False}
    if j.get("is_live"):
        info.update(kind="live", channel_url=url, duration=None)
        return info
    m = re.search(r"twitch\.tv/videos/v?(\d+)", url)
    if pf == "twitch":
        info.update(kind="clip" if "clip" in url else "vod", video_id=(m.group(1) if m else None))
        info["has_chat"] = bool(info.get("video_id"))
        info["uploader"] = j.get("uploader_id") or info["uploader"]
    else:
        info["kind"] = "video"
        info["has_chat"] = pf == "youtube" and bool(j.get("was_live") or j.get("live_status") == "was_live")
        if pf == "youtube" and j.get("id"):
            info["yt_id"] = j["id"]; info["skip"] = sponsor_segments(j["id"])
    return info


def sponsor_segments(vid):
    """SponsorBlock: рекламні інтеграції, самопіар, заклики підписатися, інтро/аутро. -> [(a, b)] с."""
    try:
        import json as _j, urllib.parse as up
        cats = up.quote(_j.dumps(["sponsor", "selfpromo", "interaction", "intro", "outro", "preview"]))
        r = http_json(f"https://sponsor.ajay.app/api/skipSegments?videoID={vid}&categories={cats}", timeout=8)
        return [(float(x["segment"][0]), float(x["segment"][1])) for x in (r or []) if x.get("segment")]
    except Exception:
        return []


_SRC = {}  # url -> (час, пряме посилання на потік), щоб кілька кадрів не питали yt-dlp кожен раз


def _stream_url(url):
    import time as _t
    hit = _SRC.get(url)
    if hit and _t.time() - hit[0] < 600: return hit[1]
    code, out, err = capture([*YTDLP, "-g", "--no-warnings", "-f", "best[height<=720]/bv*[height<=720]/best", url], timeout=120)
    if code: raise RuntimeError(err.strip()[-200:])
    _SRC[url] = (_t.time(), out.split()[0]); return _SRC[url][1]


def _local_video(info):
    """Це відео вже скачане раніше (для едіту) — кадр беремо з диска, а не з YouTube."""
    try:
        from .core import OUT_ROOT
        keys = {info.get("url"), info.get("yt_id")} - {None, ""}
        for mf in sorted(Path(OUT_ROOT).glob("*/meta.json"), key=lambda p: -p.stat().st_mtime)[:60]:
            try: m = json.loads(mf.read_text(encoding="utf-8"))
            except Exception: continue
            mi = m.get("info") or {}
            if ({m.get("vid"), m.get("url"), mi.get("yt_id")} & keys) and m.get("video") and Path(m["video"]).exists():
                return m["video"]
    except Exception:
        pass
    return None


def _ff_frame(src, seek, dest):
    cmd = [ffmpeg(), "-y", "-v", "error", "-max_error_rate", "1"]
    if seek is not None: cmd += ["-ss", f"{seek:.1f}"]
    cmd += ["-i", str(src), "-frames:v", "1", "-vf", "scale=960:-2", str(dest)]
    code, _, err = capture(cmd, timeout=120)
    if code or not Path(dest).exists(): raise RuntimeError(err.strip()[-200:] or "frame")
    return dest


def frame_at(info, t, dest):
    """Кадр у момент t. Порядок: локальний файл → пряме посилання (з повтором, якщо протухло) →
    yt-dlp качає 1 секунду сам (YouTube часто дає 403 на пряме посилання для ffmpeg)."""
    if info["platform"] == "file":
        return _ff_frame(info["url"], max(0, t), dest)
    live = info.get("kind") == "live"
    loc = None if live else _local_video(info)
    if loc:
        try: return _ff_frame(loc, max(0, t), dest)
        except Exception: pass
    seek = None if live else max(0, t)
    err = ""
    for attempt in range(2):
        try:
            return _ff_frame(_stream_url(info["url"]), seek, dest)
        except Exception as e:
            err = str(e); _SRC.pop(info["url"], None)  # протухле/заборонене посилання — беремо нове
            if "403" not in err and "Forbidden" not in err and attempt == 0 and "expired" not in err: break
    if not live:
        tmp = Path(dest).with_suffix(".part.mp4")
        try:
            a = max(0, t)
            code, _, e2 = capture([*YTDLP, "--no-warnings", "-f", "bv*[height<=720]/b[height<=720]/b", "--download-sections",
                                   f"*{a:.1f}-{a + 1.5:.1f}", "--force-keyframes-at-cuts", "--ffmpeg-location", ffmpeg(),
                                   "-o", str(tmp), info["url"]], timeout=180)
            cand = [p for p in Path(dest).parent.glob(tmp.stem + "*") if p.stat().st_size > 0]
            if cand: return _ff_frame(cand[0], None, dest)
            err = e2.strip()[-200:] or err
        except Exception as e:
            err = str(e)[-200:] or err
        finally:
            for p in Path(dest).parent.glob(tmp.stem + "*"):
                try: p.unlink()
                except Exception: pass
    raise RuntimeError(err or "frame")
