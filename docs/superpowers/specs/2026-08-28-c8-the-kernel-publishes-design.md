# C8: the kernel publishes

**Status:** design, approved in outline 2026-08-28.
**Predecessor:** `2026-08-26-v2-supersedes-v1-record-mode-design.md`, which
names C8 as explicitly out of its own scope.
**Record of the branch this builds on:**
`docs/superpowers/records/2026-08-26-v2-record-mode-acceptance.md`.

## Goal

Close the last hole in the v2 boundary: the implementer still opens its own
pull request and posts its own marker, from its own credential, so the kernel
cannot journal either. Acceptance criterion 2 — *every externally visible
mutation is journalled* — cannot hold until it does, and the acceptance record
says so.

## What already exists, and why this is smaller than it sounds

**The boundary is built and proven.** `agents/v2_implementer/config.yaml`
cannot push (`git-receive-pack` is absent from a default-deny egress list),
cannot mutate through the API (GET-only rules), and holds no GitHub
credential. M1-1 proved the authority boundary 12/12 on the live image.

**The contract is already written.** That bundle's own prompt says:

> Commit your work locally. The kernel imports the commit you nominate, by its
> object id, and performs every external effect itself.

Nothing implements the second sentence. C8 is that sentence, not a new design.

**The effect machinery is already built.** `ref_update` and `pull_request` are
existing effect classes with argv contracts, routed through `_effect`,
journalled intent-first, and covered by the halt-and-reconcile path. Publishing
needs no new effect class and no new credential domain.

## Decisions taken

### 1. The threat model is a CONFUSED implementer, not a hostile one

The kernel verifies **provenance and shape**, never content. A commit built on
a stale base, an unintended merge, or an object that is not the one nominated
are the realistic failures and each is cheap to detect. Constraining *what
paths* a commit may touch was considered and rejected: it needs a policy about
what an implementer may ever change, and that policy becomes a thing to
maintain and to get wrong. Content is the cross-vendor review's job.

### 2. The kernel OBSERVES the nomination; a claim may only disagree with it

The kernel reads the tip of the worktree branch it dispatched. If the session
also states an object id, the claim is checked against the observation and a
mismatch is a **refusal recording both values** — never a tiebreak, never a
fallback.

This is deliberate and expensive to get wrong. Five review rounds on the
predecessor branch removed a series of defects with one shape: a model-authored
string deciding something. `review=accept` from a marker minted a real kernel
verdict; `ci=suc"cess` normalised into `success` and authorised a merge. A
nomination that decides what gets **pushed** is the same shape with a larger
blast radius, so nothing model-authored decides it.

### 3. The kernel observes what decides; the implementer's account is colour

| decides | observed by |
|---|---|
| commit provenance | the kernel, against the `base_sha` it recorded at run start |
| CI status | `gh api` on the published head |
| review verdict | the cross-vendor review seat |
| merge authorisation | the kernel |
| the implementer's own summary | **recorded in the ledger; authorises nothing** |

This **retires** `bircher-status:` parsing rather than relocating it. Moving the
marker to another transport — the session's output, a file in the worktree —
was considered and rejected: it keeps a model-authored report deciding
outcomes, which is the defect class the predecessor branch spent five rounds
removing.

## Phase 1 — the publish surface

Provable without touching the path that currently works.

### `verify_nomination(store, run_id, worktree, branch, claimed_oid=None) -> str`

New, in `v2/kernel/`. Returns the object id to publish, or raises.

- the branch tip resolves to an object that exists in that worktree
- it **descends from `store.run_base_sha(run_id)`** — the base the kernel
  recorded, not the checkout's current HEAD. (The predecessor branch shipped
  exactly that confusion: a recovery bound `git rev-parse HEAD` at recovery
  time against a base recorded hours earlier, and every review was refused.)
- the lineage from base to tip contains no merge commits
- if `claimed_oid` is given it must equal the observed tip

Each refusal names which check failed and the two values involved.

### Publication

The coordinator, holding the credential, performs two existing effects:

    _effect ref_update    git push <remote> <oid>:refs/heads/<branch>
    _effect pull_request  gh pr create --head <branch> ...

Both already carry argv contracts. A push whose outcome is unknown becomes an
uncertain effect, halts the run, and is resolved by the reconciliation path
built on the predecessor branch.

### Phase 1 acceptance criteria

Each is written so it can fail.

1. **A v2_implementer session, dispatched against the throwaway repo, produces
   a commit and no PR.** Its egress denies the push; the session's own failure
   to reach GitHub is the boundary working, not an error to handle.
2. **The kernel publishes that commit**, and `effect_intended` /
   `effect_confirmed` facts exist for `ref_update` and `pull_request` naming
   the object id it verified.
3. **A commit built on a stale base is refused**, with the recorded base and
   the observed base both named in the refusal.
4. **A claimed object id that differs from the branch tip is refused**, with
   both values recorded — and nothing is pushed.
5. **The shadow report is read after a clean run** and its contents are stated,
   whatever they are. Under enforce an empty report is worth nothing on its
   own; the positive evidence is that every command reached
   `command_accepted`.

### What Phase 1 does NOT do

It does not touch `run_item`'s marker branch. The v1 path continues to work
unchanged, and Phase 1 is exercised by a dedicated invocation against the
throwaway repo.

## Phase 2 — retire the marker

Only after Phase 1 is proven.

`run_item`'s marker branch, `parse_marker`, and the scorecard fields fed from
it (`ci_first`, `rounds`, `review`, `note`) are replaced by derived
observations. The outcome vocabulary is unchanged — `ready`, `escalated`,
`noop`, `failed`, `timeout`, `skipped` — but each is **derived** rather than
read.

This is the larger and riskier half: it changes the only pipeline that
currently works. It is sequenced second so that a failure in it can be rolled
back to a Phase 1 that already works, rather than to nothing.

### Phase 2 acceptance criteria

1. A full item runs end to end on the throwaway repo with **no
   `bircher-status:` comment anywhere**, and merges.
2. Every scorecard field is traceable to an observation, and the mapping is
   written down.
3. `--self-test` stays green, and every guard removed with the marker has its
   replacement named — a deleted test is a coverage change, not a cleanup.

## Out of scope

- **Content policy.** Decision 1. The review seat judges content.
- **A second credential domain.** The kernel uses the coordinator's existing
  one; C8 is about who may act, not about adding a new holder.
- **The effect-mode deployment default.** The adapter defaults to `deny` on
  purpose. What the runner launches with is an operational decision.

## Risks, stated rather than mitigated away

- **Worktree reachability.** The kernel reads a worktree the session wrote.
  If the harness ever isolates that filesystem, the observation model needs
  rethinking — not patching with a report.
- **Phase 2 is a rewrite of the working path.** Sequencing contains it; it does
  not remove it.
- **Criterion 4 still fails by design.** Publishing through the kernel deepens
  the hard dependency: with C8, a broken kernel means no PR at all. That is the
  cost of the boundary and it should be stated to whoever operates it.
