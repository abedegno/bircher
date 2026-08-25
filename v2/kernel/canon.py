"""Versioned canonical form and hashing.

The spec requires hashing over raw bytes or a precisely versioned canonical
form, never an informal serialization whose behaviour can drift. Floats are
refused outright: their textual encoding varies across versions and platforms,
and a hash that changes silently is worse than one that fails loudly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CANON_VERSION = 1


def _check(obj: Any) -> None:
    """Recurse. Checking only the top level would leave nested floats -- the
    common case in a payload -- silently hashable."""
    if isinstance(obj, float):
        raise TypeError(
            f"{type(obj).__name__} has no stable canonical encoding; "
            "convert to int, str or bool before hashing"
        )
    if isinstance(obj, dict):
        for key, value in obj.items():
            # Keys too: json.dumps stringifies a float key, so a
            # platform-dependent float rendering would land in the canonical
            # bytes -- precisely what this guard exists to prevent.
            _check(key)
            _check(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _check(value)


def canonical_bytes(obj: Any) -> bytes:
    """The canonical encoding of *obj*. Used for storage and as hash input.

    Deliberately NOT versioned: this is also what gets written into fact
    payloads, and wrapping it in a version envelope would change the stored
    representation rather than only the hash. Versioning belongs to
    :func:`canonical_hash`.
    """
    _check(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_hash(obj: Any) -> str:
    """Hash *obj* with the canonical-form version bound into the digest.

    The version must be part of what is hashed, not merely a constant beside
    it: if the encoding rules change, every hash must change with them. An
    earlier version defined CANON_VERSION and recorded it nowhere, while a
    test named "canon_version_is_recorded" asserted only that the constant
    existed -- it passed identically whether or not anything used it.
    """
    _check(obj)
    envelope = json.dumps(
        {"v": CANON_VERSION, "d": obj},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return content_hash(envelope)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
