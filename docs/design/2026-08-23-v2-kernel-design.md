# Bircher v2: a transactional kernel with an interpreting agent

**Status:** design, for adversarial review.
**Branch:** `v2`, which becomes `main` when tested.
**Supersedes nothing.** v1's working path is absorbed, not replaced.

---

## What v2 is

v1 takes a **groomed** issue and implements it under cross-vendor review, merging on a genuine review PASS with green CI. It works: it lands PRs unattended.

v2 adds two things and fixes one.

- **Adds a front end**: take a vague goal, grill it into a decision tree, and produce a spec and a plan each under adversarial cross-vendor review, so the thing v1 implements is one that survived scrutiny before any code existed.
- **Adds a kernel**: a durable, transactional core that owns facts, authority, time, identity and side effects — so the process can run unattended without a human reading logs to tell what happened.
- **Fixes the coordinator**: `batch/run-queue.sh` is 6,851 lines of bash across 96 functions. Its problem is not that it is bash; it is that it mixes two layers that want different technologies. Mechanism wants determinism and tests. Judgement wants a model.

### The governing constraint, unchanged

**Compose, don't fork.** Every mechanism that exists upstream is used as-is so we inherit its updates. Where our needs exceed it, prefer configuration or a thin wrapper over a copy. A forked skill is a skill that stops improving.

v2 owns *less* than v1, not more: the kernel is small and the process lives in skills.

---

## The boundary

> **Code owns facts, authority, time, money, identity and side effects.
> The model supplies bounded interpretations and recommendations over frozen facts.**

The sharpest corollary, and the one that decides arguments:

> The model may decide that a finding is **blocking**. It may not decide what *blocking authorizes*.

Code maps a validated blocking finding to a legal transition. That separation is what makes the audit record trustworthy, and it must exist from the first commit — a model that initially holds ambient `gh` merge authority cannot later be proven not to have used it.

### Mechanism owns

Run, attempt, artifact, review, command, effect and external-object identity. Artifact hashes and lineage. Frozen review snapshots and their invalidation. Legal transitions. Dispatch, retry, deadlines, cancellation. Idempotency and reconciliation of ambiguous external calls. Capability preflight. Git, GitHub and CI adapters. The distinction between `code_failure`, `infrastructure_failure`, `cancelled`, `timed_out` and `unknown`. Fail-closed merge authorization. The append-only audit.

**Including vendor allocation.** A model may return a suitability assessment; code combines it with quota windows, eligibility, cross-vendor independence constraints and benchmark assignment to make the selection. Allocation is policy and scheduling, and must be reproducible.

### Judgement owns

Turning a vague goal into candidate requirements. Critiquing and revising a spec or plan. Generating, deduplicating and relating findings. Proposing severity and explaining impact. Deciding whether a technically valid finding blocks *this* artifact. Ruling on author/reviewer and reviewer/reviewer disagreement. Classifying a discovery as a defect in the implementation, a defect in the plan, an invalidating scope change, follow-up work, or irrelevant. Assessing convergence. Drafting human-facing summaries.

The judgement layer never publishes a status, mutates authoritative state, starts work, consumes a round, or merges.

---

## Why this shape

Three observations from a full end-to-end trial, in which one vague issue became a merged PR.

**The parts of v1 that behaved well were mechanism, and one that looked like mechanism was not.** The merge gate refused to proceed on an absent or ambiguous verdict — three times, each correctly — and the CI wait behaved as specified. Each is valuable *because* no judgement is involved: a layer asked "is this good enough to merge?" would have been talked into it at least once.

The recovery review's SHA pinning looked like a third example and is not one. It was **intended** to pin the checkout; a source audit found the guarantee absent (see §3 below). What was observed three times was a model choosing to obey an instruction. That distinction is the entire subject of this document, and it was missed by the author of this spec until review — which is the strongest available argument for the boundary it proposes.

**The parts that needed judgement were done ad hoc.** Sixteen rulings across seven tasks, one of which was wrong. Deciding whether a finding was blocking, whether the loop had converged, whether a defect was in scope for this plan or a later one. None of it is expressible in bash, and all of it is what v2 must encode.

**What breaks first without a kernel is temporal correctness, not reasoning quality.** A subagent hit a provider session limit mid-task. Nothing was lost only because state was on disk and was verified by inspection rather than trusted from a completion notification. The failure mode is: an operation's result is ambiguous, the coordinator infers whether it happened, retries, and the original completes late — producing two writers, an unreviewed head, a duplicate PR, or a review bound to the wrong artifact.

v1's own comments are an archive of this class: a merge call can succeed server-side while its client times out; a session stop may be unconfirmed; a returned job id is not proof a job ran.

> **The 6,851-line script is a scar record. v2's job is to turn its comments into executable transition and fault-injection tests.**

---

## The kernel

Five components. A relational database with explicit Python transition functions is sufficient. **Do not build a workflow language.**

### 1. Durable run aggregate

One record per run: stable run and attempt IDs, current state, base repository and base SHA, actor/provider/model identity, references to inputs, outputs and external objects, timestamps, terminal outcome.

### 2. Append-only facts, projected state

Every consequential observation and decision is an immutable event: command requested, command accepted or rejected, GitHub/CI observation, artifact created, review verdict, transition performed, side effect attempted, side effect reconciled, human ruling.

Current state is a rebuildable projection. Full event sourcing is not required everywhere; **distinguishing immutable facts from mutable derived state is.**

Each event carries: event type, schema version, mechanism version, causal command ID, timestamp, actor, payload. Stored events never acquire new meanings when code changes.

### 3. Frozen artifacts and invalidation

Specs, plans, reviews and implementation outputs are content-addressed. A review verdict binds to a tuple: artifact hash, base SHA, context-bundle hash, reviewer identity, policy version. **Changing any bound input invalidates the verdict.**

This is the minimum mechanism preventing yesterday's approval from authorizing today's object.

**v1 intends this property and does not have it.** `--recover-pr` captures a SHA and pins the eventual merge to it via `--match-head-commit`, which correctly prevents merging something *newer* than the reviewed head. But the review checkout itself is unverified: the generated commands are joined by semicolons rather than `&&`, so a failed fetch or `worktree add` continues anyway; `/tmp/review-$pr` is a deterministic path that is neither cleaned nor pruned, so the `cd` can land in a stale worktree at an older commit; and nothing compares `git rev-parse HEAD` to the captured SHA. "If that checkout fails, STOP" is an instruction to a model, not a mechanism.

The merge marker has the same shape. `parse_marker` extracts `bircher-status:` from anywhere in a comment and the in-run merge fires on `outcome=ready`, with no schema validation, no provenance check, no reviewer-independence check, and no cross-check of the claimed head against anything the runner observed.

Both are filed as v1 defects. They are recorded here because **this is the property v2 exists to make structural**: an approval authorizes a tuple of immutable inputs, and the evidence for it must be *observed by the mechanism*, not reported by the actor whose work it authorizes. Verifying the review worktree's actual HEAD, and validating the marker against a runner-issued attempt identity, are Milestone 1 acceptance tests.

Hashing is over raw bytes or a precisely versioned canonical form. Never an informal serialization whose behaviour can drift.

### 4. Typed commands and centralized authorization

A narrow interface, not a general one:

```
submit_spec · submit_plan · record_review · start_implementation
record_ci_observation · request_merge · cancel_run
```

Every command carries an **expected aggregate version** and an **idempotency key**. A command derived from version 12 cannot mutate version 15. Only the kernel authorizes a merge; initially it may call v1's existing machinery behind an adapter.

A decision arrives as data, not as an action:

```json
{
  "decision_id": "dec_01...",
  "run_id": "run_01...",
  "decision_type": "review_ruling",
  "based_on": {
    "state_version": 47,
    "spec_sha256": "...",
    "plan_sha256": "...",
    "base_git_sha": "...",
    "head_git_sha": "...",
    "review_bundle_sha256": "..."
  },
  "finding_rulings": [
    {"finding_id": "finding_12", "disposition": "blocking", "rationale": "...", "confidence_pct": 86}
  ],
  "recommendation": "request_revision"
}
```

Note `confidence_pct`: an integer, not a float. The canonical form refuses floats outright, because their textual encoding varies across versions and platforms and a hash that changes silently is worse than one that fails. An earlier revision of this example carried `"confidence": 0.86`, which the kernel's own rule could not hash -- the spec contradicted itself, and the kernel was right. Every numeric in a decision packet is an integer in its smallest meaningful unit.

The kernel validates the schema, confirms every referenced hash still matches, confirms the decision type is legal in the current state, checks reviewer independence and authority, checks budgets and round limits, records the decision immutably, then computes and executes the resulting transition.

`accept` from the judgement layer means "no unresolved blockers for the pinned review bundle". It does not mean merge.

### 4b. Actor identity, and why the first implementation had none

**Every authorization input must have a stated provenance: the kernel observed it, or an actor asserted it.** An earlier implementation of this design passed five review rounds while five of the six links in its merge-authorization chain were caller assertions, and it took a full-effort adversarial pass to name why.

The omission was structural rather than careless. The command envelope carried a run, a version, an idempotency key and a generation — and **no authenticated actor**. `implementer_identity` and `reviewer_identity` were fields in the command payload. The kernel recorded each accepted fact as `actor="kernel"` while faithfully copying whatever the caller claimed. So reviewer independence compared two strings the same caller chose; CI status and head were whatever the caller said; and the artifact was checked for existence but never for lineage. Each individual check was correctly implemented. The chain proved nothing.

Demonstrated, not theorised: the caller recorded as implementer submitted its own `accept` naming a different reviewer, asserted green CI, reached `merge_requested`, and then had the reviewed artifact deleted — and authorization still succeeded.

So the kernel requires:

- **An assigned actor on every command, bound at dispatch and never accepted as input.** This was investigated before being specified, and the investigation changed the requirement. A session receives no identity token: omnigent's agent-env allowlist admits only `HTTP_*`, `HTTPS_*`, proxy and `SSL_*` vars, `XDG_*`, `LANG`, `LC_`, `HOME`, `PATH`, `TERM`, `TMPDIR`, `NODE_EXTRA_CA_CERTS` and a bare `OMNIGENT=1` marker, and the runner's own auth secrets are stripped at every spawn boundary precisely so a child cannot impersonate it. Under M1-1's egress rules the session cannot reach the server either.

  So there is nothing for a session to authenticate *with*, and it needs nothing: **the kernel dispatches the attempt, so it already knows whose work it is.** Identity is read from the kernel's own dispatch record and written into the command by the kernel, never carried in from outside. "Authenticated" here means *assigned by the mechanism*, not *verified on arrival*.

  **Round 6 correction.** An earlier revision of this section said an assigned identity "cannot be forged by anyone". That is false. `dispatch()` takes its actor as an argument, so whoever can call it names both sides of the independence check — demonstrated by executing it: one actor dispatched itself as `mallory` and `mallory-the-reviewer`, passed independence, and reached `merge_requested`. Assignment displaces the payload-field defect one level; it does not remove it.

  The true property is narrower and worth stating exactly: **an assigned identity is only as good as the restriction on who may assign it.** Here that restriction is the filesystem, not the network — a model session cannot write the kernel database, because Landlock's `write_paths` confines it to its worktree and `BIRCHER_KERNEL_DB` is required with no default. Those two settings are the substrate, and Milestone 1 tests them directly rather than leaving them as background assumptions.

  The corollary is a hard rule: **`Command` must have no actor field a caller can populate.** `implementer_identity` and `reviewer_identity` were payload fields, which is how a caller came to name both sides of its own independence check.

  **Dispatch is the acquisition, not a step beside it.** Implementation established this and it is load-bearing: ownership acquisition takes its owner from the caller, so identity read from it would be exactly as forgeable as a payload field — and a worker that acquired its own generation would orphan the dispatch bound to the previous one, leaving the fence and the identity describing different attempts. The kernel fences and records who in one operation. A generation obtained any other way has no dispatched actor, and every command under it is refused rather than attributed to anyone.
- **A role assigned with the fence.** Independence is not only *who*: an implementer that could elect itself reviewer would satisfy a name comparison the moment a second actor existed. The dispatch carries the role, so the question "may this attempt review?" is answered by the same record that answers "who is this?".
- **Identity-bearing facts.** An accepted command records the authenticated actor, not `"kernel"`. `actor="kernel"` is correct only for facts the kernel originates itself.
- **Reviewer independence computed from authenticated identities**, comparing the actor who submitted the implementation against the actor who submitted the review. Both sides must be observations.
- **A current implementation artifact with lineage**, tracked by the kernel, so an approval names *this run's present output* rather than any blob the store happens to hold. Existence is not identity.

  Implementation found the cause one level down: **nothing recorded what an implementation produced.** There was no "this run's current output" to compare an approval against, so the reviewer named the artifact it was reviewing and any blob the store held satisfied the check — including one from another run. The command set gains `record_implementation_output` for this. The set is closed and growth in it is a design change to be argued; the argument is that without it every lineage check downstream is a caller's choice compared against itself.

  A second consequence: **the verdict binding is a statement about inputs, and `reviewer_identity` is not one of them.** It was the only caller-supplied member of that tuple, and keeping it there forced a merge requester to *name* the reviewer it was relying on — a thing no requester should get to choose. Who approved is a separate fact the kernel observes from its dispatch record and records beside the verdict.
- **CI as an observation, not a report.** A status and head supplied in a command payload is a claim; the kernel must either fetch it from the provider itself or bind the claim to an attestation it can verify.
- **The authorized binding carried into the effect**, so `perform()` re-evaluates the semantic evidence immediately before acting rather than checking only that the run reached a state. Reaching `merge_requested` records that authorization *happened*; it is not the authorization.

**The provenance table is a required Milestone 1 artifact.** One row per authorization input, naming its source and whether the kernel observed it. An input whose row reads "asserted" is either a defect or a declared residual with a reason; there is no third case. This document's own earlier revisions could not have produced that table, which is why the gap survived three rounds of repair.

The table must be **checked, not read**, and its input list must be *derived from the authorization source*, not written by hand. A hand-written list checked against a hand-written table is prose checking prose: an unclassified input satisfies both. The extractor that derives it needs its own known-positive test over more than one input shape — a detector that finds nothing reports total coverage.

A caller-presented value the kernel refuses unless it equals a value the kernel holds counts as **observed**: the caller can only ever supply the right answer, and the value that survives is the kernel's. **Eight rows are asserted after Milestone 1**, and the count is stated here as a number so it can be checked: verdict, CI status, CI head, requested head, context bundle hash, PR, repo, and policy version. Seven are residuals to close; the eighth, the verdict, is asserted permanently and by intent — a verdict *is* the reviewer's judgment, and the kernel binds who gave it and what it was about, not whether it is right. `test_the_spec_and_the_table_agree_on_the_count` fails if this sentence and the table disagree, because a number in prose that nothing checks is the defect this document is about.

### 5. Effect journal, defined by semantic effect class

Persist intent before invoking the effect; carry an idempotency key; record the external object identifier; reconcile an uncertain result before retrying; revalidate authorization immediately before merge.

An earlier revision scoped this to "push, status publication and merge" — three operations — while the rest of this document named PR creation, comments, labels, issue closing, recovery writes and reverts as dangerous or fenced. That is incoherent: **PR creation is one of the two implementer effects that motivated the credential boundary above, and it was absent from the journal.** The journal is therefore defined by effect class, not by an operation list:

- ref creation and update; push
- PR creation, head promotion, close and reopen, merge
- statuses and checks
- comments and review publications
- issue and label mutations
- reverts and recovery writes
- credential issuance and revocation
- session dispatch, stop, and reconciliation wherever ambiguity affects ownership

Each class carries its own identity, generation, idempotency scope, reconciliation rule and authorization recheck. Not every call is journalled — reads are not — but every externally visible mutation is.

---

## Decisions that must be right in the first commit

Everything else is reversible. These are not.

| Decision | Why retrofitting fails |
|---|---|
| **Fact / decision / effect are distinct** | Collapsed into mutable status rows, audit and replay become guesswork |
| **Stable identity and idempotency-key scope** | Integrations depend on identity semantics; changing them later is dangerous |
| **An approval authorizes a tuple of immutable inputs** | Not a filename, branch name, issue number or "latest" |
| **Actor identity is authenticated by the mechanism, never carried in a payload** | Retrofitting it means re-deriving every authorization decision ever recorded, and the facts that recorded them cannot be rewritten |
| **The authority boundary** | If models hold ambient merge authority first, you cannot establish the historical audit was complete |
| **Expected-version compare-and-swap on mutating commands** | Otherwise stale decisions silently apply to newer state |
| **Persist-before-execute for irreversible effects** | Retrofitting after calls are scattered through coordinators is expensive |
| **Event schema carries its own version** | Stored events must not silently change meaning |
| **UTC instants; integer minor units for money and tokens** | Ambiguous historical numbers cannot be repaired |

Reversible, and not worth arguing about now: database engine, Python framework, queue implementation, adapter class hierarchy, most status names.

---

## Deliberately deferred

Until a concrete case demands them: a general workflow graph or DSL; parallel scheduling; distributed leases on every operation; fencing beyond optimistic aggregate versions and single-run ownership; full cancellation propagation; a budget reservation and charging ledger; a deadline hierarchy; general capability negotiation; uniform adapters for every provider and CI system; automated compensation or rollback; identity federation; exhaustive failure-taxonomy application; cross-run resource allocation.

**v1's sequential runner is kept as a simplifier — for scheduling only.** It prevents two *newly scheduled* items overlapping. It does not prevent an already-issued provider session completing late after a timeout, cancellation, crash or restart, which is the exact failure this kernel exists to survive. v1's own singleton is an advisory local `flock` that explicitly proceeds unprotected when `flock` is unavailable, and process death releases it while remote work may still be alive.

So while distributed leases and recallable cancellation stay deferred and cut respectively, **late-result rejection and authority fencing cannot be**. Milestone 1 requires:

- **atomic ownership acquisition with a monotonically increasing fence generation.** "Ownership recorded" is not exclusion; acquisition must be a compare-and-swap, and dispatch must be tied to the generation it acquired.
- **every accepted result *and every effect request* bound to that generation.** Binding results alone fences returned data, not the external effects an attempt already performed — which is the actual failure.
- **fenced resources are exactly the journal's effect classes.** An earlier revision listed five — run state, branch and ref, PR head, comments and statuses — while the journal named eight classes, so PR create/close/reopen/merge, issue and label mutation, recovery writes, credential lifecycle and session control vanished at the very point meant to define fencing scope. **The rule is uniform: every journalled mutation is a generation-fenced resource**, and any exception is classified explicitly with its reason rather than omitted. "Every kernel-controlled write boundary" also says nothing about writes that bypass the kernel, which is exactly the implementer push path above.
- **a stated linearization point.** Binding an effect request to a generation only works if every request crosses a kernel enforcement point — and a credential already issued to an attempt can be used without submitting one. Revocation is not atomic with GitHub's authorization, and an in-flight request can survive it. So: the kernel imports an immutable artifact, atomically validates the ownership generation, persists effect intent, and submits the operation **from its exclusive credential domain**. A result arriving from an older generation is **observation-only and carries no reusable write capability** — which is a property of the domain separation above, not of the fence. The CAS design is real for kernel state; its claim over GitHub resources rests entirely on that separation.
- **credentials that make a kernel-owned effect impossible for any model process** — see the authority boundary above. Under the decided default there are no per-attempt GitHub credentials to provision or revoke, because model attempts receive none. That holds under the decided mechanism too: the egress credential proxy keeps a single credential in the unsandboxed parent and issues none per attempt, so there is nothing attempt-scoped to provision or revoke. Per-attempt provisioning would return only under an isolated staging repository or fork; if that is ever adopted, its minimum provisioning and revocation is carved out of the deferred identity-federation work rather than left adjacent to it.
- **an unconfirmed stop leaves the attempt non-terminal and halts that run pending human reconciliation** — as a durable `reconciliation_required` state, not a silent stall. Safety without an operating design is not enough for a system whose purpose is running overnight: a run that wedges at 03:00 and says nothing has failed the goal even though it correctly avoided conflicting work.

  That state records the run, attempt, generation, affected resources, last confirmed observations, stop attempts and recommended operator actions. The runner emits an alert and a morning summary, and visibly retains the queue item. **Unrelated queued runs continue; only the affected run halts.** The wedge is per-run because the conflict is per-run: an unconfirmed attempt holds that run's resources and nothing else. If a future finding shows a stopped attempt can affect another run's resources, this becomes a scheduler halt — but it is stated as a policy now rather than left to inference. The resolution itself is an **audited command with expected-version CAS**, never a manual state edit. "Forbids replacement work that could conflict" otherwise either stalls everything or protects nothing; Milestone 1 takes the safe reading and pays for it with the alert.

## Cut as goals, not merely postponed

- **A general workflow engine.** Build Bircher's transactional coordinator, not a reusable one. Explicit Python transition functions are easier to audit and change.
- **Exactly-once execution.** Unattainable across GitHub, git, CI and model providers. Promise at-least-once commands with idempotent effects and reconciliation.
- **Universal adapter purity.** Normalize only the semantics Bircher relies on; preserve provider-specific evidence rather than flattening every system to a common denominator.
- **Exhaustive capability preflight.** Preflight only hard requirements before a costly run. Capabilities can vanish after preflight, so runtime handling is needed anyway; an elaborate framework creates false confidence.
- **Budget reservation.** Start with spend limits and observed charging. Add reservation when concurrent runs compete for a hard budget, or when overruns are demonstrated.
- **Fine-grained cancellation guarantees.** Support "stop scheduling new work" and terminal cancellation. Do not promise that issued external operations can be recalled.
- **A taxonomy forced onto every error.** Keep the five terminal categories for reporting; classify at operational boundaries and retain raw provider errors and causal chains.

---

## Milestone 1

One supervised goal, end to end, through the kernel:

```
goal → grilled decisions → frozen spec → frozen plan
     → implementation → cross-vendor review → CI → merge authorization
```

Every arrow is a durable transition.

**v1 runs in a constrained execution mode, not behind a wrapper.** An earlier draft said execution, review and GitHub state could be "delegated to v1's machinery behind adapters". That is not achievable while the authority boundary holds: `run-queue.sh` launches a coordinator with a prompt, reads a model-authored `bircher-status:` comment, and calls `merge_ready_pr` on `outcome=ready`; it also changes labels, publishes the merge-authorizing commit status, closes issues and may revert. Wrapping that process in an adapter does not move those authorities into the kernel — it puts an unmediated authority holder behind one more process boundary.

So Milestone 1 requires:

- **A constrained execution mode, which does not exist yet and is itself a Milestone 1 deliverable.** v1 has exactly one relevant switch, `BIRCHER_INRUN_MERGE=0`, which skips the in-run `merge_ready_pr` call and incidentally its status publication. It does **not** disable the `bircher:running` label write, outcome comments and label edits, issue closing, recovery comments, or implementer push and PR creation. There is no seam to name.

  Building it means routing every effect through an injected effect adapter, or not running the coordinator at all and retaining only extracted logic.

  **A trap-based test cannot prove the boundary, and must not be presented as doing so.** A PATH shim in the coordinator says nothing about a model session, which is a separate process that can use an absolute path, another HTTP client, an SDK, or a language runtime. "Every mutation-capable command" is not enumerable — `python`, `node` and shell built-ins all originate network effects. And trapping `curl` breaks the retained path outright, since session creation, prompting, polling and stopping are themselves mutating POSTs.

  So the acceptance criteria split into four:

  1. **Structural routing tests** — every coordinator effect site invokes a typed effect adapter.
  2. **Fault-injection tests** — every effect branch is driven and the adapter denies it.
  3. **An end-to-end capability test** — model sessions run in their real execution domain, under the production `egress_rules`, and attempt mutations via `gh`, `git`, direct HTTPS, alternate clients, absolute paths and credential discovery. It also attempts the two routes that bypass a host-and-method reading of the boundary: **HTTP redirects to an allowed host, and every other host the rules permit**, since a rule set proven against GitHub proves nothing about a third-party host with a state-changing `GET` endpoint. All must fail at the credential, network or repository boundary. **This is the authority-boundary proof; the other three are coverage evidence.**
  4. **Provider-control effects classified explicitly** — session dispatch, prompting, polling and stopping are *permitted* kernel effects, tested separately from forbidden GitHub and repository mutations.
- **The scar-bearing behaviour ported, not called — and the unit of preservation is behaviour, not functions.** There is no clean module boundary to port along. Genuinely pure leaves exist (`_classify_ci_failure`); other read-only units depend on the global `REPO`, on deadline state, on shell dynamic scope, on `gh` pagination and error conventions. The merge and CI scars are distributed across effectful orchestration: `merge_ready_pr` alone combines required-context discovery, status publication, mergeability polling, the merge, reconciliation, main-CI observation and possible revert.

  So the thing preserved is **behaviour captured by fixtures, transition tests and fault-injection tests**. A pure leaf may be called temporarily, but Milestone 1 inventories its globals, subprocesses and dynamic-scope dependencies first. **The port is complete only when a mutation of the corresponding v1 scar still fails an equivalent v2 test** — porting named classifiers while losing scars encoded in orchestration order, timeout handling and fail-closed error paths would satisfy the letter and lose the point.

  That claim needs a manifest or it cannot be checked, and "the full retained path" can otherwise mean a happy path. **Milestone 1 produces a scar/effect matrix as a required artifact**: one row per v1 behaviour, giving its source location, the mutation that breaks it, the v2 owner, the test fixture, the injected fault, the expected durable events, and the effects it is permitted or forbidden to perform. Completion means every retained row passes and every excluded row carries an explicit disposition.

This decides open question 2, below.

The front end is **supervised**: it grills, produces spec and plan under adversarial review, and exports a frozen bundle — a human inspects it and explicitly enqueues it.

That supervised handoff is the authority boundary — **but only if something enforces it.** "Cannot merge, push, label or launch implementation" is an architectural intention until it is a property of credentials and process isolation. In v1 the runner and its prompt-driven workers share GitHub and session authority, so the same claim there would be false.

An earlier revision proposed fencing **judgement** processes. That fences the wrong actor. The dangerous writer is the **implementer**: v1's agent bundles inherit the caller's environment (`os_env: type: caller_process`), run unsandboxed, and are explicitly instructed and permitted to `git push` and `gh pr create` — the configs say so in terms: *"Implementers open their own PRs, so push / gh pr create are allowed"*. An expired implementation attempt can therefore push a new head or open a duplicate PR **after** its result has been rejected, and rejecting returned data cannot undo an external mutation that already happened.

So the enforcement mechanism for Milestone 1 is: **no model process holds credentials for a kernel-owned effect — implementers included.**

An earlier revision offered two implementations and called both viable while marking the boundary UNPROVEN. That was a decision disguised as an admission: neither is viable as stated against the current topology, and the spec is capable of deciding now.

- **Scoped credentials do not exist for this.** A GitHub PAT or App installation token is not ref-scoped; repository `contents:write` generally permits updating any writable ref. "An attempt-specific ref that cannot update an authoritative PR head" is not a property ordinary GitHub credentials can express — and an attempt branch becomes the authoritative head the moment a PR targets it. Revocation also does not fence an already authenticated or in-flight git operation.
- **Moving the push into "the kernel" is not sufficient on its own.** If the kernel runs in the same credential domain as the model sessions, nothing has been fenced: credentials remain reachable via `gh`'s credential store, git credential helpers, mounted files, sockets, and shared process and filesystem access. Scrubbing the environment does not address any of those.

**The Milestone 1 security boundary, decided:**

1. Model sessions run in a **credential-free** container or OS security domain.
2. The kernel's GitHub adapter runs **outside** that domain, with credentials unreachable from it by environment, filesystem, credential helper, socket, process inspection or session tooling.
3. The model hands off a **git object** through a one-way or kernel-copied channel.
4. The kernel imports it, records and **verifies the immutable commit or tree hash**, and only then performs the push and the PR creation.
5. Session termination and late workspace writes cannot alter the imported object — the handoff must be immutable once inspected, not a path, worktree, branch or bundle file in a shared workspace that a late process can rewrite.

The scoped-credential option is superseded rather than retained. It required "a real enforcement mechanism — an isolated staging repository or fork, or a ref-enforcing proxy." The third exists upstream and is stronger than the property this section was trying to buy, but it does not run on this deployment as shipped. What follows is the mechanism, the measured obstacle, and the route taken.

### The mechanism: a secretless credential proxy behind a network-isolating sandbox

omnigent's **secretless credential proxy** (`os_env.sandbox.credential_proxy`, `designs/SANDBOX_CREDENTIAL_PROXY.md`, upstream #236, 2026-06-15) is the enforcement mechanism. Its default mode is **swap-on-access**: the unsandboxed parent resolves the real secret and holds it in the proxy's in-memory rewrite table, the sandboxed tool issues its request carrying **no** `Authorization` header, and the mandatory L7 egress proxy attaches the credential on the way out.

This is stronger than the boundary this section originally specified. Requirement 2 asked for credentials *unreachable* from the model domain by environment, filesystem, credential helper, socket, process inspection or session tooling. Swap-on-access means there is no credential in the domain to reach; enumerating access paths is unnecessary when the secret was never present on any of them.

It also settles what the scoped-credential bullet could not. GitHub tokens are still not ref-scoped — but scoping the token is the wrong lever. Git-over-HTTPS separates the two operations by request path: fetch is `POST <repo>.git/git-upload-pack`, push is `POST <repo>.git/git-receive-pack`. `egress_rules` are default-deny and scope by method *and* path glob. Allow-listing upload-pack and simply not listing receive-pack yields a model domain that can clone and fetch and **cannot push at all**, enforced at a proxy the sandbox has no route around. The same lever restricts `gh` to `GET api.github.com/**`, so no PR, comment, label or issue mutation can leave the domain.

**The property everything rests on is that the proxy is the only egress path.** Not that traffic is *configured* to prefer it — that the sandbox has no other route. omnigent achieves this under `linux_bwrap` by combining `--unshare-net` (the helper gets no network stack at all) with a TCP-to-Unix relay reaching the parent's proxy through a **bind-mounted Unix socket** (`bwrap_sandbox.py:570`, `:615`). A client that ignores `HTTP_PROXY` has no network to fall back on. Any substitute backend must reproduce *that* property, not merely filter traffic.

### The obstacle, measured: bwrap cannot run here, and the price of making it is three relaxations

`bubblewrap` 0.11.0 is installed in the runner image (`deploy/docker/Dockerfile:213`) and **cannot start**: `bwrap --unshare-user … ` returns "No permissions to create new namespace". The host kernel is not the constraint — `/proc/sys/user/max_user_namespaces` is 126932 on kernel 6.12.30+ — the container is: `unshare --user` returns `Operation not permitted` under `Seccomp: 2` and AppArmor `docker-default (enforce)`.

This was investigated before, and the result is recorded in the operator repo (`homelab:docs/omnigent-sandbox-investigation.md`). Getting bwrap to run is a **four-layer wall**, of which one layer is already satisfied and three are container protections that must be disabled:

| Layer | Blocker | Requires |
|---|---|---|
| 1 | no `bwrap` binary | satisfied — bubblewrap is installed |
| 2 | Docker's default seccomp blocks `CLONE_NEWUSER` | `seccomp=unconfined` |
| 3 | `docker-default` AppArmor denies `mount()` | `apparmor=unconfined` |
| 4 | omnigent hard-codes `--proc /proc`; Docker masks `/proc` | `systempaths=unconfined`, or an omnigent patch binding `/proc` |

There is no targeted AppArmor profile that permits bwrap's `mount` without unconfining it, and this is the accepted recipe rather than a misconfiguration: OpenAI's own `codex#17547` lands on the same relaxations and states plainly that it "intentionally relaxes Docker's outer sandbox just enough for Codex to construct its own bubblewrap sandbox inside the container."

**This route is rejected.** Three of Docker's default protections off is privileged-lite, and it undercuts the rationale that makes the outer container a boundary worth having — while the whole purpose of this section is to *add* a boundary. Reversing a deliberate hardening decision in order to build an inner sandbox is a poor trade when a container-native mechanism exists.

An earlier revision of this section costed the remedy at "one concession, possibly two". That was wrong, and wrong in a way worth recording: it was written from a probe that stopped at the first gate that closed. **A negative result names the first gate, never the only one** — the same note appeared in this document's own method warnings and did not prevent the error. (A related correction: those probes were run through `homelab:omnigent.sh`, whose `RUNNER_NAME` defaults to `omnigent-runner`, so they measured the general runner rather than `omnigent-runner-bircher`. Re-run against the correct container, the results are identical, but they were reported as measured before they were.)

### The route taken: Landlock, which needs no relaxations

**Landlock is a kernel LSM that confines without mounts, namespaces or privileges**, so it works in a fully hardened container. It is confirmed working on this host under Docker's *default* seccomp: `landlock_create_ruleset` reports **ABI 6**. omnigent already carries a `linux_landlock` backend (`omnigent/inner/landlock_sandbox.py`), built for this deployment precisely because bwrap could not run here.

The backend enforces filesystem confinement today and **does not implement the network half — by choice, not by limitation**. `landlock_sandbox.py:105` records it: *"Landlock ABI 4+ can restrict TCP bind/connect, but that is out of scope here"*, and `:156`: *"NET_PORT was added in ABI 4 and is not used here."*

That gap is what stands between this deployment and the boundary, and closing it reproduces bwrap's invariant from the other direction. bwrap removes the network stack and leaves a Unix socket. Landlock can **deny all TCP connect outright** while leaving the bind-mounted Unix socket untouched, because Unix sockets are not TCP and `NET_PORT` rules do not reach them. The result is the same property — exactly one channel out, unreachable around — with the container's default hardening intact.

**Milestone 1 therefore depends on two bounded upstream changes**, both in omnigent and neither speculative:

1. **Implement `NET_PORT` rules in the landlock backend.** Deny-all-TCP is the simple case and the only one this design needs.
2. **Allow `egress_rules` and `credential_proxy` on `linux_landlock`.** They are refused in three places today — `omnigent/inner/loader.py:776`, `omnigent/spec/validator.py:539`, `omnigent/spec/parser.py:966` — each asserting `egress_rules requires sandbox.type=linux_bwrap`. That assertion is correct about *today's* backends and must be widened to "a backend that hard-enforces sole-egress", with landlock qualifying only once change 1 lands.

Until both ship, **the enforcement mechanism does not run and no model attempt may run either.** That is a real dependency on work outside this repository, and unlike the earlier draft's version of that claim it names the exact code that must change.

**What is verified, and what is not.** Verified by execution on `omnigent-runner-bircher`: bwrap is installed and cannot start; the kernel permits user namespaces and the container does not; the three parser sites refuse non-bwrap backends; the landlock backend declares its network support out of scope.

**The load-bearing assumption is no longer an assumption.** Run 2026-08-24 inside the bircher runner, under the container's *default* hardening:

```
RESULT abi           PASS 6
RESULT restrict_self PASS
RESULT tcp_denied    PASS PermissionError
RESULT unix_usable   PASS
```

with the control that makes the third line mean something:

```
CONTROL tcp_without_landlock PASS connected
```

TCP to `1.1.1.1:443` **succeeds** without the ruleset and raises `PermissionError` under it, so the denial is Landlock's and not a missing route — and the Unix socket remains connectable while all TCP is denied. **Landlock reproduces bwrap's sole-egress invariant with no namespaces, no mounts, no privileges and no container relaxations.** The design rests on a measured property rather than a plausible one.

What remains unverified is downstream and ordinary: that omnigent's relay, once the backend emits `NET_PORT` rules, uses that Unix path unchanged. That is what the two upstream changes and the capability test establish.

The frozen bundle must also be defined rather than gestured at. Milestone 1 fixes: which issue fields, comments and labels form the frozen input; how that snapshot is canonicalized for hashing; what counts as a relevant change; who creates a revision; whether implementation outputs invalidate spec or plan review; and the single transaction that joins artifact persistence, enqueue and the first durable transition.

**Done means:** one real issue goes from vague goal to a merged PR — or to a documented, safe refusal to merge — with every transition durable, every verdict bound to a hash, and the merge authorized by the kernel rather than by a model.

**"Safe refusal" does not cover a failed prerequisite.** The sole-egress experiment, the two upstream changes it gates, and the end-to-end capability test must all **succeed** before Milestone 1 can be called complete. Without that restriction the milestone could be declared done *because* the dependency failed — the boundary unproved, the refusal duly documented, and the acceptance criteria satisfied by the one outcome they exist to rule out. Safe refusal is a **run-level** decision available after the operational boundary has been demonstrated; it is never a way for the milestone itself to pass with the boundary untested.

### Not in Milestone 1

Autonomous front-end revision. Parallel runs. The full benchmark matrix. Budget reservation. Anything in the deferred or cut lists.

---

## Testing

The kernel's own correctness is a fault-injection problem, and v1's comments supply the fault list: crash before and after every external call; duplicate every event; delay a completion past its timeout; reorder notifications; move the PR head during review; move the target branch before merge; lose a stop acknowledgement; return malformed provider output; exhaust quota at every stage.

Two properties from the trial that every guard in this repo must satisfy:

- **Mutation-test every guard.** Break the thing the test protects; if the suite stays green, the test is decoration. State the mutation and its result. Fifteen assertions in one branch of a downstream project passed for a reason other than the one they named, and every one was found by executing a mutation rather than by reading.
- **Mutate the exact line the guard occupies.** Twice in that work a mutation aimed at a neighbouring call site left the test green and nearly recorded a correct guard as unbound. "The test still passed" means nothing until you have confirmed you broke the right thing.

A guard that genuinely cannot be bound by an end-state test is **declared**, not manufactured. A declared limit is a known limit; an undeclared one is a lie with a delay on it.

---

## Consumers whose requirements shape this now

Neither is built in Milestone 1, and both constrain interfaces that are expensive to change later.

**The autonomous front end** needs the decision schema to carry grill rulings and gate transitions, not only review rulings.

**The benchmark harness** needs provenance recorded from the first commit: provider, model, version, prompt and skill versions, tool set, policy version, context-bundle hash, mechanism version, and inference settings where available. Retrofitted, neither replay nor comparison is meaningful.

The harness decomposes into three measurements, which the kernel must not conflate: a **harness benchmark** (fixed orchestration, compare implementer/reviewer pairs), a **judgement benchmark** (frozen decision packets, compare rulings and severity and scope classification), and a **system benchmark** (let everything vary, measure end-to-end success, cost and time).

One correction to an earlier harness sketch, recorded here so it is not rebuilt wrongly: **"honest declaration" must not be a single scalar.** Score its components separately — uncertainty disclosure, assumption visibility, unsupported-success claims, recognition of missing evidence. A single number is easy to game, and this is the metric most worth not gaming.

---

## Open questions

1. **Where the kernel's state lives.** SQLite is sufficient for a single sequential runner and trivial to back up; Postgres is already a dependency of the downstream project and better if runs ever parallelize. The deferred list says parallelism is not needed yet, which argues SQLite — but the migration is real work if that changes.
2. ~~How much of v1 is called versus ported.~~ **Decided in Milestone 1, above.** Authority-bearing and journalled-effect seams are ported with their fixtures and mutation tests; pure or read-only helpers may be called. What remains genuinely open is *which* helpers qualify as pure, and that is answerable only by reading each one — a Milestone 1 task, not a design question.
3. ~~Whether the front end's grill rounds are a kernel concern.~~ **Decided — this was not genuinely open.** Milestone 1 claims every arrow from the goal onward is a durable transition, and a conversation held only in model or UI state makes `goal → grilled decisions` neither durable nor auditable. Both claims cannot stand.

   **Accepted human answers and grill decision packets are immutable kernel facts.** Conversational UI state may live outside the kernel; the decisions may not. The alternative — redefining Milestone 1 to begin at human enqueue — was considered and rejected, because it would abandon the audit trail over precisely the stage where five of eleven decisions were wrong as stated in the trial.
