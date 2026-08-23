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
    {"finding_id": "finding_12", "disposition": "blocking", "rationale": "...", "confidence": 0.86}
  ],
  "recommendation": "request_revision"
}
```

The kernel validates the schema, confirms every referenced hash still matches, confirms the decision type is legal in the current state, checks reviewer independence and authority, checks budgets and round limits, records the decision immutably, then computes and executes the resulting transition.

`accept` from the judgement layer means "no unresolved blockers for the pinned review bundle". It does not mean merge.

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

The scoped-credential option is superseded rather than retained. It required "a real enforcement mechanism — an isolated staging repository or fork, or a ref-enforcing proxy." The third of those exists upstream, is installed, and is stronger than the property this section was trying to buy — but **does not currently run on this deployment**, for the reason set out below. The supersession therefore rests on a mechanism that is real and not yet operable, and that condition travels with it.

### The enforcement mechanism exists upstream, is installed, and does not currently run

An earlier revision called this "a dependency outside Bircher, and the largest open question in this design", on the reasoning that both coordinator and worker agents run `os_env: type: caller_process`, unsandboxed, inside `omnigent-runner-bircher`, so no Bircher configuration could put credentials out of a model's reach. **The premise was right and the conclusion was wrong.** `caller_process` describes the *process* the tools run in; it does not describe the *domain*. omnigent nests an `os_env.sandbox` block under exactly that setting, and the sandbox is where domain separation lives.

The mechanism is omnigent's **secretless credential proxy** (`os_env.sandbox.credential_proxy`, `designs/SANDBOX_CREDENTIAL_PROXY.md`, upstream #236, 2026-06-15). It requires `egress_rules` and a network-isolating backend (`linux_bwrap` or `darwin_seatbelt`); the parser rejects the block otherwise, because only those backends can guarantee the L7 MITM proxy is the sole egress path and no tool can open a raw socket around it. Its default mode is **swap-on-access**: the unsandboxed parent resolves the real secret and holds it in the proxy's in-memory rewrite table, the sandboxed tool issues its request carrying **no** `Authorization` header, and the proxy attaches the credential on the way out.

This is a stronger property than the boundary decided above. Requirement 2 asked for credentials that are *unreachable* from the model domain by environment, filesystem, credential helper, socket, process inspection or session tooling. Swap-on-access means there is no credential in the domain to reach: enumerating every access path is unnecessary when the secret was never present on any of them.

It also settles the requirement the scoped-credential bullet could not. GitHub tokens are still not ref-scoped — but scoping the token is the wrong lever. Git-over-HTTPS separates the two operations by request path: fetch is `POST <repo>.git/git-upload-pack`, push is `POST <repo>.git/git-receive-pack`. `egress_rules` are default-deny and scope by method *and* path glob (`"GET,POST api.github.com/repos/org/**"`). Allow-listing upload-pack and simply not listing receive-pack yields a model domain that can clone and fetch and **cannot push at all** — enforced at a proxy the sandbox has no route around, rather than by a credential property GitHub does not offer. The same lever restricts `gh` to `GET api.github.com/**`, so no PR, comment, label or issue mutation can leave the domain.

Two details matter when configuring it. The `gh_basic` preset injects a placeholder into `GH_TOKEN` for the api host, because `gh` refuses to issue a request when it sees no local token; the placeholder is a random single-use `oa_cred_*` value, non-secret, host-bound, and 403'd if replayed anywhere else — so the model domain still holds nothing worth stealing. And SSH is an explicit non-goal of the proxy, so every remote in the model domain must be HTTPS.

**Availability, checked rather than assumed.** The proxy landed in upstream #236. Its presence was then checked **per release rather than inferred**, because omnigent's release tags sit on divergent lineages — `v0.7.0` is not an ancestor of `v0.8.0`, so a containment check against one tag says nothing about a later one, and an earlier draft of this paragraph made exactly that unsound generalisation. The ancestry-independent check is whether the file exists at each tag: `omnigent/inner/credential_proxy.py` is present at `v0.7.0`, `v0.8.1` and `v0.9.0`. Implementation spans `omnigent/inner/credential_proxy.py`, the egress controller and proxy, all three sandbox backends and the spec loader, with unit tests and a sandbox e2e suite. `bubblewrap` is installed in the runner image definition (`deploy/docker/Dockerfile:213`).

An earlier draft named `v0.7.0` as the deployed version. That was stale: Bircher's own `#60` fix replaced an endpoint **omnigent v0.9.0 removed** (`batch/run-queue.sh:1066`), so the runner is at or beyond v0.9.0. The conclusion survives — every candidate release carries the proxy — but the reasoning must not: a repository tag is not deployment evidence, and the deployed version is read from the runner (`omnigent --version`), never inferred from a branch or tag.

**Those items were then executed rather than deferred, and one came back negative.** An earlier draft listed them as "runtime facts with known remedies" for Milestone 1 to confirm. Running them took four commands and converted one from a deferred check into a blocking precondition:

- `omnigent --version` on the live runner → **0.9.0, built 2026-08-14T18:30:30Z**. Confirms the inference above, and `credential_proxy.py` is present at that tag.
- `bwrap --version` → **bubblewrap 0.11.0** at `/usr/bin/bwrap`. Installed, as the image definition promised.
- `bwrap --unshare-user --unshare-pid --ro-bind / / --dev /dev …` → **"No permissions to create new namespace"**. It cannot start.

The cause is the container, not the host. `/proc/sys/user/max_user_namespaces` is 126932 on kernel 6.12.30+, so the kernel permits user namespaces; `unshare --user --map-root-user` returns `Operation not permitted`. The container runtime denies `clone(CLONE_NEWUSER)` — Docker's default seccomp behaviour.

**This is load-bearing, not incidental.** omnigent's parser rejects `credential_proxy` unless the backend is `linux_bwrap` or `darwin_seatbelt`, on the stated grounds that only those guarantee the MITM proxy is the sole egress path. The runner is Linux, so seatbelt does not apply. Until bwrap can start, **the enforcement mechanism is inoperable on this deployment** — shipped and installed, but not running.

**The remedy is a container configuration change**, and it carries a judgement that must be made explicitly rather than absorbed.

*What is actually being conceded* is user-namespace creation from inside the container — `clone(CLONE_NEWUSER)`. That is a historically productive host-kernel attack surface, which is exactly why Docker's default profile denies it. Naming it that way rather than as "relaxing a boundary" is the difference between a costed decision and a slogan.

*The three ways to grant it are not equivalent, and the spread is orders of magnitude.* In increasing order of concession: **a custom seccomp profile permitting only the namespace syscalls** — the minimal option and the one Milestone 1 should take; `seccomp=unconfined`, which disables the entire syscall filter rather than one call; and `--cap-add SYS_ADMIN`, which grants a great deal more than namespace creation and is the worst of the three. A plan that lists them as alternatives without ranking them has not costed anything.

*What must be relaxed is probably two things, not one, and only the first is measured.* The container is confined twice over: `grep Seccomp /proc/self/status` returns `Seccomp: 2` with one filter (Docker's default profile), and `cat /proc/self/attr/current` returns **`docker-default (enforce)`**. bwrap must clear both gates — namespace creation, then bind-mounting a filesystem view inside that namespace. Only the first is measured: `unshare --user` fails before any mount is attempted, so seccomp demonstrably blocks step one, while AppArmor is merely *known to be enforcing* and to deny the operations step two needs. It has not been proven to block and must not be written as though it had. The plan therefore sequences: relax seccomp, **re-run the probe**, and only then learn whether `apparmor=unconfined` is also required — a second and blunter concession, since Docker offers no per-service AppArmor tuning comparable to a custom seccomp profile. A negative result names the first gate that closed, never the only one.

*Configuration scope is not compromise scope, and the earlier draft conflated them.* That `security_opt` applies per service establishes only **which container receives the relaxed policy** — `omnigent-runner-bircher` and not `omnigent-runner`, whose profile is untouched. It establishes nothing about the blast radius of *exploiting* the additional kernel surface. That radius runs through this service's mounts, any Docker or privileged sockets, its network peers, shared writable storage, host-access routes, and the credentials held by its unsandboxed parent — which, under this very design, is where the real secret lives. Milestone 1 therefore produces an explicit **topology inventory** for the bircher service and a **tested assertion** that it cannot pivot into the general runner, the container manager, host-sensitive mounts, or the parent's credential domain. Until that exists, "the blast radius is one service" is a statement about a config file, not about security.

*The net-gain argument, stated so it can be attacked — and it is narrower than it first appears.* Bircher's model processes today run unsandboxed with ambient `git push` and `gh pr create` authority, so against **the GitHub-effect threat** the existing outer boundary buys approximately nothing. That is the whole of what the claim supports. The container boundary still materially constrains **host compromise and lateral movement**, and reopening user-namespace creation increases exposure to precisely that second class. So the trade is not one-sided: it reduces unauthorized-repository-effect authority while increasing container-escape and pivot exposure, and it is positive only if the topology assessment above shows the second increase is small. Argued any other way — "the application already holds one dangerous authority, therefore relaxations are free" — the same reasoning would justify unrelated relaxations indefinitely, which is a sign the argument is wrong rather than a licence.

Milestone 1 records that trade as a decision with its reasoning, re-runs the probe above as the acceptance gate, and treats a pass as the precondition for the end-to-end capability test, which cannot run before it.

**The cross-domain handoff no longer needs an exotic channel.** The earlier revision demanded a container-manager copy-out or an append-only upload service, on the grounds that a shared workspace path is exactly what requirement 5 forbids. Content addressing removes the need. Kernel-owned git storage is sited **outside every `write_path`** — unwritable from the sandbox, and maskable via `mask_paths`. The kernel imports into that storage from the model's worktree, but **it does not learn which object to import by looking at the worktree**: the current generation's accepted result nominates the expected object ID first, and the kernel then fetches that ID, verifies it and its reachability, and pushes exactly it. A late write in the worktree produces different objects under different hashes — objects the kernel was never told to fetch, did not fetch, and will not push.

**But content addressing protects the object only after it is selected, and selection is the vulnerable step.** If the kernel fetches a mutable ref and *then* binds whatever SHA came back, a late or stale process that wins the pre-import race has chosen the content attributed to the current attempt — and generation-binding applied after selection cannot say which generation supplied it. Integrity after the fact is not authenticity of choice. So the order is inverted and fixed: **the current generation's accepted result nominates the expected object ID first; the kernel then fetches and verifies exactly that ID from attempt-isolated storage.** The authorized ID is never derived from a mutable source ref. Requirement 5's late-writer concern is a TOCTOU on a mutable reference; naming the object by its content is the standard answer to it, and requirement 4 already committed to it. The condition this rests on is exact and belongs in the plan: **after import the kernel uses only the object ID it resolved in kernel-owned storage — never the source ref, branch or path** — and that ID is bound to the attempt generation and review tuple, under the same generation requirement that already governs accepted results and effects. Fetching from a mutable worktree decides *which* content is imported; it cannot alter content already selected by verified SHA.

So the design is unblocked **conditional on the container relaxation being accepted — one change if the seccomp gate is the only one, two if the probe then shows AppArmor blocking as well** — and the document should not pretend that condition is slack. Every alternative has already been eliminated in this section: the scoped-credential option is superseded, `darwin_seatbelt` does not apply on Linux, and omnigent rejects the proxy without a network-isolating backend. There is exactly one enforcement path and **no fallback**; if the trade above is refused, this design needs a new mechanism rather than a smaller edit. Stated that way the distinction is a real one for planning rather than a way to keep the earlier conclusion.

The sequencing this implies is a rule, not an inference: **no model attempt runs before the gate passes.** Nothing here requires a change to omnigent or a capability it lacks: the mechanism exists, is the right shape, and is stronger than what this section originally specified. What it requires is container-configuration change on infrastructure we control — one concession, or two if the re-run probe shows AppArmor blocking after seccomp is relaxed — made as a stated security trade, with the probe above as its gate. The count is not yet known, and a plan that assumes the cheaper answer has assumed the part that was never measured.

The earlier revision of this section claimed the capability was "already deployed". That was true of the artifact and false of the capability, and the false half was the one the argument rested on — a claim outrunning its evidence, in the passage arguing that a blocker had been resolved. It survived because the check had been written down as a Milestone 1 task instead of being run. Every remaining "verify at implementation time" item in this document should be read as concealing the same thing until someone executes it.

The frozen bundle must also be defined rather than gestured at. Milestone 1 fixes: which issue fields, comments and labels form the frozen input; how that snapshot is canonicalized for hashing; what counts as a relevant change; who creates a revision; whether implementation outputs invalidate spec or plan review; and the single transaction that joins artifact persistence, enqueue and the first durable transition.

**Done means:** one real issue goes from vague goal to a merged PR — or to a documented, safe refusal to merge — with every transition durable, every verdict bound to a hash, and the merge authorized by the kernel rather than by a model.

**"Safe refusal" does not cover a failed prerequisite.** The namespace probe and the end-to-end capability test must both **pass** before Milestone 1 can be called complete. Without that restriction the milestone could be declared done *because* the deployment prerequisite failed — the boundary unproved, the refusal duly documented, and the acceptance criteria satisfied by the one outcome they exist to rule out. Safe refusal is a **run-level** decision available after the operational boundary has been demonstrated; it is never a way for the milestone itself to pass with the boundary untested.

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
