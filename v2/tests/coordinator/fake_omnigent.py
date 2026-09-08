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

Both list routes PAGE, and so does this fake. An earlier version answered every
item and every session on every request with `has_more: False`, which made a
reader that fetched one page and ignored `has_more` look complete -- while the
real routes cut a session's items off at the oldest 100 and the listing at the
20 newest sessions. The defaults below are the routes' own, so a request that
says nothing about `limit` or `order` is truncated here exactly as the server
truncates it. `page_size` is the fake's own ceiling on a page, so a test can
force several pages without inventing hundreds of rows.
"""
import itertools
import json
import urllib.parse

from coordinator.session import LookupFailed

#: The routes' own query defaults, so an unparameterised request is truncated
#: here as it is there: `GET /v1/sessions/{id}/items` is limit=100, order=asc
#: (routes_items.py); `GET /v1/sessions` is limit=20, order=desc
#: (routes_core.py). Both cap `limit` at 1000.
ITEMS_DEFAULTS = (100, "asc")
SESSIONS_DEFAULTS = (20, "desc")


class FakeOmnigent:
    def __init__(self, agent_names=None, page_size=1000):
        # agent_id -> the BUNDLE's name, as the server reports it (ruling 14).
        self.agent_names = dict(agent_names or {"ag_claude": "v2_author_claude", "ag_codex": "v2_author_codex"})
        self.sessions = {}
        self.listed = set()
        #: The most rows either list route will put in ONE page, whatever the
        #: query asks for -- the server's `le=1000` cap. Lower it to make a
        #: reader page.
        self.page_size = page_size
        self.stop_status = 200
        self.on_prompt = lambda sid, text: None
        self.on_stop = lambda sid: None
        self._ids = itertools.count(1)
        self.posts = []

    # -- read side ------------------------------------------------------------
    def _listing_order(self) -> list[str]:
        """The listed sessions, OLDEST FIRST -- creation order, because
        `self.sessions` is written in the order the creates arrived. Ids added
        to `listed` by hand and never created come last, sorted, so the order
        is total either way."""
        known = [sid for sid in self.sessions if sid in self.listed]
        return known + sorted(self.listed - set(self.sessions))

    def _page(self, rows: list, query: dict, defaults) -> tuple[list, bool]:
        """One page of *rows* (given oldest first) under `limit`, `order` and
        `after`, and whether anything is left after it."""
        default_limit, default_order = defaults
        order = query.get("order", [default_order])[0]
        if order not in ("asc", "desc"):
            raise LookupFailed(f"422: order={order!r}")
        rows = list(rows) if order == "asc" else list(reversed(rows))
        after = query.get("after", [None])[0]
        if after is not None:
            ids = [r["id"] for r in rows]
            if after not in ids:
                raise LookupFailed(f"404: after={after!r} is not in this listing")
            rows = rows[ids.index(after) + 1:]
        try:
            limit = int(query.get("limit", [default_limit])[0])
        except (TypeError, ValueError) as exc:
            raise LookupFailed(f"422: limit={query.get('limit')!r}") from exc
        if not 1 <= limit <= 1000:
            raise LookupFailed(f"422: limit={limit} is outside 1..1000")
        limit = min(limit, self.page_size)
        return rows[:limit], len(rows) > limit

    @staticmethod
    def _paginated(data: list, has_more: bool) -> str:
        return json.dumps({"object": "list", "data": data,
                           "first_id": data[0]["id"] if data else None,
                           "last_id": data[-1]["id"] if data else None,
                           "has_more": has_more})

    def fetch(self, url):
        path, _, query = url.split("//", 1)[-1].split("/", 1)[1].partition("?")
        q = urllib.parse.parse_qs(query)
        if path == "v1/sessions":
            rows = [{"id": s} for s in self._listing_order()]
            return self._paginated(*self._page(rows, q, SESSIONS_DEFAULTS))
        parts = path.split("/")
        sid = parts[2]
        if sid not in self.sessions or sid not in self.listed:
            raise LookupFailed("404")
        s = self.sessions[sid]
        if len(parts) == 4 and parts[3] == "items":
            return self._paginated(*self._page(s["items"], q, ITEMS_DEFAULTS))
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
        s["items"].append({"id": item_id, "role": "user", "response_id": f"turn_{item_id}",
                           "content": [{"type": "input_text", "text": text}]})
        s["status"] = "running"
        self.on_prompt(sid, text)
        r.stdout = json.dumps({"item_id": item_id})
        return r

    def add_user_message(self, sid, text, *, response_id=None):
        """A message in the user's voice.

        *response_id* defaults to a `turn_` id, which is what the server gives
        a real message -- the coordinator's prompt or the human's reply. Pass
        a `cancel_` one to model the item the server itself writes when a turn
        is cancelled; `human._is_cancellation_notice` tells them apart.
        """
        item_id = f"it-{next(self._ids)}"
        self.sessions[sid]["items"].append(
            {"id": item_id, "role": "user", "response_id": response_id or f"turn_{item_id}",
             "content": [{"type": "input_text", "text": text}]})
        return item_id

    def add_assistant_text(self, sid, text):
        item_id = f"it-{next(self._ids)}"
        self.sessions[sid]["items"].append(
            {"id": item_id, "role": "assistant", "response_id": f"resp_{item_id}",
             "content": [{"type": "output_text", "text": text}]})
        return item_id
