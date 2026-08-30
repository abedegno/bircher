# What the review's base binding actually checks

Recorded because it has been described — by me, repeatedly — as "the most serious
defect found", and that is an overstatement. This is the accurate version.

## The mechanism

1. `run_item` records the base at run start:
   `_base_sha=$(git -C "$WORKDIR" rev-parse HEAD)` → `_kernel_run_start`.
2. `store.create_run` writes it to `runs.base_sha`, immutable thereafter.
3. `validate_review` compares the review's asserted `binding.base_sha` against
   `store.run_base_sha(run_id)` and refuses a mismatch:

       "an approval binds inputs the mechanism saw, not ones the actor asserts"

## What it does check

An actor cannot claim a base the kernel did not record. That is real: it stops a
review binding a base of the reviewer's choosing, and the error message is
accurate about that specific property.

## What it does NOT check

**Whether the recorded base is still the target branch's tip when the merge runs.**
`store.run_base_sha` returns the value written at run start, so the comparison is
between an assertion and a stored constant. Nothing re-observes the base. On a
recovery path this is doubly so: `_kernel_adopt_run` deliberately reloads the run's
recorded base, which `kernel-client.sh` already documents as making the check
"TAUTOLOGICAL ON THIS PATH".

`--match-head-commit` does not cover the gap either — it pins the PR's HEAD, not
the base it merges into.

## Why this is not currently a live risk on muesli

`abedegno/muesli` sets `strict: true` on main ("require branches to be up to date
before merging"). When main moves, an unrebased PR becomes `mergeStateStatus=BEHIND`
and GitHub refuses the merge. The pre-merge gate defers on BEHIND rather than
attempting it, so the "reviewed Monday, merged Friday against an untested main"
scenario is stopped — by branch protection, not by the kernel.

## When it WOULD matter

- A target repository WITHOUT `strict: true`. There, GitHub permits merging a
  stale branch, and the kernel's base check is the only thing that might have
  objected — and it cannot, because it compares the run's base against itself.
- Any future change that relaxes the BEHIND handling in the pre-merge gate.

## Honest severity

Moderate, and configuration-dependent. The check is weaker than its own error
message implies, and the message is the misleading part: it says the approval
binds "inputs the mechanism saw", which reads as a live observation and is a
stored constant.

A fix means binding an approval to an OBSERVED base rather than a recorded one,
which changes what a review means and needs its own design. It is not urgent while
`strict: true` holds, and pretending otherwise crowds out work that is.
