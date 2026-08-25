# Effect sites in `batch/run-queue.sh`

*Every externally visible mutation the coordinator performs. The routing
detector asserts against this list, so an omission here becomes a boundary
with a hole in it.*

Produced by unioning five independent grep patterns rather than trusting one —
they overlap deliberately, because one pattern's exclusion is another's
inclusion and **the filter is where detectors fail**.

Confirmed against `batch/run-queue.sh` at 6851 lines on 2026-08-25. Line
numbers move: re-run the patterns and reconcile before relying on them.

```bash
grep -nE "gh (pr|issue) (merge|close|reopen|comment|edit|create|review)" batch/run-queue.sh
grep -nE "gh api [^|]*-X (POST|PUT|PATCH|DELETE)" batch/run-queue.sh
grep -nE "gh api [^|]*statuses" batch/run-queue.sh
grep -nE "git .*push" batch/run-queue.sh
grep -nE "gh .*--add-label|--remove-label" batch/run-queue.sh
```

## Mutations — 13 sites

| Line | Call | Effect class |
|---|---|---|
| 366 | `gh issue reopen` | `issue_or_label` |
| 1276 | `gh api repos/$REPO/statuses/$sha -X POST` | `status_check` |
| 1503 | `gh pr merge --squash --delete-branch` | `merge` |
| 1707 | `git push origin HEAD:main` | `ref_update` |
| 1800 | `gh api repos/$REPO/pulls/$pr/update-branch -X PUT` | `ref_update` |
| 1883 | `gh api repos/$REPO/pulls/$pr/update-branch -X PUT` | `ref_update` |
| 1963 | `gh pr close` | `pull_request` |
| 2122 | `gh pr comment` | `comment` |
| 2964 | `gh issue comment` | `comment` |
| 2965 | `gh issue edit --remove-label` | `issue_or_label` |
| 2966 | `gh issue edit --add-label` | `issue_or_label` |
| 2982 | `gh issue close` | `issue_or_label` |
| 3143 | `gh issue edit --add-label bircher:running` | `issue_or_label` |

**1503 is `merge`, not `pull_request`.** An earlier draft of the M1-4 plan
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
| 1530 | `MERGE_NOTE="merge deferred: gh pr merge failed"` | assignment value |
| 5093 | `[ "$MERGE_NOTE" = "merge deferred: gh pr merge failed" ]` | string comparison |
| 5163 | `[ "$MERGE_NOTE" = "merge deferred: gh pr merge failed" ]` | string comparison |
| 5888 | `_contains "$_body" '_net_run … git push origin'` | selftest asserting the source contains it |
| 5889 | `echo "FAIL #62: the recovery git push must be bounded"` | failure message |
| 5906 | `echo "FAIL #62: … a git push that ignores SIGTERM …"` | failure message |

## Reads — not journalled

`gh api` calls that only read. Listed so a later reader can tell an omission
from a classification:

`repos/$REPO/compare/…`, `repos/$REPO/git/trees/…`,
`repos/$REPO/branches/…/protection`, required-context discovery, PR and issue
`view`/`list`, `gh run view`, and every `git fetch` / `git worktree add`.
