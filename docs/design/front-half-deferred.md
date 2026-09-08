# Front half — deferred findings

Recorded while executing `docs/superpowers/plans/2026-09-06-front-half.md` (branch `feat/front-half`).
Every item below was raised by a task reviewer or the final whole-branch review, triaged as
non-blocking, and left unfixed on purpose. Nothing here is a known-wrong behaviour; they are
untested branches, duplicated work, and naming the next change should tidy.

## Untested branches (the largest group)

- `kernel/effects.py`: `_check_body`'s unrecognised-key branch; `perform_effect`'s new kwargs are
  exercised only through Task 15's session effects; a `Resolution` naming an unjournaled key.
- `kernel/contract.py`: no case for a session URL carrying a query string; the older constraint
  checks key their message by rule signature while the new ones key by rule name.
- `kernel/authz.py`: `_require_turn_recorded`'s no-seat branch for `record_author_empty`;
  `_check_turn_ended` with no prompt at all; `_reviewer_of`'s phase filter has no mutation test;
  `cancel_run` and `park` legality over the four new states; `revise_bundle` from `queued`.
- `kernel/policy.py`: no bool-for-int case.
- `coordinator/session.py`: `list_items`' bare-list and `LookupFailed` paths; the cap-freshness read
  (the matrix's "file planted between poll and cap" row plants the file before the first check).
- `coordinator/review.py`: no round-level test for a PASS at zero rounds left, nor for empty findings.
- `coordinator/phases.py`: `_displacing_cause`'s final fallback (the first fact) is unexercised.
- `run_loop`'s "same key, different command" catch is unconditional and was traced by hand, not pinned
  by a regression test.

## Duplication and dead code

- `_delivered_value` re-fetches the effect row it was handed (plan-verbatim).
- `revise_bundle` computes the snapshot twice.
- `run_base_sha` is read twice in the `issue_review_brief` arm.
- `recover_pr_cmd`'s `_rp_drive=0` is dead — a later `local _rp_drive=1` resets it (pre-existing).
- `enqueue()` keeps a stale local `from kernel.canon import content_hash`.
- `create_session`/`stop_session` take a `store` they never use (signature uniformity).
- The two vendor guards share a shape that could be one helper.

## Naming, messages and shape

- `ContractViolation`'s re-raise prefix says "not a JSON object" for all three `create_body` cases.
- The grouped binding-mismatch message names no field.
- `authz.py` is over 900 lines; the split was ruled out during execution because later briefs cite it
  by line, and the final review triaged it rather than requiring it.
- `cli._human_cmd` catches bare `Exception`; `cli` imports the private `session._fetch`.
- `front.human_rejections` hardcodes the string "human".
- `Turn.cursor_before` is always `None`; `author.Outcome` is vocabulary, not a symbol.
- `_kernel_run_start` prints stdout and stderr combined as the hash.
- `BIRCHER_AGENT_V2_AUTHOR_*` is exported into `phases`, which reads `--agent-claude`/`--agent-codex`.
- `coordinator.cli state` prints a traceback for an unknown run.
- Exit code 5 is reused for the issue-fetch and operator-dispatch failures.

## Behaviour worth a decision later

- **The human never sees the model's recommended answer.** `parse_questions` reads a `recommended`
  field and drops it; `record_model_question` has no place for it. Under `grill=model` the person
  answering sees the question without the model's own proposal. Decide after the first live grill
  pass whether the field is worth a kernel payload change.
- A refused dispatch on an already-halted run overwrites the evidence with generation `None`.
- `from_gh` drops a comment whose URL carries no `issuecomment` id.
- `main` exits 3 if either author bundle upload fails; `_prune_session` on a failed `_get_agent_id`
  is an unguarded effect (pre-existing).
- A back-half run is never retired by `phases` — the accepted consequence of the loop owning only
  the front half.
- The proof driver's `review_round` writes placeholder prompt and findings content, so a
  driver-built run cannot satisfy §8's session-content checks; the live proof reads real sessions.

## Raised by the final review and its fix wave

- **`_sane_arm` has a one-second boundary race** (`batch/run-queue.sh:7866` and `:7887`, self-test
  case 62). A leading-zero minimum arms a deadline at exactly `now + 60`, and the check re-samples
  the clock afterwards, so a second-boundary tick between the two fails the `-ge` comparison. Seen
  once in five runs. Pre-existing; nothing in this branch touches it.
- **`GET /v1/sessions` defaults to `kind=default`**, which excludes sub-agent child sessions, and
  `list_sessions` does not pass `kind`. A run whose sessions were created as sub-agents would stay
  unlisted however far the reader pages. Nothing in the front half creates such a session today.
- **`authz.py` is not split.** The decision was taken during execution because later task briefs
  cite it by line number, and the final review triaged it rather than requiring it.
- **`reply_refusals` fails closed into silence at the page cap.** The paged `list_sessions` raises
  a lookup failure once a listing exceeds two hundred thousand sessions, and the caller catches it
  and returns an empty list, so on a server that large every owed reply stops going out with
  nothing said on the channel. The bound is right; the silence at the caller is the part to know.
- **The human pass now pages its carrier session twice per pass.** For a long parked session that
  is a handful of round trips where it used to be one. `last_assistant_text` is untouched and still
  asks for the newest twenty.
- **The kernel keys a turn's end to the phase's newest prompt, the output guard to the session's.**
  They agree only because a prompt nobody awaits is never sent while a turn is awaited: both senders
  sit inside the human pass, the human pass runs only under a current park, and the loop reaches a
  round only when no park is current. A guard against the divergence was written, found to starve
  the replies it was meant to protect, and reverted; the invariant is stated in the sender's
  docstring instead. Any change that sends a prompt outside a park has to re-establish it.
