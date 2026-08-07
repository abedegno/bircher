# Security

## What this software does

Bircher runs AI coding agents against a real repository and **merges their work automatically**. Before running it anywhere that matters, understand what that means:

- It holds, or can reach, credentials for at least two AI vendors.
- It has push and merge rights on the target repository.
- It executes code written by an AI agent, on your machine, without a human in the loop.
- It runs unattended, typically overnight.

Treat a Bircher deployment as you would a CI runner with write access, because that is what it is.

## Reporting a vulnerability

Open a GitHub Security Advisory on this repository (Security → Report a vulnerability), or a normal issue if the problem is not sensitive. This is a small personal project rather than a funded one — expect a best-effort response, not an SLA.

Please do report:

- Any way the cross-review gate can be bypassed so unreviewed code merges.
- Credential leakage into logs, scorecards, PR comments or uploaded bundles.
- Anything that lets a crafted GitHub issue cause the runner to execute unintended commands. Issue bodies become agent prompts, so this is the most interesting attack surface here.

## Running it safely

**Give it its own credentials.** A dedicated GitHub account or fine-grained token scoped to one repository, not your personal token. Revoking it should not cost you anything else.

**Make the review gate a required status check.** The runner posts `bircher/cross-review` only after a genuine cross-vendor PASS, but *the runner does not enforce it* — branch protection does. If that check is not required, nothing stops an unreviewed merge.

**Curate what gets queued.** Only issues labelled `bircher:queued` are picked up, and that label is the trust boundary. An issue body is a prompt: someone who can label issues in your repository can direct the agent. In a repository that accepts outside issues, do not let outsiders apply that label.

**Sandbox the runner.** Run it in a container or VM with only the credentials it needs. Bircher assumes an omnigent runner host, which is a reasonable place to enforce that.

**Read the scorecard.** `.run/scorecard.jsonl` records the outcome, reviewer and verdict for every item. An unexplained `escalated` is worth reading rather than re-queueing blindly.

## Known limitations

These are design trade-offs rather than bugs, and you should decide whether you accept them:

- **Merge enforcement is delegated to branch protection.** The runner does not itself block on checks that are not *required*. Anything you want enforced must be a required status check.
- **Codex implementer sessions leave a thin forensic record** ([#8](https://github.com/abedegno/bircher/issues/8)), so after the fact the review is easier to audit than the implementation.
- **Agents run with whatever permissions the harness grants them.** Bircher does not add a sandbox of its own; it inherits omnigent's.
