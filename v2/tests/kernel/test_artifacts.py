import pytest

from kernel.artifacts import VerdictBinding, binding_hash, is_valid, put_artifact
from kernel.ids import Clock
from kernel.store import Store


def _b(**over):
    base = dict(
        artifact_hash="a" * 64,
        base_sha="b" * 40,
        context_bundle_hash="c" * 64,
        policy_version=3,
    )
    base.update(over)
    return VerdictBinding(**base)


def test_identical_bindings_are_valid():
    assert is_valid(_b(), _b())


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_hash", "d" * 64),
        ("base_sha", "e" * 40),
        ("context_bundle_hash", "f" * 64),
        ("policy_version", 4),
    ],
)
def test_changing_any_bound_input_invalidates_the_verdict(field, value):
    """The spec binds five inputs. Each is checked separately: one combined
    assertion would pass while four of the five went unverified."""
    assert not is_valid(_b(), _b(**{field: value})), f"{field} did not invalidate"


def test_binding_hash_is_stable():
    assert binding_hash(_b()) == binding_hash(_b())


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_hash", "refs/heads/main"),
        ("base_sha", "main"),
        ("context_bundle_hash", "latest"),
    ],
)
def test_binding_refuses_a_mutable_reference(field, value):
    """An approval authorizes a tuple of immutable inputs -- never a filename,
    branch name, issue number or 'latest'."""
    with pytest.raises(ValueError, match="immutable"):
        _b(**{field: value})


def test_put_artifact_is_content_addressed_and_idempotent(tmp_path):
    s = Store.open(tmp_path / "k.db", clock=Clock(start_us=1))
    h1 = put_artifact(s, b"# spec")
    h2 = put_artifact(s, b"# spec")
    assert h1 == h2
    assert s._conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1


def test_different_bytes_get_different_hashes(tmp_path):
    s = Store.open(tmp_path / "k.db", clock=Clock(start_us=1))
    assert put_artifact(s, b"one") != put_artifact(s, b"two")
