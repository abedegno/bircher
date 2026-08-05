#!/usr/bin/env bash
# launch.sh — start a Bircher wave DETACHED so it survives the exec session.
#
# Issue #11. Launching the runner with `nohup ... &` from inside a
# `docker exec` / Portainer session dies silently: nohup blocks SIGHUP, but the
# exec session teardown reaps the whole process group anyway. The symptom is a
# 0-byte log and no process, which reads as "the runner crashed instantly"
# rather than "the launch never survived", and costs a diagnosis round every
# time someone hits it fresh.
#
# `setsid` puts the runner in a NEW session with no controlling terminal, so the
# teardown has nothing to reap. `< /dev/null` stops it blocking on a stdin that
# is about to disappear.
#
# Usage:
#   batch/launch.sh                    # BIRCHER_SOURCE=issues, log to run.log
#   batch/launch.sh --log wave.log     # custom log
#   batch/launch.sh --source queue     # drain queue/*.md instead of issues
#   batch/launch.sh --foreground       # run attached (debugging)
#
# Any other args pass through to run-queue.sh untouched.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HERE/../run.log"
SOURCE="${BIRCHER_SOURCE:-issues}"
FOREGROUND=0
PASSTHRU=()

while [ $# -gt 0 ]; do
  case "$1" in
    --log)        LOG="$2"; shift 2 ;;
    --source)     SOURCE="$2"; shift 2 ;;
    --foreground) FOREGROUND=1; shift ;;
    *)            PASSTHRU+=("$1"); shift ;;
  esac
done

cd "$HERE/.."

# Refuse to start a second wave over a live one. Two runners sharing a queue and
# a work repo interleave commits and fight over the same PRs.
if pgrep -f "bash batch/run-queue.sh" >/dev/null 2>&1; then
  echo "launch: a run-queue.sh is already running — refusing to start a second wave" >&2
  echo "        (pgrep -fa 'bash batch/run-queue.sh' to see it)" >&2
  exit 1
fi

if [ "$FOREGROUND" = 1 ]; then
  exec env BIRCHER_SOURCE="$SOURCE" bash batch/run-queue.sh "${PASSTHRU[@]+"${PASSTHRU[@]}"}"
fi

command -v setsid >/dev/null 2>&1 || {
  echo "launch: setsid not found — a detached launch would not survive this session." >&2
  echo "        Install util-linux, or use --foreground." >&2
  exit 1
}

: > "$LOG"
setsid env BIRCHER_SOURCE="$SOURCE" \
  bash batch/run-queue.sh "${PASSTHRU[@]+"${PASSTHRU[@]}"}" > "$LOG" 2>&1 < /dev/null &

# Confirm it actually survived rather than reporting success on a process that is
# already gone — the exact failure #11 describes looked like a clean launch.
sleep 2
if pgrep -f "bash batch/run-queue.sh" >/dev/null 2>&1; then
  echo "launch: wave started (source=$SOURCE, log=$LOG)"
else
  echo "launch: FAILED — no run-queue.sh process 2s after launch. Log tail:" >&2
  tail -5 "$LOG" >&2 2>/dev/null
  exit 1
fi
