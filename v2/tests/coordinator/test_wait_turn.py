import json

import pytest

from coordinator.session import (AgentMismatch, LookupFailed, State, TurnEnd, list_items,
                                 state, wait_turn)
from kernel.dispatch import Role
from kernel.store import Store
from tests.coordinator.fake_omnigent import FakeOmnigent
from tests.kernel.front import Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


class Stub:
    """A fake server: status per poll, plus a hook to plant the file."""
    def __init__(self, statuses, agent_id="agent-claude", on_poll=None):
        self.statuses, self.agent_id, self.on_poll = list(statuses), agent_id, on_poll
        self.polls = 0

    def fetch(self, url):
        self.polls += 1
        status = self.statuses[min(self.polls, len(self.statuses)) - 1]
        if self.on_poll:
            self.on_poll(self.polls)
        if status == "unreachable":
            from coordinator.session import LookupFailed
            raise LookupFailed("down")
        labels = {"omnigent.last_task_error_code": "boom"} if status == "failed" else {}
        return json.dumps({"id": "s-1", "status": status, "agent_id": self.agent_id, "labels": labels})


def _seat(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    return s, f, g, sid


def _wait(s, g, sid, stub, watched, deadline_us=10**18, clock=None, agent="agent-claude"):
    ticks = iter(range(1, 10_000))
    return wait_turn(s, "r-1", g, "http://srv", sid, watched, deadline_us,
                     agent_id_expected=agent, fetch=stub.fetch,
                     clock=clock or (lambda: next(ticks)), sleep=lambda _s: None, poll_s=0)


def test_state_carries_the_agent_id():
    st = state("http://srv", "s-1", fetch=lambda u: json.dumps({"status": "idle", "agent_id": "ag_1"}))
    assert st == State(status="idle", error_code="", agent_id="ag_1")
    assert state("http://srv", "s-1", fetch=lambda u: "not json").agent_id == ""


def test_turn_end_matrix(tmp_path):
    """§11: no file -> polled, not ended, under idle/running/unknown while the
    deadline stands; the file under any status ends it `file`; failed with no
    file is `dead`; a stale prompt row is `cap` at once."""
    s, f, g, sid = _seat(tmp_path)
    out = tmp_path / "wt" / "bircher" / "artifact.md"
    out.parent.mkdir(parents=True)
    for statuses in (["idle", "idle", "running", "unreachable", "idle"],):
        def plant(n, p=out):
            if n == 5:
                p.write_text("# done")
        stub = Stub(statuses, on_poll=plant)
        assert _wait(s, g, sid, stub, [out]) == TurnEnd("file", True)
        assert stub.polls == 5
        out.unlink()
    for status in ("idle", "running", "unknown", "failed"):
        out.write_text("x")
        stub = Stub([status if status != "unknown" else "unreachable"])
        assert _wait(s, g, sid, stub, [out]).ended == "file"
        assert stub.polls == 1
        out.unlink()
    stub = Stub(["failed"])
    assert _wait(s, g, sid, stub, [out]) == TurnEnd("dead", False)
    # Deadline already expired (deadline_us=5): both ends are "at once" --
    # died() outranks the cap when a poll observes both at once.
    for status, expected in (("idle", "cap"), ("failed", "dead"), ("running", "cap")):
        stub = Stub([status])
        assert _wait(s, g, sid, stub, [out], deadline_us=5, clock=lambda: 1.0) == TurnEnd(expected, False)
        assert stub.polls <= 1
    # A file planted between the poll and the cap is still the turn's output.
    out.write_text("late")
    assert _wait(s, g, sid, Stub(["idle"]), [out], deadline_us=5, clock=lambda: 1.0).file_present is True


def test_deadline_comes_from_the_journal_not_the_process(tmp_path):
    """A restarted pass gets no fresh window: the caller computes the deadline
    from the prompt row's at_us; wait_turn takes it as given."""
    from kernel import front
    s, f, g, sid = _seat(tmp_path)
    prompt = front.newest_prompt(s, "r-1", "spec", 0)
    cap_us = 3600 * 10**6
    deadline = prompt["at_us"] + cap_us
    late = (deadline + 1) / 1e6
    assert _wait(s, g, sid, Stub(["idle"]), [tmp_path / "none"], deadline_us=deadline,
                 clock=lambda: late).ended == "cap"
    early = (deadline - 10**6) / 1e6
    stub = Stub(["idle", "failed"])
    assert _wait(s, g, sid, stub, [tmp_path / "none"], deadline_us=deadline,
                 clock=lambda: early).ended == "dead"


def test_displaced_before_author_empty_and_questions(tmp_path):
    """§11: a kernel direct during a waited turn ends it displaced at the next
    poll, before the file and status are consulted -- under grill=human with a
    questions file already present too."""
    s = _store(tmp_path)
    f = Front(s, "r-1", labels=("bircher:grill", "bircher:autonomous"))
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    q = tmp_path / "wt" / "bircher" / "questions.md"
    q.parent.mkdir(parents=True)
    q.write_text("Q1?")
    f.direct("no, do it this way")
    stub = Stub(["idle"])
    assert _wait(s, g, sid, stub, [q, tmp_path / "wt" / "bircher" / "artifact.md"]) == TurnEnd("displaced", True)
    assert stub.polls == 0                   # nothing was read


def test_agent_id_mismatch_at_poll_rc_failed(tmp_path):
    """§11: a session whose agent_id at a poll is not its snapshot's raises
    before any file is read; an unknown read compares nothing."""
    s, f, g, sid = _seat(tmp_path)
    out = tmp_path / "wt" / "bircher" / "artifact.md"
    out.parent.mkdir(parents=True)
    out.write_text("would be read")
    with pytest.raises(AgentMismatch) as exc:
        _wait(s, g, sid, Stub(["idle"], agent_id="ag_switched"), [out])
    assert (exc.value.session_id, exc.value.expected, exc.value.observed) == (sid, "agent-claude", "ag_switched")
    assert _wait(s, g, sid, Stub(["unreachable"]), [out]).ended == "file"


def test_list_items_projects_id_role_text():
    body = json.dumps({"items": [
        {"id": "i1", "role": "user", "response_id": "turn_a",
         "content": [{"type": "input_text", "text": "hello"}]},
        {"id": "i2", "role": "assistant", "content": [{"type": "output_text", "text": "hi"},
                                                    {"type": "tool_call", "name": "x"}]},
        {"id": "i3", "role": "user", "content": []},
        # The server's own item, written in the user's voice when a turn is
        # cancelled. Only `response_id` tells it from a person's message, so
        # the projection has to carry that field through.
        {"id": "i4", "role": "user", "response_id": "cancel_b",
         "content": [{"type": "input_text", "text": "[System: interrupted]"}]},
    ]})
    items = list_items("http://srv", "s-1", fetch=lambda u: body)
    assert items == [{"id": "i1", "role": "user", "text": "hello", "response_id": "turn_a"},
                     {"id": "i2", "role": "assistant", "text": "hi", "response_id": ""},
                     {"id": "i3", "role": "user", "text": "", "response_id": ""},
                     {"id": "i4", "role": "user", "text": "[System: interrupted]",
                      "response_id": "cancel_b"}]
    # omnigent's real route returns a PaginatedList -- {"object": "list",
    # "data": [...], "first_id", "last_id", "has_more"} -- not a bare "items"
    # list (omnigent/server/routes/sessions/routes_items.py, PaginatedList in
    # omnigent/server/schemas.py).
    paginated = json.dumps({"object": "list", "data": [
        {"id": "i1", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
    ], "first_id": "i1", "last_id": "i1", "has_more": False})
    assert list_items("http://srv", "s-1", fetch=lambda u: paginated) == [
        {"id": "i1", "role": "user", "text": "hello", "response_id": ""}]


def _long_session(fake, n_assistant: int, tail: str):
    """A session of *n_assistant* assistant items with the human's message
    last -- the shape a real author session has when the person answers."""
    fake.sessions["s-1"] = {"id": "s-1", "status": "idle", "agent_id": "ag_claude",
                            "agent_name": "v2_author_claude", "host_id": "h",
                            "workspace": "/ws", "title": "t", "items": [], "labels": {}}
    fake.listed.add("s-1")
    for i in range(n_assistant):
        fake.add_assistant_text("s-1", f"turn {i}")
    return fake.add_user_message("s-1", tail)


def test_list_items_pages_past_the_routes_default_limit():
    """THE DEFECT: `GET /v1/sessions/{id}/items` answers at most `limit` items
    and defaults to the OLDEST 100 (routes_items.py: limit=100, order=asc).
    A reader that took one page and ignored `has_more` never saw the newest
    item of a session past that -- and in a parked session the newest item is
    exactly the human's `approve`, so `human_pass` parked forever while the
    approval sat unread in the UI."""
    fake = FakeOmnigent(page_size=25)          # five pages for 101 items
    last = _long_session(fake, 100, "approve")
    items = list_items("http://srv", "s-1", fetch=fake.fetch)
    assert len(items) == 101
    assert items[-1] == {"id": last, "role": "user", "text": "approve",
                         "response_id": f"turn_{last}"}
    # In order, and each item once: a paging loop that re-read a page or
    # dropped one would still end on the human's message.
    assert [it["id"] for it in items] == [it["id"] for it in fake.sessions["s-1"]["items"]]


def test_list_items_asks_for_the_order_it_returns():
    """`order=asc` is IN the query, not assumed from the route's default:
    everything downstream (the cursor, `unread_human_items`, the reviewer's
    first user item) reads this listing as oldest-first."""
    seen = []

    def fetch(url):
        seen.append(url)
        return json.dumps({"object": "list", "data": [], "first_id": None,
                           "last_id": None, "has_more": False})

    assert list_items("http://srv", "s-1", fetch=fetch) == []
    assert seen == ["http://srv/v1/sessions/s-1/items?order=asc&limit=1000"]


def test_list_items_refuses_a_server_that_never_stops_paging():
    """A bounded loop, so a server answering `has_more: true` forever is a
    failed lookup rather than a hung coordinator."""
    def fetch(url):
        return json.dumps({"object": "list",
                           "data": [{"id": "i1", "role": "user", "content": []}],
                           "first_id": "i1", "last_id": "i1", "has_more": True})

    with pytest.raises(LookupFailed, match="200 pages"):
        list_items("http://srv", "s-1", fetch=fetch)


def test_list_items_refuses_a_page_that_claims_more_but_names_no_cursor():
    body = json.dumps({"object": "list", "data": [], "first_id": None,
                       "last_id": None, "has_more": True})
    with pytest.raises(LookupFailed, match="no cursor"):
        list_items("http://srv", "s-1", fetch=lambda u: body)
