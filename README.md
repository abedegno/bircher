# Bircher

An autonomous development agent that works a GitHub Issues backlog, where **the code is always reviewed by a different AI vendor than the one that wrote it**.

For each issue it dispatches an implementer, has an independent reviewer from another vendor read the resulting branch, gates on CI, and merges. If Claude writes the code, GPT reviews it, and the other way round.

This is the reference implementation, running in production against a real project. It is not a turnkey product: you need an [omnigent](https://github.com/omnigent-ai/omnigent) server and runner, a GitHub repo, and CLIs for at least two vendors.

## Why different vendors

A model reviewing its own work tends to agree with itself. It has already decided the approach is sound, and asking it to check that decision mostly produces agreement.

A model reviewing a different model's work has no such investment. In practice the disagreements are where the value is: a reviewer that rejects a plausible-looking change for reasons the implementer had confidently talked itself past.

The runner enforces this. It picks the implementer per item based on provider quota, then requires the reviewer to be a different vendor, and only posts the `bircher/cross-review` status after a genuine PASS.

## Architecture: a loop around a graph

The outer layer is a plain sequential loop. One issue at a time, run to completion (merged, or escalated with a reason) before the next one starts. State lives in GitHub rather than in memory, so a run that dies overnight can be resumed, and when something goes wrong there is exactly one item to look at.

The graph is inside a single item. A coordinator breaks the work down and dispatches sub-agents; the reviewer returns a structured verdict; blocking findings become fix-tasks that route back to the implementer and go round again until it comes back clean. Review feeding into implementation means there are real cycles in there, not a pipeline with extra steps.

Nothing about that topology is declared up front. The coordinator decides at runtime who to spawn, which vendor, and which model, based on what the work turns out to need. The deliberate trade is that the part which can surprise you is adaptive, and the part you have to debug at 2am is a loop.

## Requirements

- An omnigent server and a runner host that can reach it
- A GitHub repo to work on, and `gh` authenticated with push and merge rights
- At least two vendor CLIs installed and signed in (`claude`, `codex`)
- `git`, `jq`, `python3`, `flock`, `setsid`
- Branch protection on the target repo, with `bircher/cross-review` as a required status check

That last one matters. The runner posts `bircher/cross-review` only after a real cross-vendor PASS, so making it required is what actually prevents an unreviewed merge.

## Getting started

**1. Put the bundle where the runner can see it.**

```sh
git clone https://github.com/abedegno/bircher /workspaces/bircher
cd /workspaces/bircher
```

**2. Point it at your repo.** Every deployment value is an environment variable with a default; you will need at least these:

| variable | default | what it is |
| --- | --- | --- |
| `BIRCHER_REPO` | `abedegno/muesli` | the GitHub repo to work |
| `WORKDIR` | `/workspaces/muesli` | local checkout of that repo |
| `OMNIGENT_SERVER` | `http://omnigent:8000` | omnigent server URL |
| `BIRCHER_RECOVERY_REVIEWER` | `codex` | vendor used for out-of-band recovery reviews |
| `BIRCHER_INRUN_MERGE` | `1` | set `0` to review but never merge |
| `BIRCHER_NOOP_DIR` | `/workspaces/.bircher-noop` | coordinator signal files |
| `BIRCHER_BUNDLE_DIR` | derived from the script path | this checkout |

**3. Check the machine is actually ready.** This verifies both vendors can authenticate *and* that omnigent can launch a worker for each, which are different questions:

```sh
bash batch/run-queue.sh --preflight
```

**4. Queue some work.** Label GitHub issues `bircher:queued`. Write them as coherent slices with explicit acceptance criteria — an issue is a whole session, review and merge cycle, not a single step.

**5. Run a wave.**

```sh
bash batch/launch.sh
```

That detaches with `setsid` so the run survives the shell that started it, and refuses to start over a live run. Add `--foreground` to watch it.

Issues move `bircher:queued` → `bircher:running` → closed on merge, or `bircher:escalated` if the runner could not finish safely. Escalation is a normal outcome, not a crash: it means the run declined to merge something it could not verify.

## Commands

```
batch/launch.sh [--foreground] [--log FILE] [--source issues|queue]
    Start a wave. Detached by default.

batch/run-queue.sh --preflight
    Verify both vendors authenticate and can be dispatched through the harness.

batch/run-queue.sh --usage
    Print live provider quota and which vendor would be picked right now.

batch/run-queue.sh --recover-pr <code> <pr> [reviewer]
    Adopt an orphaned PR, run a real cross-vendor review, merge on PASS.

batch/run-queue.sh --self-test
    Run the built-in test suite. No network, no side effects.

batch/update-bundle.sh [ref]
    Update this checkout on the runner and re-run the self-test.
    Refuses to move a tree with uncommitted TRACKED changes.
```

> **First deploy only:** a runner whose checkout predates this script obviously
> cannot use it to fetch itself, and the same applies after any history rewrite.
> Bootstrap with `git fetch origin && git checkout -B main origin/main`, then use
> the script from then on.

## Layout

- `batch/run-queue.sh` — the sequential runner: CI gate, cross-review, in-run merge, recovery
- `batch/launch.sh` — detached start
- `batch/update-bundle.sh` — refresh the bundle on the runner
- `batch/issues-to-queue.sh` — render `bircher:queued` issues into queue files
- `batch/scorecard-summary.sh` — summarise a run from `.run/scorecard.jsonl`
- `skills/muesli-loop/` — the top-level procedure. **Project-specific example** — write your own for your repo
- `skills/cross-review/` — the different-vendor review step
- `config.yaml`, `agents/{codex,claude_code}/` — coordinator and sub-agent bundles
- `docs/run-forensics-runbook.md` — how to work out what a run actually did

## Adapting it to your project

`muesli-loop` is the part that knows about a specific codebase: its test commands, its CI checks, its conventions. Copy it, rewrite it for your repo, and point `config.yaml` at the new skill. The runner, the review step and the recovery machinery are project-agnostic.

## Known limitations

- The runner merges on branch protection, and does not itself block on checks that are not *required*. Anything you want enforced must be a required status check.
- Codex implementer sessions leave a thin forensic record, so after the fact you can audit the review more easily than the implementation ([#8](https://github.com/abedegno/bircher/issues/8)).
- One wave at a time per bundle. The runner takes a lock and a second run exits.

## Licence

Apache-2.0.
