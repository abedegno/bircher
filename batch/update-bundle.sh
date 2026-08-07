#!/usr/bin/env bash
# update-bundle.sh [ref] — refresh this bircher checkout on the runner.
#
# The bundle is the agent code omnigent uploads at the start of every wave, so
# "deploying bircher" means updating this checkout. That was previously an
# undocumented `git pull` typed by hand on the runner, which is easy to forget
# and easy to get wrong: run-queue.sh parses itself into memory at start, so a
# mid-run update silently applies to the NEXT wave, not the one you are watching.
#
# Usage:
#   batch/update-bundle.sh              # update to origin/main
#   batch/update-bundle.sh some-branch  # update to a branch or tag
#
# Runs the self-test afterwards, because a bundle that does not pass its own
# tests should not be sitting on the runner waiting for the next wave.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."

REF="${1:-main}"

# A wave in flight already uploaded its bundle, so updating now cannot corrupt it
# — but the operator should know the change lands on the NEXT wave, not this one.
if [ -f run.log ] && pgrep -f "run-queue.sh" >/dev/null 2>&1; then
  echo "update-bundle: NOTE a wave appears to be running." >&2
  echo "               It uploaded its bundle at start, so this update is safe," >&2
  echo "               but it applies to the NEXT wave, not the running one." >&2
fi

# Refuse to move a dirty tree. `git checkout -B` can carry local edits onto a new
# ref or fail halfway, and on a runner an uncommitted change is far more likely to
# be someone debugging live than something worth keeping — losing it silently
# during a routine deploy would be the worst outcome.
# `--untracked-files=no` is deliberate. A runner ALWAYS has untracked artifacts —
# queue/processed/*.md, run logs, .run/ — and none of them affect a checkout. Only
# modified TRACKED files can be carried onto another ref or lost, and those are the
# ones likely to be someone debugging live. Blocking on untracked files made this
# script refuse to run on a perfectly normal runner.
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "update-bundle: refusing to update — tracked files have uncommitted changes:" >&2
  git status --short --untracked-files=no >&2
  echo "               Commit, stash or discard them first." >&2
  exit 1
fi

echo "update-bundle: fetching…"
git fetch origin --prune --quiet

before=$(git rev-parse --short HEAD)

# Detach-safe: works for a branch, a tag, or a raw sha.
if git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
  git checkout --quiet -B "$REF" "origin/$REF"
else
  git checkout --quiet "$REF"
fi

after=$(git rev-parse --short HEAD)

if [ "$before" = "$after" ]; then
  echo "update-bundle: already at $after ($REF)"
else
  echo "update-bundle: $before -> $after ($REF)"
  git --no-pager log --oneline "$before..$after" 2>/dev/null | sed 's/^/  /' || true
fi

echo "update-bundle: running self-test…"
if bash batch/run-queue.sh --self-test >/tmp/bundle-selftest.log 2>&1; then
  echo "update-bundle: self-test OK — bundle ready"
else
  echo "update-bundle: SELF-TEST FAILED — this bundle should not run a wave." >&2
  tail -15 /tmp/bundle-selftest.log >&2
  exit 1
fi
