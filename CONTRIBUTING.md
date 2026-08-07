# Contributing

Bircher is a reference implementation of an autonomous dev loop, kept deliberately small. Contributions are welcome, particularly from anyone who has actually run it — the interesting bugs here only appear during an unattended overnight run.

## Before you open a PR

**Run the self-test.** It needs no network and has no side effects:

```sh
bash batch/run-queue.sh --self-test
```

**Run it on Linux.** Some cases depend on GNU tooling. macOS ships BSD `sed` and bash 3.2, and the difference is not academic — a regex using `\|` alternation parsed fine on Linux and silently matched nothing on macOS, which cost a debugging round.

## What a good change looks like

**Add a self-test case for behaviour you rely on.** Especially where the failure would be silent. The suite is deliberately paranoid about cases that pass when broken: a note truncated mid-word still looks like a note; a merge that skipped its safety pin still looks like a merge.

**Test the failure direction, not just the success.** A guard that never fires is indistinguishable from a guard that does not work. Where practical, prove the thing goes red before proving it goes green.

**Keep comments about *why*.** This codebase carries a lot of hard-won context — which GitHub API returns which shape, why a check fails closed, which idle states are benign. When you change something with a comment explaining a past failure, either keep the explanation accurate or say what replaced it.

**Prefer a pure helper you can test** over logic embedded in a long function. Several recent fixes became testable only after the decision was pulled out into a function that takes values and returns a string.

## What to be careful with

`skills/` files are instructions to language models, not documentation. Prose changes there alter agent behaviour, so treat wording as functional: an ambiguous sentence in `cross-review/SKILL.md` becomes an ambiguous review.

`muesli-loop` is a project-specific example. If your change makes it more muesli-specific, it probably belongs in your own fork of that skill instead.

## Reporting bugs

The most useful report includes the runner log, the relevant `.run/scorecard.jsonl` row, and what you expected instead. `docs/run-forensics-runbook.md` describes how to work out what a run actually did.

If the bug is that Bircher merged something it should not have, say so plainly and treat it as a security report — see [SECURITY.md](SECURITY.md).
