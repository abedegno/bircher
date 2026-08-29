"""Reading omnigent, tested without a server."""
import json

import pytest

from coordinator.session import LookupFailed, State, died, last_assistant_text, state


def _serving(payload):
    def fetch(url):
        return payload if isinstance(payload, str) else json.dumps(payload)
    return fetch


def _failing(url=None):
    def fetch(_url):
        raise LookupFailed("connection refused")
    return fetch


# --- state -------------------------------------------------------------------

def test_status_and_error_code_are_read_from_the_session():
    s = state("http://x", "c1", fetch=_serving(
        {"status": "running", "labels": {"omnigent.last_task_error_code": "E7"}}))
    assert s == State("running", "E7")


def test_an_unreachable_server_is_unknown_not_a_guess():
    """The caller counts consecutive unknowns and keeps waiting. Any other
    default would start recovery against a session that may still be live."""
    assert state("http://x", "c1", fetch=_failing()) == State("unknown", "")


def test_malformed_json_is_also_unknown():
    assert state("http://x", "c1", fetch=_serving("{not json")) == State("unknown", "")


def test_a_session_with_no_labels_has_no_error_code():
    s = state("http://x", "c1", fetch=_serving({"status": "idle", "labels": None}))
    assert s == State("idle", "")


def test_the_conversation_id_reaches_the_url():
    seen = {}

    def fetch(url):
        seen["url"] = url
        return json.dumps({"status": "idle"})

    state("http://srv", "conv-42", fetch=fetch)
    assert seen["url"] == "http://srv/v1/sessions/conv-42"


# --- died --------------------------------------------------------------------

@pytest.mark.parametrize("status", ["failed", "error", "cancelled"])
def test_explicit_failure_states_are_death(status):
    assert died(status, "") is True


@pytest.mark.parametrize("status", ["idle", "running", "", "unknown", "queued"])
def test_everything_else_is_alive(status):
    """IDLE IS NOT DEATH. A coordinator awaiting a sub-agent is idle, and
    reading that as death starts recovery against a live session."""
    assert died(status, "") is False


def test_a_task_error_code_is_death_whatever_the_status_says():
    assert died("running", "E7") is True


def test_a_whitespace_only_error_code_is_not_an_error():
    assert died("running", "   ") is False


# --- last_assistant_text -----------------------------------------------------

def test_assistant_text_is_joined_newest_first():
    txt = last_assistant_text("http://x", "c1", n=3, fetch=_serving({"data": [
        {"role": "assistant", "content": [{"text": "second"}]},
        {"role": "user", "content": [{"text": "ignored"}]},
        {"role": "assistant", "content": [{"text": "first"}]},
    ]}))
    assert txt == "second\nfirst"


def test_only_assistant_items_are_read():
    txt = last_assistant_text("http://x", "c1", fetch=_serving({"data": [
        {"role": "user", "content": [{"text": "a question"}]},
    ]}))
    assert txt == ""


def test_it_stops_at_n_items():
    data = [{"role": "assistant", "content": [{"text": str(i)}]} for i in range(10)]
    assert last_assistant_text("http://x", "c1", n=2,
                               fetch=_serving({"data": data})) == "0\n1"


def test_a_failed_lookup_RAISES_rather_than_returning_empty():
    """"no assistant text" and "could not read the session" must not be the
    same value: the caller uses this to detect a provider limit message, and
    conflating them silently skips the check."""
    with pytest.raises(LookupFailed):
        last_assistant_text("http://x", "c1", fetch=_failing())


def test_a_200_with_the_wrong_SHAPE_is_also_a_failed_lookup():
    with pytest.raises(LookupFailed):
        last_assistant_text("http://x", "c1", fetch=_serving({"data": "not a list"}))


def test_a_200_with_unparseable_json_is_a_failed_lookup():
    with pytest.raises(LookupFailed):
        last_assistant_text("http://x", "c1", fetch=_serving("{{{"))


def test_malformed_content_entries_are_skipped_not_fatal():
    txt = last_assistant_text("http://x", "c1", fetch=_serving({"data": [
        {"role": "assistant", "content": ["a bare string", {"text": "kept"}, {}]},
    ]}))
    assert txt == "kept"


# --- settle ------------------------------------------------------------------

from coordinator.session import item_count, settle


def test_a_quiet_idle_session_settles_after_the_required_polls():
    s = settle("idle", 31, 31, 2, needed=3)
    assert (s.settled, s.stable_polls, s.count) == (True, 3, 31)


def test_it_does_not_settle_before_the_required_polls():
    assert settle("idle", 31, 31, 1, needed=3).settled is False


def test_a_RUNNING_session_never_settles_however_stable_its_count():
    """A session mid-tool-call produces no items while it works. Counting that
    as quiet would derive an outcome from a coordinator still writing."""
    s = settle("running", 31, 31, 99, needed=3)
    assert (s.settled, s.stable_polls) == (False, 0)


def test_a_GROWING_session_resets_the_streak():
    s = settle("idle", 32, 31, 2, needed=3)
    assert (s.settled, s.stable_polls, s.count) == (False, 0, 32)


def test_a_FAILED_LOOKUP_resets_the_streak_rather_than_extending_it():
    """"I cannot see the session" is not "nothing is happening". Treating an
    unreadable session as quiet would settle during a server outage -- exactly
    when the coordinator is least likely to have finished."""
    s = settle("idle", None, 31, 2, needed=3)
    assert (s.settled, s.stable_polls) == (False, 0)
    assert s.count == 31, "the last known count must survive a failed poll"


def test_the_streak_survives_across_consecutive_quiet_polls():
    prev, streak = None, 0
    for _ in range(4):
        s = settle("idle", 31, prev, streak, needed=3)
        prev, streak = s.count, s.stable_polls
    assert s.settled is True


def test_one_noisy_poll_in_the_middle_restarts_the_count():
    prev, streak = 31, 2
    s = settle("idle", 32, prev, streak, needed=3)      # a new item appears
    assert s.stable_polls == 0
    s = settle("idle", 32, s.count, s.stable_polls, needed=3)
    assert s.settled is False, "must start again, not resume at 2"


# --- item_count --------------------------------------------------------------

def test_item_count_reads_the_list_length():
    assert item_count("http://x", "c1", fetch=_serving({"items": [1, 2, 3]})) == 3


def test_item_count_is_None_when_the_session_cannot_be_read():
    assert item_count("http://x", "c1", fetch=_failing()) is None


def test_item_count_is_None_when_items_is_missing_or_wrong_shaped():
    assert item_count("http://x", "c1", fetch=_serving({})) is None
    assert item_count("http://x", "c1", fetch=_serving({"items": "no"})) is None
