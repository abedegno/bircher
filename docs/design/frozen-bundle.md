# The frozen bundle: six decisions

*The spec requires Milestone 1 to **fix** six things about the frozen bundle
rather than gesture at them. Each is recorded here with the symbol that
implements it, and `v2/tests/kernel/test_frozen_bundle_doc.py` fails if a
decision loses its implementation or its reasoning.*

| # | Decision | Fixed as | Where |
|---|---|---|---|
| 1 | Which issue fields form the frozen input | `FROZEN_FIELDS` | `kernel/bundle.py` |
| 2 | How the snapshot is canonicalized for hashing | `snapshot`, `BUNDLE_CANON_VERSION` | `kernel/bundle.py` |
| 3 | What counts as a relevant change | `is_relevant_change` | `kernel/bundle.py` |
| 4 | Who creates a revision | `revise_bundle`, `propose_revision` | `kernel/bundle.py` |
| 5 | Whether implementation output invalidates spec or plan review | `invalidates` | `kernel/bundle.py` |
| 6 | The single transaction joining persistence, enqueue and first transition | `enqueue` | `kernel/enqueue.py` |

## 1. Which fields

`number`, `title`, `body`, `labels`, `comments`. Nothing else.

Volatile metadata — `updated_at`, reaction counts, view counts — is excluded
because it changes without the input changing, and including it would
invalidate approvals for no reason. The set is pinned by a test rather than by
convention: changing it rehashes every bundle ever frozen, so it must be a
deliberate, versioned change.

## 2. Canonicalization

Labels sorted; comments ordered by id; the whole snapshot encoded through the
kernel's versioned canonical form. A re-read of the same issue then hashes the
same regardless of the order the provider returns.

Sorted, **not** deduplicated. Collapsing duplicate labels with a `set` would
make a genuine change invisible. The canon version lives *inside* the
snapshot and therefore inside the hash: a hash whose canonical form can change
without a version is a hash that drifts silently, and every approval bound to
it drifts with it.

## 3. Relevant change

Any change to the frozen input. Defined by the frozen set rather than a
parallel list of "interesting" fields — a parallel list drifts away from the
set it mirrors.

`is_relevant_change` takes **raw provider issues**, not snapshots. Given two
snapshots the answer is `old != new` and the function proves nothing; the
entire content of this decision is that volatile metadata does not count, and
that only shows when something volatile is present to be ignored.

## 4. Revision authority

Only a human. The front end grills and proposes; re-authorizing work is not a
model's to do.

This is **two entry points, not a constant**. `REVISION_AUTHORITY == "human"`
compares a constant to a constant: it states the decision without enforcing
it, and a model calling a revise function would still revise. A model session
reaches `propose_revision`, which records a proposal and changes nothing, and
there is no argument it can pass to reach `revise_bundle`. That function sits
on the operator's side of the M1-1 boundary — the same enforcement
`reconcile()` already relies on — which is what makes `actor="human"` a record
rather than a claim.

The same shape governs grill rulings (`record_model_question` vs
`record_human_answer`) and the enqueue itself (`propose_enqueue` vs
`enqueue`). In each case the function a caller can reach is what decides.

## 5. Review invalidation

| verdict kind | binds |
|---|---|
| `spec_review` | artifact, base, context bundle |
| `plan_review` | artifact, base, context bundle |
| `implementation_review` | artifact, base, head |

**Implementation output does not invalidate spec or plan review.** A spec
verdict binds the spec artifact and the base; it never bound the
implementation head, so invalidating on head would discard sound approvals and
push every run into re-review churn over a change the reviewer never
considered. An implementation verdict does bind the head. Every verdict binds
the base — rebasing the world changes what any approval was about.

`invalidates` raises on an unknown verdict kind. A default of `False` would
make a typo silently mean "nothing invalidates this", which is the fail-open
direction.

## 6. The single transaction

`enqueue` persists both artifacts, creates the run, records its `RUN_STARTED`,
`ARTIFACT_CREATED` and `RUN_ENQUEUED` facts — all inside one transaction. A
crash between them leaves none of the three: a run without its inputs cannot
be replayed, and artifacts without a run are unreferenced.

It is idempotent on `run_id`, because an operator retrying after a partial
failure must not double-enqueue. It refuses an enqueue carrying unanswered
grill questions rather than trusting the front end to have checked — an
enqueue over an unfinished grill approves inputs the human never ruled on.
