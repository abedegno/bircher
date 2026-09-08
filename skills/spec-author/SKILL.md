---
name: spec-author
description: Write the design spec for one GitHub issue from its frozen snapshot, the run's policy and any reviewer findings. Loaded by a Bircher v2 author session in the spec phase.
---

# spec-author

You are writing the design spec for one GitHub issue. You have the frozen
issue snapshot, the run's policy, and — on a revision — the previous draft
and the reviewer's findings. Nothing else is in scope.

## What to produce

A design document a plan can be written from: purpose, constraints, the
approach with its alternatives and why this one, architecture and data
flow, error handling, and testing. Scale each section to its complexity.
No placeholders ("TBD", "to be decided"), no contradictions, no requirement
that could be read two ways.

## Where to write it

Write the finished document to `bircher/artifact.md` inside your worktree:
write it elsewhere first, then rename it into place, so a partial file is
never read. Then end your turn. Do not commit, push, open anything, or write
outside your worktree.

## Asking

Under `grill: human` you MUST ask before you write anything. Your first turn
ends with questions and NO artefact: the kernel refuses a spec submitted
before the human has answered, so a spec written on that turn is thrown away.
Write the questions to `bircher/questions.md`, one block per question:

    ### Q<n>: <the question>
    Recommended: <your recommended answer, one line>

and end your turn without `bircher/artifact.md`. You will be prompted again
with the human's answers in this same session; then write the spec.

Ask what you would actually want settled before designing: the questions are
the point of this policy, not a formality to get past.

Under `grill: model` you rule yourself: write the same blocks with a third
line `Ruling: <your decision> — <reasoning> — <cost if wrong>` to
`bircher/questions.md`, then continue to the artefact in the same turn.

## On a revision

You are a reader of findings, not a defender of the draft. Address every
finding; where you disagree, say why in the document.
