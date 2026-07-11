---
name: cross-review
description: Verify an implementer's PR with an INDEPENDENT, different-vendor sub-agent that reads the real branch read-only and emits a VERDICT; turn blocking issues into fix-tasks and loop until clean.
---

# cross-review — independent verification

The implementer never signs off on its own work — a different model does, and
review is a sub-agent that returns a structured report, not a transcript
anyone needs to read through.

> **INTEGRITY (non-negotiable).** A valid cross-review is a DIFFERENT-vendor
> coding sub-agent (`claude_code` / `codex`) dispatched via `sys_session_send`
> by bircher (the orchestrator), that is READ-ONLY (it never edits, commits, or
> opens/updates a PR) and that reads the ACTUAL code under review. Independence
> comes from a different model judging and not editing — NOT from withholding
> the code. A "review" produced any other way — by the same vendor as the
> implementer, by the implementer reviewing its own diff, or via the generic
> `Agent` / sub-agent tool — is INVALID; discard it. If a different-vendor
> worker genuinely cannot be dispatched (only one vendor is available), you
> CANNOT cross-review: escalate to the human (`outcome=escalated`) and say why.
> NEVER substitute a same-vendor or `Agent`-tool review to make the gate appear
> to pass.

## Procedure

1. Run the deterministic gates first — tests / lint / typecheck via
   `sys_os_shell`. If red, re-dispatch the implementer to drive it green first;
   don't involve the reviewer yet.
2. Dispatch a DIFFERENT-vendor sub-agent as reviewer (Claude built it
   (claude_code) → review with `codex`; Codex built it (codex) → review with
   `claude_code`). Use a task-based title such as `review-<task_slug>`, never
   the raw vendor name. The reviewer reads the REAL code (not a diff excerpt):
   its `input` instructs it to
   `export PATH=/root/bin:$PATH`, then
   `git fetch origin pull/<PR>/head && git worktree add --detach /tmp/review-<PR> FETCH_HEAD`,
   `cd /tmp/review-<PR>`, read the changed files AND their surrounding context,
   run the gates it can — prefixing EACH gate command with
   `export PATH=/root/bin:$PATH &&` in the same shell call, since the reviewer's
   shell may not persist env between calls (`export PATH=/root/bin:$PATH && go
build ./...`, `... && go vet ./...`, client `... && npm run typecheck` / `...
&& npx vitest run <touched>`, plugin `... && pytest`; DB-backed `go test`
   needs a DB the runner lacks, so trust the PR's green CI for those) —
   and NEVER edit/commit/open a PR (read-only). Pass the PR number + the
   acceptance contract. Example:
   `sys_session_send(agent="claude_code"|"codex", title="review-<task_slug>",
args={purpose: "review", input: "Review PR #<PR> against this contract:
<contract>. First: export PATH=/root/bin:$PATH; git fetch origin
pull/<PR>/head; git worktree add --detach /tmp/review-<PR> FETCH_HEAD; cd
/tmp/review-<PR>. READ the changed files and enough surrounding code to
verify each contract point — do NOT judge from the diff alone. Run the gates
you can, each as ONE command prefixed with `export PATH=/root/bin:$PATH &&`
(e.g. `export PATH=/root/bin:$PATH && go build ./...`) since your shell may not
persist env between calls. You are READ-ONLY: never edit/commit/open a PR.
Skills are NOT available in your session (do not call load_skill; this prompt
is your complete instructions). Report blocking / non-blocking / suggestion
findings, then a FINAL LINE that is exactly VERDICT: PASS or exactly
VERDICT: FAIL. Put findings BEFORE the verdict so the
verdict is the last line even if output is long."})`.
   NEVER set a model/model_override in the dispatch args — use the worker's
   harness default. (Run #12: a coordinator-invented `gpt-5.1-codex-max`
   override drew a provider 400 "not supported with a ChatGPT account" twice
   before a re-dispatch without exotic overrides succeeded.)
   Emit the `sys_session_send` call in the SAME turn you decide to review —
   never end a turn having only announced "I'll review" with no tool call (that
   dropped turn stalls the run; nothing dispatches and no inbox wake arrives).
   Once the reviewer dispatch is in flight, end your turn; collect the
   inbox-delivered report with `sys_read_inbox` when it returns and read its
   final `VERDICT:` line. Use `sys_session_get_history` only to debug an empty
   or unclear review result.
3. The reviewer SURFACES issues; it does not fix them.
4. For each **blocking** issue: add a fix-task to the registry scoped to the
   same worktree, and send the concrete fixes back to the SAME implementer
   conversation via `sys_session_send` — reuse the original implementer's
   `agent` + `title` (or address it by `session_id`) with
   `purpose: "implement"`, so the worker keeps its worktree/branch context and
   updates its existing PR. A new title would spawn a fresh worker with no
   memory of the task. Then loop to step 1.
5. When gates are green AND there are zero blocking issues, the PR passes
   review — mark it ready in the registry (with its PR URL). bircher (the
   coordinator) does NOT merge it; the batch runner auto-merges ready PRs
   under this same gate, or the human does.
6. If the contract can't be satisfied after a few loops, stop and escalate to
   the user with specifics.

## Notes

- Cross-review requires a reviewer from a DIFFERENT vendor than the implementer,
  so it needs at least two AVAILABLE workers (per bircher's roster preflight). If
  only one worker — or only one vendor that can review this implementer's PR —
  is available on the machine, you CANNOT run independent cross-vendor review:
  don't dispatch a reviewer that can't boot, say so explicitly, and pull in the
  human at the plan gate.
- The reviewer reads its OWN read-only checkout of the PR branch (fetched via
  its shell), plus the contract — never the implementer's transcript or the
  implementer's worktree. Independence is preserved by using a different vendor
  and never editing, not by withholding the code.
- Review is a coding sub-agent (`claude_code`/`codex`) dispatched with
  `purpose: "review"` — a DIFFERENT vendor from the one that built the diff. It
  reports issues and never edits; only the implementer opens a PR, so a stray
  reviewer edit never reaches the deliverable.
- Non-blocking issues / suggestions go in the registry as follow-ups; they
  don't block the PR.
