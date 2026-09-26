#!/usr/bin/env bash
# Render bircher:queued + UNBLOCKED GitHub issues into queue/i<n>-<slug>.md files
# that run-queue.sh drains unchanged. Sourced helper: _render_issue_item.
# Usage: issues-to-queue.sh [--dry-run]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# BIRCHER_REPO is what an operator sets for a whole wave; REPO is what a
# caller passes to this script. Honour both, so a direct run and a run under
# run-queue.sh cannot disagree about which backlog they are working.
REPO="${REPO:-${BIRCHER_REPO:-abedegno/muesli}}"
QUEUE="${QUEUE:-$HERE/../queue}"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
# shellcheck source=/dev/null
eval "$(sed -n '/^_render_issue_item()/,/^}/p' "$HERE/run-queue.sh")"
eval "$(sed -n "/^_format_issue_comments()/,/^}$/p" "$HERE/run-queue.sh")"

slug() { printf '%s' "$1" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//' | cut -c1-40; }

# unblocked = no OPEN blocked_by dependency. FAILS CLOSED (shaping spec §5):
# a blocker that cannot be read is not "no blocker" -- the links are an
# epic's sequencing now, and a child started because its blocker could not
# be read is the round-4 defect by another door. The next wave reads again.
is_unblocked() {
  local blockers
  blockers=$(gh api "/repos/$REPO/issues/$1/dependencies/blocked_by" \
    --jq '[.[] | select(.state=="open")] | length' 2>/dev/null) || {
    echo "skip #$1 (blockers unreadable; the next wave reads again)" >&2; return 1; }
  # rc 0 IS NOT AN ANSWER on its own. `gh` exits 0 having printed nothing when
  # the endpoint is unavailable to this token, and jq prints `null` for a body
  # that is not a list -- and `[ "${blockers:-0}" -eq 0 ]` read both as "no
  # open blocker", which is the fail-open this function exists to close. A
  # count is a count; anything else is an unreadable count.
  case "$blockers" in
    ''|*[!0-9]*) echo "skip #$1 (blockers unreadable, got [${blockers}]; the next wave reads again)" >&2; return 1 ;;
  esac
  [ "$blockers" -eq 0 ]
}

mkdir -p "$QUEUE"
count=0
# priority order p1->p3, then issue number
queued_nums=$(gh issue list --repo "$REPO" --state open --limit 200 \
               --json number,labels \
               --jq '[.[] | select(any(.labels[].name; .=="bircher:queued"))]
                     | sort_by((.labels|map(.name)|map(select(startswith("priority:")))|.[0] // "priority:p9"), .number)
                     | .[].number') || { echo "issues-to-queue: gh issue list failed" >&2; exit 1; }
# _emit_item <n>: the queue file, the manifest line and the count -- ONE
# body, called from both loops below. Two copies of this (a label-queued
# version and a journal-resumed version) is two places for the manifest to
# drift from what actually landed in QUEUE, which is how #46 happened once
# already for a single write site.
_emit_item() {
  local n="$1" title body comments_json comments out
  title=$(gh issue view "$n" --repo "$REPO" --json title --jq .title)
  body=$(gh issue view "$n" --repo "$REPO" --json body --jq .body)
  # #46: comments carry the human's corrections. A failure here must not lose the
  # whole item -- an item without its discussion still beats no item at all.
  comments_json=$(gh issue view "$n" --repo "$REPO" --json comments --jq .comments 2>/dev/null) || comments_json=""
  comments=$(_format_issue_comments "$comments_json")
  out="$QUEUE/i${n}-$(slug "$title").md"
  if [ "$DRY" = 1 ]; then
    echo "would write $out"
  else
    _render_issue_item "$n" "$title" "$body" "$comments" > "$out"
    basename "$out" >> "$QUEUE/.manifest"
    echo "wrote $out"
  fi
  count=$((count+1))
}

# Journal-driven resumption (spec section 5). A run that parked for a person
# has already lost its bircher:queued label -- the runner swapped it for
# running when it took the item -- and its queue file may be gone too, so the
# labels alone cannot find it and neither can the queue directory. The kernel
# can: an open run holding a current `parked` fact, a `sliced` run still
# owing a filing, or (revision 16) an open run whose journal holds an
# unresolved shape disagreement and no current park -- the pass died between
# the disputed ruling and its park -- is exactly the work the next wave must
# pick up, and it is queued here whatever the issue's labels say -- as is
# (2026-09-26) every open run short of its pull request, which subsumes the
# dispute case and catches the run a human ruling unparked. Its item
# renders from the live issue like any other; run_item adopts the open run
# rather than minting a second one. Off when there is no kernel database to
# ask.
parked_nums=""
if [ -n "${BIRCHER_KERNEL_DB:-}" ] && [ -f "$BIRCHER_KERNEL_DB" ]; then
  # VISIBLE, not swallowed. `2>/dev/null` used to sit on the python call
  # itself, so a raise inside the snippet (an import error, a corrupt
  # database, anything short of the process dying outright) yielded an empty
  # result and no line -- and the runs it drops are exactly the ones labels
  # cannot find. Captured to a file rather than piped, because the snippet's
  # own stdout is the answer this shell reads back.
  TMP_SWEEP_ERR=$(mktemp) || TMP_SWEEP_ERR=""
  trap '[ -z "${TMP_SWEEP_ERR:-}" ] || rm -f "$TMP_SWEEP_ERR"' EXIT
  parked_nums=$(PYTHONPATH="$HERE/../v2" "${BIRCHER_PY:-python3}" -c '
import os, re, sys
from kernel import back, front
from kernel.authz import FRONT_HALF_STATES, SHAPING_STATES
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
# `ended` is the only terminal state (closed-loop spec §2, fix round 1):
# `cancelled` is a stop, not a close, and still owes its terminal fact --
# excluding it here would leave a manually cancelled run with a pull request
# unreconciled forever.
closed = {"ended"}
out = []
for rid in s.all_run_ids():
    m = re.match(r"^i(\d+)-", rid)
    if not m or s.run_state(rid) in closed:
        continue
    # A parked run, whatever its labels (finding 12); a sliced run whose
    # filing is not complete (shaping spec §5) -- the crash-anywhere-during-
    # filing case -- so the next pass reaches file_owed; or (revision 16) an
    # open run whose dispute is unresolved, read without a state test
    # because the dispute exists only at `shaping` by construction. Whether
    # a queue file exists is never the test; the kernel fact is.
    # ... or (closed-loop spec §2) any open run with a pull request: waves
    # resume every one, whatever its labels, until it merges, a person stops
    # it, or it parks.
    # ... or any open run short of its pull request (2026-09-26): a human
    # ruling consumes the park, the issue still wears `bircher:running` from
    # the pass that parked, and neither the park clause nor the labels found
    # it -- #768 sat at `specified` after its approval until a person
    # relabelled it. A `cancelled` run without output is a stop, not work,
    # and stays out. This clause subsumes the revision-16 unresolved-dispute
    # test, which it replaced: a dispute exists only at `shaping`.
    if front.current_park(s, rid) is not None or (
            s.run_state(rid) in FRONT_HALF_STATES | SHAPING_STATES | {"planned"}) or (
            s.run_state(rid) == "sliced" and front.filing_complete(s, rid, front.epoch(s, rid)) is None) or (
            s.run_state(rid) in ("planned", "implementing", "reviewing", "merge_requested", "merged", "cancelled")
            and back.implementation_output_recorded(s, rid)):
        out.append(m.group(1))
print(" ".join(dict.fromkeys(out)))' 2>"${TMP_SWEEP_ERR:-/dev/null}") || parked_nums=""
  if [ -n "${TMP_SWEEP_ERR:-}" ] && [ -s "$TMP_SWEEP_ERR" ]; then
    echo "issues-to-queue: journal sweep failed ($(tr '\n' ' ' < "$TMP_SWEEP_ERR")); the queue was generated from labels alone" >&2
  fi
  [ -z "$parked_nums" ] || echo "parked runs to resume: $parked_nums" >&2
fi
[ "$DRY" = 1 ] || : > "$QUEUE/.manifest"
# EMITTED, not queued_nums membership (fix round 1, finding F1). An issue
# is_unblocked SKIPPED is still a member of queued_nums -- the label swap to
# `running` can fail (`_effect … || true`), or a person can re-add the label
# -- so testing membership there read a blocked label-derived item as
# "already handled" and the journal loop below dropped it too. That is the
# one reachable case in which a sibling blocker withholds a resumption that
# is past its own sequencing, which is exactly the outcome this union order
# exists to prevent. emitted_nums is what actually reached _emit_item.
emitted_nums=""
for n in $queued_nums; do
  is_unblocked "$n" || { echo "skip #$n (blocked)"; continue; }
  _emit_item "$n"
  emitted_nums="$emitted_nums $n"
done
# Journal-derived resumptions are unioned in AFTER is_unblocked, not before
# (shaping spec §5): the blocked check applies to label-derived items only.
# An open run is past its sequencing -- a sibling blocker reopened while it
# sat parked or disputed would otherwise withhold it every wave, with
# nothing on the issue itself to say why. A label-derived issue above is
# still checked, as today. The dedupe below reads emitted_nums, not
# queued_nums, for the same reason: an issue is_unblocked skipped never
# reached _emit_item, and the journal path is its only way in this wave.
for n in $parked_nums; do
  case " $emitted_nums " in *" $n "*) continue ;; esac
  _emit_item "$n"
done
echo "queued $count issue(s)"
