# Front half — the live log

What actually ran on the runner, in order, with the ids to look it up by. E1 is the
deployment precondition; E2 to E4 are the three live exercises the design's §8 proof
is run over.

## E1 — the deployment preconditions

*2026-09-08.*

### The codex-under-Landlock probe

The question was whether the codex harness can run at all inside the `linux_landlock`
sandbox the v2 author bundle declares, with only its model provider reachable. If it
cannot, every codex-authored round is impossible and the cross-vendor claim goes with
it, so this is probed before anything is queued.

| | |
|---|---|
| Bundle | `agents/v2_author_codex` (`harness: codex`, `model: gpt-5.6-sol`) |
| Holder session | `6dc645316bc14ae4ae7f62dc1314d100` |
| Agent | `c167fd8944894d77974c435f0b688a7b` |
| Probe session | `d6c30fff3e8441cb9e991346531773df` |
| Workspace | `/workspaces/probe-codex`, a detached worktree of `bircher-smoke` at `25a979e` |
| Instruction | create the directory `bircher`, write the single line `hello` to `bircher/artifact.md`, end the turn |
| Result | the file exists and holds exactly `hello\n`, six bytes; the session went `running` then `idle` |

**No egress rule needed correcting.** The bundle's declared list — `api.openai.com/**`
and `chatgpt.com/**` — was enough for the harness to boot, take its turn and write the
file. The proxy denied nothing, so `agents/v2_author_codex/config.yaml` is unchanged.

The probe also settles a smaller question the design leaned on: a session created
against a worktree path writes into that worktree and nowhere else. The write landed
inside `/workspaces/probe-codex`.

### The turn timeout

`HARNESS_TURN_TIMEOUT_S` on `omnigent-runner-bircher` was 480, an idle watchdog set for
v1's per-item turns. A v2 author or reviewer turn is a whole session the coordinator
waits on and ends itself, under a deadline it records in its own journal. A watchdog
shorter than that deadline ends the turn underneath the coordinator, which then reads a
file the session never finished writing — so the two bounds must not compete, and the
coordinator's cap is the one that should bite.

Raised to 5400, equal to `ITEM_TIMEOUT`, in `abedegno/homelab` at
`docker/omnigent/docker-compose.yml`, and the stack redeployed through Portainer.
Only `omnigent-runner-bircher` was recreated; the server, the second runner, postgres
and cloudflared were untouched. Confirmed from inside the container:

```
HARNESS_TURN_TIMEOUT_S=5400
OMNIGENT_RUNNER_ENV_PASSTHROUGH=…,HARNESS_TURN_TIMEOUT_S,…
```

The second line matters as much as the first: the runner strips any variable not in
that allowlist before it reaches an agent shell, so a raised timeout that is not
threaded through it never reaches the harness that reads it.

### How the branch reached the runner

`/workspaces/bircher-v2` was a clone of the older `v2` branch. It is now on
`feat/front-half`, fetched from a git bundle copied into the container rather than
from GitHub: the branch is unpushed, and deploying a test checkout is not a reason to
publish 53 commits. `/workspaces/bircher` is untouched and still runs v1 from `main`.

## Pre-registered

*Written 2026-09-08, before E3 and E4 ran.*

The vague-issue list exists so the front half cannot be credited for work the issue
already specified. An issue qualifies only if its body names no file, no function and
no acceptance test — if the spec phase had nothing to decide, a spec that reads well
proves nothing.

`docs/design/preregistered.txt` holds the numbers, and the proof script reads it with
`--issues`, refusing a run whose issue is not on the list. The ten open muesli issues
that qualify: 1, 4, 5, 6, 7, 8, 9, 10, 11, 12.

Two open issues were excluded on purpose. Issue 565 says in its own body that it is
not queued for autonomous work. Issue 756 is a build failure naming the failing job
and a run URL, which is the opposite of vague.

E3 takes issue 11, a design question about smart lists against folders. E4 takes
issue 12, the workspaces and teams boundary. Both are one-sentence design prompts
whose deliverable is a document, which keeps the first unattended merges small.

## E2 — `abedegno/bircher-smoke` under `(human, {spec})`

*2026-09-08. Issue #20, one deliberately vague sentence asking for a way to see
what has been exercised.*

Two runs. The first reached the grill, the gate and the plan phase and was
cancelled at `budget_exhausted` after its journal filled with the consequences
of a defect since fixed: three `human_direction` facts that were not the
human's, a dozen seats spent on rounds nobody reviewed, and two grants I made
on a false reading of what had happened. Evidence contaminated by a bug is
worth less than a clean second attempt, so it was cancelled through the
operator command and the database removed.

The second run reached the grill park and stopped there when attention moved
to E3 and E4. What both runs together demonstrated, live, is most of the front
half's machinery:

| Mechanism | Observed |
|---|---|
| The grill asks | six questions from a one-sentence issue, each recorded as a fact |
| The human answers | one `human_answer` naming every open question id |
| The conversation continues | the SAME session re-prompted, not a fresh one |
| Cross-vendor rotation | claude authored, codex reviewed, codex authored the revision |
| The spec gate | parked at `spec_accepted`, approved, advanced to `specified` |
| Publication | the accepted spec posted to the issue as a comment |
| A direction displaces | a direction typed into a live turn ended it and started a fresh round |
| Four kinds of park | grill, gate, `budget_exhausted`, `identical_resubmission` |
| The loop spinning | a plan resubmitted verbatim was refused, not accepted |
| Cancellation | the operator command retired the run and its sessions |

The questions were the interesting part. A one-sentence issue produced: what
counts as one exercise, where the file lives, whether it absorbs the existing
marker files, which fields each row needs, whether to backfill by inference,
and whether automatic appending is in scope. The reviewer then caught an
inconsistency in the ANSWERS: a "date merged" column cannot describe an
abandoned run. That correction is in the accepted spec.

## E3 — muesli under `(model, {spec})`

*2026-09-08. Issue #11, "Design: lists-vs-folders mental model", one sentence,
no file named. Run `i11-design-lists-vs-folders-mental-model-1788863221`.*

| | |
|---|---|
| Policy | `grill=model`, `gates={spec}`, `max_rounds=3`, `max_seats=16` |
| Spec | 3 rounds: rejected by codex, rejected by claude, accepted by codex |
| Gate | parked; approved by the human; advanced to `specified` |
| Plan | 5 rounds, 4 rejections, parked `bound_exhausted` |
| Human facts | 1 answer (misfiled, see finding 11), 2 rulings, 0 directions, 4 parks |

The spec converged with no questions asked of anyone: under `grill=model` the
author ruled its own. The plan did not converge. The rejections were not
nitpicks — the last one traced three muesli source files and showed that the
plan's focus-restore behaviour would steal focus from the create buttons and
overflow menus sitting in the same row, because it exempted only the two
disclosure triggers it knew about.

## E4 — muesli under `(model, {})`, unattended

*2026-09-08. Issue #12, "Design: workspaces/teams boundary", one sentence.
Run `i12-design-workspaces-teams-boundary-default-1788871358`.*

| | |
|---|---|
| Policy | `grill=model`, `gates={}`, `max_rounds=5`, `max_seats=40` |
| Spec | 2 rounds: rejected by codex, accepted by claude |
| Plan | 6 rounds, 5 rejections, parked `bound_exhausted` |
| Human facts | **zero** answers, rulings and directions; 1 park |

The raised bounds came from a Project config created for this run, after E2
and E3 both stopped at three rounds. They did not change the outcome: the plan
failed at five rounds as it had at three.

**This is the run that answers the claim, and the answer is half.** A vague
issue reached an accepted, published, cross-vendor-reviewed spec with no human
in the loop at all. It did not reach `planned`, so it did not reach a merged
pull request. The front half's spec phase works unattended; its plan phase
does not.

## E5 — the rotation experiment, muesli issue 12 again

*2026-09-08, evening. `BIRCHER_AUTHOR_ROTATION=fixed`: the phase keeps its
author; the reviewer, being the other vendor, is therefore fixed too.
Run `i12-design-workspaces-teams-boundary-default-1788882864`.*

| Spec round | Author | Draft | Reviewer | Verdict | Findings |
|---|---|---|---|---|---|
| 1 | claude | 19 KB | codex | revise | 3.7 KB |
| 2 | claude | 36 KB | codex | revise | 3.9 KB |
| 3 | claude | 50 KB | codex | revise | 1.8 KB |
| 4 | claude | 64 KB | codex | revise | 1.8 KB |
| 5 | claude | 77 KB | codex | revise | 2.0 KB |
| 6 | claude | 92 KB | codex | — | bound exhausted |

A clean negative. The same issue passed its spec in two rounds at 15 KB under
rotation; with a fixed author it grew by about 14 KB a round and never passed.
Every finding became an addition, nothing was ever cut, and a fresh reviewer
kept finding real contradictions in an ever-larger document — the last review
found a revocation race and a projection leaking metadata beyond the
document's own stated grant. Accumulation without a way to refuse a finding is
a growth spiral, and rotation was doing real work for specs by forcing each
round to consolidate.

## E6 — the disposition experiment, muesli issue 12 again

*2026-09-08, evening. `BIRCHER_REVIEW_DISPOSITIONS=on`, rotation back on.
Run `i12-design-workspaces-teams-boundary-default-1788888080`.*

Three changes, borrowed from the loop that works elsewhere
(chaseai-yt/claudex-loop), none of them to the kernel's rules: findings carry
a severity and FAIL needs a high or medium one; a revision ends with a
`## Dispositions` section answering every prior finding by number, accepted
with what changed or rejected with why, inside the artefact so it is hashed
and reviewed; and the reviewer's brief carries the previous round's findings
with the instruction not to re-raise what was resolved, and to re-raise a
rejection only with new evidence.

| Round | Author | Draft | Reviewer | Blocking (high+medium) | Verdict |
|---|---|---|---|---|---|
| spec 1 | claude | 24 KB | codex | 6 | revise |
| spec 2 | codex | 16 KB | claude | 1 | revise |
| spec 3 | claude | 19 KB | codex | 1 | revise |
| spec 4 | codex | 16 KB | claude | 0 | **accept** |
| plan 1 | codex | 19 KB | claude | 1 | revise |
| plan 2 | claude | 23 KB | codex | 1 | revise |
| plan 3 | codex | 19 KB | claude | 2 | revise |
| plan 4 | claude | 20 KB | codex | 1 | revise |
| plan 5 | codex | 19 KB | claude | 0, two low | **accept** |

**Both phases converged, with zero human facts in the journal.** No park, no
answer, no ruling, no direction. Draft sizes stayed between 16 and 24 KB
throughout. The remaining findings each round were new and specific, not
relitigation — undefined pagination, then unfiltered soft-deletes — and every
one was dispositioned as accepted with a concrete change. The last plan
review carried two low findings and passed, which is the severity rule doing
what it was written for.

`python -m tools.prove_front_half --issues docs/design/preregistered.txt`
over this run prints **`PROOF: PASS`** — the done criterion of *The claim* —
after one fix to the proof itself (finding 13).

**Then the existing back half took over**, implemented the accepted plan,
pushed branch `i12-team-sharing`, and opened muesli PR #757: 5,297 additions.
Its session then died with a runner error, the Claude subscription's five-hour
limit, while watching CI. The Go job on that PR is red and the cross-vendor
review never ran, so the PR is open and unmerged. That is the v1 pipeline's
failure, on cost and a failing test, after the v2 front half had done its job.

## E7 — issue 11 under dispositions, to a merged pull request

*2026-09-08 22:41 to 2026-09-09 00:17. Muesli issue 11, "Design: lists-vs-folders
mental model", labelled autonomous. `BIRCHER_REVIEW_DISPOSITIONS=on`, rotation on,
five rounds, forty seats. Run `i11-design-lists-vs-folders-mental-model-1788902841`.*

| Round | Author | Draft | Reviewer | Blocking (high+medium) | Verdict |
|---|---|---|---|---|---|
| spec 1 | claude | 13 KB | codex | 3 | revise |
| spec 2 | codex | 10 KB | claude | 1 | revise |
| spec 3 | claude | 13 KB | codex | — | **no verdict** (finding 15) |
| spec 3, retried | claude | 13 KB | codex | 1 | revise |
| spec 4 | codex | 12 KB | claude | 0, four low | **accept** |
| plan 1 | codex | 15 KB | claude | 0, four low | **accept** |

Then the existing back half implemented the plan, opened muesli PR #758, watched CI
go red on two of its own new tests — a Go test for the folder resolver, and a
renderer test batching 301 ids that timed out — pushed a fix at 23:07, passed the
Codex cross-review, and merged at 00:17. **Vague issue to merged pull request, with
one human fact in the journal**: a `retry` I posted after the stall, against a
defect fixed the same night. `prove_front_half --expect-human` prints
`PROOF: PASS`; E6 still passes in zero-touch mode under the corrected proof.

The stall is finding 15. The codex reviewer had a real finding in its last message
and a **zero-byte** `review.md` on disk beside a `.write-check` probe: the poll saw
the path exist, ended the turn, and stopped the session before the content landed.
A watched file now counts as present only with bytes in it. Findings 16 and 17 were
in the proof itself and were exposed by this being the first *parked* run to pass:
a park prompt is not an awaited turn and earns no `turn_ended`, and a person's
message the coordinator read past is not an unrecorded prompt.

## E8 — the shaping phase on `abedegno/bircher-smoke`, epic to closed umbrella

*2026-09-10 08:38 to 09:49 UTC. The shaping phase (spec
`docs/superpowers/specs/2026-09-09-shaping-phase-design.md`, merged as bircher
PR #94 at `8028068`) deployed to the runner by fast-forward. Smoke epic #21,
"Keep a record of which exercises have been completed": three coherent parts, a
store, an API and a page, naming no file. Kernel database `.run/kernel-e8.db`,
run `i21-keep-a-record-of-which-exercises-have-be-1789029708`.*

| Step | When (UTC) | What the journal and GitHub show |
|---|---|---|
| shaping round | 08:41 to 08:44 | the author ruled an epic and wrote a three-slice plan (hash `35e3962b`); the reviewer accepted; the run parked at the **slices** gate; the notice on #21 named the phase and the carrier session |
| approval | 09:10 | `approve`, posted into the carrier session on the person's behalf, read by a hand-launched wave: `human_ruling {approve, slices}`, transition to `sliced` |
| filing, pass 1 | 09:12 | children #22, #23, #24 created with `bircher:slice` and the rendered bodies; `slice_filed` with the read-back database ids |
| filing, pass 2 | 09:12 | the first blocked-by link **failed with HTTP 422**: `-f issue_id=` sent a string, the endpoint wants an integer (finding 18). The effect became `uncertain`, the run halted at `sliced`, the queue file stayed, the scorecard row said escalated |
| repair | 09:35 | the composer changed to `-F` (bircher PR #95), the uncertain link reconciled as not delivered at version 13, the wave relaunched |
| filing, passes 2 to 4 | 09:37 | links (#23 by #22, #24 by #23), `bircher:queued` on each child, the umbrella comment `bircher: sliced 35e3962b`, `bircher:sliced` on the parent, `filing_complete {22, 23, 24}`; scorecard row `sliced` |
| the sweep | 09:49 | the three children closed by hand; a wave's sweep recorded the closures, posted `bircher: sliced complete` naming each child closed, closed #21, ended the run `sliced` |

`prove_front_half --repo abedegno/bircher-smoke --expect-approval`: the four
slice assertions pass; the one failing line is "the run was reconciled or
halted", which is true and is the point of the line. This run is not
zero-touch; a fresh epic on the merged fix would be. The gate worked the way
§4 says: nothing appeared in an inbox, the notice on the issue and the
message in the carrier session were the two channels, and the reply was read
by the next wave.

## E9 — muesli #12, a one-piece ruling to a merged pull request

*2026-09-10 09:44 to 20:55 UTC. Muesli #12, "Design: workspaces/teams boundary
+ default-private + shareable folders", labelled `bircher:queued` under the
default policy. Run `i12-design-workspaces-teams-boundary-default-1789033898`,
picked up by the scheduled wave at 09:50.*

The shaping author ruled the issue **one piece**: "a design decision … the
deliverable is a single design document", with a cost-if-wrong clause
promising the spec phase would hand back a re-shape if it found two surfaces.
The spec took two rounds, the plan three, and the implementer built the whole
feature: muesli PR #759, forty files, +2719/−297, a migration, store, API and
client. Nothing in the design gave the spec phase the hand-back the ruling
counted on, and a one-piece ruling has no gate of its own (finding 20).

| Step | When (UTC) | Outcome |
|---|---|---|
| shaping → spec gate | 09:50 to 10:09 | one-piece ruling; spec accepted after one revision; parked at the **spec** gate |
| approval | 11:10 | `approve` posted on the person's behalf; the plan accepted after two revision requests; `planned`, `implementing` by 11:43 |
| implementation | 12:53 | PR #759 opened; Go tests red, repaired to green |
| cross-review | 13:10 to 14:08 | **FAIL three times without a review**: the reviewer's setup line ran `rm -rf /tmp/review-…`, which omnigent 0.9.0's `blast_radius` guardrail rejects before execution; both repair rounds spent on it (finding 19); the run ended `failed` |
| harness fix | 18:35 | the setup line moves a stale worktree aside instead of deleting it (bircher PR #96); `--recover-pr … 759 codex` re-adopted the run |
| cross-review | 18:40 | **PASS** on `47f05f8` at the first attempt; `bircher/cross-review` posted and verified; every required check green |
| merge | 20:55 | the runner declined to merge an ended run and left the PR to the person; merged by hand as `4985598`; #12 closed |

The pipeline's verdict was earned, but the shape was wrong: this is the
epic-sized change the shaping phase exists to slice, and it went through as one
piece because the only judgement on a one-piece ruling is the author's own.

### What the shaping proof found

Three defects, none reachable from a suite that now stands at 1616 tests.

18. **The link's `issue_id` was a string.** `gh api … -f issue_id=<id>` sends
    a string field; GitHub's dependencies endpoint requires an integer and
    answers 422. The test fake accepted the string, the same unfaithful
    reproduction as before. Fixed by `-F` in the composer, a contract rule that
    admits only `-F`, a binding that refuses `-f`, and a fake that refuses the
    string form with GitHub's own message (bircher #95).
19. **The reviewer's own setup was denied.** The out-of-band review prompt
    asked the reviewer to `rm -rf` its scratch worktree; the guardrail treats
    `rm -rf /…` as catastrophic and rejected it before execution, and each
    attempt derived as FAIL rather than BLOCKED. Fixed by removing, pruning,
    and moving any leftover aside, in the bash prompt and its byte-identical
    Python twin, with the self-test pinning the absence of `rm -rf` (bircher #96).
20. **A one-piece ruling has no gate and no hand-back.** By the brainstorm's
    decision only slice plans are reviewed and gated; a one-piece ruling sends
    the run straight to the spec phase, and no command lets the spec author
    return it to shaping. The remedy is a design change, recorded as a gap
    in the spec's amendments and in ARCHITECTURE §9.

## What the plan phase does

Three runs, two repositories, two policies, two round limits:

| Run | Loop | Spec rounds to accept | Plan rounds | Plan accepted |
|---|---|---|---|---|
| E2 (smoke, human grill) | rotate | 2 (first run) | 4 | no |
| E3 (muesli, 3-round bound) | rotate | 3 | 5 | no |
| E4 (muesli, 5-round bound) | rotate | 2 | 6 | no |
| E5 (muesli, fixed author) | fixed | never (6, growing) | — | — |
| E6 (muesli, dispositions) | rotate + dispositions | 4 | 5 | **yes** |
| E7 (muesli #11, dispositions, autonomous) | rotate + dispositions | 4 | 1 | **yes — and merged** |

Specs converge in two or three rounds every time. Plans converge never. Since
raising the bound changed nothing, "plans need a little more room" is ruled
out. Three hypotheses remain, and they are distinguishable by reading the
rounds: reviewers hold plans to a standard no author reaches; authors do not
absorb findings between rounds (E2 resubmitted a plan verbatim, including the
defect it had just been told about); or the plan format demanded of a design
issue is wrong for the work, since the plan skill asks for a failing test, the
code and the commit, and a design document has none of those.

E5 and E6 settled it. Rounds were not the lever (E4 at five rounds failed
like E3 at three), and neither was a fixed author (E5 grew without bound).
The lever was letting an author refuse a finding on the record and telling
the reviewer not to relitigate what was resolved. With that, the same epic
issue that failed three times passed both phases in nine rounds unattended, and the smaller issue that had failed at five plan rounds passed in five, then went all the way to a merged pull request.

## What the live runs found

Seventeen defects across the two days, none reachable from a test suite that
now stands at 1439 tests, because the tests were written from the same
assumptions as the code. All seventeen closed by the morning of 2026-09-09,
the last three under the same commit that made dispositions the default.

| # | Finding | State |
|---|---|---|
| 1 | The coordinator read its own stop as a human direction: every interrupted turn's "[System: interrupted]" notice was a user-role item | fixed |
| 2 | Two skill files lacked YAML frontmatter, so every bundle upload 400'd and no wave could start | fixed |
| 3 | The runner never passed the target repo to its queue generator: a wave aimed at the smoke repo would have drained muesli's backlog | fixed |
| 4 | The skill said the author MAY ask under `grill=human` where the kernel says it MUST; the loop had no handler for the refusal | fixed |
| 5 | A missing label halted a run mid-edit, leaving the issue with neither label | fixed |
| 6 | Preflight did not check the label vocabulary it would set | fixed |
| 7 | A resumed author was not told its draft had been moved aside, so it wrote nothing and the coordinator polled an empty path for the full cap | fixed |
| 8 | A park is invisible: omnigent's inbox counts elicitations, which nothing raises | fixed — the park now comments on its issue |
| 9 | The notice did not say a reply is read by the next wave, not when it is sent | fixed |
| 10 | Under `grill=model` the model's own questions made every human message an "answer", so a gate approval was eaten and the gate was unreachable on the DEFAULT policy | fixed |
| 11 | A not-delivered effect is never retried: its key names the run and the label, so it can only ever be attempted once | fixed — the key carries the generation, so the next pass attempts it afresh |
| 12 | A parked run can become unreachable from the runner — the queue file is consumed and the issue loses its label, so neither source finds it | fixed — the queue generator asks the journal: an open run with a current park is queued whatever its labels say |
| 13 | The human's control channel is a live agent session: every reply wakes a model, which echoes, costs a turn, and could act | fixed, prompt-side — the park prompt opens by telling the model it and any reply are not for it. Checked live on 2026-09-09: a message to a STOPPED session revives it and gets a reply, so stopping the carrier before the park would prevent nothing; the prompt is the whole fix |
| 14 | The proof read the server's cancellation notice as an unrecorded prompt, failing the first run that converged unattended on four sessions | fixed |
| 15 | An empty watched file counted as present: a reviewer's zero-byte review file ended its turn before the content landed, parking an unattended run with no verdict | fixed |
| 16 | The proof held a park prompt to the seat's rule and demanded a `turn_ended` nobody awaits | fixed |
| 17 | The proof read a person's message the coordinator had already read past as an unrecorded prompt | fixed |

Four of them are one shape: a condition testing a proxy rather than the thing
it meant. Unanswered questions stood in for "the human owes an answer". The
phase's newest prompt stood in for "the awaited turn". A live process stood in
for "work is happening". A user-role message stood in for "a person spoke".
Each was right in the case it was written against and wrong in a neighbour.

## Verdict

The kernel did its job throughout. Every refusal in these runs was correct,
including the ones that cost hours: the spec submitted before the human had
answered, the plan resubmitted unchanged, the outcome the runner tried to
write over a run the coordinator owned. Nothing was corrupted, and every
recovery was possible from the journal.

The claim of *The claim* is met twice. E6: a pre-registered vague epic to an
accepted spec and plan with zero human facts, `PROOF: PASS`. E7: a smaller
pre-registered issue through the same front half and on through the existing
back half to a **merged pull request**, with one human fact in the journal — a
retry against a defect fixed the same night — and `PROOF: PASS` in the mode
that admits it. Both took a mechanism the spec did not have, the disposition
step, which is still behind a flag. Two samples on two shapes of work,
against three failures without it: that is the case for making it the default,
and it is the next decision.

## E10 — muesli #1, the spec author's hand-back, live

*Pre-registered 2026-09-15, before the run. Revision 16 merged as bircher
PR #99 (`5e1a4d0`) and deployed to `/workspaces/bircher-v2` on
`omnigent-runner-bircher` at about 21:30 UTC by a fast-forward pull of `main`
(`eacb42e` to `5e1a4d0`); the wave loop was not touched by the branch and
spawns the scripts fresh each cycle. Muesli #1, "Recipes -- pre/during/after/
cross-meeting prompts", labelled `bircher:queued` at 21:31 UTC. The loop fires at
about :03 and :33 UTC (not the :20/:50 recorded when it was recreated on
09-09; the phase has drifted), so the ~21:33 wave is both the first on the new
code and the one that picks #1 up. The 21:03 wave, the last on the old code,
found nothing queued. A first draft of this entry carried wrong times; this is
the correction, made before any outcome was known.*

**Why #1.** It is on the pre-registered list and its body names no file, no
function and no acceptance test. It has E9's shape exactly: one noun over three
named surfaces (pre-meeting briefs, live prompts, cross-meeting analysis), with
the body's own "Big; overlaps templates + chat" as the kind of hint a fresh
perspective acts on and an anchored shaper rationalises past. #7 was not chosen
because its body says it "gates several design calls" and a shaper will slice
it unprompted, which proves E8 again rather than revision 16.

**What the mechanism must do, stated before it runs.** Three outcomes, and
what each shows:

1. *The shaper rules one piece and the spec author hands back.* Revision 16's
   claim, live: the question-turn brief withholds the ruling and a real seat
   disagrees on the raw issue. Then either the shaper concedes and slices, or
   reaffirms and the run parks `disagreement` for a person. Both are E10.
2. *The shaper slices without a hand-back.* Revision 16 never fires; E8 is
   re-proven on muesli. Not a failure of the mechanism, but not evidence for it.
3. *The shaper rules one piece and the spec author agrees.* The fresh
   perspective ran live and did not disagree. Evidence for open question 3
   (is the withheld ruling enough independence?), and E9's shape repeats
   unless the reviewer's finding on the spec carries the hand-back later.

**What is measured.** For outcome 1: whether the spec author's reasoning names
something the shaper's did not (open question 3), which vendor held each seat
and whether the reaffirmation crosses vendors (open question 4), and whether a
person reading the issue notice and the session prompt can act without reading
the journal. The proof (`prove_front_half --children`) is run over the finished
journal whatever the outcome.

**Not staged.** Nothing about the issue was edited to provoke a hand-back, and
no seat was told this is a test.

### E10, first attempt: outcome 2 — the shaper sliced

*Run `i1-recipes-pre-during-after-cross-meeting-p-1789508081`, minted by the
21:33 UTC wave on 2026-09-15, parked at the slice gate by 21:37.*

| Step | When (UTC) | Outcome |
|---|---|---|
| mint, resume fence, label swap | 21:34 | `bircher:queued` → `bircher:running`; shaping seat dispatched to **claude**, session `b785e26b` |
| shaping turn | 21:34 to 21:36 | a three-slice plan, `artifact_submitted {phase: slices, visit: 1}` — the visit stamp is revision-16 code, live for the first time |
| review | 21:36 to 21:37 | **codex: PASS, no findings**; `slices_accepted` |
| gate | 21:37 | `parked {reason: gate}` on the shaping session; the run waits for a person |

The shaper read muesli's code rather than the issue alone: its plan names
`handleSummarizeTemplate` requiring a completed `GetTranscript`, and argues
that the three unexecuted phases (`pre`, `during`, `cross`) are three
triggers, three data sources and three surfaces sharing one authoring model.
Slice 1 generalises the execution path and ships pre-meeting briefs; slices 2
and 3 depend on it and add live prompts and cross-meeting analysis, the last
routed through the existing chat surface as the issue suggests.

**What this is and is not evidence for.** The shaping phase worked on a real
muesli epic with zero human touches before the gate, under the merged
revision-16 code, which is E8b (the optional zero-touch re-proof) as a side
effect. It is not evidence for revision 16's mechanism: the fresh-perspective
question was never asked, because the spec round was never reached. The
pre-registration's outcome 2. The body's own "Big" and its three named surfaces
were enough for the shaper; the E9 shape needs an issue whose body undersells
it.

The gate was approved on the person's instruction at 22:00:49 UTC: `approve`
posted into the carrier session as a `message` event with a `user`-role
`input_text` part (item `e6e75aec`), the same body the kernel composes for its
own prompts. The 22:03 wave read it.

| Step | When (UTC) | Outcome |
|---|---|---|
| resume | 22:04 | the runner re-froze the bundle; `revise_bundle` refused, "no relevant change" — the designed answer, no new epoch |
| approval | 22:04 | `human_ruling {approve, slices}` (seq 3506) → `sliced` |
| filing | 22:04 | children **#763** Pre-meeting briefs, **#764** Live in-meeting prompts, **#765** Cross-meeting analysis; #764 and #765 blocked by #763; all three `bircher:queued` + `bircher:slice`; umbrella comment `bircher: sliced 0c4a8fcd` on #1; `filing_complete`; #1 now `bircher:sliced` |
| runner | 22:04:55 | scorecard row `sliced`, "children filed: 763 764 765"; queue file retired to `processed` |
| proof | 22:06 | `prove_front_half --expect-approval`: **PASS** over the parent. With `--children` the tool reports "child #763/#764/#765 has no run yet" — correct, since no wave has picked them up |

Zero human touches before the gate, one word at it, and the proof passes:
E8b, the optional zero-touch re-proof on a real muesli epic, delivered as a
side effect of an E10 attempt that did not reach revision 16's mechanism.
The consequence is live: #763 is unblocked and queued, so the next wave
starts a real run on it unless the label is removed first.

### E10, second attempt: muesli #5, pre-registered

*Written 2026-09-16 08:35 UTC, before the run. Muesli #5, "Mobile client
(iOS/Android)", labelled `bircher:queued` at 08:32 UTC.*

**Why #5.** Its whole body is "At least viewing, maybe capture. Keep the API
mobile-friendly." — one noun that reads small and is not. That is E9's shape
and the opposite of #1's, whose body announced itself as big and named its
three surfaces. A shaper anchored on the body's minimising language rules one
piece; a fresh perspective reading the title sees two platforms and at least
two capabilities. The same three outcomes as the first attempt apply, and the
same measurements.

**A datum from overnight, recorded here because it arrived first.** #763, the
first child filed from #1, ran to its spec gate between 22:33 and 05:35 UTC.
A child carries `bircher:slice` and cannot be sliced, so its shaper ruled one
piece, and its spec author was therefore briefed with the question and not
the ruling — revision 16's mechanism live for the first time, on an issue
that genuinely is one piece. The spec author did not hand back; it wrote a
spec, which was accepted. That is outcome 3 on a true positive: no false
hand-back. What it says about open question 3 is limited to "the question
did not provoke a spurious disagreement", which is the cheaper half of the
question. The verification of what that seat was shown follows below.

**#763's spec seat, verified from the journal.** Shaper: claude, one-piece
ruling at visit 1 ("already a pre-scoped slice"). Spec seat: **codex**, the
vendor that is not the ruling's actor — the §2 vendor rule, live. Its first
prompt (5,159 bytes) carries the question section, the `Ruling: epic` grammar
block and the availability section; it carries none of the ruling's reasoning
or cost, no findings, no previous draft and no resolution section. No
`reshape_requested`; no dispute. The spec then took three rounds with
authors codex, claude, codex and reviewers claude, codex, claude, so the
rotation continued to cross vendors under the new rule. The run parks at the
spec gate from 05:35, re-parking every wave since.


### E10, second attempt: outcome 2 again — the shaper sliced, after one refusal

*Run `i5-mobile-client-ios-android-1789547767`, minted by the 08:34 UTC wave
on 2026-09-16 — two minutes after the label. The loop fires at :04 and :34
UTC; its log stamps are container-local BST, which is where the earlier
":03/:33" and "~09:03" came from. Corrected here before any later outcome.*

| Step | When (UTC) | Outcome |
|---|---|---|
| shaping, turn 1 | 08:36 | claude (gen 3, session `c21fde1a`) wrote a plan; `submit_slices` **refused**: "slice 4: Scope has 2 sentence(s); 3 to 6 by full stops" — the grammar's sentence bound, live |
| shaping, turn 2 | 08:37 to 08:38 | the refusal became the round's findings; claude again (gen 5 — a refused submit is a reauthor, not a rotation) submitted a conforming plan, `visit: 1` |
| review | 08:38 to 08:40 | **codex: request_revision**, one high finding: slice 1 (the API surface) "is explicitly a prerequisite layer with no user-visible mobile capability, so it is a bare step of the later viewing/authentication work rather than a coarse capability" — §1's definition, applied by the reviewer; back to `shaping` |
| shaping, turn 3 | from 08:40 | by the rotation the reviewer authors the next round: **codex** holds the pen, with its own finding as the round's findings. It chose a revised plan, at 08:41: **two slices**, view notes on iOS and view notes on Android, the API work folded into the first, capture dropped as "undecided rather than committed scope", all three findings dispositioned accepted. `visit: 1`, as it must be — no hand-back has opened another |
| review, round 2 | 08:41 | **claude: accept**; `slices_accepted` |
| gate | 08:41 | `parked {reason: gate}`; the run waits for a person |

Four slices: a mobile-friendly API surface; the app shell and
authentication; viewing notes; audio capture. The rationale reads the body's
"at least viewing, maybe capture" as two feature surfaces over a shared shell
and a prerequisite API pass, "each large enough to be its own review".

**What this says.** Two epic-shaped issues, two slice plans, no one-piece
ruling — the pre-registered hypothesis that an undersold body would anchor
the shaper into one piece did not hold for claude on #5. The hand-back path
has still not been exercised on an epic. The §7 row "shaper writes a plan
the grammar refuses; the refusal is the next round's findings" ran live and
recovered in one round, which is a datum for the shaping phase rather than
for revision 16. Revision 16's only live evidence remains #763's true
negative above.


### E10 so far, assessed

Two epic-shaped issues, two slice plans, no one-piece ruling: the hand-back
path has not been exercised on an epic. #1 announced itself as big and
claude sliced it in one turn; #5 undersold itself and claude sliced it too,
after the grammar refused its first plan and codex rewrote the second. The
pre-registered hypothesis — an undersold body anchors the shaper into one
piece — did not hold, at least for claude as shaper.

What revision 16 has shown live is the half that does not need an epic. On
#763, a genuine one-piece, the spec seat went to the other vendor, was
briefed with the question and without the ruling, and did not hand back.
That is a true negative, and it is the cheaper half of open question 3.

What the shaping phase showed as a by-product is worth more than expected:
the sentence bound refused a live plan and the seat recovered in one round;
a reviewer applied §1's definition to reject a prerequisite-layer slice as a
bare step; and the rotation moved the pen to the other vendor, whose
rewrite the first vendor then accepted. Every one of those is a §7 row
running on a real issue.

**What would reach the hand-back path.** A one-piece ruling on an epic.
E9's #12 drew one, and its distinguishing feature was the "Design:" framing
— a single deliverable named in the title. None of the remaining
pre-registered issues (#4, #8, #9, #10) is framed that way. The honest
options are to wait for such an issue to arrive naturally, or to accept
that the mechanism's live proof is the #763 true negative plus the
constructed histories of the branch review until one does. Staging one is
excluded by this log's own rule.

### Approvals, both gates

*2026-09-16 12:37 UTC, on the person's instruction.* `approve` posted into
#5's slice-gate session `2ca05ac1` (item `d012c56c`) and into #763's
spec-gate session `911827ff` (item `d2a23700`), each as a `message` event
with a `user`-role `input_text` part, each read back as its session's newest
user item. The 13:04 wave reads both. #763 had waited at its spec gate for
seven hours, re-parking every wave without spending a seat, which is the
park doing what it is for.

### Both approvals taken; #5 sliced; #763's back half

*Read at 21:46 UTC on 2026-09-16, eight hours after the approvals.*

**#5.** The 14:35 UTC wave took the approval at 15:01: `human_ruling
{approve, slices}` → `sliced`; children **#767** View notes on iOS and
**#768** View notes on Android, #768 blocked by #767; umbrella `bircher:
sliced ec3f5915` on #5; `filing_complete`; scorecard row `sliced`.
`prove_front_half --expect-approval` over the parent: **PASS**. By 21:46
#767 had run its own shaping (one piece, by construction), three spec
rounds with the vendors alternating, and parked at its spec gate.

**#763, the first child's back half.** The 13:05 UTC wave's implementer
(claude, gen 88) opened muesli **PR #766** at 14:15, +6285/−199 over 63
files, then pushed a test-determinism fix at 14:49 while CI was red. At
15:00 the same wave derived the outcome from the repository — "PR up, CI
red, coordinator died before fix" — and recorded `failed`; the run is
`ended`. The implementer's session was not: its worktree kept receiving
commits at 15:03, 17:32, 19:19 ("fix cross-review blocking findings"),
19:51 and 20:12, and the head `e1a86e68` has every CI check green. The
"cross-review" in those messages is the implementer's own in-session review
tool, not the pipeline's: no `bircher/cross-review` was posted, and
`review-gate` is **pending**. The v1 runner did not touch it. So the PR is
green, unreviewed by the pipeline, and orphaned from its run — E9's
position exactly, and `--recover-pr i763 766 codex` is the same remedy.

Two things to note rather than fix here. The runner's derivation gave up
on a session that was still working, and the outcome it recorded is now
false; the runner reads the repository at one instant and the session does
not stop when it does. And the implementer's in-session review reached a
green head without a second vendor, which is the thing the pipeline's
cross-review exists to add — its verdict is the one still owed.

### #763 recovered for cross-review: codex FAIL, one blocking finding

*2026-09-16 22:29 to 22:32 UTC, on the person's instruction.* Launched as
the wave launches, with the recovery arguments passed through:
`BIRCHER_KERNEL_DB=…/kernel-muesli.db bash batch/launch.sh --log … --recover-pr i763 766 codex`.

| Step | When (UTC) | Outcome |
|---|---|---|
| adopt | 22:29 | run `i763-…-1789511682` adopted, generation 89, role implementer |
| derive | 22:29 | PR #766 CI green at `e1a86e6` → codex review out of band |
| review | 22:31 | **codex: FAIL**, one blocking finding, none non-blocking; posted on the PR |
| record | 22:31 | "no review verdict at all" — the kernel records no verdict on an `ended` run; "past the lifecycle stages; not re-driving them"; PR left open with a marker for a person |

The finding, quoted: `internal/worker/prebriefs.go:49-65` "only examines
events currently inside `(now, now+7d]`. If an event with an existing brief
is rescheduled outside that window — or has already started — it is excluded
before cleanup, leaving its stale brief attached and retrievable through the
calendar API." Codex ran `go build`, `go vet`, `gofmt -l` and the
determinism check itself, all clean, and could not run the Node gates
locally.

So the second vendor found what the implementer's in-session review did not:
a cleanup scoped to the eligibility window rather than to the source's
existing briefs. `review-gate` stays pending. The pipeline will not repair an
ended run, so the next step is a person's — fix the finding on the branch
and recover again for a fresh verdict, or decide otherwise. One cosmetic
defect of the recovery path is recorded here: the posted comment begins with
the harness's own "omnigent: Connecting…" lines, which the out-of-band review
does not strip.

### #763: the finding fixed on the branch

*2026-09-17, on the person's instruction, in a worktree of `abedegno/muesli`
on `i763-pre-meeting-briefs`; commit `b2c4df9`.*

Test-first. Three DB-backed tests in a new
`internal/worker/prebriefs_window_test.go`: an event with a brief is
rescheduled to eight days out and the brief and its queued job are gone on
the next pass; an event with a completed brief is moved into the past and
the brief is gone; and the cleanup is source-scoped, leaving another
source's out-of-window brief for that source's own pass. The red was shown
at the compile level only — the store method did not exist — because the
DB-backed tests skip without `TEST_DATABASE_URL` and Docker's daemon was
not running here; the behavioural red-then-green is CI's to show.

The fix: `DeleteEventBriefsOutsideWindow(owner, source, now)`, a
source-scoped delete of every brief whose event is outside the window,
joined through `calendar_events` and reusing the existing helpers for the
returning-ids delete and the queued-job cleanup; called once per source
before the batch loop. The window is now defined once — `preBriefWindow`,
`(now, now+7d]` — and read by both the batch query and the cleanup, so the
cleanup is exactly the batch predicate negated and the two cannot drift.
That is the same rule this branch learned about the dispute: one
definition, every reader reads it. Build, vet, gofmt clean; the determinism
rule checked by hand (no `time.Now`/`time.Sleep` in the new test).

### #763: CI green, recovered again, codex PASS, gate green

*2026-09-17 07:22 to 07:37 UTC.* The first push's CI failed on one of the
three new tests — the source-scoping fixture seeded two sources' events at
the same start, and the seeding helper lists the owner's events within a
minute and requires exactly one. Fixed by starting them an hour apart
(`6f1a365`); the two behavioural tests had passed. CI fully green at 07:34,
`server (go)` included, so the database-backed red-then-green happened
where it had to.

Recovered again the same way, launched after waiting for the :34 wave to
release the lock: generation 90, PR CI green at `6f1a365` → codex out of
band → **PASS** at 07:37 → `bircher/cross-review=success` posted and
verified → `review-gate: pass` → PR #766 mergeable, clean. The recovery
did not merge: "run is at 'ended' — past the lifecycle stages; not
re-driving them", which is the same stop as E9's, and the merge is a
person's. The kernel recorded the two status effects under generation 90
and nothing else; the run stays `ended`.

**The first child's back half, in one line.** The pipeline's second vendor
found a real defect the implementer's own review had missed, the finding
was fixed test-first with the window defined once for both readers, and
the same vendor passed the fix. Two cosmetic defects for later: the
recovery's posted comment begins with the harness's own "omnigent:
Connecting…" lines, and the run's recorded outcome is `failed` for a PR
that is now green and reviewed.

### #766 merged; #767 approved at its spec gate

*2026-09-17 07:53 UTC, on the person's instruction.* PR #766 squash-merged
to muesli `main` as `551d148` "Pre-meeting briefs (issue #763) (#766)" —
the pipeline's own convention, `--squash --delete-branch` — and #763 closed
by the PR's "Closes #763". The first child of the first live epic is
merged: shaped by the parent's slicing, specified and planned under
revision 16's vendor rule, implemented, self-reviewed, caught by the
cross-vendor review, fixed test-first, passed, merged. `approve` posted into
#767's spec-gate session `872409a0` (item `76751fd0`) for the 08:04 wave.

**What the next wave does on its own.** #764 and #765 were blocked by #763
and carry `bircher:queued`; with #763 closed the generator's blocked check
clears and both are queued, so two more runs start without anyone typing
anything. The sweep records #763's closure toward #1's umbrella, which
closes when all three children have. #767 moves to its plan phase.

### #767 halted on a transient GitHub error at the wave that would have read its approval; reconciled

*Found 2026-09-17 20:17 UTC, twelve hours after the approval was posted.*
#767's state and fact count had not moved since 07:53, and every wave since
10:18 had escalated it: "halted or holds unresolved effects". The cause was
the 08:04 wave's own resume: the runner's per-resume label swap
(`running:767:44`, class `issue_or_label`) got "GraphQL: Something went
wrong while executing your query" back from GitHub, the kernel recorded the
effect as **uncertain**, and the run halted for a person — before `phases`
ran, so the approval in the session was never read. The label was in fact
present on the issue; the mutation had landed (or already stood from the
earlier resumes, each confirmed with the issue's URL) and only the reply was
lost. Twelve hours of waves each skipped the run correctly and spent no seat.

Reconciled at 20:20 UTC with the runner's own client, in the legacy
observed-resolution form: the typed delivered/not-delivered form refuses a
bare label effect because it "carries no obligation", which is right — only
an obligation-bearing effect has a delivered VALUE to bind later commands to.
Resolution recorded: "bircher:running is present on #767 and bircher:queued
is absent — the label edit landed despite GitHub returning a GraphQL error
to the client". `effect_reconciled` by the human actor, version 54 → 55,
halt cleared, the park and the unread `approve` untouched. Two things
learned about the tooling: `_kernel_reconcile` sourced on its own needs the
runner script's `_net_run` wrapper, and the kernel's refusal message named
the right form without a docs lookup.

Meanwhile, on their own: #764 and #765 were minted by the 08:04 wave once
#763 closed, each ruled one piece as a slice, each through four spec rounds
with the vendors alternating, and each parked at its spec gate by 10:18.
The sweep recorded #763 closed toward #1's umbrella.

### Approvals posted for #764 and #765; every wave refused since 20:08 — Codex out of quota

*2026-09-17 21:07 UTC, on the person's instruction.* `approve` posted into
#764's spec-gate session `8d673fc4` (item `892b19ca`) and #765's `91b47114`
(item `47c9464a`), each read back as its session's newest user item.

None of the three posted approvals — #767's from 07:53, these two — has
been read, and the reason is upstream of every run: from the 20:08 UTC wave
onward, preflight fails on Codex ("You've hit your usage limit … try again
at Sep 21st, 2026 8:22 PM") and the runner refuses to start the queue at
all. That refusal is correct. A wave with one vendor would put claude in
review of claude, which is the one thing the rotation exists to prevent, and
the runner would rather do nothing than do that. Claude's own probe passes
and GitHub auth is fine. The quota was spent honestly: #766's two
cross-reviews, and the spec rounds of #764, #765 and #767, in which codex
authored or reviewed every other round.

Nothing is lost. The three runs are parked at their gates at zero cost, the
approvals are durable in their sessions, and the first wave that passes
preflight reads all three. The options are to wait for the reset, or to buy
credits; there is no honest third.

### Codex back; a hand-fired wave reads #767's approval

*2026-09-17 22:59 to 23:08 UTC.* The person restored Codex (the direct probe
answered READY at 22:59; the 22:38 wave had still failed). A wave fired by
hand at 23:00 (`BIRCHER_WAVE_ONCE=1`, E8's precedent) passed preflight —
"both providers healthy" — and the generator listed the three parked runs.
#767 went first: its approval, posted at 07:53 the previous day and held
through a halt, a reconcile and a quota stop, was read at last:
`human_ruling {approve, spec}` → `specified`, park cleared, plan author
dispatched. #764 and #765 follow in the same wave, since `run_item` is
sequential and the implementer runs inside it, so their approvals are read
when #767's block completes.

### Overnight: #764 to a red PR, #767 and #765 out of plan rounds

*Read at 06:26 UTC on 2026-09-18.* The hand-fired wave and the timer waves
after it (preflight OK from 05:09) took all three approvals in turn.

**#764 → PR #769, red.** Approval read; the plan accepted after three rounds
(claude, codex, claude); the implementer opened muesli PR #769 at 00:45,
+4106/−22 over 36 files, six commits to 00:53. At 01:05 the runner derived
`failed` ("PR up, CI red, coordinator died before fix") and ended the run.
Unlike #763, the session did not carry on: nothing after 00:53, and the head
is genuinely red — two worker tests (`RunLiveGenerate`: a rendered revision
of 2 where 1 was fixed; a terminal failure left `pending`), one API test
(the new `/api/notes/{id}/live-prompts` route is missing from #12's
authorization classification table), and the `internal/api` and
`internal/store` packages **hung to the ten-minute timeout**. The run is
`ended`, so the pipeline's repair loop cannot reach it; the PR waits for a
person, and the hang is the first thing to understand.

**#767 and #765: `bound_exhausted` at the plan phase.** Five rounds each,
vendors alternating, every verdict `request_revision`. They are not the same
case. #767's fifth review (claude) marks the prior high finding **resolved**
and adds one **medium** — the scale-proof fixture must give the target owner
20,000 of the 100,000 rows and mix in deleted rows — so the plan is
converging and one more round would likely pass. #765's fifth review (codex)
carries two **highs**, both about the aggregate token budget: a lookup error
falling back to the static proxy the same way a genuine absence does, and
`ceil(bytes/3)` not being a safe upper bound on tokenizer output outside
English prose. Each round of #765 has found a new high in a different place
— persistence contract, lifecycle, budget — which is the shape the pipeline
trial called too expensive to run per issue, and the reason the park exists:
a person decides whether to grant a sixth round, correct, or stop.

The parks cost nothing while they wait. The park notice on each issue names
the phase and says what to type.

### #769 (#764) repaired on its branch: two production defects behind the hang

**2026-09-18 07:14 UTC.** The user's instruction was "Try to fix 764". The
work was done in a worktree on the PR's own branch
`764-live-in-meeting-prompts`, in two commits, `e8bc7ba` and `084bd03`. CI is
fully green at `084bd03`; only `review-gate` is pending, and that is the
cross-review's to post. No recover run was launched and nothing was merged;
both are the user's call.

**What the goroutine dumps said.** The ten-minute hangs in `internal/api` and
`internal/store` were one mechanism twice. `pgxpool.Close`, called by the
per-test pool's cleanup, blocks until every connection is returned, and in
each package one test had left a connection checked out.

- In `internal/api` the connection belonged to the live-prompts LISTEN
  listener, parked in `WaitForNotification`. The handler starts it lazily on
  the first stream and nothing ever stops it; the listener object was not
  even kept. That is a production defect, not a test one: graceful shutdown
  would block on `pool.Close()` the same way. The server now owns the
  listener, `Server.Close()` stops it (mutex-guarded, so a late first stream
  cannot start one after Close), `Run` defers Close, and the API test server
  registers Close as a cleanup after the pool's, so it runs first.
- In `internal/store`, `TestScheduleLiveJob_EnforcesCadence` "simulated the
  first job having started" by clearing `active_job_id` while leaving that job
  `pending`. The second schedule then violated `jobs_live_active_uniq` (one
  pending or running `live_generate` job per output row), the test called
  `t.Fatalf` with its transaction still open, and the cleanup blocked.
  Postgres's own line in the CI output, `duplicate key value violates unique
  constraint "jobs_live_active_uniq"`, was the tell. The test now settles the
  first job as `done` and defers a rollback. The hang had masked every later
  test in the package.

**The three red tests.**

- `TestRunLiveGenerate_TerminalFailure_NoGrowthCreatesNoSuccessor`
  (`status = "pending", want failed`) was the second production defect.
  `handleLiveGenerateTerminalFailure` read `target_revision` from the job
  struct the pipeline holds, which the queue claim populated before the live
  claim captured the target. It was therefore always nil, the failure path
  compared `desired_revision` against 0, every terminal failure looked like
  growth, and the row was rescheduled instead of marked failed. The
  completion fence now returns the job row's own target and the failure
  transaction uses that; a job with no captured target gets no follow-up.
- `TestRunLiveGenerate_GrowthDuringExecutionCreatesOneFollowUp`
  (`rendered_revision = 2, want 1`) was the test's premise: it appended
  growth after the queue claim but before the live claim that captures the
  target, so the target was 2. It now captures the target through
  `ClaimLiveGenerateJobTx` first, as the worker does, then grows.
- `TestNoteScopedRouteRegistrationCompleteness`: the new
  `GET /api/notes/{id}/live-prompts` route is classified shared-readable,
  matching its `GetReadableNote` gate and the API doc's
  "ownership/readability" wording.

**One more, unmasked by the fix.** With the store package running to
completion, `TestReconcileOwnerEligibility_RunningRow_HiddenAndCancellationRequested`
failed with `no rows in result set`. It deleted the template to make it
ineligible, but `live_template_outputs.template_id` cascades on template
delete, so the running row and its job vanish before the reconciliation that
would hide them ever runs. The rule the test proves (hidden and
cancellation-requested, not deleted) can only apply to a template that
becomes ineligible while it still exists; the test now switches `auto_run`
off instead. Whether deletion should cascade at all is a question for the
cross-review, not something to change in a repair.

**What this says about the run.** The session stopped at 00:53, twelve
minutes before CI finished red, so the implementer never saw the result of
its own branch. The two defects that mattered are both of the
"identifier that names the wrong thing" kind: a job struct standing in for
the job row, and a listener with no owner. Neither is visible in a diff;
both were visible in the first goroutine dump.

### #769 recovered: codex PASS on eight lines of a four-thousand-line PR

**2026-09-18 18:14 UTC.** The user said "recover 769 for cross review. You
can do this." The recover run was launched like #763's, from
`launch.sh --recover-pr i764 769 codex`. It adopted PR #769, found CI green
at `084bd03`, dispatched the out-of-band codex review, and two minutes later
posted and verified `bircher/cross-review=success`; GitHub's `review-gate`
went green on the same head. Codex's verdict was PASS with no findings of
any severity.

**What the review actually covered.** Codex's own Verification section says
"Exact commit `084bd03…` reviewed", and its narrative opens "The commit only
corrects a DB-backed regression test". It reviewed the head commit: one
file, eight insertions, the test-only change from the repair above. The PR
against `main` is 38 files and 4,171 insertions. Neither the implementer's
36-file change nor the repair commit `e8bc7ba`, which fixed two production
defects, has been read by a second vendor. The pipeline's own review stage
never ran either: the session stopped at 00:53, before it.

**Why.** The recovery prompt (`_recovery_review_prompt` in
`batch/run-queue.sh`) says "You are reviewing EXACTLY commit <sha>" and
"READ the changed files AND enough surrounding code", and never names the
other side of the diff. That wording exists to pin the reviewer to a fixed
sha (#66); it says nothing about the range. On #766 the same prompt
produced a whole-PR review ("Reviewed the cleanup implementation and
surrounding reconciliation, schema, job-lifecycle, API, and test code"). On
#769 it produced a one-commit review. Same prompt, two readings; the gate
cannot tell them apart, and this is the first live case where the narrow
reading met a PR whose head commit was trivial.

**Consequence.** The gate is green and attests to nothing about the PR's
substance. Not merging. The fix is one sentence in the prompt, stating the
range ("the PR's changes are `git diff origin/main...<sha>`; review all of
them, not only the head commit"), followed by a redeploy and a re-run of
the recovery so that codex reads the 38 files. That is the user's call;
the recommendation is recorded here and in memory.

### #769 recovered again with the range named: codex FAIL, one blocking finding

**2026-09-18 19:41 UTC.** The user said "Do all three". The prompt fix is
bircher PR #101 (`5cf5386`, branch `fix/recovery-review-range`, open): one
sentence after "You are reviewing EXACTLY commit", stating that the change
under review is every file in `git diff origin/main...<sha>` and that a head
commit touching one file does not narrow the review to one file, plus a
self-test assertion that the range reaches the prompt text. The assertion
was proved binding on the runner: the old prompt fails it, the new one
passes. The bundle was deployed from the branch with `update-bundle.sh`, so
the runner is on `fix/recovery-review-range` until #101 merges and `main`
is redeployed.

**A pre-existing failure in the bundle self-test.** `update-bundle.sh` runs
the self-test and it fails on the runner, at `5e1a4d0` exactly as at
`5cf5386`, on the rollback test that makes a findings file "unremovable"
with `chmod 500`: the runner is uid 0, and root removes it anyway. The
self-test exits at that first failure, before the recovery-prompt
assertions, so on the runner they have never run; the wave timer does not
run the self-test at all. Not caused here, not fixed here, recorded for an
issue.

**The re-run.** Launched 19:37:32 UTC, generation 48, same head `084bd03`.
Codex's narrative this time opens "This is a broad feature PR (38 files,
roughly 4.2k added lines), so I'm tracing the server lease/listener
lifecycle, worker revision logic, and Electron reconnect/cleanup paths".
Four minutes later: **VERDICT: FAIL**, one blocking finding, no
non-blocking ones:

> The new SSE endpoint acquires a database-backed subscriber lease
> (`live_prompts.go:194`), which must be released by the defer
> (`live_prompts.go:203`). Tests cover successful streaming and manually
> releasing a seeded lease, but no post-acquisition failure path (initial
> snapshot query failure, SSE write failure, heartbeat reread failure, or
> renewal failure) asserts that the handler releases its lease.

The suggestion: an injectable store boundary or a focused handler fixture
that forces a failure after admission and verifies the lease count returns
to zero, with renewal failure and initial-snapshot failure as the
highest-value cases. This is the prompt's own release-on-error rule, applied
to the file the first review never opened.

**The gate is still green.** The recover path posts a cross-review status
only when the outcome is ready; on FAIL it leaves the PR "with marker for
human" and posts nothing. So `084bd03` still carries the 18:16 success from
the one-commit review, and GitHub's `review-gate` is green on a PR whose
real review is FAIL. The next push moves the head and clears it; until
then the green is a scope artefact, and the recover path should post
failure on FAIL. Both are for #101's follow-up, not for this repair.

**What this says.** Same PR, same head, same vendor, same tooling: PASS
with no findings at 18:16, FAIL with a blocking finding at 19:41. The only
difference is a sentence naming the range. The first verdict was not wrong
about what it read; it read the wrong thing, and said so in a line nobody
was required to check. Next, on the user's word: fix the finding on the
branch (a test that forces a post-admission failure and asserts the lease
is released), push, recover again.

### #769, third recovery: the lease finding closed, a race in the template cap opened

**2026-09-18 20:15 UTC.** The user said "fix the finding on the PR branch
with a release-on-error test, push, and recover again". The fix is
`d7e5cd0` on the PR branch: the SSE handler now reaches the store through a
five-method `liveStore` boundary (production still passes the real store)
and its three timers are variables, so an internal test can wrap the real
store, force one failure after admission, and shorten the tickers rather
than sleep. Four tests, one per class codex named: the initial snapshot
query fails, the SSE write fails, the heartbeat reread fails, the renewal
fails. Each asserts the lease was released exactly once and that no lease
row remains for the note; the injected failures leave the row in place
themselves, so only the handler's deferred release can satisfy the
assertion. CI green at `d7e5cd0`, all DB-backed suites `ok`.

**The re-run.** Launched 20:11:12 UTC, generation 49. Codex read the whole
PR again ("schema, scheduling/worker, SSE API, Electron relay, renderer"),
ran every non-DB gate locally, reconciled the DB-backed suites against the
CI log, and reported the lease fix without comment, which is how a closed
finding reads. Then, four minutes in: **VERDICT: FAIL**, one new blocking
finding, nothing non-blocking:

> The eight-template cap is vulnerable to concurrent creates or updates.
> Two transactions can each observe seven eligible templates, both pass the
> unlocked `count(*)`, then enable distinct eighth templates and commit,
> leaving nine enabled. `UpdateTemplate` locks only the individual template
> row, not a shared owner-scoped row. Serialize cap validation per owner or
> enforce the invariant at the database level, and add a concurrent
> regression test.

The cited code is `validateLiveTemplateCap` and its count in
`live_templates.go`, and the create and update transactions in
`templates.go`. It is a plain check-then-act: the count is read without a
lock that the competing writer would have to wait on. The observed-versus-
asserted question again, one layer down: the cap is asserted by a count
and observed by nobody.

**The gate.** The head moved, so `d7e5cd0` carries no cross-review status
and `review-gate` is pending, which is the truth. The stale green on
`084bd03` is now behind the head and harmless.

**Where this leaves the round.** Three recoveries, three different
readings: PASS on one commit, FAIL on the lease, FAIL on the cap. The
second and third are what the seat is for. Each new blocking finding is in
a different subsystem, which is the shape #765's plan rounds showed and
the pipeline trial priced as expensive. Next, on the user's word: serialize
cap validation per owner (an owner-scoped row lock or an advisory
transaction lock at the top of the create and update transactions) with a
concurrent regression test, push, recover again.
