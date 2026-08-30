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
First: export PATH=/root/bin:$PATH; git fetch origin pull/{pr}/head; git worktree add --detach /tmp/review-{pr} {co}; cd /tmp/review-{pr}.
You are reviewing EXACTLY commit {co}. If that checkout fails, STOP and report it -- do not review a different commit.
READ the changed files AND enough surrounding code to verify correctness -- do NOT judge from the diff alone.
Run the gates you can, EACH as ONE command prefixed with 'export PATH=/root/bin:$PATH &&' (e.g. 'export PATH=/root/bin:$PATH && go build ./...', '... && go vet ./...', client '... && npm run typecheck' / '... && npx vitest run', plugin '... && pytest'); DB-backed 'go test' needs a DB the runner lacks, so for THOSE you must not simply accept a green check.
A green check is a CLAIM, not evidence: for any gate you could not run yourself, open the run log (`gh pr checks {pr}` to find the run, then `gh run view <run-id> --log`) and RECONCILE it with the check's conclusion -- a step can execute, report failing tests, and STILL be reported green if its exit code was swallowed (`|| true`, continue-on-error, a wrapper that always exits 0). Quote the log line showing test counts or the failure, and NAME every gate you delegated rather than ran. If you cannot reach the log, say so and treat that gate as UNVERIFIED -- do not report it as passing. (muesli #705 shipped a CI gate that reported success while tests failed; it passed review because the reviewer was told to trust the check.)
If the change acquires a resource that must be released -- a capture device, stream, handle, lock or subscription -- verify its FAILURE paths are tested, not just the happy path; a missing release-on-error test is a blocking finding. (muesli #666 left a microphone recording when a capture start failed.) Keep that scope narrow: do not treat every state change as in scope.
Report blocking / non-blocking / suggestion findings, then a FINAL LINE that is EXACTLY 'VERDICT: PASS' or 'VERDICT: FAIL'. Put findings BEFORE the verdict so the verdict is the last line even if output is long."""


def review_prompt(pr: str, repo: str, sha: str = "") -> str:
    """The prompt handed to the reviewer.

    #66: the worktree is created at the EXACT captured commit, not FETCH_HEAD.
    `pull/N/head` is a MOVING ref -- a push between capture and the reviewer's
    fetch would have it read one commit while the merge pinned another.
    """
    return _PROMPT.format(pr=pr, repo=repo, co=(sha or "FETCH_HEAD"))


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
    prompt = review_prompt(pr, repo, sha)
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
