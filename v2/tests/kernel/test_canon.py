import pytest

from kernel.canon import CANON_VERSION, canonical_bytes, content_hash


def test_key_order_does_not_change_the_hash():
    assert content_hash(canonical_bytes({"a": 1, "b": 2})) == content_hash(
        canonical_bytes({"b": 2, "a": 1})
    )


def test_different_values_change_the_hash():
    assert content_hash(canonical_bytes({"a": 1})) != content_hash(canonical_bytes({"a": 2}))


def test_hash_is_sha256_hex():
    assert content_hash(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_canonical_form_rejects_types_whose_encoding_could_drift():
    """floats have no single stable textual form -- refuse them rather than
    hash something that may encode differently on another version."""
    with pytest.raises(TypeError):
        canonical_bytes({"x": 1.5})


def test_nested_floats_are_also_rejected():
    """The check must recurse, or the guard covers only the top level."""
    with pytest.raises(TypeError):
        canonical_bytes({"a": {"b": [1, 2.5]}})


def test_canon_version_is_recorded():
    assert isinstance(CANON_VERSION, int) and CANON_VERSION >= 1
