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

## What the plan phase does

Three runs, two repositories, two policies, two round limits:

| Run | Loop | Spec rounds to accept | Plan rounds | Plan accepted |
|---|---|---|---|---|
| E2 (smoke, human grill) | rotate | 2 (first run) | 4 | no |
| E3 (muesli, 3-round bound) | rotate | 3 | 5 | no |
| E4 (muesli, 5-round bound) | rotate | 2 | 6 | no |
| E5 (muesli, fixed author) | fixed | never (6, growing) | — | — |
| E6 (muesli, dispositions) | rotate + dispositions | 4 | 5 | **yes** |

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
issue that failed three times passed both phases in nine rounds unattended.

## What the live runs found

Thirteen defects, none reachable from a test suite that ended the day at
1437 tests, because the tests were written from the same assumptions as the
code. Ten fixed, three open.

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
| 11 | A not-delivered effect is never retried: its key names the run and the label, so it can only ever be attempted once | open |
| 12 | A parked run can become unreachable from the runner — the queue file is consumed and the issue loses its label, so neither source finds it | open |
| 13 | The human's control channel is a live agent session: every reply wakes a model, which echoes, costs a turn, and could act | open |
| 14 | The proof read the server's cancellation notice as an unrecorded prompt, failing the first run that converged unattended on four sessions | fixed |

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

The claim of *The claim* is met, once, on the last run of the day: a
pre-registered vague issue to an accepted spec and an accepted plan with zero
human facts, `PROOF: PASS`, and the existing back half opening a real pull
request from the result. It took a mechanism the spec did not have — the
disposition step — and it is a sample of one. The front half is finished
enough to be argued about; the numbers above are what to argue from.
