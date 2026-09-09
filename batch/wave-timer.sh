#!/usr/bin/env bash
# wave-timer.sh — the scheduled wave. Started once, in the background, by the
# runner container's boot command; launches a wave every interval.
#
# Why it exists. A run that parks for a person posts its notice on the issue
# (spec section 4) and then waits for THE NEXT WAVE to read the reply: nothing
# happens when the person types. Without a schedule, every park waits for
# someone to launch a wave by hand at exactly the moment they cannot know is
# needed -- a run that parked at three in the morning and was answered at
# eight sat until someone remembered. With one, the schedule IS the resume
# sweep: the queue generator asks the journal for parked runs (finding 12),
# the wave reads the reply, and the run continues.
#
# Safe to fire into anything: run-queue.sh holds a flock singleton, so a
# firing that overlaps a live wave is refused at once; a wave that finds
# nothing queued and nothing parked prints "queue empty" and exits; a failed
# preflight (vendor auth, kernel, labels) refuses to start.
#
# Every setting is an environment variable with a default, so the compose
# command carries one line and the behaviour lives here, reviewed and tested.
#   BIRCHER_WAVE_DIR         the bircher checkout to launch from
#   BIRCHER_WAVE_INTERVAL_S  seconds between firings (default 1800)
#   BIRCHER_WAVE_KERNEL_DB   the kernel database the wave uses
#   BIRCHER_WAVE_ONCE        set to 1 to fire once and exit (tests)
set -u
DIR="${BIRCHER_WAVE_DIR:-/workspaces/bircher-v2}"
INTERVAL="${BIRCHER_WAVE_INTERVAL_S:-1800}"
DB="${BIRCHER_WAVE_KERNEL_DB:-$DIR/.run/kernel-muesli.db}"

fire() {
  # A missing checkout is not an error to crash on: the container may boot
  # before the volume is populated. Say so and try again next interval.
  if [ ! -f "$DIR/batch/launch.sh" ]; then
    echo "wave-timer: no checkout at $DIR; skipping" >&2
    return 0
  fi
  local stamp; stamp=$(date +%Y%m%d-%H%M%S)
  mkdir -p "$DIR/.run/timer"
  # launch.sh truncates its --log, so every firing gets its own file: the
  # previous wave's log is evidence, not scratch.
  ( cd "$DIR" && BIRCHER_KERNEL_DB="$DB" bash batch/launch.sh --source issues \
      --log ".run/timer/$stamp.log" ) 2>&1 | sed "s/^/wave-timer $stamp: /"
  return 0
}

if [ "${BIRCHER_WAVE_ONCE:-0}" = 1 ]; then
  fire; exit 0
fi
echo "wave-timer: every ${INTERVAL}s from $DIR (db $DB)" >&2
while true; do
  sleep "$INTERVAL"
  fire
done
