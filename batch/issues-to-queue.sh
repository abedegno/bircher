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
# Journal-driven resumption (spec section 5). A run that parked for a person
# has already lost its bircher:queued label -- the runner swapped it for
# running when it took the item -- and its queue file may be gone too, so the
# labels alone cannot find it and neither can the queue directory. The kernel
# can: an open run holding a current `parked` fact is exactly the work the
# next wave must pick up, and it is queued here whatever the issue's labels
# say. Its item renders from the live issue like any other; run_item adopts
# the open run rather than minting a second one. Off when there is no kernel
# database to ask.
parked_nums=""
if [ -n "${BIRCHER_KERNEL_DB:-}" ] && [ -f "$BIRCHER_KERNEL_DB" ]; then
  parked_nums=$(PYTHONPATH="$HERE/../v2" "${BIRCHER_PY:-python3}" -c '
import os, re, sys
from kernel import front
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
closed = {"ended", "cancelled"}
out = []
for rid in s.all_run_ids():
    m = re.match(r"^i(\d+)-", rid)
    if not m or s.run_state(rid) in closed:
        continue
    # A parked run, whatever its labels (finding 12); and a sliced run whose
    # filing is not complete (shaping spec §5) -- the crash-anywhere-during-
    # filing case -- so the next pass reaches file_owed. Whether a queue
    # file exists is never the test; the kernel fact is.
    if front.current_park(s, rid) is not None or (
            s.run_state(rid) == "sliced" and front.filing_complete(s, rid, front.epoch(s, rid)) is None):
        out.append(m.group(1))
print(" ".join(dict.fromkeys(out)))' 2>/dev/null) || parked_nums=""
  [ -z "$parked_nums" ] || echo "parked runs to resume: $parked_nums" >&2
fi
# Union, label-queued first, then parked runs not already listed.
for n in $parked_nums; do
  case " $queued_nums " in *" $n "*) ;; *) queued_nums="$queued_nums $n" ;; esac
done
[ "$DRY" = 1 ] || : > "$QUEUE/.manifest"
for n in $queued_nums; do
  is_unblocked "$n" || { echo "skip #$n (blocked)"; continue; }
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
done
echo "queued $count issue(s)"
