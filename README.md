# Bircher — a cross-vendor-reviewed autonomous dev agent (reference implementation)

Bircher works a GitHub-Issues backlog: for each item it dispatches an **implementer**
(one vendor) and an independent **reviewer** (a _different_ vendor), gates on CI, and
merges. This is the **reference implementation** we run on
[muesli](https://github.com/abedegno/muesli) — it depends on
[omnigent](https://github.com/omnigent-ai/omnigent) as the execution substrate and on
GitHub for the tracker.

- `batch/run-queue.sh` — the sequential batch runner (CI gate, in-run merge, recovery).
- `skills/muesli-loop/` — the top-level procedure (project-specific **example** skill).
- `skills/cross-review/` — the different-vendor review step.
- `config.yaml`, `agents/{codex,claude_code}/` — the coordinator + sub-agent bundles.
- `docs/run-forensics-runbook.md` — how to forensically evaluate a run.

Not a turnkey tool: it needs an omnigent server + runner and a GitHub repo. `muesli-loop`
is the muesli-specific example; adapt it (or write your own project skill) for your repo.

Licensed under Apache-2.0.
