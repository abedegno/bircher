"""A fake omnigent for coordinator tests: the read side through `fetch`,
the write side as a fake `subprocess.run` for the curl argv the kernel's
executor builds (Task 3 appends --data-binary @<file> to it).

`fetch`'s two list endpoints answer omnigent's REAL `PaginatedList` shape --
`{"object": "list", "data": [...], "first_id", "last_id", "has_more"}`
(omnigent server/schemas.py `SessionList`/`PaginatedList`; routes_core.py,
routes_items.py) -- not the `{"sessions": [...]}` / `{"items": [...]}` shape
an earlier draft of this fake used. `coordinator.sessions.list_sessions` and
`coordinator.session.list_items` read `data` first (Task 15's fix); this fake
must answer what they actually read.
"""
import itertools
import json

from coordinator.session import LookupFailed


class FakeOmnigent:
    def __init__(self, agent_names=None):
        # agent_id -> the BUNDLE's name, as the server reports it (ruling 14).
        self.agent_names = dict(agent_names or {"ag_claude": "v2_author_claude", "ag_codex": "v2_author_codex"})
        self.sessions = {}
        self.listed = set()
        self.stop_status = 200
        self.on_prompt = lambda sid, text: None
        self.on_stop = lambda sid: None
        self._ids = itertools.count(1)
        self.posts = []

    # -- read side ------------------------------------------------------------
    def fetch(self, url):
        path = url.split("//", 1)[-1].split("/", 1)[1]
        if path == "v1/sessions":
            data = [{"id": s} for s in sorted(self.listed)]
            return json.dumps({"object": "list", "data": data, "first_id": None,
                               "last_id": None, "has_more": False})
        parts = path.split("/")
        sid = parts[2]
        if sid not in self.sessions or sid not in self.listed:
            raise LookupFailed("404")
        s = self.sessions[sid]
        if len(parts) == 4 and parts[3] == "items":
            return json.dumps({"object": "list", "data": s["items"], "first_id": None,
                               "last_id": None, "has_more": False})
        return json.dumps({k: s[k] for k in ("id", "status", "agent_id", "agent_name", "host_id",
                                             "workspace", "title", "labels")})

    # -- write side -------------------------------------------------------------
    def run(self, argv, **kw):
        class R:
            returncode, stdout, stderr = 0, "", ""
        url = next(a for a in argv if a.startswith("http"))
        path = url.split("//", 1)[-1].split("/", 1)[1]
        if "--data-binary" in argv:
            body = json.loads(open(argv[argv.index("--data-binary") + 1][1:], "rb").read())
        else:
            body = json.loads(argv[argv.index("-d") + 1])
        self.posts.append((path, body))
        r = R()
        if path == "v1/sessions":
            sid = f"s-{next(self._ids)}"
            snap = {"id": sid, "status": "idle", "agent_id": body["agent_id"],
                    "agent_name": self.agent_names[body["agent_id"]], "host_id": body["host_id"].removeprefix("host_"),
                    "workspace": body["workspace"], "title": body["title"], "items": [], "labels": {}}
            self.sessions[sid] = snap
            self.listed.add(sid)
            r.stdout = json.dumps({k: v for k, v in snap.items() if k != "items"})
            return r
        sid = path.split("/")[2]
        if sid not in self.sessions:
            r.returncode, r.stderr = 22, "curl: (22) The requested URL returned error: 404"
            return r
        s = self.sessions[sid]
        if body.get("type") == "stop_session":
            if self.stop_status != 200:
                r.returncode, r.stderr = 22, f"curl: (22) The requested URL returned error: {self.stop_status}"
                return r
            s["status"] = "idle"
            self.on_stop(sid)
            r.stdout = '{"queued": false}'
            return r
        text = body["data"]["content"][0]["text"]
        item_id = f"it-{next(self._ids)}"
        s["items"].append({"id": item_id, "role": "user", "content": [{"type": "input_text", "text": text}]})
        s["status"] = "running"
        self.on_prompt(sid, text)
        r.stdout = json.dumps({"item_id": item_id})
        return r

    def add_user_message(self, sid, text):
        item_id = f"it-{next(self._ids)}"
        self.sessions[sid]["items"].append(
            {"id": item_id, "role": "user", "content": [{"type": "input_text", "text": text}]})
        return item_id

    def add_assistant_text(self, sid, text):
        item_id = f"it-{next(self._ids)}"
        self.sessions[sid]["items"].append(
            {"id": item_id, "role": "assistant", "content": [{"type": "output_text", "text": text}]})
        return item_id
