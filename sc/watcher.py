"""Очередь ссылок и слежение за каналами (автостарт лайв-нарезки, когда стример выходит в эфир)."""
import threading, time, uuid

from .core import settings, save_settings, notify
from . import sources as S


class Queue:
    def __init__(self, start_job):
        self.items, self.lock, self.start_job, self.current = [], threading.Lock(), start_job, None
        threading.Thread(target=self.loop, daemon=True).start()

    def add(self, urls, opts):
        with self.lock:
            for u in urls:
                if u.strip(): self.items.append({"id": uuid.uuid4().hex[:8], "url": u.strip(), "opts": opts, "state": "wait", "job": None})

    def remove(self, qid):
        with self.lock: self.items = [i for i in self.items if i["id"] != qid or i["state"] == "run"]

    def view(self):
        return [{k: i[k] for k in ("id", "url", "state", "job")} for i in self.items[-50:]]

    def loop(self):
        while True:
            time.sleep(2)
            if self.current and self.current.state in ("running", "review"): continue
            if self.current:
                for i in self.items:
                    if i["job"] == self.current.id: i["state"] = self.current.state
                self.current = None
            with self.lock: nxt = next((i for i in self.items if i["state"] == "wait"), None)
            if not nxt: continue
            nxt["state"] = "run"
            try:
                info = S.probe(nxt["url"], live=bool(S.channel_of(nxt["url"])))
                self.current = self.start_job(nxt["url"], nxt["opts"], info)
                nxt["job"] = self.current.id
            except Exception as e:
                nxt["state"] = "error"; nxt["err"] = str(e)[:200]


class Watcher:
    """settings.watch = [{platform,name,url,enabled,avatar,display}]"""
    def __init__(self, start_job, active_live):
        self.start_job, self.active_live, self.status = start_job, active_live, {}
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            for w in list(settings().get("watch") or []):
                key = f"{w['platform']}:{w['name'].lower()}"
                try:
                    ci = S.channel_info(w["platform"], w["name"]) or {}
                except Exception:
                    continue
                was = self.status.get(key, {}).get("live")
                self.status[key] = {"live": ci.get("live"), "title": ci.get("title"), "checked": int(time.time())}
                if ci.get("avatar") and ci.get("avatar") != w.get("avatar"):
                    lst = settings().get("watch") or []
                    for x in lst:
                        if x["name"].lower() == w["name"].lower() and x["platform"] == w["platform"]: x["avatar"] = ci["avatar"]; x["display"] = ci.get("name")
                    save_settings({"watch": lst})
                if ci.get("live") and w.get("enabled") and not self.active_live(key):
                    if not was: notify("StreamClipper", f"{w.get('display') or w['name']} в ефірі")
                    try:
                        url = w.get("url") or (f"https://kick.com/{w['name']}" if w["platform"] == "kick" else f"https://www.twitch.tv/{w['name']}")
                        info = S.probe(url, live=True)
                        prof = (settings().get("profiles") or {}).get(w["name"].lower(), {})
                        self.start_job(url, {**prof.get("opts", {}), **({"layout": "cam", "cam": prof["cam"]} if prof.get("cam") else {}),
                                             "live_minutes": 0}, info, watch_key=key)
                    except Exception:
                        pass
            time.sleep(120)
