# Scar / effect matrix

*A required Milestone 1 artifact. Without it, "the full retained path" can mean
a happy path.*

One row per v1 behaviour: its source, the mutation that breaks it, who owns it
in v2, the fixture, the injected fault, the durable events expected, and the
effects it may or may not perform.

**Every "mutation that breaks it" in the retained table below was executed
against a committed tree and its result observed** — not reasoned about. The
port is complete only when a mutation of the v1 scar still fails an equivalent
v2 test; porting named classifiers while losing scars encoded in orchestration
order, timeout handling and fail-closed error paths satisfies the letter and
loses the point.

Each source cell is `file:line`, the enclosing scope, and an **anchor**: a
literal that must appear on that line. A line number alone proves nothing --
any number under 6900 falls inside some function -- and two of these citations
were wrong while a range check passed them.

Verified against `batch/run-queue.sh` on 2026-08-25. `v1 binds` records whether
v1's own `--self-test` catches the mutation: where it does not, the v2 test is
**new coverage rather than a port**, and those are different claims.

## Retained

| v1 behaviour | source | mutation that breaks it | v2 owner | test fixture | fault injected | expected durable events | effects |
|---|---|---|---|---|---|---|---|
| CI failure classification | `run-queue.sh:263` `_classify_ci_failure` `echo genuine` | `-gt 0` → `-ge 0`; or always `infra` — **v1 binds, both directions** | not yet ported — M1-5 | v1 `--self-test` | 0 and 3 failed steps | `external_observation` | none |
| Merge gate fail-closed | `run-queue.sh:3290` `_merge_gate` `printf 'skip'` | drop the empty-head `skip` branch — **v1 binds** | kernel `request_merge` head binding | v1 `--self-test` | no head supplied | `command_rejected` | none |
| Merge-authorizing status publication | `run-queue.sh:1167` `_post_cross_review_status` `_effect status_check` | write the `gh api` call directly instead of through `_effect` — **v2 binds** | kernel effect journal, class `status_check` | `test_routing.py` | an unrouted call planted in the source | `effect_intended`, `effect_confirmed` | `status_check` |
| Merge orchestration | `run-queue.sh:1631` `merge_ready_pr` `_effect merge` | route as `pull_request` instead of `merge` — **v2 binds** | `perform` + `revalidate_merge` | `test_merge_revalidation.py` | reviewed artifact deleted after authorization | `command_rejected`, no `effect_intended` | `merge` |
| Recovery push is bounded | `run-queue.sh:1837` `merge_ready_pr` `revert-push:$pr` | cap → `-`; or `_net_run` drops `-k` — **v1 binds both** | effect adapter cap → `_net_run` | v1 `--self-test` #62 | the bound removed | `effect_uncertain` on timeout | `ref_update` |
| Reopening reverted issues is bounded | `run-queue.sh:291` `_reopen_reverted_issues` `_effect issue_or_label` | cap → `-` — **v1 binds** | effect adapter cap → `_net_run` | v1 `--self-test` #62 | the bound removed | `effect_intended` | `issue_or_label` |
| The adapter is actually wired in | `run-queue.sh:20` `«top-level»` `effect-adapter.sh` | remove the `source` line — **v1 binds, via #50** | `batch/lib/effect-adapter.sh` | v1 `--self-test` | adapter not sourced | none — the run cannot start | none |
| Effect denial fails closed | `effect-adapter.sh:84` `_effect` `BIRCHER_EFFECT_MODE:-deny` | default mode `deny` → `legacy` — **v2 binds** | `batch/lib/effect-adapter.sh` | `test_fault_injection.py` | `BIRCHER_EFFECT_MODE` unset | none — refused before execution | none |

## Excluded, with dispositions

| v1 behaviour | source | disposition |
|---|---|---|
| PR recovery checkout chaining | `run-queue.sh:2137` `recover_pr_cmd` `recover_pr_cmd()` | **Unprobed.** The plan proposed joining commands with `;` instead of `&&` as the breaking mutation. Not executed this session, so no claim is made about whether v1 binds it. Carried to M1-5. |

## Retired — the code they described no longer exists

These rows carry no `file:line` citation because there is nothing left to cite.
They are kept, not deleted, because a scar record that forgets its own history
stops being able to answer "was this ever considered?" — and because a retired
scar must say what took over its job.

**Every row here MUST name a replacement guard.** A retirement with no
replacement is a coverage loss, and saying so plainly is the point.

| v1 behaviour | why it is retired | replacement guard |
|---|---|---|
| Marker author validation | **Did not exist in v1, and the acceptance test that assumed it cannot be met as worded.** The spec names "validating the marker against a runner-issued attempt identity" as a Milestone 1 acceptance test. `parse_marker` performs no author check of any kind, so there is no scar to port — and §4b established there is no runner-issued attempt identity to validate against: a session receives no token and cannot reach the server. Superseded by M1-3b: identity is assigned at dispatch, and the marker is not an authorization input at all. Recorded rather than quietly dropped, because an acceptance test presuming a mechanism that does not exist would otherwise be satisfied by inspection. Phase 2 removed the marker entirely, so the question is now closed rather than merely unanswerable. | `test_marker_is_gone.py` |
| Marker extraction off a line start | The scar was "a marker posted mid-line is missed, so the item polls to timeout" (EXP02, 2026-07-08). It is not ported and never will be: nothing parses comment text any more. `run_item` derives its outcome from the repository via `observe_outcome`, so there is no extraction to get wrong. The guard that replaces it is `test_marker_is_gone.py`, which fails if any shipped file writes or reads the marker again — a stronger property than the original scar, since it forbids the channel rather than fixing one way of misreading it. | `test_marker_is_gone.py` |
