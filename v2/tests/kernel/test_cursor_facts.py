import pytest

from kernel import front
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.commands import HUMAN_GENERATION, Command, execute_as_human
from kernel.dispatch import Role
from kernel.events import EventKind
from kernel.store import Store
from tests.kernel.front import SPEC_BYTES, Front


def _store(tmp_path):
    return Store.open(tmp_path / "k.db")


def _refused_human_approve(s, f):
    """A human token the kernel refuses: approve at spec_submitted."""
    with pytest.raises(NotAuthorized):
        execute_as_human(s, Command(name="approve_artifact", run_id="r-1",
                                    expected_version=s.run_version("r-1"),
                                    idempotency_key=f._key("bad"), generation=HUMAN_GENERATION,
                                    payload={"artifact_hash": s.phase_artifact("r-1", "spec")}))
    return s.newest_fact("r-1", EventKind.COMMAND_REJECTED)


def test_dismiss_names_a_human_rejection_once(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    rej = _refused_human_approve(s, f)
    assert rej.actor == "human"
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="command_rejected"):
        f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-5", "rejection": "not-a-fact"})
    # A rejection attributed to a dispatched actor is not a human token.
    with pytest.raises(NotAuthorized):
        f._cmd(g, "approve_artifact", {"artifact_hash": "0" * 64})
    model_rej = s.newest_fact("r-1", EventKind.COMMAND_REJECTED)
    with pytest.raises(NotAuthorized, match="human"):
        f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-5", "rejection": model_rej.id})
    park_before = front.current_park(s, "r-1")
    f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-5", "rejection": rej.id})
    d = s.newest_fact("r-1", EventKind.HUMAN_ITEM_DISMISSED)
    assert d.payload == {"epoch": 0, "cursor_item_id": "i-5", "rejection": rej.id}
    assert d.actor == "runner"
    assert front.dismissed_rejection_ids(s, "r-1") == {rej.id}
    assert front.current_park(s, "r-1") == park_before      # not a human fact
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-6", "rejection": rej.id})


@pytest.mark.parametrize("bad_rejection", [["a", "b"], None])
def test_dismiss_refuses_a_malformed_rejection(tmp_path, bad_rejection):
    """`rejection` must be a non-empty string before it is used as a dict key:
    an unhashable value (a list) or None must raise NotAuthorized, not
    TypeError."""
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.OPERATOR, "runner")
    with pytest.raises(NotAuthorized, match="command_rejected fact"):
        f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-5", "rejection": bad_rejection})


def test_dismiss_does_not_consume_a_park(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    f.author_round(SPEC_BYTES)
    g = f._dispatch(Role.REVIEWER, "codex")
    f._cmd(g, "park", {"reason": "no_verdict", "session_id": "s-x", "cursor_item_id": "i-1",
                       "findings_hash": None, "verdict": None, "reviewer": "codex"})
    rej = _refused_human_approve(s, f)
    f._cmd(g, "dismiss_human_item", {"cursor_item_id": "i-2", "rejection": rej.id})
    assert front.current_park(s, "r-1") is not None


def test_prompt_item_binds_to_a_satisfied_prompt_of_that_session(tmp_path):
    s = _store(tmp_path)
    f = Front(s, "r-1")
    g = f._dispatch(Role.AUTHOR, "claude")
    sid = f._session(g, f._newest_id())
    hashes = front.prompt_hashes_of(s, "r-1", sid)
    assert len(hashes) == 1
    (h,) = hashes
    with pytest.raises(NotAuthorized, match="sess-prompt"):
        f._cmd(g, "record_prompt_item", {"session_id": sid, "item_id": "it-1", "sha256": "a" * 64})
    with pytest.raises(NotAuthorized, match="sess-prompt"):
        f._cmd(g, "record_prompt_item", {"session_id": "s-other", "item_id": "it-1", "sha256": h})
    f._cmd(g, "record_prompt_item", {"session_id": sid, "item_id": "it-1", "sha256": h})
    p = s.newest_fact("r-1", EventKind.PROMPT_ITEM)
    assert p.payload == {"session_id": sid, "item_id": "it-1", "sha256": h}
    with pytest.raises(NotAuthorized, match="already"):
        f._cmd(g, "record_prompt_item", {"session_id": sid, "item_id": "it-1", "sha256": h})
