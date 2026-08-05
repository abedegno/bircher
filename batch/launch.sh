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

# NOTE: no "is a wave already running?" check here. run-queue.sh already holds a
# `flock -n` singleton lock and exits if a second run starts, so a check here
# would be redundant — and a `pgrep -f "bash batch/run-queue.sh"` version of it is
# actively wrong: pgrep -f matches any command line CONTAINING that string,
# including the `sh -c '... bash batch/run-queue.sh ...'` wrapper that invoked
# this script, so it refuses to launch on the strength of seeing itself. Let the
# lock do its job and surface what the runner says.

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

# Confirm it actually survived, rather than reporting success for a process that
# is already gone — the failure in #11 looked like a clean launch. The tell is the
# log: a reaped run leaves it 0 bytes ("empty log, no process"), while a live one
# has printed its preflight banner within a second or two.
#
# Deliberately NOT pgrep: `pgrep -f` matches this script's own invoking command
# line, so it reports success even when nothing started.
sleep 3
if [ ! -s "$LOG" ]; then
  echo "launch: FAILED — log is still empty ${LOG}; the run did not survive detach." >&2
  echo "        This is the #11 symptom. Check setsid is present and retry." >&2
  exit 1
fi

# Non-empty log only proves it got far enough to speak. Show the first line so the
# operator sees which it was: a real start, or run-queue's singleton refusal.
echo "launch: wave started (source=$SOURCE, log=$LOG)"
echo "        $(head -1 "$LOG")"
