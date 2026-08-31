"""Reading a reviewer's verdict.

`extract_verdict` is ported from `_extract_verdict`, which had 45 call sites
and a trimming loop carrying its own scars. The logic is preserved exactly; it
is merely legible now.
"""

from __future__ import annotations

#: How many trim passes before giving up. BOUNDED on purpose: without it a line
#: of pure decoration could normalise into a verdict one character at a time.
_MAX_TRIM_PASSES = 8

_EDGE = "*`_"


def extract_verdict(text: str) -> str | None:
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

    if last == "VERDICT: PASS":
        return "PASS"
    if last == "VERDICT: FAIL":
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
