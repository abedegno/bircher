import pytest

from kernel.ids import Clock
from kernel.ownership import OwnershipLost, acquire, current_generation
from kernel.store import Store


@pytest.fixture
def store():
    s = Store.open(":memory:", clock=Clock(start_us=1_000))
    s.create_run(run_id="r", base_repo="o/r", base_sha="a" * 40)
    return s


def test_first_acquisition_yields_generation_one(store):
    assert acquire(store, "r", owner="attempt_1") == 1


def test_generations_increase_monotonically(store):
    assert [acquire(store, "r", owner=f"a{i}") for i in range(3)] == [1, 2, 3]


def test_acquisition_is_atomic_not_merely_recorded(store):
    """'Ownership recorded' is not exclusion: two acquisitions must never
    believe they hold the same generation."""
    g1 = acquire(store, "r", owner="a")
    g2 = acquire(store, "r", owner="b")
    assert g1 != g2
    assert current_generation(store, "r") == g2


def test_superseded_generation_is_detectable(store):
    g_old = acquire(store, "r", owner="a")
    acquire(store, "r", owner="b")
    assert g_old < current_generation(store, "r")


def test_acquire_records_a_fact(store):
    acquire(store, "r", owner="a")
    assert "ownership_acquired" in [f.kind for f in store.facts_for("r")]


def test_acquire_on_unknown_run_raises(store):
    with pytest.raises(KeyError):
        acquire(store, "nope", owner="a")


class _InterleavingConn:
    """Delegates to the real connection, running a competing acquisition once,
    between a SELECT of owner_generation and whatever writes it.

    sqlite3.Connection.execute is read-only so it cannot be patched directly;
    Store._conn is an ordinary attribute, so the whole connection is wrapped.
    """

    def __init__(self, real, on_select):
        self._real, self._on_select, self._fired = real, on_select, False

    def execute(self, sql, *args):
        if not self._fired and sql.lstrip().upper().startswith("SELECT OWNER_GENERATION"):
            self._fired = True
            self._on_select()
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_concurrent_acquisition_cannot_share_a_generation(store):
    """Forces the interleaving rather than sampling for it.

    A sequential test cannot tell CAS from read-then-write: both yield
    distinct generations. This runs a competing acquisition in the window a
    read-then-write implementation leaves open -- between reading
    owner_generation and writing it back -- and asserts no update was lost.

    Under the correct single-statement CAS there is no SELECT in acquire, so
    the interloper never fires and this passes trivially. That is the point: it
    is red only for the broken shape.
    """
    import kernel.ownership as own

    real = store._conn

    def interlope():
        store._conn = real  # avoid recursing through the wrapper
        own.acquire(store, "r", owner="interloper")

    store._conn = _InterleavingConn(real, interlope)
    try:
        victim_gen = own.acquire(store, "r", owner="victim")
    finally:
        store._conn = real

    assert victim_gen == current_generation(store, "r"), (
        f"victim holds generation {victim_gen} but current is "
        f"{current_generation(store, 'r')}: a competing acquisition was lost"
    )
