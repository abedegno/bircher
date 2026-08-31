"""Every row of the design's recovery table, driven from a REAL journal.

Criterion 6. The rows each get their own test because each was missing from a
draft and each does something different -- and because the dangerous ones are
dangerous in opposite directions: one must never re-execute a merge, another
must never treat a stale approval as current.

Hand-built fact lists are avoided deliberately. Writing `recover.py` I guessed
that `command_rejected` carried its name under `command`; it is `command_name`,
so a hand-built fixture would have agreed with the guess and the row would have
been dead in production while green here. Everything below drives real commands
and real effects through the kernel and reads back what it actually wrote.
"""

import pytest

from coordinator.recover import decide
from kernel.artifacts import put_artifact
from kernel.commands import Command, StaleVersion, submit
from kernel.dispatch import Role, dispatch
from kernel.effects import EffectClass, perform
from kernel.ids import Clock
from kernel.store import Store

BASE, HEAD, BUNDLE = "c" * 40, "d" * 40, "e" * 64
MERGE_ARGV = {"argv": ["gh", "pr", "merge", "42", "--repo", "o/r"]}


def _sub(s, name, key, actor, role, **p):
    return submit(s, Command(
        name=name, run_id="r", expected_version=s.run_version("r"),
        idempotency_key=key,
        generation=dispatch(s, "r", actor=actor, role=role).generation,
        payload=p))


@pytest.fixture()
def s():
    st = Store.open(":memory:", clock=Clock(start_us=1))
    st.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    return st


@pytest.fixture()
def reviewing(s):
    """A run at `reviewing` with an accept binding its current output."""
    art = put_artifact(s, b"# output one")
    _sub(s, "submit_spec", "k1", "claude", Role.IMPLEMENTER, spec_sha256=art)
    _sub(s, "submit_plan", "k2", "claude", Role.IMPLEMENTER, plan_sha256=art)
    _sub(s, "start_implementation", "k3", "claude", Role.IMPLEMENTER)
    _sub(s, "record_implementation_output", "k4", "claude", Role.IMPLEMENTER,
         artifact_hash=art)
    _sub(s, "record_ci_observation", "k5", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha=HEAD)
    _sub(s, "record_review", "k6", "codex", Role.REVIEWER, verdict="accept",
         artifact_hash=art, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    return s, art


def _binding(art):
    """The binding hash the run would present NOW.

    Built with the KERNEL's own hasher rather than read back from the verdict
    fact: a value fetched from the mechanism and handed straight back makes the
    mechanism's check compare a value against itself."""
    from kernel.artifacts import binding_hash
    from kernel.authz import _binding_from
    return binding_hash(_binding_from(dict(
        artifact_hash=art, base_sha=BASE, context_bundle_hash=BUNDLE,
        policy_version=1)))


def _do(s, current_artifact=None, **kw):
    return decide(s.facts_for("r"),
                  current_binding_hash=(_binding(current_artifact)
                                        if current_artifact else None), **kw)


def _authorize(s, art):
    _sub(s, "request_merge", "m1", "claude", Role.IMPLEMENTER, pr=42,
         repo="o/r", head_git_sha=HEAD, artifact_hash=art, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)


# --- the review rows ---------------------------------------------------------

def test_no_verdict_at_all_derives_from_scratch(s):
    assert _do(s).do == "derive"


def test_a_revision_with_nothing_started_dispatches_the_implementer(reviewing):
    s, art = reviewing
    _sub(s, "record_review", "rv", "codex", Role.REVIEWER,
         verdict="request_revision", artifact_hash=art, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)
    assert _do(s).do == "dispatch_implementer"


def test_a_revision_with_an_implementer_already_up_settles_it(reviewing):
    """Dispatching a second implementer against one PR is how two sessions end
    up pushing to the same branch."""
    s, art = reviewing
    _sub(s, "record_review", "rv", "codex", Role.REVIEWER,
         verdict="request_revision", artifact_hash=art, base_sha=BASE,
         context_bundle_hash=BUNDLE, policy_version=1)
    _sub(s, "start_implementation", "si", "claude", Role.IMPLEMENTER)
    assert _do(s).do == "settle_implementer"


def test_an_accept_binding_the_current_output_merges(reviewing):
    s, art = reviewing
    assert _do(s, current_artifact=art).do == "merge"


def test_an_accept_binding_a_SUPERSEDED_output_is_re_reviewed(reviewing):
    """The approval is stale. `validate_review` refuses it at the merge gate
    anyway -- but it refuses LATE, after the effect path has been entered."""
    s, art = reviewing
    newer = put_artifact(s, b"# output two")
    assert _do(s, current_artifact=newer).do == "re_review"


def test_a_reject_is_terminal(reviewing):
    s, art = reviewing
    _sub(s, "record_review", "rj", "codex", Role.REVIEWER, verdict="reject",
         artifact_hash=art, base_sha=BASE, context_bundle_hash=BUNDLE,
         policy_version=1)
    assert _do(s, current_artifact=art).do == "terminal"


def test_a_review_that_lost_the_CAS_re_derives_rather_than_reusing_the_accept(
        reviewing):
    """THE row a naive resume gets wrong. The external review said FAIL, the
    command validated and then lost the CAS, so no verdict fact exists -- while
    the older ACCEPT is still the latest verdict. Reading the state name, or the
    latest verdict, merges work a reviewer has just rejected."""
    s, art = reviewing
    stale = s.run_version("r")
    _sub(s, "record_ci_observation", "bump", "claude", Role.IMPLEMENTER,
         status="success", head_git_sha="f" * 40)
    with pytest.raises(StaleVersion):
        submit(s, Command(
            name="record_review", run_id="r", expected_version=stale,
            idempotency_key="rv-lost",
            generation=dispatch(s, "r", actor="codex",
                                role=Role.REVIEWER).generation,
            payload=dict(verdict="request_revision", artifact_hash=art,
                         base_sha=BASE, context_bundle_hash=BUNDLE,
                         policy_version=1)))
    assert _do(s, current_artifact=art).do == "re_derive", (
        "a lost revision let the older acceptance stand as current")


# --- the merge rows ----------------------------------------------------------

def test_authorized_with_no_effect_performs_the_merge(reviewing):
    """Do NOT re-issue request_merge: it is illegal from `merge_requested` and
    would be refused, stranding a valid authorization."""
    s, art = reviewing
    _authorize(s, art)
    assert _do(s, current_artifact=art).do == "perform_merge"


def test_an_UNCERTAIN_merge_effect_halts_rather_than_re_executing(reviewing):
    """The merge may ALREADY have happened at GitHub. This is the halt muesli
    #726 took on the first live merge."""
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation

    def boom(*a):
        raise KeyboardInterrupt("crash mid-merge")

    with pytest.raises(KeyboardInterrupt):
        perform(s, "r", gen, EffectClass.MERGE, "mk", MERGE_ARGV, boom)
    a = _do(s, current_artifact=art)
    assert a.do == "halt_and_reconcile", a


def test_an_INTENDED_merge_with_no_confirmation_also_halts(reviewing):
    """A crash between journalling the intent and confirming it leaves only
    the intent. Same danger, different evidence -- and `effect_uncertain` does
    not carry the effect CLASS, so a fact-only caller reaches this row only by
    joining back through the intent."""
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    s.journal_intent("mk2", "r", gen, EffectClass.MERGE, "mk2", MERGE_ARGV)
    # `journal_intent` writes to the effects TABLE and appends NO fact, so the
    # fact stream cannot see this one -- which is why the caller passes the
    # table's own answer when it holds the store. Asserted both ways, because
    # discovering that difference is the point of the row.
    assert s.effect_state("mk2", run_id="r") == "intended"
    assert _do(s, current_artifact=art,
               merge_effect="intended").do == "halt_and_reconcile"
    assert _do(s, current_artifact=art).do == "perform_merge", (
        "a fact-only caller cannot see a table-only intent; if this changes, "
        "the fallback has become authoritative and the comment above is wrong")


def test_a_CONFIRMED_merge_with_no_outcome_records_it_and_does_not_re_execute(
        reviewing):
    """The merge HAPPENED. Re-executing is the worst available action."""
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    perform(s, "r", gen, EffectClass.MERGE, "mk", MERGE_ARGV,
            lambda *a: "merged-sha")
    assert _do(s, current_artifact=art).do == "record_merge_outcome"


def test_a_recorded_merge_is_done(reviewing):
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    perform(s, "r", gen, EffectClass.MERGE, "mk", MERGE_ARGV,
            lambda *a: "merged-sha")
    _sub(s, "record_merge_outcome", "mo", "claude", Role.IMPLEMENTER,
         outcome="merged", merge_commit_sha="a" * 40)
    assert _do(s, current_artifact=art).do == "done"


def test_a_FAILED_merge_retries_the_merge_and_does_not_consume_a_revision(
        reviewing):
    """`record_merge_outcome(failed)` returns the run to `reviewing`, which is
    indistinguishable by state name from a fresh acceptance. Spending a repair
    round here would burn the allowance on work no reviewer asked for."""
    s, art = reviewing
    _authorize(s, art)
    _sub(s, "record_merge_outcome", "mo", "claude", Role.IMPLEMENTER,
         outcome="failed")
    a = _do(s, current_artifact=art)
    assert a.do == "retry_merge"
    assert "not consume a revision" in a.why


# --- ordering ----------------------------------------------------------------

def test_merge_evidence_outranks_review_evidence(reviewing):
    """ORDER IS THE DESIGN. A merged run still has an `accept` as its latest
    verdict, so a table that checked the review rows first would answer
    `merge` for a run that has already merged."""
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    perform(s, "r", gen, EffectClass.MERGE, "mk", MERGE_ARGV,
            lambda *a: "merged-sha")
    _sub(s, "record_merge_outcome", "mo", "claude", Role.IMPLEMENTER,
         outcome="merged", merge_commit_sha="a" * 40)
    verdicts = [f for f in s.facts_for("r")
                if getattr(f, "kind", "") == "review_verdict"]
    assert verdicts and verdicts[-1].payload["verdict"] == "accept", (
        "the premise of this test is gone; it no longer proves ordering")
    assert _do(s, current_artifact=art).do == "done"


# --- the CLI the runner actually calls ---------------------------------------

def test_the_cli_reports_the_halt_for_an_uncertain_merge(tmp_path, capsys):
    """Drives `coordinator.cli recover` against a real on-disk journal.

    Not redundant with the unit tests above: the CLI reads the effect TABLE to
    answer the merge rows, and it looks the key up by `causal_command_id`. Its
    first version read `payload["idempotency_key"]`, which does not exist --
    `effect_state` returned None for every effect and the halt row could never
    fire, while every test above still passed because they call `decide`
    directly.
    """
    from coordinator.cli import main
    path = str(tmp_path / "k.db")
    st = Store.open(path, clock=Clock(start_us=1))
    st.create_run(run_id="r", base_repo="o/r", base_sha=BASE)
    art = put_artifact(st, b"# output one")
    for n, k, p in (("submit_spec", "k1", dict(spec_sha256=art)),
                    ("submit_plan", "k2", dict(plan_sha256=art)),
                    ("start_implementation", "k3", {}),
                    ("record_implementation_output", "k4", dict(artifact_hash=art)),
                    ("record_ci_observation", "k5",
                     dict(status="success", head_git_sha=HEAD))):
        submit(st, Command(name=n, run_id="r", expected_version=st.run_version("r"),
                           idempotency_key=k,
                           generation=dispatch(st, "r", actor="claude",
                                               role=Role.IMPLEMENTER).generation,
                           payload=p))
    submit(st, Command(name="record_review", run_id="r",
                       expected_version=st.run_version("r"), idempotency_key="k6",
                       generation=dispatch(st, "r", actor="codex",
                                           role=Role.REVIEWER).generation,
                       payload=dict(verdict="accept", artifact_hash=art,
                                    base_sha=BASE, context_bundle_hash=BUNDLE,
                                    policy_version=1)))
    submit(st, Command(name="request_merge", run_id="r",
                       expected_version=st.run_version("r"), idempotency_key="m1",
                       generation=dispatch(st, "r", actor="claude",
                                           role=Role.IMPLEMENTER).generation,
                       payload=dict(pr=42, repo="o/r", head_git_sha=HEAD,
                                    artifact_hash=art, base_sha=BASE,
                                    context_bundle_hash=BUNDLE, policy_version=1)))
    gen = dispatch(st, "r", actor="claude", role=Role.IMPLEMENTER).generation

    def boom(*a):
        raise KeyboardInterrupt("crash mid-merge")

    with pytest.raises(KeyboardInterrupt):
        perform(st, "r", gen, EffectClass.MERGE, "mk", MERGE_ARGV, boom)
    assert st.effect_state("mk", run_id="r") == "uncertain"

    assert main(["recover", "--db", path, "--run-id", "r",
                 "--base-sha", BASE, "--context-hash", BUNDLE]) == 0
    out = capsys.readouterr().out
    assert out.split("|")[0] == "halt_and_reconcile", out


def test_the_table_lookup_sees_what_the_facts_alone_cannot(reviewing):
    """The ONE case where the table and the fact stream disagree, which is the
    only case that can bind the table lookup.

    An earlier version of `_merge_effect_from_table` read the key from
    `payload["idempotency_key"]`, which does not exist -- so `effect_state`
    returned None for every effect and the lookup was dead. Mutating it back
    changed nothing in any end-to-end test, because `recover.merge_effect_state`
    answers identically from the facts: the fallback covered for the bug.

    The table can also move AFTER the facts are written -- `mark_effect` records
    a reconciliation outcome there -- so a merge the facts call `confirmed` can
    be `uncertain` in the table, and only a caller that reads the table halts
    on it.
    """
    from coordinator.cli import _merge_effect_from_table
    from coordinator.recover import merge_effect_state
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    # An effect that reached the table and CONFIRMED -- so the FACTS say
    # `confirmed` -- and was then moved back to `uncertain` in the table by a
    # reconciliation that could not settle the forge state.
    perform(s, "r", gen, EffectClass.MERGE, "mk", MERGE_ARGV, lambda *a: "sha")
    st = s
    facts = st.facts_for("r")
    assert merge_effect_state(facts) == "confirmed"
    st.mark_effect("mk", "uncertain", None, run_id="r")

    assert _merge_effect_from_table(st, "r", facts) == "uncertain", (
        "the table lookup did not read the table")
    assert decide(facts, merge_effect=_merge_effect_from_table(st, "r", facts)
                  ).do == "halt_and_reconcile"
    assert decide(facts).do == "record_merge_outcome", (
        "the fact-only answer no longer differs, so this test cannot bind "
        "the table lookup any more")


def test_the_recover_cli_runs_as_a_SUBPROCESS(tmp_path):
    """THE test that was missing, and its absence let a dead guard ship.

    Two helpers were appended BELOW `if __name__ == "__main__": sys.exit(main())`.
    Running as `python3 -m coordinator.cli` -- the only way production ever
    invokes this -- executes the module top to bottom, so `main()` ran before
    those `def` statements and the recover branch raised `NameError` on every
    real call. Every test above stayed green: they call `main()` in-process,
    after the import has completed, where the definitions exist.

    The shell then swallowed it. `_recovery_action` ends `2>/dev/null || true`,
    and an empty action deliberately does NOT forbid a merge -- so the recovery
    gate was inert and nothing anywhere said so.

    So: run the real command line, in a real subprocess, and read stdout.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path

    v2 = Path(__file__).resolve().parents[2]
    db = tmp_path / "k.db"
    st = Store.open(str(db), clock=Clock(start_us=1))
    st.create_run(run_id="r", base_repo="o/r", base_sha=BASE)

    r = subprocess.run(
        [_sys.executable, "-m", "coordinator.cli", "recover",
         "--db", str(db), "--run-id", "r"],
        capture_output=True, text=True, cwd=str(v2),
        env={"PYTHONPATH": str(v2), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"
    assert "Error" not in r.stderr and "Traceback" not in r.stderr, r.stderr
    assert r.stdout.split("|")[0] == "derive", r.stdout


def test_a_MISSING_database_is_a_lookup_failure_not_an_empty_history(tmp_path):
    """`Store.open` is `sqlite3.connect`, which CREATES the file. So a
    misspelled BIRCHER_KERNEL_DB would answer "no review verdict at all; derive
    from scratch" -- and `_recovery_forbids_merge` does not forbid `derive`. A
    typo in a path would quietly become permission to merge."""
    from coordinator.cli import RC_LOOKUP_FAILED, main
    missing = str(tmp_path / "nope.db")
    assert main(["recover", "--db", missing, "--run-id", "r"]) == RC_LOOKUP_FAILED
    import os
    assert not os.path.exists(missing), (
        "the lookup created the database it was meant to refuse")


def test_a_table_only_intent_halts_the_CLI_too(reviewing):
    """Finding 1 of the second-vendor review, and it is the double-merge case.

    `perform` calls `journal_intent` (table) then `append_fact`. A process
    death between them leaves a table row with NO fact -- exactly the crash the
    `intended` halt row exists for. The CLI helper used to ENUMERATE from the
    facts, so it found no key, returned None, and `decide` fell through to
    `merge_authorized` and answered `perform_merge`: re-executing a merge that
    may already have landed.

    The earlier test built this state and passed `merge_effect="intended"` by
    hand, so it proved `decide` and not the lookup.
    """
    from coordinator.cli import _merge_effect_from_table
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    s.journal_intent("mk-tbl", "r", gen, EffectClass.MERGE, "mk-tbl", MERGE_ARGV)
    facts = s.facts_for("r")
    assert not [f for f in facts if f.kind == "effect_intended"], (
        "journal_intent appended a fact; this test no longer builds the state "
        "it names")

    assert _merge_effect_from_table(s, "r", facts) == "intended"
    assert decide(facts, merge_effect=_merge_effect_from_table(s, "r", facts)
                  ).do == "halt_and_reconcile"


def test_a_RECONCILED_merge_does_not_assert_that_it_happened(reviewing):
    """Finding 2. A resolution is free text and the kernel's own tests resolve
    merges as "PR was not merged". Mapping `reconciled` onto "the merge
    HAPPENED" asserts an outcome the mechanism cannot read."""
    s, art = reviewing
    _authorize(s, art)
    gen = dispatch(s, "r", actor="claude", role=Role.IMPLEMENTER).generation
    perform(s, "r", gen, EffectClass.MERGE, "mk", MERGE_ARGV, lambda *a: "sha")
    facts = s.facts_for("r")
    a = decide(facts, merge_effect="reconciled")
    assert a.do == "reconciled_ruling_needed", a
    assert "free text" in a.why
    # It still fails closed at the runner's gate.
    assert decide(facts, merge_effect="confirmed").do == "record_merge_outcome"
