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
        for value in obj.values():
            _check(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _check(value)


def canonical_bytes(obj: Any) -> bytes:
    _check(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
