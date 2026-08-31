# Effect sites in `batch/run-queue.sh`

*Every externally visible mutation the coordinator performs. The routing
detector asserts against this list, so an omission here becomes a boundary
with a hole in it.*

Produced by unioning six grep patterns. The first five overlap deliberately,
because one pattern's exclusion is another's inclusion and **the filter is
where detectors fail**.

**Round 6 correction.** Those five were not independent: every one of them
looked for `gh` or `git push`, so the union of five patterns was one pattern.
Three live `session_control` mutations — session create, prompt and stop, all
plain `curl` — were invisible to all of them, and to the detector built from
them. The adversarial attention went entirely to the exclusions; the
*inclusion* set was never questioned. The `curl` pattern is the sixth.

Confirmed against `batch/run-queue.sh` at 6883 lines on 2026-08-26. Line
numbers move: re-run the patterns and reconcile before relying on them.

```bash
grep -nE "curl[^|]*-X *(POST|PUT|PATCH|DELETE)" batch/run-queue.sh
grep -nE "gh (pr|issue) (merge|close|reopen|comment|edit|create|review)" batch/run-queue.sh
grep -nE "gh api [^|]*-X (POST|PUT|PATCH|DELETE)" batch/run-queue.sh
grep -nE "gh api [^|]*statuses" batch/run-queue.sh
grep -nE "git .*push" batch/run-queue.sh
grep -nE "gh .*--add-label|--remove-label" batch/run-queue.sh
```

## Mutations — 18 routed sites

| Line | Call | Effect class |
|---|---|---|
| 291 | `gh issue reopen` | `issue_or_label` |
| 5093 | `gh api repos/$REPO/statuses/$sha -X POST` | `status_check` |
| 1655 | `gh pr merge --squash --delete-branch` | `merge` |
| 1859 | `git push origin HEAD:main` | `ref_update` |
| 2006 | `gh api repos/$REPO/pulls/$pr/update-branch -X PUT` | `ref_update` |
| 2511 | `gh pr close` | `pull_request` |
| 5105 | `gh pr comment` | `comment` |
| 3707 | `gh issue comment` | `comment` |
| 3708 | `gh issue edit --remove-label` | `issue_or_label` |
| 3709 | `gh issue edit --add-label` | `issue_or_label` |
| 3726 | `gh issue close` | `issue_or_label` |
| 4017 | `gh issue edit --add-label bircher:running` | `issue_or_label` |
| 1088 | `curl -X POST $SERVER/v1/sessions/$1/events` | `session_control` |
| 1114 | `curl -X DELETE $SERVER/v1/sessions/$1` | `session_control` |
| 1064 | `curl -X POST $SERVER/v1/sessions` | `session_control` |
| 1100 | `curl -X POST $SERVER/v1/sessions/$1/events` | `session_control` |

**1519 is `merge`, not `pull_request`.** An earlier draft of the M1-4 plan
classified it as `pull_request`. M1-3 split `merge` into its own class
precisely so the authority-bearing operation would not share a gate with the
routine one, and it is now the only class `perform()` revalidates. Routing it
as `pull_request` would have moved the merge through the adapter while
bypassing every check the revalidation exists to run — the letter satisfied,
the point lost.

## Matches that are NOT calls — 6 lines

Every one is a mutation-shaped string inside a **quoted literal**: an
assignment value, a string comparison, or a selftest assertion about the
source. They are recorded here because the detector must suppress them, and a
suppression nobody wrote down is a suppression nobody re-reads.

| Line | Text | Why it is not a call |
|---|---|---|
| 1682 | `MERGE_NOTE="merge deferred: gh pr merge failed"` | assignment value |
| 6675 | `[ "$MERGE_NOTE" = "merge deferred: gh pr merge failed" ]` | string comparison |
| 7592 | `_contains "$_body" '_net_run … git push origin'` | selftest asserting the source contains it |
| 7593 | `echo "FAIL #62: the recovery git push must be routed AND bounded"` | failure message |
| 7616 | `echo "FAIL #62: … a git push that ignores SIGTERM …"` | failure message |

## Reads — not journalled

`gh api` calls that only read. Listed so a later reader can tell an omission
from a classification:

`repos/$REPO/compare/…`, `repos/$REPO/git/trees/…`,
`repos/$REPO/branches/…/protection`, required-context discovery, PR and issue
`view`/`list`, `gh run view`, and every `git fetch` / `git worktree add`.

## Dispositioned — present, not routed

An unrouted mutation must appear here with a reason.
`test_routing.py::test_criterion_1_run_queue_has_no_unrouted_mutation` reads
this table, so a site cannot be quietly left out: it is either routed or
named here.

| Line | Call | Class | Why not routed |
|---|---|---|---|
| 3514 | `gh run rerun` | *(none)* | **Re-triggers a workflow. Unrouted, and UNDETECTED until 2026-08-29** — `MUTATION` enumerates verbs and `gh run` was a noun it had never been taught, so a whole command family was invisible. Second instance of that class in one day; the first hid behind a wrapper. Now detected, and enumerated by `test_every_gh_subcommand_is_classified` so a new `gh <noun> <verb>` cannot join silently. **Left unrouted deliberately:** it creates no object and changes no repository content — it re-runs an existing workflow after an INFRASTRUCTURE failure, and its only influence on a kernel decision is via CI status, which the derivation re-observes rather than trusts. Routing it needs an effect class for "trigger a workflow", which the journal does not have — a design decision, not a migration step. Its real cost is CI minutes, bounded by `BIRCHER_CI_RERUN_MAX` (default 4). |
| 3516 | `gh run rerun` | *(none)* | Same site class as the other `gh run rerun` row above — see it for the reasoning. **Named, not numbered**, because the repointing tool remaps the Line column and cannot follow a line number written in prose: this reference had been stale since the row moved to 3457. |

| 1010 | `curl -X POST $SERVER/v1/sessions` | `session_control` | **Its response body is parsed, but that is NOT what blocks routing — corrected 2026-08-29.** The recorded reason was that routing needs the intent contract to carry a response. It does not: `perform` already returns the executor's stdout as the external object id, and `_create_session` was routed on exactly that basis, capturing the response and parsing it afterwards. The ACTUAL blocker is `-w`. The upload uses `-w '\n%{http_code}'` to capture the status alongside the body, and `-w` is deliberately absent from the `session_control` contract because its value supports `%output{path}` — an arbitrary filesystem write from a class that exists to control a session. Checked directly: the contract refuses this argv, reading the `-w` value as the URL. **The route out is `--fail-with-body`** (curl 8.14.1 on the runner supports it), which yields the body AND a non-zero exit on an HTTP error, making `-w` unnecessary. Not done here because it changes what the caller receives on failure, on the coordinator's hot path where every run begins. |
