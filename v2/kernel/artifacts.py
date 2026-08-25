"""Content-addressed artifacts and the verdict binding tuple.

A verdict binds five immutable inputs; changing any one invalidates it. This
is the minimum mechanism preventing yesterday's approval from authorizing
today's object -- the property v1 intends and does not have.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from kernel.canon import canonical_bytes, content_hash

_HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class VerdictBinding:
    artifact_hash: str
    base_sha: str
    context_bundle_hash: str
    reviewer_identity: str
    policy_version: int

    def __post_init__(self) -> None:
        if not _HEX64.match(self.artifact_hash):
            raise ValueError(
                "artifact_hash must be an immutable content hash, not a name, "
                "branch or reference"
            )
        if not _HEX40.match(self.base_sha):
            raise ValueError("base_sha must be an immutable commit id")
        if not _HEX64.match(self.context_bundle_hash):
            raise ValueError("context_bundle_hash must be an immutable content hash")


def binding_hash(b: VerdictBinding) -> str:
    return content_hash(canonical_bytes(asdict(b)))


def is_valid(stored: VerdictBinding, current: VerdictBinding) -> bool:
    return binding_hash(stored) == binding_hash(current)


def put_artifact(store, data: bytes) -> str:
    h = content_hash(data)
    store.put_blob(h, data)  # store.py owns every SQLite construct
    return h
