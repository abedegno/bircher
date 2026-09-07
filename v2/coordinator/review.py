"""Reading a reviewer's verdict.

`extract_verdict` is ported from `_extract_verdict`, which had 45 call sites
and a trimming loop carrying its own scars. The logic is preserved exactly; it
is merely legible now.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How many trim passes before giving up. BOUNDED on purpose: without it a line
#: of pure decoration could normalise into a verdict one character at a time.
_MAX_TRIM_PASSES = 8

_EDGE = "*`_"


def extract_verdict(text: str, hash8: str | None = None) -> str | None:
    """`PASS`, `FAIL`, or None from a reviewer's output.

    `VERDICT: BLOCKED` maps to None -- the reviewer says it formed no opinion,
    which is exactly the "approved nothing" case, NOT a rejection. Before this
    existed the prompt offered only PASS and FAIL, so a reviewer whose checkout
    failed had to pick one; it picked FAIL, and the run recorded
    `review=codex:fail` for a pull request nobody had read.

    Reads the LAST non-blank line and requires it to be a BARE verdict. A
    verdict mentioned mid-report is not a verdict -- a reviewer writing "if the
    tests passed I would say VERDICT: PASS" must not merge anything.

    Decoration is tolerated because reviewers emit markdown: `**VERDICT:
    PASS**`, `` `VERDICT: PASS` ``, `VERDICT: PASS.` all count. ONE trailing
    sentence-ending mark is allowed, once -- a line ending `...` is prose.

    *hash8* is the artefact mode this task adds (spec §3 Review round): the
    reviewer names WHAT it reviewed, not just its opinion of it. Without it a
    reviewer's stale verdict from a superseded revision -- or a verdict for a
    different phase's artefact entirely -- would read as approval of whatever
    the run currently holds. `None` keeps the PR path's grammar exactly as it
    was: a bare `VERDICT: PASS|FAIL`, no hash.
    """
    lines = [l.rstrip() for l in (text or "").splitlines()]
    non_blank = [l for l in lines if l.strip()]
    if not non_blank:
        return None

    last = non_blank[-1]
    punct_stripped = False
    for _ in range(_MAX_TRIM_PASSES):
        before = last
        last = last.strip()
        if last[:1] in _EDGE:
            last = last[1:]
        if last[-1:] in _EDGE:
            last = last[:-1]
        elif last[-1:] in ".!" and not punct_stripped:
            last = last[:-1]
            punct_stripped = True
        if last == before:
            break

    if hash8 is None:
        if last == "VERDICT: PASS":
            return "PASS"
        if last == "VERDICT: FAIL":
            return "FAIL"
        return None
    # Artefact mode: the verdict names what it reviewed (spec §3 Review round).
    if last == f"VERDICT: PASS {hash8}":
        return "PASS"
    if last == f"VERDICT: FAIL {hash8}":
        return "FAIL"
    return None


#: The review prompt, ported VERBATIM from `_recovery_review_prompt`.
#:
#: Prose, not logic -- but prose carrying its own scars (muesli #705's CI gate
#: that reported success while tests failed; #666's microphone left recording;
#: #66's moving `pull/N/head` ref). A paraphrase would drop one silently, so
#: `test_the_rendered_prompt_is_byte_identical_to_the_bash` renders both and
#: compares.
_PROMPT = r"""Review PR #{pr} in {repo} as an INDEPENDENT, READ-ONLY reviewer. Do NOT edit, commit, or open/update any PR.
First: export PATH=/root/bin:$PATH; git fetch origin pull/{pr}/head; git worktree remove --force /tmp/review-{pr}-{nonce}-oob 2>/dev/null; rm -rf /tmp/review-{pr}-{nonce}-oob; git worktree add --detach /tmp/review-{pr}-{nonce}-oob {co}; cd /tmp/review-{pr}-{nonce}-oob.
You are reviewing EXACTLY commit {co}. If that checkout fails, STOP and report it -- do not review a different commit.
READ the changed files AND enough surrounding code to verify correctness -- do NOT judge from the diff alone.
Run the gates you can, EACH as ONE command prefixed with 'export PATH=/root/bin:$PATH &&' (e.g. 'export PATH=/root/bin:$PATH && go build ./...', '... && go vet ./...', client '... && npm run typecheck' / '... && npx vitest run', plugin '... && pytest'); DB-backed 'go test' needs a DB the runner lacks, so for THOSE you must not simply accept a green check.
A green check is a CLAIM, not evidence: for any gate you could not run yourself, open the run log (`gh pr checks {pr}` to find the run, then `gh run view <run-id> --log`) and RECONCILE it with the check's conclusion -- a step can execute, report failing tests, and STILL be reported green if its exit code was swallowed (`|| true`, continue-on-error, a wrapper that always exits 0). Quote the log line showing test counts or the failure, and NAME every gate you delegated rather than ran. If you cannot reach the log, say so and treat that gate as UNVERIFIED -- do not report it as passing. (muesli #705 shipped a CI gate that reported success while tests failed; it passed review because the reviewer was told to trust the check.)
If the change acquires a resource that must be released -- a capture device, stream, handle, lock or subscription -- verify its FAILURE paths are tested, not just the happy path; a missing release-on-error test is a blocking finding. (muesli #666 left a microphone recording when a capture start failed.) Keep that scope narrow: do not treat every state change as in scope.
Report blocking / non-blocking / suggestion findings, then a FINAL LINE that is EXACTLY 'VERDICT: PASS', 'VERDICT: FAIL', or 'VERDICT: BLOCKED'.
Use BLOCKED, and ONLY BLOCKED, when you could not review at all -- the checkout failed, the tooling was unavailable, the commit was unreachable. BLOCKED means "I formed no opinion"; FAIL means "I reviewed this and it must not merge". They are routed differently and confusing them is expensive: a reviewer that could not check out its worktree once emitted FAIL, and the run recorded a code rejection for a PR nobody had read.
Put findings BEFORE the verdict so the verdict is the last line even if output is long."""


def review_prompt(pr: str, repo: str, sha: str = "", nonce: str = "") -> str:
    """The prompt handed to the reviewer.

    #66: the worktree is created at the EXACT captured commit, not FETCH_HEAD.
    `pull/N/head` is a MOVING ref -- a push between capture and the reviewer's
    fetch would have it read one commit while the merge pinned another.
    """
    # A UNIQUE WORKTREE PER REVIEWER, and the `-oob` suffix is the part that
    # does the work. A sha-derived nonce alone is NOT enough: the two reviewers
    # review the SAME commit, so they would compute the same nonce and collide
    # exactly as before. The suffix names WHICH reviewer this is -- the
    # out-of-band one the coordinator dispatches, as opposed to the one the
    # lead session arranges from `skills/cross-review`.
    #
    # AND THE PATH IS CLEARED BEFORE IT IS CREATED, because the nonce identifies
    # the COMMIT and the repair loop reviews one commit more than once. muesli
    # #711 round 2: the reviewer found `/tmp/review-751-934bda3d-oob` left by
    # round 1 and answered, correctly, "Exact checkout failed because it already
    # exists. Per instruction, no review was performed. VERDICT: BLOCKED" -- so
    # the round produced `codex:na` and the item escalated with a working PR.
    #
    # Clearing beats a better nonce here, and both are done. A nonce fixes the
    # collisions we predict; clearing also fixes the leftovers we do not --
    # crashed runs, killed sessions, and the worktrees this runner has been
    # accumulating since smoke PR #11 with nothing to remove them.
    #
    # Both reviewers in this system used
    # `/tmp/review-<PR>`: `skills/cross-review/SKILL.md` for the lead session's
    # and this prompt for the coordinator's. On muesli PR #745 the first
    # created the directory and the second died on
    # `fatal: '/tmp/review-745' already exists`. Nothing cleans these up
    # either -- the runner still holds worktrees from smoke PRs #11-#16.
    return _PROMPT.format(pr=pr, repo=repo, co=(sha or "FETCH_HEAD"),
                          nonce=(nonce or (sha or "head")[:8]))


def dispatch(pr: str, repo: str, sha: str, *, reviewer: str, bundle_dir: str,
             server: str, log_path: str, run=None) -> tuple[str | None, str]:
    """Dispatch a reviewer and read its verdict. Returns (verdict, output).

    `verdict` is `PASS`, `FAIL`, or None. **None is not a soft PASS.** A
    reviewer that crashed, timed out or produced no parseable verdict has
    approved nothing, and the classifier routes that to `escalated`.

    A NON-ZERO EXIT returns None WITHOUT reading the log: a dead reviewer's
    stdout is not evidence, and mining it would let a crash that echoed its own
    prompt authorise a merge.
    """
    import os
    import subprocess

    # `stderr=STDOUT`, exactly like the bash's `2>&1` -- ONE stream, interleaved
    # as it is produced.
    #
    # Capturing them separately and concatenating looks equivalent and is not:
    # omnigent writes its progress lines ("Launching your agent...") to stderr,
    # so the joined text ended with those instead of the reviewer's last line.
    # `extract_verdict` reads the LAST non-blank line, so a genuine
    # `VERDICT: PASS` was read as no verdict and every review escalated. Four
    # live runs to find; invisible to every test, because the tests inject a
    # runner and never produce two streams.
    runner = run or (lambda argv, cwd: subprocess.run(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True))
    # THE NONCE NAMES THE ATTEMPT, not the commit. `BIRCHER_GENERATION` is
    # re-minted by `_kernel_dispatch` for every dispatch, so two rounds of the
    # repair loop get different worktrees even when they review the SAME sha --
    # which is exactly what happens when a repair round pushes nothing.
    #
    # Falls back to the sha when the variable is absent, which is what every
    # caller outside the runner does; the prompt clears a stale path either way,
    # so the fallback degrades to "slightly less informative", not "collides".
    gen = os.environ.get("BIRCHER_GENERATION", "").strip()
    nonce = f"{(sha or 'head')[:8]}-g{gen}" if gen.isdigit() else ""
    prompt = review_prompt(pr, repo, sha, nonce=nonce)
    r = runner(["omnigent", "run", f"agents/{reviewer}", "--server", server,
                "-p", prompt], bundle_dir)
    out = r.stdout or ""
    try:
        with open(log_path, "w") as fh:
            fh.write(out)
    except OSError:
        pass
    if r.returncode != 0:
        return None, out
    return extract_verdict(out), out


# -- Artefact mode (spec §3 Review round) -------------------------------------
#
# The PR path above dispatches a reviewer through `omnigent run` and reads a
# finished process's stdout. Artefact mode reviews a spec or a plan: the
# reviewer is a SEAT (a session the coordinator itself prompts and watches,
# same as the author round), the brief the kernel rendered is its first and
# only prompt, and the verdict must name the artefact's `hash8` -- the
# grammar `extract_verdict` gained above. The two paths share nothing but the
# grammar; keeping them in one file is a statement that reading a verdict is
# one problem even though producing the prompt that earns one is two.


@dataclass
class ReviewOutcome:
    """What one call to `review_round` did, for the loop to park on.

    `status` is the discriminator: "recorded" (accept or request_revision
    written), "no_verdict" (the file was absent, unparsable, or named the
    wrong hash), "bound_exhausted" (a FAIL with no rounds left -- recorded
    nothing, since the kernel itself refuses that `request_revision`),
    "budget" (no seat left to dispatch), "failed" (an adopted session turned
    out to belong to someone else), "no_brief" (the kernel refused to issue
    one, e.g. the run has moved on).
    """
    status: str
    verdict: str | None = None
    findings_hash: str | None = None
    reviewer: str = ""
    recorded: bool = False
    rounds_left: int = 0
    session_id: str | None = None
    cursor: str | None = None
    generation: int | None = None


def choose_reviewer_vendor(ctx) -> str:
    """spec §3 Rotation: the vendor that did not author the artefact under review."""
    from kernel import front
    sub = front.newest_submission(ctx.store, ctx.run_id, ctx.phase(), ctx.epoch())
    author_vendor = sub.payload["author"]
    others = [v for v in sorted(ctx.agent_ids) if v != author_vendor]
    return others[0] if others else author_vendor


def review_round(ctx) -> ReviewOutcome:
    """One pass of the review round: dispatch the reviewer, issue the brief,
    run its one turn, and record what it said (spec §3 Review round).

    The seat is dispatched BEFORE the brief is issued, not after: the brief
    the kernel renders binds itself to a generation (`review_brief_issued`
    carries `generation`), and `validate_review` later requires that
    generation to be the one the reviewer's `record_review` comes from. Brief
    first would render under whatever generation happened to be current --
    not necessarily the seat about to be prompted with it -- and the reviewer
    would submit a review the kernel refuses for carrying the wrong
    generation's brief. `run_turn`'s `reuse_generation=True` is the other half
    of this: it must NOT dispatch a second time and strand the brief under a
    generation nothing was ever prompted under.

    The SESSION obligation's cause is the round's own (`phases.seat_cause`):
    a round has one seat, so a retry of the same round -- no new submission,
    no new grant -- adopts it rather than opening a second one. The PROMPT's
    cause is this call's own dispatch id, not the round's: two review_round
    calls under the same round cause but with no verdict recorded between
    them are two separate attempts at reading a verdict from the SAME
    ongoing session, and the second must send its brief as a fresh turn
    rather than read whatever the first attempt's turn left on disk. Keying
    the prompt to the round's cause instead collapsed every no-verdict retry
    into one already-satisfied `sess-prompt`, so a second attempt never
    re-prompted at all and silently reported the first attempt's stale file.
    """
    from coordinator import phases, seat
    from coordinator.session import AgentMismatch
    from kernel import front
    from kernel.artifacts import put_artifact
    from kernel.authz import NotAuthorized
    from kernel.brief import hash8
    from kernel.dispatch import Role, SeatsExhausted, dispatch

    store, run_id = ctx.store, ctx.run_id
    phase, n = ctx.phase(), ctx.epoch()
    cause = phases.seat_cause(ctx)
    vendor = choose_reviewer_vendor(ctx)
    session_ob = {"kind": "sess-create", "run": run_id, "phase": phase, "epoch": n, "cause": cause.id}
    # The seat is dispatched here, before the brief, so the brief's generation
    # is the seat's; run_turn re-uses ctx.generation when it is the newest.
    try:
        dispatched = dispatch(store, run_id, actor=vendor, role=Role.REVIEWER)
    except SeatsExhausted:
        return ReviewOutcome("budget", reviewer=vendor)
    ctx.generation = dispatched.generation
    try:
        seat.command(ctx, "issue_review_brief", {"phase": phase})
    except NotAuthorized as exc:
        ctx.log(f"brief refused: {exc}")
        return ReviewOutcome("no_brief", reviewer=vendor, generation=ctx.generation)
    issued = front.brief_for(store, run_id, ctx.generation).payload
    brief_bytes = store.read_blob(issued["brief_hash"])
    try:
        # This attempt's own dispatch id, not the round's cause -- see the
        # docstring above.
        turn = seat.run_turn(ctx, role=Role.REVIEWER, vendor=vendor, session_obligation=session_ob,
                             prompt_cause=dispatched.dispatch_id, prompt_text=brief_bytes,
                             watched=[seat.REVIEW_OUT], reuse_generation=True)
    except SeatsExhausted:
        return ReviewOutcome("budget", reviewer=vendor)
    except AgentMismatch as exc:
        ctx.log(f"agent mismatch: {exc}")
        return ReviewOutcome("failed", reviewer=vendor)
    data = turn.files.get(seat.REVIEW_OUT)
    cursor = turn.listing[-1]["id"] if turn.listing else None
    if data is None:
        return ReviewOutcome("no_verdict", reviewer=vendor, session_id=turn.session_id, cursor=cursor,
                             generation=turn.generation)
    findings_hash = put_artifact(store, data)
    verdict = extract_verdict(data.decode("utf-8", "replace"), hash8(issued["artifact_hash"]))
    left = front.round_bound(store, run_id, phase, n) - front.rounds_used(store, run_id, phase, n)
    base = dict(reviewer=vendor, findings_hash=findings_hash, session_id=turn.session_id, cursor=cursor,
                generation=turn.generation, rounds_left=left, verdict=verdict)
    if verdict is None:
        return ReviewOutcome("no_verdict", **base)
    # A FAIL with no rounds left is not recorded: the kernel itself refuses
    # that request_revision (`_review_destination`'s max_rounds check), and
    # sending it anyway would turn a bound the spec means to enforce into an
    # exception the loop has to catch instead of a status it can park on.
    if verdict == "FAIL" and left <= 0:
        return ReviewOutcome("bound_exhausted", **base)
    seat.command(ctx, "record_review", {
        "phase": phase, "verdict": "accept" if verdict == "PASS" else "request_revision",
        "artifact_hash": issued["artifact_hash"], "base_sha": issued["base_sha"],
        "context_bundle_hash": issued["context_bundle_hash"], "policy_version": issued["policy_version"],
        "findings_hash": findings_hash,
    })
    return ReviewOutcome("recorded", recorded=True, **base)
