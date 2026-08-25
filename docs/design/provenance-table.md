# Merge authorization: where every input comes from

*A required Milestone 1 artifact, not documentation. `v2/tests/kernel/test_provenance.py`
parses this file and fails if a row is missing or an `asserted` row has no reason.*

One row per input the authorization path reads. **observed** means the
mechanism saw it; **asserted** means an actor supplied it. An `asserted` row is
either a defect or a declared residual, and it must say which. There is no
third case.

Round 5's audit found five of the six links in the merge chain were asserted.
Each comparison was correctly implemented, and the chain proved nothing. What
was missing was not a check — it was this table.

A caller-presented value that the kernel refuses unless it equals a value the
kernel holds is **observed**: the caller can only ever supply the right
answer, and the value that survives is the kernel's.

## Identity

| Input | Enters at | Provenance | Bound by / reason |
|---|---|---|---|
| `actor` | `submit()` | observed | `dispatches`, written by `dispatch()`, which also fences the generation. No payload may name an actor. |
| `role_for` | `authorize`, `validate_review` | observed | the same dispatch row |
| `_implementer_of` | `validate_review`, `revalidate_merge` | observed | `fact.actor` on the last accepted `start_implementation`, written by the kernel from the dispatch record |
| `_reviewer_of` | `revalidate_merge` | observed | `reviewer_identity` on a `review_verdict` fact, written by the kernel from the dispatch record |

## Kernel state

| Input | Enters at | Provenance | Bound by / reason |
|---|---|---|---|
| `store.run_state` | `authorize`, `revalidate_merge` | observed | the aggregate's own state, moved only by an accepted transition |
| `store.current_artifact` | `validate_review`, `authorize`, `revalidate_merge` | observed | `runs.current_artifact_hash`, written only by an accepted `record_implementation_output` |
| `store.has_artifact` | `validate_review`, `authorize`, `revalidate_merge` | observed | content-addressed membership |
| `store.run_base_sha` | `validate_review` | observed | recorded at `create_run` |
| `store.facts_for` | verdicts, CI, implementer, reviewer | observed | append-only fact log, enforced by a trigger |
| `store.has_confirmed_effect` | `record_merge_outcome` | observed | the effect journal |

## Caller-presented, bound to kernel state

| Input | Enters at | Provenance | Bound by / reason |
|---|---|---|---|
| `cmd.payload['artifact_hash']` | `record_review`, `request_merge`, `record_implementation_output` | observed | refused unless equal to `store.current_artifact`; on `record_implementation_output`, unless `store.has_artifact` |
| `cmd.payload['base_sha']` | `record_review` | observed | refused unless equal to `store.run_base_sha` |
| `cmd.payload['outcome']` | `record_merge_outcome` | observed | `merged` refused unless `store.has_confirmed_effect` |
| `authorized['artifact_hash']` | `revalidate_merge` | observed | read from the kernel's own `merge_authorized` fact |
| `authorized['head_git_sha']` | `revalidate_merge` | observed | read from the kernel's own `merge_authorized` fact |
| `fact.payload['binding_hash']` | `_merge_is_authorized` | observed | written by the kernel after `validate_review` |
| `fact.payload['verdict']` | `_merge_is_authorized`, `_reviewer_of` | observed | written by the kernel alongside the binding it validated |
| `fact.payload['reviewer_identity']` | `_reviewer_of` | observed | written by the kernel from the dispatch record |
| `fact.payload['command_name']` | `_implementer_of`, `_ci_is_green` | observed | written by the kernel |
| `fact.payload['payload']` | `_ci_is_green` | observed | the envelope is kernel-written; its CONTENTS are the CI residual below |

## Asserted — declared residuals

Each of these is an actor's claim the kernel does not observe. None is a
defect; each is a limit of Milestone 1, named here so it cannot be mistaken
for a check.

| Input | Enters at | Provenance | Bound by / reason |
|---|---|---|---|
| `cmd.payload['verdict']` | `record_review` | asserted | **Intentional and permanent.** A verdict IS the reviewer's judgment. The kernel binds WHO gave it and WHAT it was about; whether the judgment is correct is not a thing a kernel can observe. |
| `latest['status']` | `_ci_is_green` | asserted | **Residual, M1-4.** The kernel does not run CI; it records what an actor reported. Until the kernel observes the check run itself, a green CI observation is a claim. |
| `latest['head_git_sha']` | `_ci_is_green` | asserted | **Residual, M1-4.** Which head CI ran on is reported by the same actor. The kernel compares it to the head presented for merge, so the two must agree — but both are claims by the same party. |
| `cmd.payload['head_git_sha']` | `request_merge` | asserted | **Residual, M1-4.** Nothing ties the git head to the reviewed artifact. The artifact is content-addressed and the head is not; closing this needs the kernel to observe the ref. |
| `cmd.payload['context_bundle_hash']` | `record_review`, `request_merge` | asserted | **Residual, M1-4.** The kernel never sees the context bundle. It enforces only that review and merge present the SAME one, which detects drift between the two but not a bundle that never existed. |
| `payload['policy_version']` | `_binding_from` | asserted | **Residual, M1-4.** Type-checked (`type(...) is int`, so no float or bool coerces in) but not compared against any policy the kernel holds. |
