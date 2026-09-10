# v2/kernel/filing.py
"""What `perform` refuses of a filing effect, and the argvs the filing step
composes (shaping spec §2 *What perform refuses*, *Bound to the effect, not
only to the metadata*, *One satisfied effect per obligation*).

A command's from-set guards the FACT; the create is an effect performed
between commands, and `perform` checked the run's state only for a merge.
So every obligation kind this design adds carries a precondition the kernel
checks when the effect is journalled (ruling 19); the argv is bound to the
targets the kernel holds (ruling 21); every argv shape this design adds is
admitted only with its kind (round 11, finding 1); and one satisfied effect
exists per obligation (ruling 23). All of it reads what the kernel already
holds -- the plan, the obligation rows, the facts these rows name.

The composers are here too, so the coordinator and the binding read one
argv shape and not two.
"""
from __future__ import annotations

import re

from kernel import front, slices
from kernel.authz import NotAuthorized
from kernel.contract import endpoint_path, parse
from kernel.dispatch import Role, role_for
from kernel.effect_class import EffectClass

KINDS = front.FILING_KINDS | front.COMPLETION_KINDS
_WITH_HASH = frozenset({"slice_issue", "slice_dependency", "slice_queue", "umbrella", "umbrella_label"})
_WITH_SLICE = frozenset({"slice_issue", "slice_dependency", "slice_queue"})
#: The endpoint the contract admits for a blocked-by link, matched THE WAY THE
#: CONTRACT MATCHES IT: over `endpoint_path`, unanchored at the front. The old
#: `^repos/...` recognised only the kernel's own spelling, so
#: `/repos/o/r/issues/41/...` and the full `https://api.github.com/repos/...`
#: URL -- both admitted by the same rule in `contract.CONTRACTS` -- demanded no
#: obligation at all (final review, finding 1).
_BLOCKED_BY = re.compile(r"/issues/(\d+)/dependencies/blocked_by$")
#: `gh issue close` takes an issue number or an issue URL, and they close the
#: same issue.
_ISSUE_URL = re.compile(r"/issues/(\d+)$")
_VALUED = frozenset({"--repo", "--title", "--label", "--body", "--add-label", "--remove-label",
                     "--comment", "-X", "-f", "--jq"})


# -- composers -------------------------------------------------------------------

def create_argv(repo: str, title: str, labels: list[str]) -> list[str]:
    argv = ["gh", "issue", "create", "--repo", repo, "--title", title]
    for lab in labels:
        argv += ["--label", lab]
    return argv


def link_argv(repo: str, child: int, blocker_id: int) -> list[str]:
    return ["gh", "api", f"repos/{repo}/issues/{child}/dependencies/blocked_by", "-X", "POST",
            "-f", f"issue_id={blocker_id}"]


def queue_argv(repo: str, child: int) -> list[str]:
    return ["gh", "issue", "edit", str(child), "--repo", repo, "--add-label", "bircher:queued"]


def comment_argv(repo: str, issue: int, body: str) -> list[str]:
    return ["gh", "issue", "comment", str(issue), "--repo", repo, "--body", body]


def umbrella_label_argv(repo: str, parent: int) -> list[str]:
    return ["gh", "issue", "edit", str(parent), "--repo", repo, "--add-label", "bircher:sliced",
            "--remove-label", "bircher:running"]


def close_argv(repo: str, parent: int) -> list[str]:
    return ["gh", "issue", "close", str(parent), "--repo", repo]


# -- what the kernel holds ---------------------------------------------------------

def expected_labels(store, run_id: str) -> list[str]:
    """`bircher:slice` plus the parent's inherited policy labels (spec §1)."""
    return sorted(["bircher:slice", *slices.inherited_labels(front.frozen_labels(store, run_id))])


def filed_numbers(store, run_id: str, epoch_n: int) -> dict[int, int]:
    return {n: f.payload["issue"] for n, f in front.slices_filed(store, run_id, epoch_n).items()}


def filed_ids(store, run_id: str, epoch_n: int) -> dict[int, int]:
    return {n: f.payload["issue_id"] for n, f in front.slices_filed(store, run_id, epoch_n).items()}


def _parsed(argv: list[str]):
    return parse(list(argv), _VALUED)


def _blocked_by(ops: list[str]) -> tuple[str, int] | None:
    """`(endpoint, issue)` when *ops* is a `gh api` call on a blocked-by
    endpoint; None otherwise. The endpoint is the CONTRACT's normalisation with
    any leading slash dropped, so the three spellings one rule admits --
    `repos/o/r/...`, `/repos/o/r/...` and `https://api.github.com/repos/o/r/...`
    -- are one shape here. Every path the contract admits is recognised, and
    the BINDING is what refuses one that does not name this run's repo and
    child."""
    if ops[:2] != ["gh", "api"] or len(ops) < 3:
        return None
    path = endpoint_path(ops[2]).lstrip("/")
    m = _BLOCKED_BY.search(path)
    return (path, int(m.group(1))) if m else None


def _issue_operand(tok: str) -> int | None:
    """The issue an operand names: `12`, or a URL/path ending `/issues/12`."""
    if not isinstance(tok, str):
        return None
    # `gh` reads `#12` as issue 12 and tolerates a trailing slash on the URL;
    # both closed the parent with no obligation until the final re-review.
    tok = tok.lstrip("#")
    if tok.isdigit():
        return int(tok)
    m = _ISSUE_URL.search(endpoint_path(tok).rstrip("/"))
    return int(m.group(1)) if m else None


def _labels(p, flag: str) -> set[str]:
    """The labels a `--add-label`/`--remove-label` flag NAMES. `gh` splits each
    value on commas, so `--add-label bircher:queued,bircher:grill` adds
    bircher:queued -- and testing the whole value for membership missed it."""
    return {lab.strip() for v in p.values.get(flag, []) for lab in v.split(",") if lab.strip()}


# -- the mandate -----------------------------------------------------------------

def required_kinds(store, run_id: str, effect_class: str, argv: list[str]) -> frozenset[str]:
    """The obligation kinds an argv SHAPE is admitted with; empty when the
    shape is none this design added. Every shape here is admitted only with
    its kind, whether or not the effect announces one (round 11, finding 1)."""
    if effect_class == EffectClass.ISSUE_CREATE:
        return frozenset({"slice_issue"})
    p = _parsed(argv)
    ops = list(p.operands)
    if _blocked_by(ops) is not None:
        return frozenset({"slice_dependency"})
    added = _labels(p, "--add-label")
    if "bircher:queued" in added:
        return frozenset({"slice_queue"})
    if "bircher:sliced" in added:
        return frozenset({"umbrella_label"})
    if ops[:3] == ["gh", "issue", "comment"] and any(
            v.startswith("bircher: sliced ") for v in p.values.get("--body", [])):
        return frozenset({"umbrella", "umbrella_close"})
    if ops[:3] == ["gh", "issue", "close"] and len(ops) > 3 and store.run_state(run_id) == "sliced" \
            and _issue_operand(ops[3]) == front.issue_number(store, run_id):
        return frozenset({"parent_close"})
    return frozenset()


def canonical_obligations(store, run_id: str, epoch_n: int) -> list[dict]:
    """Every obligation this epoch admits, whole: the five the accepted plan
    implies, plus the two completion obligations, which carry neither
    `plan_hash` nor `slice` (spec §2, the per-kind table).

    Checking the fields the refusals happen to name is not checking the
    obligation. `front.satisfied_obligation` keys on WHOLE-dict equality, so a
    second `slice_issue` for a filed slice with `parent` omitted or wrong is a
    different dict, satisfies nothing, and used to pass every field check and
    mint a duplicate child. Membership of this set binds `parent` -- and every
    field nobody thought to name -- by construction.
    """
    return front.filing_obligations(store, run_id, epoch_n) + [
        {"kind": k, "run": run_id, "epoch": epoch_n} for k in sorted(front.COMPLETION_KINDS)
    ]


# -- the check -------------------------------------------------------------------

def check_filing_effect(store, run_id: str, generation: int, effect_class: str, key: str, intent: dict) -> None:
    argv = list(intent.get("argv") or [])
    ob = intent.get("obligation") or {}
    kind = ob.get("kind")
    demanded = required_kinds(store, run_id, effect_class, argv)
    if demanded and kind not in demanded:
        raise NotAuthorized(
            f"{effect_class} {argv[:4]}: this argv shape is admitted only with an obligation of kind "
            f"{sorted(demanded)}, not {kind!r} (shaping spec §2 *Bound to the effect*)"
        )
    if kind not in KINDS:
        return
    if store.effect_by_key(key, run_id=run_id) is not None:
        # A replay of a confirmed key, or an uncertain retry: `_perform_unhalted`'s
        # key rules decide, and uniqueness must not refuse the replay.
        return
    _common(store, run_id, generation, ob)
    epoch_n = front.epoch(store, run_id)
    plan = front.accepted_plan(store, run_id, epoch_n)
    _per_kind(store, run_id, generation, ob, plan, epoch_n)
    # AFTER the per-field refusals, so their messages -- the accepted hash, the
    # epoch, the slice, the edge, the missing slice_filed -- reach the caller
    # rather than one blanket "not canonical" for every malformed obligation.
    if ob not in canonical_obligations(store, run_id, epoch_n):
        raise NotAuthorized(
            f"{ob.get('kind')}: the obligation {ob} is not one the accepted plan implies for epoch "
            f"{epoch_n}; a filing effect carries an obligation the kernel would itself compose "
            "(shaping spec §2 *One satisfied effect per obligation*)"
        )
    _binding(store, run_id, effect_class, argv, intent, ob, plan, epoch_n)
    if front.satisfied_obligation(store, run_id, ob) is not None:
        raise NotAuthorized(f"one satisfied effect per obligation: {ob} is already satisfied (ruling 23)")


def _common(store, run_id: str, generation: int, ob: dict) -> None:
    kind = ob["kind"]
    if store.run_state(run_id) != "sliced":
        raise NotAuthorized(f"{kind}: the run is {store.run_state(run_id)!r}, not sliced")
    if role_for(store, run_id, generation) != Role.OPERATOR:
        raise NotAuthorized(f"{kind}: a filing effect comes from the coordinator's operator dispatch, not an author or reviewer")
    epoch_n = front.epoch(store, run_id)
    if ob.get("run") != run_id or ob.get("epoch") != epoch_n:
        raise NotAuthorized(f"{kind}: obligation names run {ob.get('run')!r} epoch {ob.get('epoch')!r}; this is {run_id!r} epoch {epoch_n}")
    accepted = front.accepted_slices_hash(store, run_id, epoch_n)
    plan = front.accepted_plan(store, run_id, epoch_n)
    # Before every reader of them. `umbrella_close` and `parent_close` are not
    # in `_WITH_HASH`, so nothing else refuses first, and `plan.by_number()` on
    # None and `accepted[:8]` in the binding raise AttributeError and TypeError
    # -- which `perform` does not route through `shadow_or_raise`, so a
    # modelling gap becomes a hard failure under shadow and a traceback with no
    # reason under enforce.
    if accepted is None or plan is None:
        raise NotAuthorized(
            f"{kind}: no accepted slice plan in epoch {epoch_n}: every filing effect is read from "
            "the plan a reviewer accepted and the gate passed"
        )
    if kind in _WITH_HASH and ob.get("plan_hash") != accepted:
        raise NotAuthorized(f"{kind}: obligation names plan_hash {str(ob.get('plan_hash'))[:12]}, not the accepted hash {str(accepted)[:12]}")
    if kind in _WITH_SLICE and ob.get("slice") not in plan.by_number():
        raise NotAuthorized(f"{kind}: slice {ob.get('slice')!r} is not in the accepted plan")
    if kind == "slice_dependency" and ob.get("blocker") not in plan.by_number():
        raise NotAuthorized(f"slice_dependency: blocker {ob.get('blocker')!r} is not in the accepted plan")


def _per_kind(store, run_id: str, generation: int, ob: dict, plan, epoch_n: int) -> None:
    kind = ob["kind"]
    filed = front.slices_filed(store, run_id, epoch_n)
    if kind == "slice_issue":
        missing = [b for b in plan.by_number()[ob["slice"]].depends_on if b not in filed]
        if missing:
            raise NotAuthorized(f"slice_issue: blocker(s) {missing} of slice {ob['slice']} have no filed issue")
    elif kind == "slice_dependency":
        if (ob["slice"], ob["blocker"]) not in plan.edges():
            raise NotAuthorized(f"slice_dependency: the edge {ob['slice']} <- {ob['blocker']} is not in the plan")
        for n in (ob["slice"], ob["blocker"]):
            if n not in filed:
                raise NotAuthorized(f"slice_dependency: slice {n} has no slice_filed in epoch {epoch_n}")
    elif kind == "slice_queue":
        owed = [o for o in front.unsatisfied_filing(store, run_id, epoch_n)
                if o["kind"] in ("slice_issue", "slice_dependency")]
        if owed:
            raise NotAuthorized(f"slice_queue: {len(owed)} create/link obligation(s) unsatisfied, first {owed[0]['kind']} "
                                f"of slice {owed[0]['slice']}: a queued child has every link")
        # A satisfied obligation is not the fact. A create confirmed with its
        # `record_slice_filed` never landed -- the crash window of §7 -- leaves
        # the obligations satisfied and no fact for the binding to read the
        # child's number from, as the dependency clause above already refuses.
        if ob["slice"] not in filed:
            raise NotAuthorized(f"slice_queue: slice {ob['slice']} has no slice_filed in epoch {epoch_n}: "
                                "the fact the label's target is read from")
    elif kind in ("umbrella", "umbrella_label"):
        for s in plan.slices:
            if s.number not in filed:
                raise NotAuthorized(f"{kind}: slice {s.number} has no slice_filed")
    else:  # umbrella_close, parent_close
        if front.filing_complete(store, run_id, epoch_n) is None:
            raise NotAuthorized(f"{kind}: no filing_complete in epoch {epoch_n}")
        if front.observed_closed_under(store, run_id, epoch_n, generation) is None:
            raise NotAuthorized(f"{kind}: no children_observed_closed under generation {generation}")


def _binding(store, run_id: str, effect_class: str, argv: list[str], intent: dict, ob: dict, plan, epoch_n: int) -> None:
    kind = ob["kind"]
    repo, parent = store.run_base_repo(run_id), front.issue_number(store, run_id)
    p = _parsed(argv)
    ops = list(p.operands)
    # Each read is made in the branch that needs it. Computing them all up
    # front meant `hash8` was derived for kinds that never use a plan hash,
    # so a kind whose preconditions do not mention the plan still failed on
    # `None[:8]` before its own binding could refuse anything.
    if kind == "slice_issue":
        if effect_class != EffectClass.ISSUE_CREATE:
            raise NotAuthorized("slice_issue is admitted on issue_create only")
        numbers = filed_numbers(store, run_id, epoch_n)
        hash8 = front.accepted_slices_hash(store, run_id, epoch_n)[:8]
        s = plan.by_number()[ob["slice"]]
        title, body = slices.render_child(s, parent, hash8, numbers)
        if p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"slice_issue: --repo {p.values.get('--repo')} is not the run's repo {repo!r}")
        if p.values.get("--title") != [title]:
            raise NotAuthorized(f"slice_issue: --title {p.values.get('--title')} is not the slice's title {title!r}")
        blob = store.read_blob((intent.get("body") or {}).get("artifact") or "")
        if blob != body.encode("utf-8"):
            raise NotAuthorized("slice_issue: the body artefact is not render_child over the accepted plan")
        if sorted(p.values.get("--label", [])) != expected_labels(store, run_id):
            raise NotAuthorized(f"slice_issue: labels {sorted(p.values.get('--label', []))} are not {expected_labels(store, run_id)}")
        return
    if kind == "slice_dependency":
        numbers, ids = filed_numbers(store, run_id, epoch_n), filed_ids(store, run_id, epoch_n)
        # The BINDING is byte-exact on the composer's spelling: the mandate
        # recognises every spelling the contract admits, but only the kernel's
        # own -- no scheme, no authority, no query -- is bound, so an obligation
        # can never be spent on a call aimed at another host (final re-review).
        want = f"repos/{repo}/issues/{numbers[ob['slice']]}/dependencies/blocked_by"
        if ops[:2] != ["gh", "api"] or len(ops) < 3 or ops[2] != want:
            raise NotAuthorized(f"slice_dependency: the path is not exactly {want!r}")
        if p.values.get("-f") != [f"issue_id={ids[ob['blocker']]}"]:
            raise NotAuthorized(f"slice_dependency: -f {p.values.get('-f')} is not issue_id={ids[ob['blocker']]}, the blocker's database id")
        return
    if kind == "slice_queue":
        child = filed_numbers(store, run_id, epoch_n)[ob["slice"]]
        if ops[:4] != ["gh", "issue", "edit", str(child)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"slice_queue: the argv does not edit child {child} of {repo}")
        if p.values.get("--add-label") != ["bircher:queued"] or p.values.get("--remove-label"):
            raise NotAuthorized("slice_queue: adds exactly bircher:queued and removes nothing")
        return
    if kind in ("umbrella", "umbrella_close"):
        if ops[:4] != ["gh", "issue", "comment", str(parent)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"{kind}: the comment is not on the parent #{parent} of {repo}")
        numbers = filed_numbers(store, run_id, epoch_n)
        if kind == "umbrella":
            want = slices.umbrella_body(front.accepted_slices_hash(store, run_id, epoch_n)[:8], plan, numbers)
        else:
            states = {s.number: front.current_closure(store, run_id, epoch_n, s.number).payload["state"] for s in plan.slices}
            want = slices.completion_body(plan, numbers, states)
        if p.values.get("--body") != [want]:
            raise NotAuthorized(f"{kind}: the body is not the rendered {kind} text")
        return
    if kind == "umbrella_label":
        if ops[:4] != ["gh", "issue", "edit", str(parent)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"umbrella_label: the argv does not edit the parent #{parent}")
        if p.values.get("--add-label") != ["bircher:sliced"] or p.values.get("--remove-label") != ["bircher:running"]:
            raise NotAuthorized("umbrella_label: adds bircher:sliced and removes bircher:running, nothing else")
        return
    if kind == "parent_close":
        if ops[:4] != ["gh", "issue", "close", str(parent)] or p.values.get("--repo") != [repo]:
            raise NotAuthorized(f"parent_close: the argv does not close the parent #{parent} of {repo}")
