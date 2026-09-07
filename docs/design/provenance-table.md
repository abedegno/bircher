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
| `actor_for` | `_check_submit` | observed | the same dispatch row |
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
| `store.phase_artifact` | `validate_review`, `_check_submit` | observed | `phase_artifacts`, written only by an accepted `submit_spec`/`submit_plan` |
| `store.read_blob` | `_check_submit` | observed | content-addressed bytes |
| `store.facts_of_kind` | `front.submissions`, `front.epoch` | observed | the append-only journal |

## Caller-presented, bound to kernel state

| Input | Enters at | Provenance | Bound by / reason |
|---|---|---|---|
| `cmd.payload['artifact_hash']` | `submit_spec`, `submit_plan`, `record_review`, `request_merge`, `record_implementation_output` | observed | on a submit, refused unless `store.has_artifact` holds it; on a front-half review, unless equal to `store.phase_artifact` for the state's phase; on a back-half review and on `request_merge`, unless equal to `store.current_artifact`; on `record_implementation_output`, unless `store.has_artifact` |
| `cmd.payload['phase']` | `record_review` | observed | refused unless equal to `phase_of(store.run_state)` |
| `cmd.payload['findings_hash']` | `record_review` | observed | refused unless the store holds it |
| `cmd.payload['hash']` | `_check_submit` | observed | read from the kernel's own `artifact_submitted` facts |
| `cmd.payload['author']` | `validate_review` | observed | read from the kernel's own `artifact_submitted` facts |
| `cmd.payload['ended']` | `record_turn_ended` | observed | refused unless one of `TURN_ENDS` |
| `cmd.payload['base_sha']` | `record_review` | observed | refused unless equal to `store.run_base_sha` |
| `cmd.payload['outcome']` | `record_merge_outcome` | observed | `merged` refused unless `store.has_confirmed_effect` |
| `authorized['artifact_hash']` | `revalidate_merge` | observed | read from the kernel's own `merge_authorized` fact |
| `authorized['head_git_sha']` | `revalidate_merge` | observed | read from the kernel's own `merge_authorized` fact |
| `fact.payload['binding_hash']` | `_merge_is_authorized` | observed | written by the kernel after `validate_review` |
| `fact.payload['verdict']` | `_merge_is_authorized`, `_reviewer_of` | observed | written by the kernel alongside the binding it validated |
| `fact.payload['phase']` | `_merge_is_authorized`, `_reviewer_of` | observed | written by the kernel from `phase_of(store.run_state)` when the review was recorded; only an `implementation` verdict authorizes a merge |
| `fact.payload['reviewer_identity']` | `_reviewer_of` | observed | written by the kernel from the dispatch record |
| `fact.payload['command_name']` | `_implementer_of`, `_ci_is_green` | observed | written by the kernel |
| `fact.payload['payload']` | `_ci_is_green` | observed | the envelope is kernel-written; its CONTENTS are the CI residual below |
| `cmd.payload['rejection']` | `dismiss_human_item` | observed | refused unless it names a `command_rejected` fact of this run attributed to `human`, and unless it is not already dismissed (one reply per refusal) |
| `cmd.payload['sha256']` | `record_prompt_item` | observed | refused unless a satisfied `sess-prompt` of the named session carries it as `body.artifact` |
| `cmd.payload['issue']` | `revise_bundle` | observed | refused unless its snapshot hashes differently from the current bundle -- the kernel observes only that COMPARISON; the fetched issue's content is asserted by the runner adapter (§9), named as a residual below |
| `cmd.payload['session']` | `record_turn_ended`, `record_author_empty` | observed | refused unless it names the session the PHASE's newest satisfied sess-prompt names (`record_turn_ended`, ruling 15), or the session the newest satisfied author sess-create delivered (`record_author_empty`) -- Task 10 |

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
| `cmd.payload['pr']` | `request_merge` | asserted | **Residual, blocked on PR creation.** The requester names the pull request. The kernel now binds the merge EFFECT to this recorded target, so the effect cannot diverge from the authorization — but the link from *this run's artifact* to *that PR number* is never observed. Closing it requires the kernel to create the PR itself and record the number, which does not exist yet (round 6, C8). |
| `cmd.payload['repo']` | `request_merge` | asserted | **Residual, blocked on PR creation.** As above: the repository is named by the requester. `runs.base_repo` is recorded at `create_run` and is a candidate to compare against, but nothing does so today. |
| `payload['policy_version']` | `_binding_from` | asserted | **Residual, M1-4.** Type-checked (`type(...) is int`, so no float or bool coerces in) but not compared against any policy the kernel holds. |
| `cmd.payload['reason']` | `park` | asserted | **Residual, §4.** The coordinator's reading of a park and of a listing: `reason` is bounded to `PARK_REASONS`, `cursor_item_id` to the §4 cursor invariant test (Task 19), neither to a kernel object. |
| `cmd.payload['cursor_item_id']` | `park` | asserted | **Residual, §4.** The coordinator's reading of a park and of a listing: `reason` is bounded to `PARK_REASONS`, `cursor_item_id` to the §4 cursor invariant test (Task 19), neither to a kernel object. |
| `cmd.payload['session_id']` | `park` | asserted | **Residual, §4.** The coordinator's reading of a listing (`session_id`, the session it parked; `reviewer`, the seat's vendor as the coordinator saw it); string-or-null shape checked, bound by the §8 cursor invariant and the §8 proof, not by a kernel object. |
| `cmd.payload['reviewer']` | `park` | asserted | **Residual, §4.** The coordinator's reading of a listing (`session_id`, the session it parked; `reviewer`, the seat's vendor as the coordinator saw it); string-or-null shape checked, bound by the §8 cursor invariant and the §8 proof, not by a kernel object. |
| `cmd.payload['question_ids']` | `record_human_answer` | asserted | **Intentional and permanent.** The human's words. The kernel binds who (the `execute_as_human` path, no dispatched generation can reach it) and when (the epoch, the cursor); the content is the human's to say. |
| `cmd.payload['answer']` | `record_human_answer` | asserted | **Intentional and permanent.** The human's words. The kernel binds who (the `execute_as_human` path, no dispatched generation can reach it) and when (the epoch, the cursor); the content is the human's to say. |
| `cmd.payload['text']` | `record_human_direction` | asserted | **Intentional and permanent.** The human's words. The kernel binds who (the `execute_as_human` path, no dispatched generation can reach it) and when (the epoch, the cursor); the content is the human's to say. |
| `cmd.payload['findings']` | `record_review` (human ruling) | asserted | **Intentional and permanent.** The human's words. The kernel binds who (the `execute_as_human` path, no dispatched generation can reach it) and when (the epoch, the cursor); the content is the human's to say. |
| `cmd.payload['question_id']` | `record_model_question`, `record_model_ruling` | asserted | **Intentional and permanent.** The model's words; the kernel binds the epoch and phase they were asked in and refuses a ruling on a question no `model_question` of the epoch asked. |
| `cmd.payload['question']` | `record_model_question` | asserted | **Intentional and permanent.** The model's words; the kernel binds the epoch and phase they were asked in and refuses a ruling on a question no `model_question` of the epoch asked. |
| `cmd.payload['ruling']` | `record_model_ruling` | asserted | **Intentional and permanent.** The model's words; the kernel binds the epoch and phase they were asked in and refuses a ruling on a question no `model_question` of the epoch asked. |
| `cmd.payload['reasoning']` | `record_model_ruling` | asserted | **Intentional and permanent.** The model's words; the kernel binds the epoch and phase they were asked in and refuses a ruling on a question no `model_question` of the epoch asked. |
| `cmd.payload['cost_if_wrong']` | `record_model_ruling` | asserted | **Intentional and permanent.** The model's words; the kernel binds the epoch and phase they were asked in and refuses a ruling on a question no `model_question` of the epoch asked. |
| `cmd.payload['item_id']` | `record_prompt_item` | asserted | **Residual, §4.** A per-session identity the kernel cannot see: omnigent's item ids are checked for uniqueness within the named session, but the kernel has no way to confirm the id names the item the coordinator means. |
