"""`EffectClass`, alone, with no dependency on `kernel.effects` or
`kernel.contract`.

Split out of `kernel.effects` because `kernel.contract` needs `EffectClass`
as `CONTRACTS`' dict keys, and `kernel.effects` needs `kernel.contract`'s
`create_body`/`parse` so its own executor and the reconciler (Tasks 3/4) read
a session-control argv through the SAME parse `check` uses, not a second one.
Those two needs, both satisfied by a plain top-level import, make
`kernel.contract` and `kernel.effects` import each other -- and whichever one
a caller imports first hits the other before it has defined anything. Neither
module needs the OTHER'S full machinery for this, only this one class, so it
lives here instead, imported by both without either depending on the other.

`kernel.effects` still re-exports `EffectClass` (`from kernel.effect_class
import EffectClass` at its own top), so every existing `from kernel.effects
import EffectClass` keeps working.
"""

from __future__ import annotations


class EffectClass:
    REF_UPDATE = "ref_update"
    PULL_REQUEST = "pull_request"
    # Merge is its own class. Folding it into PULL_REQUEST meant the effect
    # journal could not distinguish opening a PR from merging one, and the
    # authority-bearing operation shared a gate with the routine one.
    MERGE = "merge"
    STATUS_CHECK = "status_check"
    COMMENT = "comment"
    ISSUE_OR_LABEL = "issue_or_label"
    REVERT_OR_RECOVERY = "revert_or_recovery"
    CREDENTIAL_LIFECYCLE = "credential_lifecycle"
    SESSION_CONTROL = "session_control"
    # Creating an issue mints work another run will consume (shaping spec
    # §2 *The effect class*): a blast radius of its own, kept distinct from
    # editing or labelling one (ruling 6).
    ISSUE_CREATE = "issue_create"
    ALL = frozenset({
        REF_UPDATE, PULL_REQUEST, MERGE, STATUS_CHECK, COMMENT,
        ISSUE_OR_LABEL, REVERT_OR_RECOVERY, CREDENTIAL_LIFECYCLE, SESSION_CONTROL,
        ISSUE_CREATE,
    })
