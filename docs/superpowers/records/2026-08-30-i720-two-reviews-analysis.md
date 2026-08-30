# Analysis: the i720 run (muesli PR #739), and why two reviews disagreed

## Observed facts

Run log (22 lines), journal (31 facts), PR timeline, both review texts.

- 19:17:59  PR #739 created, author `abedegno`, branch `i720-pcm-flush`, 1 commit `55a3479`
- 19:24:38  coverage bot comment
- 19:27:07  comment: "Cross-vendor verified: implementer codex, reviewer claude_code
            (independent, read-only checkout)... zero blocking/non-blocking findings.
            Ready to merge." + `bircher-status: outcome=ready review=claude_code:pass`
- 19:37:08  comment: bircher's derivation review — one BLOCKING finding, VERDICT: FAIL
- outcome=failed, review=claude_code:fail, wall 1095s, issue labelled bircher:escalated

Journal: 31 facts, ALL confirmed. No `effect_uncertain`, no halt, no `command_rejected`.
No `merge_authorized` (correct — review failed). No `pull_request` effect: bircher did
NOT create the PR.

## Why there were two reviews

NOT a missing rule and NOT a violated one. `run_item` builds this prompt:

    IMPLEMENTER VENDOR DIRECTIVE: dispatch the implement sub-agent to codex;
    the cross-vendor reviewer MUST be the opposite vendor (claude_code).

The session bircher creates is a COORDINATOR. Bircher's own prompt instructs it to
arrange a cross-vendor review. It did exactly that and reported the result. Then the
derivation ran its own independent review.

This is a v1 leftover. In v1 the coordinator arranged the review and reported it via
the `bircher-status:` marker. C8 Phase 2 retired the marker and moved reviewing into
the derivation — but the PROMPT still asks the coordinator to arrange one. The
duplication is designed-in, by a directive nobody updated.

`agents/codex/config.yaml` DOES forbid a sub-agent from reviewing ("the orchestrator
ALONE owns the cross-vendor review"). That binds the codex IMPLEMENT sub-agent, not
the coordinator, which is told to arrange the review. `agents/v2_implementer` (which
forbids pushing entirely, `gate_pushes: true`) is NOT the bundle this flow uses — the
PR was pushed and commented, which that bundle makes impossible.

## Why the two reviews disagreed — UNRESOLVED

**My first answer here was wrong, and cross-review killed it.**

I claimed the difference was the prompt: that `v2/coordinator/review.py::_PROMPT`
encodes muesli #666 (release-on-failure paths) and #705 (green checks can be masked),
and the coordinator's reviewer had no such instructions.

`skills/cross-review/SKILL.md` contains the SAME guidance, in near-identical words
(lines 47, 52-53, 70, 75-77) — it is plainly the text `_PROMPT` was derived from. And
`skills/muesli-loop/SKILL.md` requires the coordinator to load it. **Both review paths
encode both scars.** The prompt difference I built the conclusion on does not exist.

What the matching output structure actually shows is that bircher's prompt shaped
bircher's review. It says nothing about what the other reviewer was told.

So the cause is unresolved. Viable explanations, none eliminated:

- model nondeterminism on a genuinely marginal call
- differing effective context or diff presentation
- the coordinator's reviewer never ran and the result was misreported (its claim that
  one ran is its own assertion; the omnigent session list came back empty)

Settling it needs the coordinator child-session transcript, which I have not recovered.

## Why there were two reviews — corrected

Also not what I first said. `run_item`'s vendor directive allocates the implementer and
the opposite reviewer; it does not CAUSE the review. `skills/muesli-loop/SKILL.md`
independently mandates the step-5 cross-vendor review and defaults the vendors when no
directive exists. Deleting or editing the directive would change nothing.

The mechanism is directly traceable and needed no inference from observed pushes:
startup calls `_upload_bundle "$BUNDLE_DIR"`, `AGENT_ID` comes from that uploaded root
bundle, and `run_item` creates the coordinator session with that ID. `v2_implementer` is
conclusively not on this path — for that reason, not because pushes happened.

## Removing the coordinator review would break repair, not just tidy up

`skills/muesli-loop/SKILL.md:150-151`: "Blocking issues become fix-tasks: implementer
fixes, CI re-greens, re-review. Bound to 3 rounds."

**The coordinator's review IS the autonomous repair loop.** Bircher's derivation review
runs after that session has ended and can only classify: `observe.py:115` turns
`reviewer:fail` into outcome `failed`, terminal. Removing the coordinator's review would
convert every repairable finding into a failed run and strand more PRs, which is the
opposite of what this programme needs.

My recommendation to "remove the coordinator's, keep bircher's" was therefore wrong. The
real question is which layer owns review-and-repair, and moving the bounded fix loop
with it — a design question, not a deletion.

## And the marker is REQUIRED, not merely tolerated

`skills/muesli-loop/SKILL.md:167` instructs the coordinator to emit:

    bircher-status: outcome=<...> ci=<...> review=<vendor>:<pass|fail> rounds=<n> ...

For two days I described criterion 1's partial as "the bundle prompt already forbids it
and prompt wording is not a mechanism". That was wrong in both directions: nothing
forbids it, and the skill actively REQUIRES it. The marker keeps appearing because the
coordinator is told to produce one. Retiring it means editing that skill, which is a
concrete, findable change rather than a wording problem.

## Standing on the observed facts

Everything in the Observed Facts section above is verified from the log, the journal,
the PR API and the two review texts, and none of it changed under review.

## What I got wrong, in order

1. "The v2_implementer bundle is missing the prohibition" — that bundle is not used.
2. "The rule was present and violated" — the coordinator was told to arrange a review.
3. "The prompts differ, and that explains the verdicts" — they do not differ.

Three wrong answers to one question, each delivered with more confidence than the
evidence supported. The pattern is the same each time: I found a mechanism that WOULD
explain the observation and stopped looking, instead of checking whether the alternative
explanation was also present in the repository.
