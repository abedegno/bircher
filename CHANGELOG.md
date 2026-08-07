# Changelog

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
