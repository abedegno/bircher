#!/usr/bin/env bash
# Render bircher:queued + UNBLOCKED GitHub issues into queue/i<n>-<slug>.md files
# that run-queue.sh drains unchanged. Sourced helper: _render_issue_item.
# Usage: issues-to-queue.sh [--dry-run]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-abedegno/muesli}"
QUEUE="${QUEUE:-$HERE/../queue}"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
# shellcheck source=/dev/null
eval "$(sed -n '/^_render_issue_item()/,/^}/p' "$HERE/run-queue.sh")"

slug() { printf '%s' "$1" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//' | cut -c1-40; }

# unblocked = no OPEN blocked_by dependency
is_unblocked() {
  local blockers; blockers=$(gh api "/repos/$REPO/issues/$1/dependencies/blocked_by" \
    --jq '[.[] | select(.state=="open")] | length' 2>/dev/null || echo 0)
  [ "${blockers:-0}" -eq 0 ]
}

mkdir -p "$QUEUE"
count=0
# priority order p1->p3, then issue number
queued_nums=$(gh issue list --repo "$REPO" --state open --limit 200 \
               --json number,labels \
               --jq '[.[] | select(any(.labels[].name; .=="bircher:queued"))]
                     | sort_by((.labels|map(.name)|map(select(startswith("priority:")))|.[0] // "priority:p9"), .number)
                     | .[].number') || { echo "issues-to-queue: gh issue list failed" >&2; exit 1; }
[ "$DRY" = 1 ] || : > "$QUEUE/.manifest"
for n in $queued_nums; do
  is_unblocked "$n" || { echo "skip #$n (blocked)"; continue; }
  title=$(gh issue view "$n" --repo "$REPO" --json title --jq .title)
  body=$(gh issue view "$n" --repo "$REPO" --json body --jq .body)
  out="$QUEUE/i${n}-$(slug "$title").md"
  if [ "$DRY" = 1 ]; then
    echo "would write $out"
  else
    _render_issue_item "$n" "$title" "$body" > "$out"
    basename "$out" >> "$QUEUE/.manifest"
    echo "wrote $out"
  fi
  count=$((count+1))
done
echo "queued $count issue(s)"
