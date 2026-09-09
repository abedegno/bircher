"""Task 5: what `perform` refuses of a filing effect (shaping spec §2 *What
perform refuses*, *Bound to the effect*, *One satisfied effect per
obligation*), the contract, the delivered forms, the executor."""
import json

import pytest

from kernel import filing, front, slices
from kernel.artifacts import put_artifact
from kernel.authz import NotAuthorized
from kernel.contract import CONTRACTS, ContractViolation, check
from kernel.dispatch import Role
from kernel.effects import EffectClass, NotReplayable, Resolution, _delivered_value, perform, reconcile_typed
from kernel.store import Store
from tests.kernel.front import Front

ISSUE = {"number": 12, "title": "Epic", "body": "B", "labels": ["bircher:autonomous", "bircher:gate-plan"], "comments": []}


class FakeGH:
    """An executor that answers the filing argvs as GitHub would."""
    def __init__(self):
        self.calls, self.next_issue, self.next_comment = [], 40, 1

    def __call__(self, effect_class, intent, key):
        argv = list(intent["argv"])
        self.calls.append((effect_class, argv, intent.get("body")))
        if effect_class == EffectClass.ISSUE_CREATE:
            n = self.next_issue; self.next_issue += 1
            return json.dumps({"url": f"https://github.com/o/r/issues/{n}", "number": n, "id": 1000 + n}, sort_keys=True)
        if argv[:3] == ["gh", "issue", "comment"]:
            c = self.next_comment; self.next_comment += 1
            return f"https://github.com/o/r/issues/{argv[3]}#issuecomment-{c}"
        if argv[:3] == ["gh", "issue", "close"]:
            return "closed"
        return "ok"


def _sliced(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g = f._dispatch(Role.OPERATOR, "coordinator")
    return s, f, g, FakeGH()


def _create(s, f, g, gh, n, *, key=None, title=None, labels=None, body=None, repo="o/r", plan_hash=None, epoch=0):
    """A child create for slice *n*, composed as file_owed would; each
    keyword plants one defect. Robust to the states the refusal tests need:
    before the gate there is no accepted hash (the phase artefact is used), a
    slice outside the plan renders a placeholder, and an unfiled blocker
    leaves the body a placeholder so the KERNEL's refusal is what the test
    sees, not render_child's."""
    h = plan_hash or front.accepted_slices_hash(s, f.run_id, 0) or s.phase_artifact(f.run_id, "slices")
    plan = slices.parse(s.read_blob(h))
    filed = filing.filed_numbers(s, f.run_id, 0)
    if n in plan.by_number():
        try:
            t, b = slices.render_child(plan.by_number()[n], 12, h[:8], filed)
        except slices.PlanError:
            t, b = plan.by_number()[n].title, "unfiled blocker\n"
    else:
        t, b = "x", "x\n"
    title = t if title is None else title
    body = b if body is None else body
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": epoch, "slice": n, "parent": 12, "plan_hash": h}
    argv = filing.create_argv(repo, title, filing.expected_labels(s, f.run_id) if labels is None else labels)
    return perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, key or f"slice:{f.run_id}:{n}:{g}",
                   {"argv": argv, "obligation": ob, "body": {"artifact": put_artifact(s, body.encode())}}, gh)


# -- the contract --------------------------------------------------------------

def test_issue_create_contract():
    ok = filing.create_argv("o/r", "T", ["bircher:slice", "bircher:autonomous"])
    assert check(EffectClass.ISSUE_CREATE, ok).sig == "gh issue create"
    for bad in (["gh", "issue", "create", "--title", "T"],                              # no --repo
                ok + ["--body-file", "/tmp/x"],                                          # the executor's flag
                ok + ["--label", "bircher:queued"], ok + ["--label", "bircher:running"],
                ok + ["--label", "bircher:sliced"],
                ["gh", "issue", "edit", "1", "--repo", "o/r"]):
        with pytest.raises(ContractViolation):
            check(EffectClass.ISSUE_CREATE, bad)
    link = filing.link_argv("o/r", 41, 1040)
    assert check(EffectClass.ISSUE_OR_LABEL, link).url_path.endswith("blocked_by$")
    with pytest.raises(ContractViolation):
        check(EffectClass.ISSUE_OR_LABEL, ["gh", "api", "repos/o/r/issues/41/dependencies/blocked_by", "-X", "DELETE"])
    assert EffectClass.ISSUE_CREATE in EffectClass.ALL and EffectClass.ISSUE_CREATE in CONTRACTS


# -- the mandate ---------------------------------------------------------------

def test_every_added_argv_shape_needs_its_kind(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    cases = [
        (EffectClass.ISSUE_CREATE, filing.create_argv("o/r", "T", ["bircher:slice"]), {"slice_issue"}),
        (EffectClass.ISSUE_OR_LABEL, filing.link_argv("o/r", 41, 1040), {"slice_dependency"}),
        (EffectClass.ISSUE_OR_LABEL, filing.queue_argv("o/r", 41), {"slice_queue"}),
        (EffectClass.ISSUE_OR_LABEL, filing.umbrella_label_argv("o/r", 12), {"umbrella_label"}),
        (EffectClass.COMMENT, filing.comment_argv("o/r", 12, f"bircher: sliced {h[:8]}\n"), {"umbrella", "umbrella_close"}),
        (EffectClass.ISSUE_OR_LABEL, filing.close_argv("o/r", 12), {"parent_close"}),
    ]
    for i, (cls, argv, kinds) in enumerate(cases):
        assert filing.required_kinds(s, f.run_id, cls, argv) == frozenset(kinds), argv
        with pytest.raises(NotAuthorized, match="admitted only with an obligation"):      # none
            perform(s, f.run_id, g, cls, f"k-none-{i}", {"argv": argv}, gh)
        with pytest.raises(NotAuthorized, match="admitted only with an obligation"):      # another kind
            perform(s, f.run_id, g, cls, f"k-other-{i}",
                    {"argv": argv, "obligation": {"kind": "publish", "run": f.run_id, "phase": "spec", "hash": "x"}}, gh)
    assert gh.calls == []
    # The runner's own swap is admitted with no obligation, as today.
    assert filing.required_kinds(s, f.run_id, EffectClass.ISSUE_OR_LABEL,
                                 ["gh", "issue", "edit", "12", "--repo", "o/r", "--add-label", "bircher:running",
                                  "--remove-label", "bircher:queued"]) == frozenset()
    # A close on some OTHER issue, or on the parent of a run not in sliced, demands nothing.
    assert filing.required_kinds(s, f.run_id, EffectClass.ISSUE_OR_LABEL, filing.close_argv("o/r", 99)) == frozenset()


# -- preconditions and bindings for the create -----------------------------------

def test_create_is_bound_to_state_role_epoch_hash_slice_and_body(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    ext = json.loads(_create(s, f, g, gh, 1))
    assert ext["number"] == 40 and "artifact" in gh.calls[-1][2]
    key1 = [r for r in s.effects_for(f.run_id) if r["effect_class"] == "issue_create"][-1]["idempotency_key"]
    f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": key1})         # slice 2's blocker is filed
    with pytest.raises(NotAuthorized, match="operator dispatch"):
        _create(s, f, f._dispatch(Role.AUTHOR, "claude"), gh, 2)
    g = f._dispatch(Role.OPERATOR, "coordinator")
    with pytest.raises(NotAuthorized, match="not in the accepted plan"):
        _create(s, f, g, gh, 9)
    with pytest.raises(NotAuthorized, match="title"):
        _create(s, f, g, gh, 2, title="Wrong")
    with pytest.raises(NotAuthorized, match="render_child"):
        _create(s, f, g, gh, 2, body="Slice 2 of #12 (bircher shaping xxxxxxxx)\n\nother\n")
    with pytest.raises(NotAuthorized, match="labels"):
        _create(s, f, g, gh, 2, labels=["bircher:slice"])                     # inherited ones missing
    with pytest.raises(NotAuthorized, match="repo"):
        _create(s, f, g, gh, 2, repo="o/other")
    assert len([c for c in gh.calls if c[0] == EffectClass.ISSUE_CREATE]) == 1    # nothing else reached GitHub


def test_create_is_refused_before_the_gate_and_against_a_rejected_plan(tmp_path):
    s = Store.open(tmp_path / "k.db")
    f = Front(s, "i12-epic-1", shape=False, issue=ISSUE)
    from tests.kernel.front import SLICES_BYTES
    rejected = f.shape_round(SLICES_BYTES)
    f.review_round("request_revision", findings=b"no")
    accepted = f.shape_round(SLICES_BYTES.replace(b"the API.", b"the API and the client."))
    f.review_round("accept")
    assert s.run_state(f.run_id) == "slices_accepted"
    g = f._dispatch(Role.OPERATOR, "coordinator")
    gh = FakeGH()
    with pytest.raises(NotAuthorized, match="sliced"):                          # before the gate
        _create(s, f, g, gh, 1)
    f.approve()
    plan = slices.parse(s.read_blob(accepted))
    t, b = slices.render_child(plan.slices[0], 12, rejected[:8], {})
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": 0, "slice": 1, "parent": 12, "plan_hash": rejected}
    with pytest.raises(NotAuthorized, match="accepted hash"):                   # the rejected plan's hash
        perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, "k", {"argv": filing.create_argv("o/r", t, filing.expected_labels(s, f.run_id)),
                                                               "obligation": ob, "body": {"artifact": put_artifact(s, b.encode())}}, gh)
    assert gh.calls == []


def test_create_is_refused_while_a_blocker_is_unfiled_and_a_stale_epoch(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    with pytest.raises(NotAuthorized, match="no filed issue"):
        _create(s, f, g, gh, 2)                                                 # slice 2 depends on 1
    h = front.accepted_slices_hash(s, f.run_id, 0)
    plan = front.accepted_plan(s, f.run_id)
    t, b = slices.render_child(plan.slices[0], 12, h[:8], {})
    ob = {"kind": "slice_issue", "run": f.run_id, "epoch": 1, "slice": 1, "parent": 12, "plan_hash": h}
    with pytest.raises(NotAuthorized, match="epoch"):
        perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, "k", {"argv": filing.create_argv("o/r", t, filing.expected_labels(s, f.run_id)),
                                                               "obligation": ob, "body": {"artifact": put_artifact(s, b.encode())}}, gh)


# -- uniqueness and replay -----------------------------------------------------

def test_one_satisfied_effect_per_obligation(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    key = f"slice:{f.run_id}:1:{g}"
    first = _create(s, f, g, gh, 1, key=key)
    assert _create(s, f, g, gh, 1, key=key) == first                              # the confirmed key replays
    assert len([c for c in gh.calls if c[0] == EffectClass.ISSUE_CREATE]) == 1
    g2 = f._dispatch(Role.OPERATOR, "coordinator")
    with pytest.raises(NotAuthorized, match="one satisfied effect per obligation"):
        _create(s, f, g2, gh, 1, key=f"slice:{f.run_id}:1:{g2}")                 # a fresh key, same obligation
    # A create reconciled NOT delivered leaves the obligation unsatisfied: a fresh key is admitted.
    s2 = Store.open(tmp_path / "nd.db")
    f2 = Front(s2, "i12-epic-1", shape=False, issue=ISSUE).to_sliced()
    g2 = f2._dispatch(Role.OPERATOR, "coordinator")
    h = front.accepted_slices_hash(s2, f2.run_id, 0)
    f2._journal(g2, "slice:old", {"argv": ["gh", "issue", "create"], "obligation": {"kind": "slice_issue", "run": f2.run_id, "epoch": 0, "slice": 1, "parent": 12, "plan_hash": h}},
                None, cls="issue_create", state="reconciled")
    assert json.loads(_create(s2, f2, g2, FakeGH(), 1))["number"] == 40


# -- the links, the labels, the umbrella, the completion pair -----------------------

def _file_two(s, f, g, gh):
    for n in (1, 2):
        ext = json.loads(_create(s, f, g, gh, n))
        key = [c for c in s.effects_for(f.run_id) if c["effect_class"] == "issue_create"][-1]["idempotency_key"]
        f._cmd(g, "record_slice_filed", {"slice": n, "effect_key": key})
    return filing.filed_numbers(s, f.run_id, 0), filing.filed_ids(s, f.run_id, 0)


def test_link_binding_and_precondition(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    dep = {"kind": "slice_dependency", "run": f.run_id, "epoch": 0, "plan_hash": h, "slice": 2, "blocker": 1}
    with pytest.raises(NotAuthorized, match="slice_filed"):                     # nothing filed yet
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l0", {"argv": filing.link_argv("o/r", 41, 1040), "obligation": dep}, gh)
    numbers, ids = _file_two(s, f, g, gh)
    with pytest.raises(NotAuthorized, match="path"):                            # a sibling's number
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l1", {"argv": filing.link_argv("o/r", numbers[1], ids[1]), "obligation": dep}, gh)
    with pytest.raises(NotAuthorized, match="issue_id"):                        # not the blocker's id
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l2", {"argv": filing.link_argv("o/r", numbers[2], 999), "obligation": dep}, gh)
    with pytest.raises(NotAuthorized, match="edge"):                            # not in the plan
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l3", {"argv": filing.link_argv("o/r", numbers[1], ids[2]), "obligation": dict(dep, slice=1, blocker=2)}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l4", {"argv": filing.link_argv("o/r", numbers[2], ids[1]), "obligation": dep}, gh) == "ok"


def test_queue_label_waits_for_every_create_and_link(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    q1 = {"kind": "slice_queue", "run": f.run_id, "epoch": 0, "plan_hash": h, "slice": 1}
    numbers, ids = _file_two(s, f, g, gh)
    with pytest.raises(NotAuthorized, match="slice_dependency"):                # the link is owed
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q0", {"argv": filing.queue_argv("o/r", numbers[1]), "obligation": q1}, gh)
    dep = {"kind": "slice_dependency", "run": f.run_id, "epoch": 0, "plan_hash": h, "slice": 2, "blocker": 1}
    perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "l", {"argv": filing.link_argv("o/r", numbers[2], ids[1]), "obligation": dep}, gh)
    with pytest.raises(NotAuthorized, match="child"):                           # the wrong child
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q1", {"argv": filing.queue_argv("o/r", numbers[2]), "obligation": q1}, gh)
    with pytest.raises(NotAuthorized, match="exactly"):                         # a second label
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q2", {"argv": filing.queue_argv("o/r", numbers[1]) + ["--add-label", "x"], "obligation": q1}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q3", {"argv": filing.queue_argv("o/r", numbers[1]), "obligation": q1}, gh) == "ok"


def test_umbrella_and_its_label_wait_for_every_filing_fact(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    plan = front.accepted_plan(s, f.run_id)
    um = {"kind": "umbrella", "run": f.run_id, "epoch": 0, "plan_hash": h}
    ul = {"kind": "umbrella_label", "run": f.run_id, "epoch": 0, "plan_hash": h}
    _create(s, f, g, gh, 1)                                                     # created, not yet recorded
    with pytest.raises(NotAuthorized, match="slice_filed"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "u0", {"argv": filing.umbrella_label_argv("o/r", 12), "obligation": ul}, gh)
    numbers, _ = _file_two(s, f, g, gh)
    body = slices.umbrella_body(h[:8], plan, numbers)
    with pytest.raises(NotAuthorized, match="umbrella"):                        # a child, not the parent
        perform(s, f.run_id, g, EffectClass.COMMENT, "c0", {"argv": filing.comment_argv("o/r", numbers[1], body), "obligation": um}, gh)
    with pytest.raises(NotAuthorized, match="body"):
        perform(s, f.run_id, g, EffectClass.COMMENT, "c1", {"argv": filing.comment_argv("o/r", 12, body + "extra"), "obligation": um}, gh)
    assert perform(s, f.run_id, g, EffectClass.COMMENT, "c2", {"argv": filing.comment_argv("o/r", 12, body), "obligation": um}, gh).endswith("#issuecomment-1")
    with pytest.raises(NotAuthorized, match="remove"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "u1", {"argv": ["gh", "issue", "edit", "12", "--repo", "o/r", "--add-label", "bircher:sliced"], "obligation": ul}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "u2", {"argv": filing.umbrella_label_argv("o/r", 12), "obligation": ul}, gh) == "ok"


def test_completion_pair_needs_filing_complete_and_a_same_generation_observation(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    numbers = f.file_all(g)
    plan = front.accepted_plan(s, f.run_id)
    close = {"kind": "parent_close", "run": f.run_id, "epoch": 0}
    with pytest.raises(NotAuthorized, match="children_observed_closed"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "p0", {"argv": filing.close_argv("o/r", 12), "obligation": close}, gh)
    for n in (1, 2):
        f._cmd(g, "record_slice_closed", {"slice": n, "closed_at": "t", "state": "merged"})
    f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    body = slices.completion_body(plan, numbers, {1: "merged", 2: "merged"})
    uc = {"kind": "umbrella_close", "run": f.run_id, "epoch": 0}
    with pytest.raises(NotAuthorized, match="body"):
        perform(s, f.run_id, g, EffectClass.COMMENT, "cc0", {"argv": filing.comment_argv("o/r", 12, "bircher: sliced complete\n\nnope\n"), "obligation": uc}, gh)
    assert perform(s, f.run_id, g, EffectClass.COMMENT, "cc1", {"argv": filing.comment_argv("o/r", 12, body), "obligation": uc}, gh)
    with pytest.raises(NotAuthorized, match="parent"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "p1", {"argv": filing.close_argv("o/r", numbers[1]), "obligation": close}, gh)
    assert perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "p2", {"argv": filing.close_argv("o/r", 12), "obligation": close}, gh) == "closed"
    g2 = f._dispatch(Role.OPERATOR, "coordinator")                          # a later firing that observed again
    f._cmd(g2, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t2"})
    with pytest.raises(NotAuthorized, match="one satisfied effect per obligation"):
        perform(s, f.run_id, g2, EffectClass.ISSUE_OR_LABEL, "p3", {"argv": filing.close_argv("o/r", 12), "obligation": close}, gh)


# -- review round 1: every refusal is a NotAuthorized with a reason ----------------

def test_queue_label_refuses_a_slice_whose_create_is_confirmed_but_unrecorded(tmp_path):
    """`_per_kind`'s slice_queue clause reads satisfied OBLIGATIONS; the
    binding reads `slice_filed` FACTS. A create confirmed whose
    `record_slice_filed` never landed -- the crash window of §7 -- satisfies
    the obligations and leaves no fact, and the binding used to reach
    `numbers[ob["slice"]]` and raise KeyError, which `perform` does not route
    through `shadow_or_raise`."""
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    for ob in front.filing_obligations(s, f.run_id, 0):
        if ob["kind"] in ("slice_issue", "slice_dependency"):
            f._journal(g, f"x:{ob['kind']}:{ob.get('slice')}:{ob.get('blocker')}",
                       {"argv": ["gh", "issue", "create"], "obligation": ob},
                       json.dumps({"url": "u", "number": 40, "id": 1040}),
                       cls="issue_create" if ob["kind"] == "slice_issue" else "issue_or_label")
    assert front.slices_filed(s, f.run_id, 0) == {}                     # no fact...
    assert [o["kind"] for o in front.unsatisfied_filing(s, f.run_id, 0)] == [
        "slice_queue", "slice_queue", "umbrella", "umbrella_label"]     # ...and no create or link owed
    q1 = {"kind": "slice_queue", "run": f.run_id, "epoch": 0, "plan_hash": h, "slice": 1}
    with pytest.raises(NotAuthorized, match="slice_filed"):
        perform(s, f.run_id, g, EffectClass.ISSUE_OR_LABEL, "q",
                {"argv": filing.queue_argv("o/r", 40), "obligation": q1}, gh)
    assert gh.calls == []


def test_an_epoch_with_no_accepted_plan_is_a_refusal_not_a_traceback(tmp_path, monkeypatch):
    """`umbrella_close` and `parent_close` carry no `plan_hash`, so nothing in
    `_common` refuses first when the epoch has no accepted plan: `_common`'s
    `plan.by_number()` raised AttributeError and `_binding`'s `hash8` raised
    TypeError, neither of which `perform` routes through `shadow_or_raise`.

    The state machine cannot reach this state -- a bundle revision starts a new
    epoch AND leaves `sliced`, so a `sliced` run's current epoch always has an
    accepted hash -- so the query is made to answer None, which is what a
    reader of any future state that separates the two would see. Driven through
    `perform`, so the refusal is shown reaching the caller and not only raised.
    """
    s, f, g, gh = _sliced(tmp_path)
    f.file_all(g)
    for n in (1, 2):
        f._cmd(g, "record_slice_closed", {"slice": n, "closed_at": "t", "state": "merged"})
    f._cmd(g, "record_children_observed_closed", {"parent_state": "open", "observed_at": "t"})
    body = {"artifact": put_artifact(s, b"x\n")}
    monkeypatch.setattr(front, "accepted_slices_hash", lambda *a, **k: None)
    cases = [
        (EffectClass.ISSUE_OR_LABEL, filing.close_argv("o/r", 12),
         {"kind": "parent_close", "run": f.run_id, "epoch": 0}, None),
        (EffectClass.ISSUE_CREATE, filing.create_argv("o/r", "T", filing.expected_labels(s, f.run_id)),
         {"kind": "slice_issue", "run": f.run_id, "epoch": 0, "slice": 1, "parent": 12, "plan_hash": None}, body),
    ]
    for i, (cls, argv, ob, b) in enumerate(cases):
        intent = {"argv": argv, "obligation": ob}
        if b is not None:
            intent["body"] = b
        with pytest.raises(NotAuthorized, match="no accepted slice plan"):
            perform(s, f.run_id, g, cls, f"np-{i}", intent, gh)
    assert gh.calls == []


def test_the_obligation_must_be_one_the_accepted_plan_implies(tmp_path):
    """`front.satisfied_obligation` keys on whole-dict equality, and the field
    checks never name `parent`: a second create for a filed slice with `parent`
    omitted or wrong is a different dict, satisfies nothing, passed every
    check and minted a duplicate child."""
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    _create(s, f, g, gh, 1)
    key1 = [r for r in s.effects_for(f.run_id) if r["effect_class"] == "issue_create"][-1]["idempotency_key"]
    f._cmd(g, "record_slice_filed", {"slice": 1, "effect_key": key1})
    plan = front.accepted_plan(s, f.run_id)
    t, b = slices.render_child(plan.by_number()[1], 12, h[:8], {})
    argv = filing.create_argv("o/r", t, filing.expected_labels(s, f.run_id))
    body = {"artifact": put_artifact(s, b.encode())}
    for i, ob in enumerate((
            {"kind": "slice_issue", "run": f.run_id, "epoch": 0, "slice": 1, "plan_hash": h},           # parent omitted
            {"kind": "slice_issue", "run": f.run_id, "epoch": 0, "slice": 1, "parent": 99, "plan_hash": h})):
        assert front.satisfied_obligation(s, f.run_id, ob) is None      # uniqueness sees nothing
        with pytest.raises(NotAuthorized, match="not one the accepted plan implies"):
            perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, f"dup-{i}",
                    {"argv": argv, "obligation": ob, "body": body}, gh)
    # ...and the canonical obligation for the same slice is still refused by
    # uniqueness, so the set membership did not replace the rule it guards.
    with pytest.raises(NotAuthorized, match="one satisfied effect per obligation"):
        perform(s, f.run_id, g, EffectClass.ISSUE_CREATE, "dup-canonical",
                {"argv": argv, "obligation": {"kind": "slice_issue", "run": f.run_id, "epoch": 0,
                                              "slice": 1, "parent": 12, "plan_hash": h}, "body": body}, gh)
    assert len([c for c in gh.calls if c[0] == EffectClass.ISSUE_CREATE]) == 1


# -- delivered forms -----------------------------------------------------------

def test_delivered_forms(tmp_path):
    s, f, g, gh = _sliced(tmp_path)
    h = front.accepted_slices_hash(s, f.run_id, 0)
    rows = {}
    for kind, cls, argv in (("slice_issue", "issue_create", filing.create_argv("o/r", "T", ["bircher:slice"])),
                            ("slice_dependency", "issue_or_label", filing.link_argv("o/r", 41, 1040)),
                            ("slice_queue", "issue_or_label", filing.queue_argv("o/r", 41)),
                            ("umbrella", "comment", filing.comment_argv("o/r", 12, "b")),
                            ("umbrella_label", "issue_or_label", filing.umbrella_label_argv("o/r", 12)),
                            ("umbrella_close", "comment", filing.comment_argv("o/r", 12, "b")),
                            ("parent_close", "issue_or_label", filing.close_argv("o/r", 12))):
        key = f"u-{kind}"
        f._journal(g, key, {"argv": argv, "obligation": {"kind": kind, "run": f.run_id, "epoch": 0, "plan_hash": h}}, None,
                   cls=cls, state="uncertain")
        rows[kind] = key
    assert json.loads(_delivered_value(s, f.run_id, rows["slice_issue"], json.dumps({"url": "u", "number": 40, "id": 1040}))) == {"id": 1040, "number": 40, "url": "u"}
    with pytest.raises(ValueError, match="url, number, id"):
        _delivered_value(s, f.run_id, rows["slice_issue"], "40")
    for kind in ("slice_dependency", "slice_queue", "umbrella_label"):
        assert _delivered_value(s, f.run_id, rows[kind], None) == "ok"
        with pytest.raises(ValueError, match="no delivered value"):
            _delivered_value(s, f.run_id, rows[kind], "x")
    for kind in ("umbrella", "umbrella_close"):
        assert _delivered_value(s, f.run_id, rows[kind], "https://github.com/o/r/issues/12#issuecomment-3").endswith("-3")
        with pytest.raises(ValueError, match="comment URL"):
            _delivered_value(s, f.run_id, rows[kind], None)
    assert _delivered_value(s, f.run_id, rows["parent_close"], None) == "closed"
    n = reconcile_typed(s, f.run_id, [Resolution(rows["slice_queue"], True, None)], s.run_version(f.run_id))
    assert n == 1 and front.is_satisfied(s.effect_by_key(rows["slice_queue"], run_id=f.run_id))


def test_the_seventh_status_prefix():
    from kernel import bundle
    assert bundle.BIRCHER_STATUS_PREFIXES[-1] == "bircher: sliced "
    assert bundle.is_bircher_status("bircher: sliced abcd1234\n\n- #40 Slice 1: x")
    assert bundle.is_bircher_status("bircher: sliced complete\n")
    assert not bundle.is_bircher_status("bircher: slicing this by hand")
