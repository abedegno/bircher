"""The coordinator's command line, mirroring `kernel.cli`.

A seam, and a temporary one. It exists because `batch/run-queue.sh` is still
the process that runs, and bash reaches Python through a subprocess. As the
coordinator moves into this package the callers become Python and this module
gets thinner, not thicker -- if it grows a subcommand per bash caller, the
migration has stalled and turned into an API.
"""

from __future__ import annotations

import argparse
import os
import sys

from coordinator.ci import DEFAULT_IGNORED, keep_blocking, normalize
from coordinator.effects import EffectDenied, NotDispatched, perform_effect
from coordinator.observe import ci_history, classify
from coordinator.outcome import derive
from coordinator.pr_selection import is_abandoned, select
from coordinator.review import extract_verdict
from coordinator.session import (LookupFailed, item_count, last_assistant_text,
                                 settle, state)

RC_OK = 0
RC_USAGE = 2
RC_LOOKUP_FAILED = 3
#: The adapter's `_EFFECT_RC_DENIED`. Kept identical so the two entry points
#: are interchangeable to a caller that checks the code.
RC_EFFECT_DENIED = 87
# The findings could not be committed to disk. Deliberately NOT RC_OK: the
# runner escalates on a non-zero rc, which is the right response, because
# the alternative is dispatching a repair against findings that are stale,
# truncated, or absent -- and all three read as a normal repair.
RC_FINDINGS_UNWRITABLE = 88


def _maybe_stdin(value: str) -> str:
    """`-` means the payload is on stdin.

    Any other value is returned unchanged, so every existing caller and test
    keeps working. Only the two CI-list commands use it, and only because
    their input has no upper bound.
    """
    if value != "-":
        return value
    return sys.stdin.read()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bircher-coordinator")
    subs = p.add_subparsers(dest="mode", required=True)

    h = subs.add_parser("ci-history")
    h.add_argument("--repo", required=True)
    h.add_argument("--branch", required=True)

    c = subs.add_parser("classify")
    # Empty is meaningful for --pr: "no PR at timeout" is an outcome, not an
    # error, so this is deliberately not `required`.
    c.add_argument("--pr", default="")
    c.add_argument("--ci", required=True)
    c.add_argument("--verdict", default="")
    c.add_argument("--reviewer", required=True)

    st = subs.add_parser("session-state")
    st.add_argument("--server", required=True)
    st.add_argument("--id", required=True, dest="conv_id")

    # `-` means READ FROM STDIN. These carry CI check lists, which are
    # unbounded: passed as an argv element they hit the kernel's per-argument
    # ceiling (128KB on Linux, MAX_ARG_STRLEN) and `timeout` fails with
    # "Argument list too long". The shell then reads the empty result as
    # `pending`, and `_wait_ci` loops on pending -- so an oversized input HANGS
    # rather than erroring. Found by bircher's first CI run, on Linux; macOS
    # allows a larger argument and never reproduced it.
    cn = subs.add_parser("ci-normalize")
    cn.add_argument("--buckets", required=True)

    cb = subs.add_parser("ci-keep-blocking")
    cb.add_argument("--lines", required=True)
    cb.add_argument("--required", default="")

    vd = subs.add_parser("verdict")
    vd.add_argument("--text", required=True)

    # Mirrors `_effect <class> <key> <cap> -- argv...`, including its exit
    # codes, so a caller can be swapped from one entry point to the other
    # without changing how it checks the result.
    ef = subs.add_parser("effect")
    ef.add_argument("--class", dest="effect_class", required=True)
    ef.add_argument("--key", required=True)
    ef.add_argument("--timeout", type=float, default=None)
    ef.add_argument("cmd", nargs=argparse.REMAINDER)

    dv = subs.add_parser("derive")
    dv.add_argument("--item", required=True)
    dv.add_argument("--code", default="")
    dv.add_argument("--pr", default="")
    dv.add_argument("--issue", default="")
    # EXPLICIT, never inherited. `RECOVERY_REVIEWER` is a plain shell
    # assignment, not an export, so a subprocess never saw it -- and the
    # default silently made the reviewer the SAME vendor as the implementer.
    dv.add_argument("--reviewer", required=True)
    dv.add_argument("--repo", required=True)
    dv.add_argument("--server", default="http://omnigent:8000")
    dv.add_argument("--bundle-dir", default=".", dest="bundle_dir")
    # PASSED, not inherited: run-queue.sh assigns MAIN_CI_POLL_INTERVAL without
    # exporting it, so reading it from the environment here silently discarded
    # the operator's BIRCHER_MAIN_CI_POLL_INTERVAL and always polled at 30s.
    dv.add_argument("--poll-interval", type=int, default=30,
                    dest="poll_interval")
    # PASSED and already VALIDATED by `_ci_policy` in run-queue.sh. Read from
    # the environment here instead, they were interpreted a second time and
    # differently: the shell clamped `BIRCHER_CI_RERUN_MAX=abc` to 4 and
    # computed a budget from it, while a bare `int()` here raised ValueError
    # and escalated every item. One malformed operator value, two answers.
    # The repair loop's two arguments.
    #
    # `--revisions-left` is the allowance, computed by the caller from the
    # journal (`observe.revisions_used`) rather than here, because the caller
    # owns the kernel database handle. 0 -- the default -- reproduces the
    # behaviour before the loop existed.
    #
    # `--findings-out` is a PATH and not a tuple field on purpose: the
    # reviewer's blocking findings are multi-paragraph text containing pipes
    # and newlines, and the tuple is one pipe-delimited line whose width guard
    # rejects both. Writing them to a file keeps the transport intact.
    #
    # The path is REMOVED before derivation and REPLACED atomically after, so
    # the file existing means this derivation wrote it. Without that, a round
    # that leaves an old file behind pairs a fresh `revise` with a previous
    # round's findings -- and a repair briefed on the wrong review looks
    # exactly like a repair briefed on the right one.
    dv.add_argument("--revisions-left", type=int, default=0, dest="revisions_left")
    dv.add_argument("--findings-out", default="", dest="findings_out")
    dv.add_argument("--ci-wait", type=int, default=1500, dest="ci_wait")
    dv.add_argument("--rerun-max", type=int, default=4, dest="rerun_max")
    dv.add_argument("--rerun-wait", type=int, default=900, dest="rerun_wait")

    # `revisions` is the runner's window onto the journal, and it answers the
    # two questions the repair loop asks of it:
    #
    #   how many rounds are left   -- before derivation, to set --revisions-left
    #   did the revision land      -- after submitting record_review, before
    #                                 any repair work is dispatched
    #
    # Both read the SAME journal, and neither is answerable from the runner:
    # bash has no sqlite handle and the kernel adapter is advisory, so an exit
    # code from it proves nothing about what was recorded.
    rv = subs.add_parser("revisions")
    rv.add_argument("--db", required=True)
    rv.add_argument("--run-id", required=True)
    rv.add_argument("--max", type=int, default=2, dest="max_revisions")
    # The idempotency key of the record_review command whose fact we are
    # looking for. Supplied by the caller and never derived here: deriving it
    # would rebuild `kernel.cli`'s default-key format in a second place, and
    # two subsystems that rebuild the same string eventually disagree about it.
    rv.add_argument("--confirm-command", default="", dest="confirm_command")

    # `recover` answers "this run was interrupted -- what now?" from the
    # journal, because the STATE NAME cannot answer it: `reviewing` is reached
    # from an accept, from a reject AND from a failed merge.
    #
    # base-sha and context-hash are the CALLER'S OWN values, as they are for
    # record_review, and for the same reason: a binding fetched from the
    # mechanism and handed back makes the mechanism compare a value against
    # itself. The current artifact IS read from the store, because "what this
    # run currently outputs" is the store's fact and not the caller's.
    rc = subs.add_parser("recover")
    rc.add_argument("--db", required=True)
    rc.add_argument("--run-id", required=True)
    rc.add_argument("--base-sha", default="")
    rc.add_argument("--context-hash", default="")
    rc.add_argument("--policy-version", type=int, default=1)

    pa = subs.add_parser("pr-abandoned")
    pa.add_argument("--state", default="")
    pa.add_argument("--merged", default="")

    ps = subs.add_parser("pr-select")
    ps.add_argument("--signal", default="")
    ps.add_argument("--matches", default="")

    se = subs.add_parser("session-settle")
    se.add_argument("--server", required=True)
    se.add_argument("--id", required=True, dest="conv_id")
    se.add_argument("--prev-count", default="")
    se.add_argument("--stable-polls", type=int, default=0)
    se.add_argument("--needed", type=int, default=4)

    la = subs.add_parser("last-assistant-text")
    la.add_argument("--server", required=True)
    la.add_argument("--id", required=True, dest="conv_id")
    la.add_argument("--n", type=int, default=3)

    a = p.parse_args(argv)

    if a.mode == "ci-history":
        r = ci_history(a.repo, a.branch)
        # The pipe form is what the shell callers parse. It is a wire format
        # for one consumer, not a public interface: `resubmissions` is empty
        # rather than a number when unknown, exactly as the bash version was,
        # so `unknown|` cannot be mistaken for `false|0`.
        print(f"{r.ci_first}|{'' if r.resubmissions is None else r.resubmissions}",
              end="")
        return RC_OK

    if a.mode == "session-state":
        s_ = state(a.server, a.conv_id)
        print(f"{s_.status}|{s_.error_code}", end="")
        return RC_OK

    if a.mode == "ci-normalize":
        print(normalize(_maybe_stdin(a.buckets)), end="")
        return RC_OK

    if a.mode == "ci-keep-blocking":
        # The operator's policy, resolved HERE at the boundary. The shell used
        # to apply `${BIRCHER_CI_IGNORE_CHECKS:-...}` with its own grep; once
        # `_keep_blocking_checks` delegated to this mode, calling
        # `keep_blocking` with no `ignore` silently reinstated the library
        # default and discarded the override -- so a custom-ignored FAILING
        # check went back to being treated as blocking on the shell paths.
        #
        # Read from the environment rather than added as a flag: run-queue.sh
        # never ASSIGNS this name, every use is `${BIRCHER_CI_IGNORE_CHECKS:-}`
        # against the operator's own environment, so both languages already see
        # the same value. That is the contract test_env_boundary_contract.py
        # calls `operator`.
        print(keep_blocking(_maybe_stdin(a.lines), a.required,
                            os.environ.get("BIRCHER_CI_IGNORE_CHECKS")
                            or DEFAULT_IGNORED), end="")
        return RC_OK

    if a.mode == "verdict":
        v = extract_verdict(a.text)
        print(v or "", end="")
        # WARN when a reviewer said SOMETHING that was not a verdict. Silence
        # would leave an operator unable to tell "the reviewer never ran" from
        # "the reviewer rambled", and those need different responses. The first
        # port dropped this and `--self-test` caught it; `extract_verdict` stays
        # pure, so the warning belongs at the boundary, not in the rule.
        if v is None and a.text.strip():
            print("[batch] WARN: review's final line is not a bare verdict "
                  "-> treating as no verdict", file=sys.stderr)
        return RC_OK

    if a.mode == "effect":
        cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
        if not cmd:
            print("no command given", file=sys.stderr)
            return RC_USAGE
        try:
            print(perform_effect(a.effect_class, a.key, cmd, timeout=a.timeout))
        except EffectDenied as exc:
            # 87, the adapter's own RC_DENIED: a caller that already
            # distinguishes "refused" from "failed" keeps working unchanged.
            print(f"effect refused: {exc}", file=sys.stderr)
            return RC_EFFECT_DENIED
        except NotDispatched as exc:
            print(f"effect not dispatched: {exc}", file=sys.stderr)
            return RC_EFFECT_DENIED
        return RC_OK

    if a.mode == "derive":
        # Imported here so the rest of the CLI stays usable when the world is
        # not reachable -- `wiring` builds real gh and effect callables.
        from coordinator.wiring import live_deps
        # `_gh` reads the repo from here rather than from an unexported global.
        os.environ["BIRCHER_GH_REPO"] = a.repo
        # FIRST, before anything can succeed: clear any file a previous round
        # left at this path. Derivation runs for as long as CI does and can be
        # killed by its timeout at any point in that window; if it dies with an
        # old file still there, the next reader finds findings that look
        # current and are not. Removing it up front means the file's existence
        # is evidence, not an assumption.
        if a.findings_out:
            try:
                os.unlink(a.findings_out)
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"could not clear stale findings at {a.findings_out}: "
                      f"{exc}", file=sys.stderr)
                return RC_FINDINGS_UNWRITABLE
        r = derive(a.item, a.code, a.pr, a.issue,
                   deps=live_deps(a.item, repo=a.repo, reviewer=a.reviewer,
                                  server=a.server, bundle_dir=a.bundle_dir,
                                  poll_interval=a.poll_interval,
                                  ci_wait=a.ci_wait, rerun_wait=a.rerun_wait,
                                  revisions_left=a.revisions_left),
                   rerun_max=a.rerun_max)
        # Written BEFORE the tuple is printed, and ATOMICALLY: the caller reads
        # the tuple, sees `revise`, and then reads this file. Printing first
        # would let a caller act on `revise` while the findings were unwritten;
        # writing in place would let a crash mid-write leave a truncated brief
        # that reads as a complete one. Temp file in the SAME directory (so the
        # replace is a rename within one filesystem), fsync, then replace.
        if a.findings_out and r.findings:
            tmp = f"{a.findings_out}.{os.getpid()}.tmp"
            try:
                with open(tmp, "w") as fh:
                    fh.write(r.findings)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, a.findings_out)
            except OSError as exc:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                # FATAL, and the tuple is NOT printed. A `revise` the caller
                # cannot brief is worse than no answer: it dispatches a repair
                # with an empty brief and no way to know. A non-zero rc makes
                # the runner escalate, which is the outcome we want.
                print(f"could not write findings to {a.findings_out}: {exc}",
                      file=sys.stderr)
                return RC_FINDINGS_UNWRITABLE
        print(r.as_line(), end="")
        return RC_OK

    if a.mode == "revisions":
        from kernel.store import Store

        from coordinator.observe import revision_confirmed, revisions_used
        try:
            facts = Store.open(a.db).facts_for(a.run_id)
        except Exception as exc:
            # An unreadable journal is NOT "zero revisions used", which would
            # hand the loop a full allowance every round and make the bound
            # unenforceable. It is a lookup failure, and the runner escalates.
            print(f"could not read the journal at {a.db}: {exc}",
                  file=sys.stderr)
            return RC_LOOKUP_FAILED
        used = revisions_used(facts)
        left = max(0, a.max_revisions - used)
        ok = revision_confirmed(facts, a.confirm_command)
        print(f"{used}|{left}|{'yes' if ok else 'no'}", end="")
        return RC_OK

    if a.mode == "recover":
        from kernel.store import Store

        from coordinator.recover import decide
        # A MISSING FILE IS A LOOKUP FAILURE, not an empty history. `Store.open`
        # is `sqlite3.connect`, which CREATES the file -- so a misspelled
        # BIRCHER_KERNEL_DB would answer "no review verdict at all; derive from
        # scratch", and `_recovery_forbids_merge` does not forbid that. A typo
        # in a path would quietly become permission to merge.
        if not os.path.exists(a.db):
            print(f"no kernel database at {a.db}", file=sys.stderr)
            return RC_LOOKUP_FAILED
        try:
            store = Store.open(a.db)
            facts = store.facts_for(a.run_id)
        except Exception as exc:
            print(f"could not read the journal at {a.db}: {exc}",
                  file=sys.stderr)
            return RC_LOOKUP_FAILED
        binding = None
        if a.base_sha and a.context_hash:
            art = store.current_artifact(a.run_id)
            if art:
                try:
                    from kernel.artifacts import binding_hash
                    from kernel.authz import _binding_from
                    binding = binding_hash(_binding_from(dict(
                        artifact_hash=art, base_sha=a.base_sha,
                        context_bundle_hash=a.context_hash,
                        policy_version=a.policy_version)))
                except Exception as exc:
                    # A binding we cannot build is NOT "the accept is current".
                    # Leaving it None makes `decide` skip the staleness check
                    # and answer `merge`, so say so rather than letting a
                    # malformed base silently authorise the merge path.
                    print(f"could not build the current binding: {exc}",
                          file=sys.stderr)
                    return RC_LOOKUP_FAILED
        print(f"{_recover_action(store, a.run_id, facts, binding, decide)}",
              end="")
        return RC_OK

    if a.mode == "pr-abandoned":
        # EXIT CODE, not stdout: the shell calls this in an `if`, and a
        # printed word would have to be compared, which is one more place to
        # get a default wrong.
        return RC_OK if is_abandoned(a.state, a.merged) else 1

    if a.mode == "pr-select":
        c = select(a.signal, a.matches)
        print(f"{c.decision}|{c.value}", end="")
        return RC_OK

    if a.mode == "session-settle":
        # ONE call does the read and the decision, so the shell carries only two
        # loop variables and none of the judgement.
        prev = int(a.prev_count) if a.prev_count.strip().isdigit() else None
        r = settle(state(a.server, a.conv_id).status,
                   item_count(a.server, a.conv_id),
                   prev, a.stable_polls, needed=a.needed)
        print(f"{'' if r.count is None else r.count}|{r.stable_polls}|"
              f"{'yes' if r.settled else 'no'}", end="")
        return RC_OK

    if a.mode == "last-assistant-text":
        try:
            print(last_assistant_text(a.server, a.conv_id, a.n), end="")
        except LookupFailed as exc:
            # NON-ZERO, so the caller can tell "no assistant text" from "could
            # not read the session" -- the distinction the limit check needs.
            print(f"session-items lookup failed: {exc}", file=sys.stderr)
            return RC_LOOKUP_FAILED
        return RC_OK

    o = classify(a.pr or None, a.ci, a.verdict or None, reviewer=a.reviewer)
    print(f"{o.outcome}|{o.review}|{o.ci}|{o.note}", end="")
    return RC_OK


def _merge_effect_from_table(store, run_id, facts):
    """The effect TABLE's answer for this run's merge, or None.

    THE TABLE IS AUTHORITATIVE, and it is also where the ENUMERATION comes
    from. An earlier version walked `effect_intended` FACTS to find which keys
    were merges and then read the table per key -- which is authoritative about
    the state and not about the existence.

    `perform` calls `journal_intent` (table) and then `append_fact`. A process
    death between those two statements leaves a table row with NO fact, and
    that is exactly the crash the `intended` halt row exists for --
    `Store.uncertain_effects` says so in its own comment: "a real process death
    after journalling never runs the handler that marks it uncertain". A
    fact-driven enumeration is blind to it, returns None, and `decide` falls
    through to `merge_authorized` and answers `perform_merge` -- re-executing a
    merge that may already have landed, which is the one thing this gate exists
    to prevent. Found by cross-review, not by a test: the test that builds this
    state passed `merge_effect` by hand.

    `uncertain_effects` reads the table, is already class-tagged, and covers
    both `intended` and `uncertain`. The facts remain a fallback for a caller
    with no store.
    """
    worst = None
    for e in store.uncertain_effects(run_id):
        if e.get("effect_class") != "merge":
            continue
        # The WORST unresolved state wins: one uncertain merge among several
        # confirmed ones still means the forge may hold a merge nobody recorded.
        st = store.effect_state(e["idempotency_key"], run_id=run_id)
        if st in ("intended", "uncertain"):
            return st
        worst = worst or st
    if worst:
        return worst
    # Nothing unresolved. Ask the facts which keys were merges at all, so a
    # CONFIRMED or RECONCILED merge is still reported rather than read as "no
    # merge was ever attempted".
    for f in facts:
        k = getattr(f, "kind", None)
        k = getattr(k, "value", k)
        payload = getattr(f, "payload", None) or {}
        if k != "effect_intended" or payload.get("effect_class") != "merge":
            continue
        key = getattr(f, "causal_command_id", None)
        if key:
            worst = worst or store.effect_state(key, run_id=run_id)
    return worst


def _recover_action(store, run_id, facts, binding, decide):
    act = decide(facts, current_binding_hash=binding,
                 merge_effect=_merge_effect_from_table(store, run_id, facts))
    return f"{act.do}|{act.why}"


# LAST IN THE FILE, and it must stay last. Running as `python3 -m
# coordinator.cli` -- which is how the runner invokes this, and the only way
# production ever does -- executes the module top to bottom, so anything
# defined BELOW this line does not exist when `main()` runs. Two helpers were
# appended after it and the `recover` branch died with `NameError` on every
# real call, while every test passed: the tests call `main()` in-process after
# the import has completed, where the defs exist.
#
# `test_the_recover_cli_runs_as_a_SUBPROCESS` drives the real invocation.
if __name__ == "__main__":
    sys.exit(main())
