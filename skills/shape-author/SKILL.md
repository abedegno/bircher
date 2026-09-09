---
name: shape-author
description: Decide whether one GitHub issue is one piece of work or an epic, and if an epic, write its slice plan. Loaded by a Bircher v2 author session in the shaping phase, before any spec.
---

# shape-author

You are deciding the SHAPE of one GitHub issue before anyone specs it. You
have the frozen issue snapshot, the run's policy, the definition of a slice
in the brief, and — on a revision — your previous slice plan and the
reviewer's findings. Nothing else is in scope.

## The decision

Read the issue and decide: is this one coherent piece of work a single spec,
plan and pull request can carry, or several? Use the definition of coarse in
the brief, and nothing finer. A one-piece issue costs you one ruling; an
epic costs you a slice plan the other vendor reviews.

## One piece

Write exactly this to `bircher/shape.md` and end your turn:

    Ruling: one piece
    Reasoning: <why one spec can carry it, one or more lines>
    Cost if wrong: <what the spec phase discovers if it was an epic after all>

The first non-blank line must be exactly `Ruling: one piece`; both blocks
must be non-empty. Do not also write `bircher/artifact.md`: a ruling beside
a plan is read as the ruling and the plan is discarded.

## An epic

Write the slice plan to `bircher/artifact.md` — write it elsewhere first,
then rename it into place, so a partial file is never read — and end your
turn without `bircher/shape.md`. The grammar is fixed and the kernel refuses
a plan that does not fit it:

    # <title of the slice plan>

    <preamble: why this issue is several pieces — free text>

    ## Slice 1: <title>
    Scope: <three to six sentences>
    Non-goals: <one line>
    Depends on: none

    ## Slice 2: <title>
    Scope: ...
    Non-goals: ...
    Depends on: 1

Two to five slices, numbered 1..N in order. Each has exactly one `Scope:`
(three to six sentences by full stops), one `Non-goals:` and one
`Depends on:` (`none` or a comma-separated list of slice numbers, no cycles,
no self-dependency). A slice plan names no files, tasks or acceptance
tests: the design of each slice is its own spec phase's work. Every slice
is filed as its own issue and worked through the whole pipeline; a plan
that sliced the issue into its plan-steps has recreated the problem.

## On a revision

You are a reader of findings, not a defender of the plan. End the revised
plan with a section headed `## Dispositions`, one line per finding of the
round before, by its number:

    N. accepted — <what changed, in one sentence>
    N. rejected — <why the finding is wrong or out of scope>

You may also rule the issue one piece on a revision: write `bircher/shape.md`
instead of a plan. The reviewer reads the dispositions first and will not
re-raise a finding you resolved or refused with reasons.

Do not commit, push, open anything, or write outside your worktree.
