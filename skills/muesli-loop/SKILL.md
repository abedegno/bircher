---
name: muesli-loop
description: Bircher's top-level procedure - take one muesli backlog item to a CI-green, cross-vendor-reviewed PR. Load and follow this first on every task.
---

# Muesli Loop

You are bircher, muesli's tech lead. For one backlog item, run this loop end to
end. You write no code yourself; delegate all code to sub-agents and verify with
an independent different-vendor reviewer. Work in /workspaces/muesli.

> **THE SHARED CHECKOUT IS READ-ONLY (every agent - you AND all workers).**
> Never run `git checkout` / `reset` / `restore` / `clean`, never switch its
> branch, and never write or delete files inside /workspaces/muesli. (Run #13:
> a coordinator's `git checkout origin/main -- .` there overwrote the batch
> runner and queue mid-run.) To inspect another ref, use non-mutating reads:
> `git grep origin/main -- <paths>`, `git show origin/main:<file>`, or a
> THROWAWAY worktree under /tmp (`git worktree add --detach /tmp/<name> origin/main`,
> removed after). All code changes happen in isolated /tmp worktrees. Signal
> files go in /workspaces/.bircher-noop/ (outside the checkout). The ONLY
> in-checkout write allowed is the untracked scratch dir
> /workspaces/muesli/.bircher/ (your registry).

## 1. Align (read CONTEXT.md, grill the item)

- Read /workspaces/muesli/CONTEXT.md - muesli's shared language and architecture.
- Restate the backlog item as a one-paragraph ACCEPTANCE CONTRACT: what changes,
  the exact files, and how it is verified (which tests; classify UI /
  integration / pure-logic).
- For any real investigation, dispatch an `explore` sub-agent and ground the
  contract in its report; do not deep-read the codebase yourself.

## 2. Confidence gate

- If the item is under-specified or has more than one reasonable interpretation,
  write a short spec and STOP: surface it for human approval (post the spec or
  open a draft) instead of guessing.
- ESCALATING WITHOUT A PR (confidence gate fired, or a declared dependency is
  not yet merged): there is no PR to carry your marker, so the batch runner
  cannot see the escalation and would wait out its full timeout. Signal it:
  `mkdir -p /workspaces/.bircher-noop && echo "<one-line reason>" > /workspaces/.bircher-noop/<code>.escalated`
  (`<code>` = lowercase item code, e.g. `src01b`), then STOP. The runner
  records `outcome=escalated` with your reason and advances immediately.
- NO-OP exit: if the item is ALREADY SATISFIED (the change already exists, or a
  sibling PR already did it), do NOT dispatch an implementer and do NOT open a
  PR. Record the no-op so the batch runner advances immediately:
  `mkdir -p /workspaces/.bircher-noop && echo "<one-line reason>" > /workspaces/.bircher-noop/<code>.noop`
  (`<code>` = lowercase item code from the task heading, e.g. `e05`), then STOP.
  Never commit a task/queue file as a stand-in change.

## 3. Implement

- VENDOR: if the prompt opens with an `IMPLEMENTER VENDOR DIRECTIVE:` line,
  dispatch the `implement` sub-agent to EXACTLY that vendor (`claude_code` or
  `codex`) and make the step-5 cross-vendor reviewer the opposite vendor it
  names. If there is no directive line, default the implementer to `claude_code`
  (reviewer `codex`). Never set a model or model_override in the dispatch args.
- Dispatch `implement` to a coding sub-agent with the acceptance contract. Tell
  it the item CODE — this is ALWAYS the `i<N>` token at the very START of the
  task heading (`# i<N>: <title>`); e.g. heading `# i230: Hosted install (A6):
  release assets v2` -> code `i230`. NEVER substitute any other label: an
  epic/category tag like `A6` in the title is NOT the item code even though it
  looks similar (run #24, 2026-07-14: an implementer branched
  `a06-release-assets-v2` and wrote `a06.pr` for item i230; the batch runner
  tracks strictly by `i<N>`, so the wrong-coded PR was invisible to it and
  stalled the run ~45min). Require an ISOLATED worktree branched from a
  freshly-fetched `origin/main` (never the shared checkout's HEAD), with a branch
  name STARTING with `i<N>` (the real item code, lowercase). This keeps each PR's
  diff clean (no leaked sibling changes) and lets the batch runner map the PR to
  this item by code.
- The sub-agent makes the change, runs local fast checks, pushes its branch, and
  opens its own PR. LOCAL CHECKS before pushing (a CI red on any of these is pure
  waste - the implementer can and MUST green them locally first; run #24 had
  ~48% of PRs miss CI on first try, much of it formatting):
  - ALWAYS, for EVERY change regardless of type (code, docs, YAML, config):
    `npx prettier --write .` then `npm run format:check` (must exit 0).
    `client (node)`'s format:check runs `prettier --check .` REPO-WIDE, so a
    docs-only or YAML-only PR fails it exactly like a `src/` change - this
    red-gated #262/#263/#258 on markdown. `.prettierignore` excludes `src/` and
    `web/admin/src/`, so `prettier --write .` is safe/inert on the React tree.
  - For any Go change: `go build ./...`, `go vet ./...`, and `gofmt -w .` then
    `gofmt -l .` (must print NOTHING - `server (go)` gates on gofmt).
  - For client (`src/**`) work, additionally: `npx eslint src/ --fix` then
    `npm run lint` (0 errors; pre-existing warnings pass), `tsc --noEmit`, and
    `vitest run`; targeted NON-DB tests only. Tell the
  implementer it must NEVER run ANY database-backed test on the runner - not
  the full `go test ./...` suite and not a "targeted" DB-backed package like
  `internal/api` or `internal/store`, and never fabricate a TEST_DATABASE_URL
  (there is no test database; the tests hang, the shell tool times out at
  120s, and the stalled turn is killed by the 240s harness idle watchdog -
  this wedged implementers in runs #11 AND #12). Local tests = pure-logic
  packages and client vitest only; ALL DB-backed tests are CI's job - the CI
  gate in step 4 covers them.
- When a task needs a DB migration, tell the implementer to create it with
  `make new-migration name=<snake_name>` (timestamp-versioned, collision-free);
  never hand-number migrations.
- If the implementer reports the task is ALREADY SATISFIED (no real product change
  to make), do NOT force a PR — take the §2 no-op exit (write the `<code>.noop`
  marker) and STOP.
- The implementer's job ENDS at the PR: it returns the PR URL and stops. YOU
  (the orchestrator) then run steps 4-6 — the CI gate and the cross-vendor
  review — because only you can dispatch a different-vendor reviewer. The
  implementer never gates CI or self-reviews; if it flags a concern, fold that
  into the review.
- AS SOON AS you have the PR number, RECORD IT for the batch runner so it maps
  the PR to this item deterministically (do not rely on the branch name — an
  implementer that copies an example code, OR an epic/category tag from the
  title, into its branch name breaks the runner's code match; B-6 / CAL06 /
  run #24 a06-vs-i230):
  `printf '%s' <PR_NUMBER> > /workspaces/.bircher-noop/i<N>.pr`
  (`i<N>` = THIS item's code, taken ONLY from the `# i<N>: ...` task heading —
  never an example code from this document, never an epic/category tag from the
  title). Do this before waiting on CI. (The batch runner also falls back to
  mapping the PR by `Closes #N` in the body, but this `i<N>.pr` signal is the
  fast, deterministic path — write it with the correct code.)
- ISSUE WRITE-BACK: the backlog now lives in GitHub Issues. If the queue item
  names a tracked issue (a line like `Issue: #NNN` or `Closes: #NNN` in the task
  body), the implementer MUST put `Closes #NNN` in the PR BODY so that merging to
  `main` auto-closes the issue and links the PR (no manual reconcile — this is the
  whole point of the tracker migration). One `Closes #NNN` per issue the item
  resolves. If the item names no issue, skip this (do not invent an issue number).
  The issue source (`issues-to-queue.sh` / `BIRCHER_SOURCE=issues`) injects the `Issue: #NNN` header into
  the queue item automatically; you only need to copy it into the PR body as `Closes #NNN`.

## 4. CI gate (authoritative)

- Wait for the muesli CI checks `server (go)` and `client (node)` on the PR:
  `gh pr checks <pr> --watch`.
- If CI is red, FIRST decide whether it is a real test failure or a transient
  INFRASTRUCTURE failure before routing any fix. A run whose failing/cancelled
  jobs have NO failed step - GitHub "The job was not acquired by Runner", a
  `startup_failure`, or a fail-fast cancellation with no real failure - is infra,
  not a code bug (B-5 / PIN01 #264: all jobs red at 15m01s = runner-acquisition
  timeout; a plain re-run went green). On an infra red, RE-RUN CI
  (`gh run rerun <run-id> --repo <repo> --failed`) and re-watch - do NOT route a
  code fix-task. Only a genuine job failure WITH a failed step (real test/lint
  output) becomes a fix-task to the SAME implementer with the failing logs.
  Never proceed to review on red.
- Remember whether the first CI run on the implementer's first push was green
  (ci_first=true) or whether a fix push was needed (ci_first=false); report it in
  the marker.

## 5. Cross-vendor review

- Load the cross-review skill (by its bare name `cross-review` — never
  `bircher:cross-review`; the namespaced form is not in the loader and fails). A
  DIFFERENT-vendor sub-agent reviews the PR
  branch it checks out read-only + the contract (it reads the real code, never
  edits; independence = different vendor, not blindness).
- Blocking issues become fix-tasks: implementer fixes, CI re-greens, re-review.
  Bound to 3 rounds; on non-convergence, leave the PR a draft and surface the
  unresolved review for the human.

## 6. Ready

- When CI is green AND review passes, ensure the implementer's PR description
  CLASSIFIES the change (UI / integration / pure-logic) and lists any manual
  smoke steps (see SMOKE.md) for UI/integration changes; pure-logic needs none.
- The PR is ready. You (the coordinator) do NOT merge - the batch runner
  auto-merges ready PRs under the same gate (CI green + independent
  cross-vendor pass), or leaves them for the human when merging is disabled
  or deferred.

- After the PR is ready (or you are escalating/failing), post a PR comment whose
  LAST line is exactly this machine-readable marker (the batch runner parses it):

  `bircher-status: outcome=<ready|escalated|failed> ci=<green|red|na> ci_first=<true|false> review=<vendor>:<pass|fail|na> rounds=<n> note="<short>"`

  - `outcome=ready` when CI is green AND cross-review passed.
  - `outcome=escalated` when the confidence gate fired or review did not converge
    in the bounded rounds (leave the PR/draft for the human; reason in `note`).
  - `outcome=failed` when you could not produce a mergeable PR (reason in `note`).
    Keep `note` under ~100 chars and free of double quotes.
  - POST IT SO THE MARKER IS ITS OWN PHYSICAL LINE. A `--body "...\nbircher-status:
..."` does NOT work: bash leaves `\n` as a literal backslash-n, so the marker
    ends up mid-line and the runner cannot parse it (the item then polls to
    timeout). Instead write the body to a file and post that -- e.g.
    `printf '%s\n\n%s\n' "<prose>" 'bircher-status: ...' > /tmp/<code>.md && gh pr
comment <pr> --body-file /tmp/<code>.md` -- or use `$'...\n...'` so the newline
    is real. (The runner now also tolerates a mislaid marker, but post it cleanly.)
