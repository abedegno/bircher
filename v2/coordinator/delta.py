"""The PR's own three-dot delta, digested (gate integrity, spec §4).

Ported from `_pr_delta_digest` in `batch/run-queue.sh`, which is gone: one
implementation, used by the derivation (to record what was reviewed on the
verdict fact) and by the runner's re-stamp after an update-branch (through
`coordinator.cli delta`).

GitHub's compare is three-dot, so `base...ref` is the change the PR
contributes and never the base branch's own commits. That property is what
makes the digest comparable across an update-branch: that operation MERGES
the base into the head (it does not rebase), so the new head CONTAINS the new
base, the merge-base IS the new base, and the compare still yields only the
PR's work.

An unprovable delta is `None`, never "assume equal": GitHub caps the
changed-file list at 300, omits `patch` for binaries, pure renames and
anything over its size limit, and truncates a large tree. Digesting a partial
answer would produce a confident wrong one, which here means merging code no
reviewer read.
"""

from __future__ import annotations

import hashlib
import json

from coordinator.ci import GhError, _gh

#: GitHub's documented cap on a compare's file list; at exactly 300 it may be truncated.
_FILE_CAP = 300


def canonical_delta(compare: dict, tree: dict) -> str | None:
    """The canonical text of the delta, or None when it cannot be established."""
    files = (compare or {}).get("files")
    if not isinstance(files, list):
        return None
    if not (1 <= len(files) < _FILE_CAP):
        return None
    if any("patch" not in f for f in files):
        return None
    # Require an explicit false: an absent or null `truncated` is an answer we
    # did not get, not a reassuring one.
    if (tree or {}).get("truncated") is not False:
        return None
    entries = {e.get("path"): f"{e.get('mode')}|{e.get('type')}" for e in (tree.get("tree") or [])}
    rows = []
    for f in sorted(files, key=lambda f: f["filename"]):
        rows.append({
            "filename": f["filename"],
            "previous_filename": f.get("previous_filename"),
            "status": f.get("status"),
            "sha": f.get("sha"),
            "patch": f.get("patch"),
            "entry": entries.get(f["filename"], "ABSENT"),
        })
    # Sorted keys, no whitespace, unicode kept raw: the same bytes `jq -cS` produced.
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def delta_digest(repo: str, base: str, ref: str, gh=_gh) -> str:
    """sha256 of the canonical delta of `base...ref`, or "" when unprovable.

    Hashes the canonical text WITH a trailing newline appended, not the bare
    text `canonical_delta` returns. The retired bash `_pr_delta_digest` piped
    `jq -cS`'s stdout -- which jq always newline-terminates -- straight into
    `sha256sum`, so that byte was part of every digest it ever produced.
    Dropping it here would still be internally consistent (every digest this
    module computes agrees with every other one), but it would silently
    diverge from what the retired implementation actually hashed for
    identical delta content, which is exactly the property
    `test_the_port_reproduces_the_bash_digest_for_the_fixture` checks against
    a bash-produced oracle value.
    """
    try:
        compare = json.loads(gh(["api", f"repos/{repo}/compare/{base}...{ref}"]) or "null")
        tree = json.loads(gh(["api", f"repos/{repo}/git/trees/{ref}?recursive=1"]) or "null")
    # OSError too: `_gh` execs a binary, and a runner with no `gh` on PATH
    # raises FileNotFoundError, not GhError. An unprovable delta is the
    # answer here in every case -- a traceback out of this function aborts
    # a derivation that is otherwise complete and correct.
    except (GhError, ValueError, TypeError, RuntimeError, OSError):
        return ""
    text = canonical_delta(compare, tree)
    if text is None:
        return ""
    return hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()
