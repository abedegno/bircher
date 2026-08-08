# Changelog

## v0.1.1 — 2026-08-08

Six fixes, all for bugs that were live in v0.1.0. Three were found by running the
thing rather than reading it; two were found by cross-vendor review of Bircher's
own changes.

### Merge safety

- **Auto-revert never worked.** `_revert_git_args` emitted `-q`, which `git revert`
  does not accept — it exits 129 with a usage dump and reverts nothing. Every
  auto-revert had failed since the function was written. The self-test asserted the
  exact argument string *including* `-q`, so it stayed green for months while
  pinning the defect; there is now a check that runs `git revert` for real against
  both a squash commit and a true merge commit.

- **Non-CI checks could turn `main` red.** `/commits/<sha>/check-runs` reports more
  than CI: adding Dependabot to the target repo put 28 check-runs named `Dependabot`
  on a merge commit, two of them failed, and the runner declared a healthy `main`
  red, attempted a revert and halted mid-wave. Filtered by name via
  `_drop_non_ci_checkruns` (`BIRCHER_CI_IGNORE_CHECKS`).

- **A required review gate could deadlock recovery.** Where the target repo gates
  merges on a status that Bircher itself posts, `--recover-pr` waited for CI to go
  green while that gate waited for the review the same code was about to perform.
  Both waited forever. The same filter now excludes it at all three polling sites.

### Dispatch

- **Codex model pinned in the agent spec**, not only in the coordinator's prompt.
  omnigent resolves codex's default to the bare family name `gpt-5.6`, which a
  ChatGPT-account login rejects outright, and its curated catalog spells the
  variants with hyphens, which the API also rejects. A directive the coordinator
  has to remember is not a fix — it held for several waves, then didn't. Pinning it
  on the executor makes it the default for every dispatch.
  Upstream: omnigent-ai/omnigent#4063.

### Tooling

- **`update-bundle.sh` refused to run on a real runner.** Its dirty-tree guard
  used `git status --porcelain`, which includes untracked files — and a runner
  always has them (`queue/processed/*.md`, run logs, `.run/`). It blocked routine
  deploys, including the deploy of its own fix. Now checks tracked files only.

- **The attribution hook was unanchored and missed session trailers**, so it would
  strip prose that merely mentioned a trailer while leaving a session URL behind:
  attribution removed, fingerprint kept.

### Known limitations

Unchanged from v0.1.0, and #8 (thin forensic record for codex implementer
sessions) remains open.

## v0.1.0 — 2026-08-07

First tagged release. Bircher has been running unattended against a real repository
since July, so this is a marker on working software rather than a first draft.

### What it does

Works a GitHub Issues backlog. For each item it dispatches an implementer, has an
independent reviewer **from a different AI vendor** read the resulting branch, gates on
CI, and merges. A sequential outer loop for operability; a runtime-constructed graph of
sub-agents, with review feeding back into implementation, inside each item.

### Safety and correctness

- **Cross-vendor review is enforced**, and the `bircher/cross-review` status is posted
  only after a genuine PASS. Making it a required check is what prevents an unreviewed
  merge; the runner posts, branch protection enforces.
- **Merges pin to the reviewed commit.** The reviewer emits the SHA it reviewed in its
  status marker, and the merge is atomic against it (`--match-head-commit`). If the
  reviewer does not say what it reviewed, the item **fails closed** and the PR is left
  for a human rather than merged unverified.
- **Status posting retries and verifies**, with an end-of-run reconciliation sweep that
  fails closed on anything it cannot confirm.
- **Recovery from ground truth** when a coordinator dies: adopt the real PR, run a real
  cross-vendor review, merge on PASS. A tracked PR that has been closed without merging
  is discarded rather than followed to a timeout.
- **Preflight probes dispatch, not just authentication.** A healthy vendor CLI does not
  prove the harness can launch a worker with it — the two failed independently in
  practice, and only the dispatch probe catches the second.

### Operability

- `batch/launch.sh` — detached start via `setsid`, which survives the session that
  launched it. `nohup … &` does not, inside `docker exec`.
- `batch/update-bundle.sh` — refresh the bundle on the runner; refuses a dirty tree and
  re-runs the self-test.
- `run-queue.sh --preflight`, `--usage`, `--recover-pr`, `--self-test`, `--help`.
- Vendor selection follows live provider quota across both 5-hour and weekly windows,
  and pauses rather than consuming items when a provider is unhealthy.
- A `flock` singleton stops two waves sharing one queue and work repo.
- Every run writes `.run/scorecard.jsonl`: outcome, implementer, reviewer verdict,
  rounds, wall time and notes per item.

### Known limitations

- Merge enforcement is delegated to branch protection: the runner does not itself block
  on checks that are not *required*.
- Codex implementer sessions leave a thin forensic record, so the review is easier to
  audit after the fact than the implementation
  ([#8](https://github.com/abedegno/bircher/issues/8)).
- `muesli-loop` is a project-specific example skill. Adapt it, or write your own.

### Notes

Requires an omnigent server and runner, a GitHub repo, and CLIs for at least two
vendors. See the README for setup and SECURITY.md before running it anywhere that
matters — this software holds vendor credentials, has merge rights, and runs unattended.
