import re

from kernel.ids import Clock, new_id, now_us


def test_ids_are_prefixed_and_unique():
    a, b = new_id("run"), new_id("run")
    assert a.startswith("run_") and b.startswith("run_")
    assert a != b


def test_id_shape_is_stable():
    """Identity semantics are irreversible; pin the shape so a later change is
    a visible test failure rather than a silent integration break."""
    assert re.fullmatch(r"run_[0-9a-f]{32}", new_id("run"))


def test_now_us_is_integer_microseconds_utc():
    t = now_us()
    assert isinstance(t, int)
    assert t > 1_700_000_000_000_000  # microseconds, not seconds or millis


def test_clock_is_injectable_and_advances():
    c = Clock(start_us=1_000_000, step_us=5)
    assert c.now_us() == 1_000_000
    assert c.now_us() == 1_000_005
