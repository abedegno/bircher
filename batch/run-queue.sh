#!/usr/bin/env bash
# Bircher batch runner: work queue/*.md one at a time through a
# fresh Bircher session each, detect completion via the PR's bircher-status
# marker, append a scorecard row, then move on. Sequential by design (M4).
set -uo pipefail

# _derive_bundle_dir <script-path> -> the bundle root (the checkout containing batch/,
# skills/, agents/, config.yaml): the parent of the script's dir. Works flattened
# (/workspaces/bircher/batch/run-queue.sh -> /workspaces/bircher) and nested
# (.../agents/bircher/batch/run-queue.sh -> .../agents/bircher). PURE-ish (needs the dir to exist).
_derive_bundle_dir() { ( cd "$(dirname "$1")/.." && pwd ); }

REPO="${BIRCHER_REPO:-abedegno/muesli}"
WORKDIR="${WORKDIR:-/workspaces/muesli}"                        # the WORK repo (target app)
BUNDLE_DIR="${BIRCHER_BUNDLE_DIR:-$(_derive_bundle_dir "${BASH_SOURCE[0]}")}"  # the bircher checkout
QUEUE="${QUEUE:-$BUNDLE_DIR/queue}"
PROCESSED="$QUEUE/processed"
SCORECARD="${SCORECARD:-$BUNDLE_DIR/.run/scorecard.jsonl}"
DEFERRED_READY_FILE="${DEFERRED_READY_FILE:-$BUNDLE_DIR/.run/deferred-ready.tsv}"
# No-op signal dir: the coordinator drops <code>.noop here when an item is
# already satisfied (no product change needed) so the runner records a `noop`
# and advances instantly instead of polling out the full ITEM_TIMEOUT (gap #3).
NOOP_DIR="${BIRCHER_NOOP_DIR:-/workspaces/.bircher-noop}"
SERVER="${OMNIGENT_SERVER:-http://omnigent:8000}"
# Safety cap (NOT the primary done-signal): completion is detected from the PR
# marker or a dead server session (see run_item). 90 min lets a legitimately
# long coordinator (multi-round in-run fix-loop) finish; the omnigent 0.4
# reaper no longer caps sessions at ~30 min.
ITEM_TIMEOUT="${ITEM_TIMEOUT:-5400}"
POLL="${POLL_INTERVAL:-45}"
# Layer-2 recovery: vendor for the out-of-band review when a coordinator dies
# before posting its marker. Default codex = opposite the standing claude_code
# implementer (cross-vendor). Override for a codex-implemented item.
RECOVERY_REVIEWER="${BIRCHER_RECOVERY_REVIEWER:-codex}"
# B-1 in-run merge: when an item completes outcome=ready (CI green + independent
# cross-vendor pass - the same gate the human applied mechanically), merge its
# PR BEFORE launching the next item, so every later item builds on merged
# siblings and the merge-order conflict class (run #15: 3 reviewed-ready PRs
# lost to serial admin-nav conflicts) disappears. Opt out with =0.
INRUN_MERGE="${BIRCHER_INRUN_MERGE:-1}"
# How long to watch MAIN's CI on each merge commit before halting conservatively.
MAIN_CI_TIMEOUT="${BIRCHER_MAIN_CI_TIMEOUT:-900}"
# B-3 vendor allocation: which vendor implements each item.
#   auto (default) = usage-aware selection that balances the two subscriptions'
#   WEEKLY windows (pick the lower used_percent) and rides out 5h-window
#   exhaustion by waiting for the sooner reset; claude_code | codex = pinned.
# A per-item queue-file tag `bircher-implementer: <vendor>` overrides everything.
# auto is the DEFAULT: the codex-as-implementer quality pilot passed (2026-07-08,
# EXP01 PR #246 -- ci_first, rounds=1, zero blocking findings). The cross-vendor
# reviewer is always the opposite vendor and CI gates every PR, so a weaker
# implementation is caught regardless of vendor. Pin with
# BIRCHER_IMPLEMENTER=claude_code to force a single-vendor run.
IMPLEMENTER="${BIRCHER_IMPLEMENTER:-auto}"
# 5h-window utilization (%) above which a vendor is excluded from selection.
FIVEH_MAX="${BIRCHER_5H_MAX:-92}"
# Claude usage: read live from Claude Code's OWN statusLine, harvested by a
# short PTY probe (claude-usage-probe.py). It runs the genuine `claude` binary
# interactively with a one-shot --settings statusLine override + one trivial
# turn, then reads the authoritative account-wide rate_limits.five_hour/seven_day
# {used_percentage, resets_at} Claude feeds its own statusLine hook -- the exact
# data `/usage` shows, reflecting ALL consumption sources. This is ToS-clean (it
# is Claude Code running + reporting its own usage; NO OAuth-token reuse and NO
# scope-gated endpoint) and works with the runner's inference-only setup-token
# (rate_limits come from the inference response, not the user:profile endpoint).
# Verified live on macOS + the NAS runner 2026-07-08. Probing costs one tiny
# claude turn, so we cache the tuple briefly and reuse. codex usage is read from
# the newest ~/.codex rollout.
CLAUDE_USAGE_CACHE="${BIRCHER_CLAUDE_USAGE_CACHE:-/tmp/claude-usage.tuple}"
CLAUDE_USAGE_TTL="${BIRCHER_CLAUDE_USAGE_TTL:-150}"
CLAUDE_USAGE_PROBE_TIMEOUT="${BIRCHER_CLAUDE_USAGE_PROBE_TIMEOUT:-55}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
CLAUDE_USAGE_PROBE="${BIRCHER_CLAUDE_USAGE_PROBE:-$SELF_DIR/claude-usage-probe.py}"
CODEX_SESSIONS_DIR="${BIRCHER_CODEX_SESSIONS_DIR:-/root/.codex/sessions}"
export PATH="/root/bin:$PATH"

# -- Dedicated bircher runner (Phase B2) ----------------------------------------
# Isolation mechanism: this script runs INSIDE the omnigent-runner-bircher
# container.  When 'omnigent run' is exec'd here it auto-binds the session to
# the co-located runner -- verified live: session launched in the bircher
# container bound to runner_token_1c7bae19.../host_83b59621..., not no2's.
#
# OMNIGENT_RUNNER is exported so any transitive omnigent.sh calls made by child
# processes also target this container (belt-and-suspenders).  No runner_id PATCH
# is needed or possible: the real runner id is a per-connection runner_token_<hex>
# that is not known in advance and changes each connection.
export OMNIGENT_RUNNER="${OMNIGENT_RUNNER:-omnigent-runner-bircher}"
# -------------------------------------------------------------------------------

# parse_marker <text> -> "outcome|ci|ci_first|review|rounds|note"; rc 1 if no marker
parse_marker() {
  local line
  # Extract the marker WHEREVER it appears, not only at a line start. Coordinators
  # sometimes post `gh pr comment --body "...\nbircher-status:..."` where the \n is
  # a LITERAL backslash-n (bash double quotes don't expand it), so the marker sits
  # mid-line and a strict `^bircher-status:` anchor misses it -- the item then polls
  # to timeout and never merges (EXP02, 2026-07-08). `grep -oE` pulls the marker
  # substring from wherever it begins to end-of-line; tail -1 takes the last if the
  # prose mentions it more than once. Portable (no sed newline substitution).
  line=$(printf '%s\n' "$1" | grep -oE 'bircher-status:.*' | tail -1)
  [ -n "$line" ] || { echo "|||||"; return 1; }
  local o c cf r n note
  o=$(printf '%s' "$line"  | sed -n 's/.*outcome=\([^ ]*\).*/\1/p')
  c=$(printf '%s' "$line"  | sed -n 's/.* ci=\([^ ]*\).*/\1/p')
  cf=$(printf '%s' "$line" | sed -n 's/.* ci_first=\([^ ]*\).*/\1/p')
  r=$(printf '%s' "$line"  | sed -n 's/.*review=\([^ ]*\).*/\1/p')
  n=$(printf '%s' "$line"  | sed -n 's/.*rounds=\([0-9]*\).*/\1/p')
  note=$(printf '%s' "$line" | sed -n 's/.*note="\([^"]*\)".*/\1/p')
  echo "${o}|${c}|${cf}|${r}|${n}|${note}"
}

# _extract_verdict <text> -> "PASS" | "FAIL" | "" (empty).
# The cross-review contract puts the verdict on the final line, so the LAST
# match is authoritative even if the reviewer echoed the token earlier in prose.
_extract_verdict() {
  printf '%s\n' "$1" | grep -oE 'VERDICT: (PASS|FAIL)' | tail -n1 | sed 's/^VERDICT: //'
}

# _normalize_ci <newline-separated gh check buckets> -> green|red|pending
# `gh pr checks --json bucket` emits one bucket per check:
#   pass | fail | pending | skipping | cancel
# Precedence: any fail/cancel -> red; else any pending -> pending; else green.
# No checks at all (empty) -> pending: CI has not registered yet, do not review.
_normalize_ci() {
  local buckets="$1"
  [ -z "${buckets//[[:space:]]/}" ] && { echo pending; return; }
  if printf '%s\n' "$buckets" | grep -qE '^(fail|cancel)$'; then echo red; return; fi
  if printf '%s\n' "$buckets" | grep -qE '^pending$'; then echo pending; return; fi
  echo green
}

# classify_recovery <pr> <ci_state> <verdict> -> "outcome|review|ci|note"
# Pure mapping from ground truth to a scorecard row. Maps ONLY onto the existing
# outcome vocabulary; the RECOVERED: note carries the detail. Reads the global
# RECOVERY_REVIEWER for the review-vendor label.
classify_recovery() {
  local pr="$1" ci="$2" verdict="$3"
  if [ -z "$pr" ]; then
    echo "timeout|na|na|no PR at timeout (reaped before implement delivered)"; return
  fi
  case "$ci" in
    red)     echo "failed|na|red|RECOVERED: PR up, CI red, coordinator died before fix"; return ;;
    pending) echo "escalated|na|pending|RECOVERED: CI still pending at timeout"; return ;;
  esac
  # ci == green
  case "$verdict" in
    PASS) echo "ready|${RECOVERY_REVIEWER}:pass|green|RECOVERED: coordinator reaped; out-of-band review PASS" ;;
    FAIL) echo "failed|${RECOVERY_REVIEWER}:fail|green|RECOVERED: out-of-band review FAIL" ;;
    *)    echo "escalated|${RECOVERY_REVIEWER}:na|green|RECOVERED: review produced no verdict; needs human" ;;
  esac
}

# _checkrun_state <lines of "status|conclusion"> -> green|red|pending
# Classifies GitHub check-runs on a commit (gh api .../check-runs). RED on any
# failing conclusion; PENDING while anything is queued/in_progress or when no
# check-runs have registered yet (empty input - never treat silence as green);
# otherwise GREEN (success/neutral/skipped).
_checkrun_state() {
  local lines="$1"
  [ -z "${lines//[[:space:]]/}" ] && { echo pending; return; }
  if printf '%s\n' "$lines" | grep -qE '\|(failure|cancelled|timed_out|action_required|stale)$'; then echo red; return; fi
  if printf '%s\n' "$lines" | grep -qE '^(queued|in_progress)\|'; then echo pending; return; fi
  echo green
}

# _classify_ci_failure <failed_step_count> -> infra|genuine   (PURE, self-tested)
# A red CI run whose failed/cancelled jobs produced ZERO failed STEPS never
# actually ran the tests -- transient GitHub infra ("job not acquired by Runner",
# startup_failure, or a fail-fast cancellation with no real failure). A genuine
# test failure always leaves at least one failed step. (B-5, 2026-07-09: PIN01
# #264 showed all jobs red at 15m01s = runner-acquisition timeout; a plain re-run
# went green.) Unknown/empty count -> genuine, so we never loop re-runs blindly.
_classify_ci_failure() {
  [ "${1:-0}" -gt 0 ] 2>/dev/null && echo genuine || echo infra
}

# _ci_run_id <pr> -> databaseId of the PR head branch's most recent CI run ("" on failure).
_ci_run_id() {
  local pr="$1" ref
  ref=$(gh pr view "$pr" --repo "$REPO" --json headRefName -q .headRefName 2>/dev/null) || return 1
  [ -n "$ref" ] || return 1
  gh run list --repo "$REPO" --branch "$ref" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null
}

# _ci_failure_kind <pr> -> infra|genuine (best-effort; defaults genuine on any
# lookup failure so a real red is never mistaken for infra).
_ci_failure_kind() {
  local pr="$1" rid fsc
  rid=$(_ci_run_id "$pr") || { echo genuine; return; }
  [ -n "$rid" ] || { echo genuine; return; }
  fsc=$(gh run view "$rid" --repo "$REPO" --json jobs \
    -q '[.jobs[] | select(.conclusion=="failure" or .conclusion=="cancelled") | .steps[]? | select(.conclusion=="failure")] | length' 2>/dev/null)
  [ -n "$fsc" ] || fsc=1
  _classify_ci_failure "$fsc"
}

# _poll_ci <pr> <timeout_s> -> green|red|pending
# Poll `gh pr checks` until CI settles (not pending) or the timeout elapses.
_poll_ci() {
  local pr="$1" timeout="${2:-900}" w=0 buckets ci
  while [ "$w" -lt "$timeout" ]; do
    buckets=$(gh pr checks "$pr" --repo "$REPO" --json bucket -q '.[].bucket' 2>/dev/null)
    ci=$(_normalize_ci "$buckets")
    [ "$ci" != pending ] && { echo "$ci"; return; }
    sleep 30; w=$((w + 30))
  done
  echo pending
}

# _wait_ci <pr> -> settle CI (B-5 part 2). Used when a coordinator DIED while CI
# was still running: run-queue survives the wait, so wait for CI to finish
# (queue delays pushed CI to ~12min+ this window) rather than escalating on
# 'pending'. Bounded by BIRCHER_CI_WAIT (default 1500s).
_wait_ci() { _poll_ci "$1" "${BIRCHER_CI_WAIT:-1500}"; }

# _rerun_and_wait_ci <pr> -> final ci state after re-running the failed jobs and
# polling until CI settles (B-5 part 1; bounded by BIRCHER_CI_RERUN_WAIT).
_rerun_and_wait_ci() {
  local pr="$1" rid
  rid=$(_ci_run_id "$pr") || { echo red; return; }
  [ -n "$rid" ] || { echo red; return; }
  gh run rerun "$rid" --repo "$REPO" --failed >/dev/null 2>&1 || gh run rerun "$rid" --repo "$REPO" >/dev/null 2>&1
  sleep 20
  _poll_ci "$pr" "${BIRCHER_CI_RERUN_WAIT:-900}"
}

# _is_limit_message <text> -> yes|no. Matches the provider usage-limit
# signature a coordinator emits as its FIRST message when the window is
# exhausted (run #11: "You've hit your session limit / resets 6pm ...").
_is_limit_message() {
  printf '%s' "$1" | grep -qiE "hit your (session|usage|weekly) limit|usage limit (reached|hit)" \
    && echo yes || echo no
}

# _pick_implementer <c5> <c5reset> <c7> <x5> <x5reset> <x7> <now>
#   -> claude_code | codex | wait:<epoch>
# PURE usage-aware vendor selection ("-" = signal unavailable):
#   1. Exclude a vendor whose 5h window used_percent >= FIVEH_MAX.
#   2. Both excluded -> wait:<soonest 5h reset>.
#   3. Among eligible, pick the LOWER WEEKLY used_percent (balances the two
#      subscriptions against their own allocations - percentages normalize
#      unequal quotas/burn rates). Missing signal = eligible with weekly 0
#      (never block on absent data). Tie -> claude_code.
_pick_implementer() {
  local c5="$1" c5r="$2" c7="$3" x5="$4" x5r="$5" x7="$6" now="$7"
  local c_ok=1 x_ok=1
  [ "$c5" != "-" ] && [ "${c5%.*}" -ge "$FIVEH_MAX" ] 2>/dev/null && c_ok=0
  [ "$x5" != "-" ] && [ "${x5%.*}" -ge "$FIVEH_MAX" ] 2>/dev/null && x_ok=0
  if [ "$c_ok" = 0 ] && [ "$x_ok" = 0 ]; then
    local w="${c5r:--}"
    [ "$x5r" != "-" ] && { [ "$w" = "-" ] || [ "$x5r" -lt "$w" ] 2>/dev/null; } && w="$x5r"
    [ "$w" = "-" ] && w=$((now + 900))
    echo "wait:$w"; return
  fi
  [ "$c_ok" = 0 ] && { echo codex; return; }
  [ "$x_ok" = 0 ] && { echo claude_code; return; }
  local cw="${c7%.*}" xw="${x7%.*}"
  [ "$c7" = "-" ] && cw=0; [ "$x7" = "-" ] && xw=0
  if [ "${xw:-0}" -lt "${cw:-0}" ] 2>/dev/null; then echo codex; else echo claude_code; fi
}

# _claude_usage -> "5h_pct|5h_reset|7d_pct|7d_reset" (percent 0-100, resets as
# epochs), or non-zero when the probe fails (no claude / PTY drive failed / no
# rate_limits captured) -- callers degrade to the default vendor. A fresh cached
# tuple (< CLAUDE_USAGE_TTL) is served first so back-to-back gate evaluations do
# not each pay a probe turn. The probe (claude-usage-probe.py) runs the genuine
# `claude` binary and reads its own statusLine -- see the config note above.
_claude_usage() {
  local now age tup
  now=$(date +%s)
  if [ -f "$CLAUDE_USAGE_CACHE" ]; then
    age=$(( now - $(stat -c %Y "$CLAUDE_USAGE_CACHE" 2>/dev/null || stat -f %m "$CLAUDE_USAGE_CACHE" 2>/dev/null || echo 0) ))
    if [ "$age" -ge 0 ] && [ "$age" -lt "$CLAUDE_USAGE_TTL" ]; then
      tup=$(cat "$CLAUDE_USAGE_CACHE" 2>/dev/null)
      [ -n "$tup" ] && { printf '%s\n' "$tup"; return 0; }
    fi
  fi
  [ -f "$CLAUDE_USAGE_PROBE" ] || return 1
  tup=$(python3 "$CLAUDE_USAGE_PROBE" "$CLAUDE_USAGE_PROBE_TIMEOUT" 2>/dev/null) || return 1
  [ -n "$tup" ] || return 1
  mkdir -p "$(dirname "$CLAUDE_USAGE_CACHE")" 2>/dev/null
  printf '%s\n' "$tup" > "$CLAUDE_USAGE_CACHE"
  printf '%s\n' "$tup"
}

# _parse_codex_rate_limits: read ONE codex rollout JSONL line on stdin, emit
# "5h_pct|5h_reset|7d_pct|7d_reset" (percent 0-100, resets as epochs; "-" for an
# absent window). The whole line is parsed as JSON and searched for a rate_limits
# object; each present window (primary/secondary) is classified by window_minutes
# -- <=360 -> 5h bucket, >=1440 -> weekly bucket -- because the labels are
# plan-dependent (codex-cli 0.144.x "prolite" exposes only a weekly primary with
# secondary:null; legacy plans had a 5h primary + a weekly secondary). Prints
# nothing on any parse failure so callers degrade to the default vendor.
_parse_codex_rate_limits() {
  python3 -c '
import json,sys
line=sys.stdin.read().strip()
def find_rl(o):
    if isinstance(o,dict):
        rl=o.get("rate_limits")
        if isinstance(rl,dict): return rl
        for v in o.values():
            r=find_rl(v)
            if r: return r
    elif isinstance(o,list):
        for v in o:
            r=find_rl(v)
            if r: return r
    return None
try:
    rl=find_rl(json.loads(line))
except Exception:
    sys.exit(0)
if not isinstance(rl,dict): sys.exit(0)
five=("-","-"); week=("-","-")
for w in (rl.get("primary"), rl.get("secondary")):
    if not isinstance(w,dict): continue
    up=w.get("used_percent")
    if up is None: continue
    rs=w.get("resets_at"); rs="-" if rs is None else rs
    wm=w.get("window_minutes")
    if isinstance(wm,(int,float)) and wm<=360: five=(up,rs)
    elif isinstance(wm,(int,float)) and wm>=1440: week=(up,rs)
print("%s|%s|%s|%s" % (five[0],five[1],week[0],week[1]))
'
}

# _codex_usage -> "5h_pct|5h_reset|7d_pct|7d_reset" from the LAST rate_limits
# snapshot in the NEWEST codex rollout file (every `codex exec` - incl. the
# preflight probe - writes one). Non-zero when no rollout / no snapshot so
# callers degrade to the default vendor.
_codex_usage() {
  local f line
  f=$(ls -t "$CODEX_SESSIONS_DIR"/*/*/*/rollout-*.jsonl 2>/dev/null | head -1)
  [ -n "$f" ] || return 1
  line=$(grep '"rate_limits"' "$f" 2>/dev/null | tail -1)
  [ -n "$line" ] || return 1
  printf '%s' "$line" | _parse_codex_rate_limits
}

# _session_died <status> <err_code> -> "died" | "alive"
# The server session is DEAD (recovery may run) only when it has a non-empty
# last_task_error_code OR a terminal-dead status. It is ALIVE for running AND
# idle: a coordinator ends its turn and goes idle between turns while a
# sub-agent runs, then is woken on delivery -- idle is NOT terminal. Empty /
# unknown status -> alive (never recover against a session we can't confirm
# dead).
_session_died() {
  local status="$1" errcode="$2"
  [ -n "${errcode//[[:space:]]/}" ] && { echo died; return; }
  case "$status" in
    failed|error|cancelled) echo died ;;
    *)                      echo alive ;;
  esac
}

# _session_state <conv_id> -> "<status>|<err_code>" ("|" on any failure).
# Reads the server session so run_item can detect death (vs the ambiguous
# local client process). $SERVER is the omnigent server (e.g. http://omnigent:8000).
_session_state() {
  local conv_id="$1" json
  json=$(curl -sf --max-time 10 "$SERVER/v1/sessions/$conv_id" 2>/dev/null) || { printf '|'; return; }
  printf '%s' "$json" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print("|"); sys.exit(0)
print("%s|%s" % (d.get("status") or "", (d.get("labels") or {}).get("omnigent.last_task_error_code") or ""))
' 2>/dev/null || printf '|'
}

# _last_assistant_text <conv_id> [n] -> concatenated text of the newest n (default
# 3) assistant messages in the conversation, one blob on stdout ("" on any error).
# Used by the within-item fast limit check (B-2): a young session whose first
# assistant turn is a provider "hit your limit" message means the implementer
# vendor's window is exhausted -- fail fast and re-gate rather than idle the cap.
# (omnigent/server/API.md: GET /v1/conversations/{id}/items.)
_last_assistant_text() {
  local conv_id="$1" n="${2:-3}" json
  json=$(curl -sf --max-time 10 "$SERVER/v1/conversations/$conv_id/items?order=desc&limit=20" 2>/dev/null) || return 0
  printf '%s' "$json" | python3 -c '
import json,sys
n=int(sys.argv[1])
try: data=json.load(sys.stdin).get("data") or []
except Exception: sys.exit(0)
out=[]
for it in data:
    if it.get("role")!="assistant": continue
    for c in (it.get("content") or []):
        t=c.get("text")
        if t: out.append(t)
    if len(out)>=n: break
print("\n".join(out))
' "$n" 2>/dev/null || true
}

# --- REST launch helpers (omnigent server API; see omnigent/server/API.md) ----

# _http_json <method> <path> [json_body] -> response body on stdout; curl rc.
_http_json() {
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sf --max-time 30 -X "$method" "$SERVER$path" \
      -H 'content-type: application/json' -d "$body" 2>/dev/null
  else
    curl -sf --max-time 30 -X "$method" "$SERVER$path" 2>/dev/null
  fi
}

# _json_get <key> -- read stdin JSON, print d[key] or "" (never errors).
_json_get() {
  python3 -c 'import json,sys
try: print(json.load(sys.stdin).get(sys.argv[1]) or "")
except Exception: print("")' "$1" 2>/dev/null
}

# _upload_bundle <agent_dir> <title> -> holder session_id ("" on failure).
# Multipart POST /v1/sessions {metadata, bundle} -> 201 {session_id}.
_upload_bundle() {
  local dir="$1" title="$2" tgz resp
  tgz=$(mktemp "/tmp/bircher-bundle-XXXXXX.tgz") || return 1
  tar czf "$tgz" -C "$dir" . 2>/dev/null || { rm -f "$tgz"; return 1; }
  resp=$(curl -sf --max-time 60 -X POST "$SERVER/v1/sessions" \
    -F "metadata={\"title\":\"$title\"}" \
    -F "bundle=@$tgz;type=application/gzip" 2>/dev/null)
  rm -f "$tgz"
  printf '%s' "$resp" | _json_get session_id
}

# _get_agent_id <session_id> -> agent_id ("") via SessionResponse.
_get_agent_id() { _http_json GET "/v1/sessions/$1" | _json_get agent_id; }

# _create_session <agent_id> <host_id> <workspace> -> conv_id ("") = SessionResponse.id
_create_session() {
  local body
  body=$(python3 -c 'import json,sys; print(json.dumps({"agent_id":sys.argv[1],"host_id":sys.argv[2],"workspace":sys.argv[3]}))' "$1" "$2" "$3" 2>/dev/null) || return 1
  _http_json POST "/v1/sessions" "$body" | _json_get id
}

# _send_prompt <conv_id> <prompt> -> rc 0 on success. POST events (message).
# Uses a 120s timeout (not _http_json's 30s): per API.md, a message POSTed
# before the host-launched runner settles WAITS for the launch - on a cold
# start that exceeded 30s and logged a false "send_prompt failed" (run #13
# EMB02) even though the server delivered the message.
_send_prompt() {
  local body
  body=$(python3 -c 'import json,sys; print(json.dumps({"type":"message","data":{"role":"user","content":[{"type":"input_text","text":sys.argv[1]}]}}))' "$2" 2>/dev/null) || return 1
  curl -sf --max-time 120 -X POST "$SERVER/v1/sessions/$1/events" \
    -H 'content-type: application/json' -d "$body" >/dev/null 2>&1
}

# _stop_session <conv_id> -> POST stop_session (hard-terminate incl. host runner).
_stop_session() {
  _http_json POST "/v1/sessions/$1/events" '{"type":"stop_session"}' >/dev/null 2>&1 \
    || echo "[batch] WARN: stop_session for $1 failed" >&2
}

# _prune_session <session_id> -> DELETE a session. MANUAL-CLEANUP-ONLY for
# holders: upstream #1388 - the holder OWNS the run's session-scoped agent, so
# deleting it cascade-deletes EVERY session of the run (coordinators, children,
# their items) - i.e. the run's entire UI-visible history and forensic record.
# Run #11b's history was lost exactly this way (pruned at run end). Never call
# this on a holder whose run's history you still want; run-queue itself only
# prunes a DUD holder (failed agent_id lookup, no run ever started).
_prune_session() {
  curl -sf --max-time 15 -X DELETE "$SERVER/v1/sessions/$1" >/dev/null 2>&1 \
    || echo "[batch] WARN: prune of session $1 failed" >&2
}

# _post_cross_review_status <item> <pr> -> post + VERIFY a `bircher/cross-review`
# = success commit status on the PR's head commit, so a repo whose branch protection
# REQUIRES that check (in lieu of an approving review) can self-merge. Only called
# from merge_ready_pr, which the caller reaches ONLY on an outcome=ready item
# (cross-vendor review PASS) - so the status is only ever posted on a genuine PASS.
# Posted as the runner's own identity: setting a commit status is NOT a self-approval,
# so (unlike `gh pr review --approve`) it needs no second GitHub account. On a repo
# WITHOUT that required check the status is harmless. Retries transient GitHub API
# failures (5xx / secondary rate limit) and reads the status back to confirm it landed;
# a single swallowed hiccup here previously stranded a reviewed, CI-green PR until a
# human merged it. Contract: rc 0 = status confirmed present on the head sha;
# rc 1 = gave up after retries (caller escalates + records for the end-of-run sweep,
# not a silent defer).
_post_cross_review_status() {
  local item="$1" pr="$2" sha attempt err
  # Head sha, with a few retries (gh pr view can transiently fail too).
  for attempt in 1 2 3; do
    sha=$(gh pr view "$pr" --repo "$REPO" --json headRefOid -q '.headRefOid' 2>/dev/null)
    [ -n "$sha" ] && break
    [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep $((attempt * 2))
  done
  [ -n "$sha" ] || { echo "[batch:merge] WARN $item: no head sha for PR #$pr -> cross-review status skipped" >&2; return 1; }
  # Post, then read the status back to confirm it landed. Retry both with
  # exponential backoff; log the REAL gh error (no more 2>/dev/null) so a
  # non-transient cause is diagnosable next time.
  for attempt in 1 2 3 4 5; do
    err=$(gh api "repos/$REPO/statuses/$sha" -X POST -f state=success \
            -f context=bircher/cross-review \
            -f description="cross-vendor review PASS (Bircher)" 2>&1 >/dev/null)
    if gh api "repos/$REPO/commits/$sha/status" -q '.statuses[].context' 2>/dev/null \
         | grep -qx 'bircher/cross-review'; then
      echo "[batch:merge] $item: posted+verified bircher/cross-review=success on ${sha:0:7} (attempt $attempt)" >&2
      return 0
    fi
    echo "[batch:merge] WARN $item: cross-review status not confirmed on ${sha:0:7} (attempt $attempt/5)${err:+: $err}" >&2
    [ "$attempt" -lt 5 ] && { [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep $((attempt * attempt * 2)); }
  done
  echo "[batch:merge] ERROR $item: could NOT post bircher/cross-review on PR #$pr after 5 attempts -> ESCALATE (ready, needs human merge)" >&2
  return 1
}

# merge_ready_pr <item> <pr> -> rc 0 (merged or deferred; MERGE_NOTE set on
# deferral) | rc 2 (HALT the run: main went red and the merge was reverted, or
# main CI never resolved). B-1 in-run merge: merging each ready PR before the
# next item launches means every later implementer branches from a main that
# already contains its siblings - the merge-order conflict class disappears.
# Safety: watch MAIN's CI on the merge commit; on red, revert (throwaway
# worktree; never touches the shared working tree) and halt; on timeout, halt
# without reverting (conservative).
MERGE_NOTE=""
MERGE_RETRY_ELIGIBLE=""
merge_ready_pr() {
  local item="$1" pr="$2"
  MERGE_NOTE=""
  MERGE_RETRY_ELIGIBLE=0
  # Wait out GitHub's mergeability recompute, then merge.
  local m=UNKNOWN t=0
  while [ "$m" = "UNKNOWN" ] && [ "$t" -lt 60 ]; do
    m=$(gh pr view "$pr" --repo "$REPO" --json mergeable -q '.mergeable' 2>/dev/null)
    [ "$m" = "UNKNOWN" ] && { sleep 5; t=$((t + 5)); }
  done
  if [ "$m" != "MERGEABLE" ]; then
    MERGE_NOTE="merge deferred: mergeable=$m"
    [ "$m" = "UNKNOWN" ] && MERGE_RETRY_ELIGIBLE=1
    echo "[batch:merge] $item: PR #$pr not mergeable ($m) -> left open for the human" >&2
    return 0
  fi
  # #10: satisfy a required-check branch protection (bircher/cross-review) so a
  # protected repo self-merges without an approving review. No-op on repos that
  # don't require the check.
  if ! _post_cross_review_status "$item" "$pr"; then
    MERGE_NOTE="ready but cross-review status post failed -> human merge"
    MERGE_RETRY_ELIGIBLE=1
    echo "[batch:merge] $item: PR #$pr READY but status-post failed -> left open for the human" >&2
    return 0
  fi
  # Merge, retrying briefly: the status just posted needs a moment to propagate to
  # the protected-branch merge gate (a single early attempt can still see BLOCKED).
  local merged=0 mt=0
  while [ "$mt" -lt 30 ]; do
    if gh pr merge "$pr" --repo "$REPO" --squash --delete-branch >/dev/null 2>&1; then merged=1; break; fi
    [ "${BIRCHER_STATUS_BACKOFF:-1}" = 0 ] || sleep 5
    mt=$((mt + 5))
  done
  if [ "$merged" != 1 ]; then
    MERGE_NOTE="merge deferred: gh pr merge failed"
    MERGE_RETRY_ELIGIBLE=1
    echo "[batch:merge] $item: merge of PR #$pr FAILED -> left open for the human" >&2
    return 0
  fi
  local sha
  sha=$(gh pr view "$pr" --repo "$REPO" --json mergeCommit -q '.mergeCommit.oid' 2>/dev/null)
  echo "[batch:merge] $item: PR #$pr MERGED (${sha:-sha unknown}); watching main CI" >&2
  [ -z "$sha" ] && { MERGE_NOTE="merged; main-CI watch skipped (no merge sha)"; return 0; }
  # Watch main's CI on the merge commit (the #157 green-per-PR-red-on-main net).
  local waited=0 state=pending lines
  while [ "$waited" -lt "$MAIN_CI_TIMEOUT" ]; do
    sleep 30; waited=$((waited + 30))
    lines=$(gh api "repos/$REPO/commits/$sha/check-runs" \
      -q '.check_runs[] | "\(.status)|\(.conclusion // "")"' 2>/dev/null)
    state=$(_checkrun_state "$lines")
    [ "$state" != "pending" ] && break
  done
  local decision
  if [ "$state" = green ] || [ "${BIRCHER_MAIN_CI_RERUN:-1}" = 0 ]; then
    decision=$(_main_ci_verdict "$state" "")
  else
    echo "[batch:merge] $item: main CI $state on $sha -> re-running once before deciding (flake check)" >&2
    local second; second=$(_rerun_main_ci "$sha")
    decision=$(_main_ci_verdict "$state" "$second")
    echo "[batch:merge] $item: re-run main CI -> $second (verdict: $decision)" >&2
  fi
  case "$decision" in
    continue)
      echo "[batch:merge] $item: main CI green on $sha" >&2
      return 0 ;;
    revert-halt)
      echo "[batch:merge] !!!! $item: MAIN CI RED on merge $sha (confirmed) -> reverting + HALTING the run !!!!" >&2
      # Guard: never run a bare `git revert` (empty sha -> a usage error that leaves
      # main red, exactly the 2026-07-10 failure). Fix by hand if we have no sha.
      if [ -z "$sha" ]; then
        echo "[batch:merge] WARN $item: no merge sha to revert - main is red; fix by hand" >&2
        MERGE_NOTE="merged; main CI red but NO sha to revert (fix by hand)"
        return 2
      fi
      local rw="/tmp/revert-$pr" pc rargs reverted=0
      if ( cd "$WORKDIR" && git fetch origin -q \
            && git worktree add --detach "$rw" origin/main -q ); then
        # parents = (fields in `rev-list --parents` line) - 1; a merge commit needs -m 1.
        pc=$(git -C "$rw" rev-list --parents -n1 "$sha" 2>/dev/null | wc -w | tr -d ' ')
        pc=$(( pc > 0 ? pc - 1 : 1 ))
        rargs=$(_revert_git_args "$sha" "$pc")
        # shellcheck disable=SC2086
        if [ -n "$rargs" ] && ( cd "$rw" && git revert $rargs && git push origin HEAD:main -q ); then
          echo "[batch:merge] $item: revert pushed to main (parents=$pc)" >&2; reverted=1
        else
          echo "[batch:merge] WARN $item: automatic revert FAILED (sha=$sha parents=$pc) - main is red; fix by hand" >&2
        fi
      else
        echo "[batch:merge] WARN $item: revert setup (fetch/worktree) FAILED - main is red; fix by hand" >&2
      fi
      git -C "$WORKDIR" worktree remove --force "$rw" 2>/dev/null
      # MERGE_NOTE must reflect what ACTUALLY happened (it lands in the scorecard).
      if [ "$reverted" = 1 ]; then
        MERGE_NOTE="merged then REVERTED: main CI red (confirmed on re-run)"
      else
        MERGE_NOTE="merged; automatic revert FAILED - main RED, fix by hand"
      fi
      return 2 ;;
    halt)
      echo "[batch:merge] !!!! $item: main CI unresolved on $sha (confirmed) -> HALTING (no revert) !!!!" >&2
      MERGE_NOTE="merged; main CI unresolved after re-run"
      return 2 ;;
    *)
      echo "[batch:merge] !!!! $item: unexpected merge-gate verdict '$decision' on $sha -> HALTING (fail-closed) !!!!" >&2
      MERGE_NOTE="merged; unexpected CI verdict '$decision' -> halted (fail-closed)"
      return 2 ;;
  esac
}

# _record_deferred_ready <item> <pr> <merge_rc>: append (item,pr) to
# DEFERRED_READY_FILE iff the PR deferred on a transient/retry-eligible class, so
# the end-of-run sweep can re-drive it by its EXACT pr number (no re-discovery ->
# no GOTCHA-1 mapping blind spot). No-op for a clean merge or a human-hand-off
# deferral (CONFLICTING/DIRTY/reverted).
_record_deferred_ready() {
  local item="$1" pr="$2" mrc="$3"
  [ "$mrc" = 0 ] && [ -n "$MERGE_NOTE" ] && [ "${MERGE_RETRY_ELIGIBLE:-0}" = 1 ] || return 0
  mkdir -p "$(dirname "$DEFERRED_READY_FILE")"
  printf '%s\t%s\n' "$item" "$pr" >> "$DEFERRED_READY_FILE"
}

# reconcile_deferred_ready -> end-of-run self-heal: re-drive every ready PR that a
# TRANSIENT failure left open (recorded in DEFERRED_READY_FILE). By end-of-run the
# startup gh burst is long gone, so re-posting bircher/cross-review + merging
# overwhelmingly succeeds. Reuses merge_ready_pr UNCHANGED, so its post+verify gate
# and main-CI-watch/revert safety still apply. Anything still unmergeable becomes a
# loud escalation scorecard row for the (now rare) human hand-off. NEVER --admin.
reconcile_deferred_ready() {
  echo "[batch:sweep] reconciling ready-but-open PRs deferred by transient failures" >&2
  local item pr st mss mrc
  sort -u "$DEFERRED_READY_FILE" | while IFS=$'\t' read -r item pr; do
    [ -n "$pr" ] || continue
    st=$(gh pr view "$pr" --repo "$REPO" --json state -q '.state' 2>/dev/null)
    case "$st" in
      MERGED|CLOSED)
        echo "[batch:sweep] $item: PR #$pr already $st -> skip" >&2
        continue ;;
      OPEN) : ;;
      *)  # empty/unknown = a transient state-lookup failure (the very hiccup the sweep
          # recovers from). Do NOT silently skip -- attempt the merge; merge_ready_pr
          # re-checks mergeability and records a scorecard row either way.
        echo "[batch:sweep] $item: PR #$pr state unresolved ('${st}') -> attempting anyway" >&2 ;;
    esac
    # Strict branch protection: a PR deferred early in the run is likely BEHIND by
    # end-of-run (later items merged to main). merge_ready_pr does NOT update-branch,
    # so bring it up to date + settle the re-triggered CI first (mirrors recover_pr_cmd),
    # else the merge just fails BLOCKED and a still-ready PR gets escalated.
    mss=$(gh pr view "$pr" --repo "$REPO" --json mergeStateStatus -q '.mergeStateStatus' 2>/dev/null)
    if [ "$mss" = "BEHIND" ]; then
      echo "[batch:sweep] $item: PR #$pr BEHIND main -> update-branch + settle re-triggered CI" >&2
      gh api "repos/$REPO/pulls/$pr/update-branch" -X PUT >/dev/null 2>&1 \
        || echo "[batch:sweep] WARN $item: update-branch call failed (already updating or up to date)" >&2
      _wait_ci "$pr" >/dev/null 2>&1 || true
    fi
    echo "[batch:sweep] $item: retrying merge of ready PR #$pr" >&2
    merge_ready_pr "$item" "$pr"; mrc=$?
    case "$mrc:$MERGE_NOTE" in
      0:|0:merged*)
        echo "[batch:sweep] $item: PR #$pr merged by sweep" >&2
        json_row "$item" "$pr" ready true sweep 0 0 "merged by end-of-run reconciliation sweep" ok >> "$SCORECARD" ;;
      2:*)
        # rc 2 = merge_ready_pr merged then main CI went red (reverted or halted).
        # STOP the sweep: do NOT merge further PRs onto a possibly-red main -- the
        # same halt safety the main item loop honors on an rc-2 merge.
        echo "[batch:sweep] !!!! $item: PR #$pr sweep merge triggered a main-CI HALT (rc=2; $MERGE_NOTE) -> stopping sweep !!!!" >&2
        json_row "$item" "$pr" ready false sweep 0 0 "sweep merge halted the run (rc=2): ${MERGE_NOTE:-unknown}" ok >> "$SCORECARD"
        break ;;
      *)
        echo "[batch:sweep] $item: PR #$pr STILL not merged (rc=$mrc; $MERGE_NOTE) -> escalate for human" >&2
        json_row "$item" "$pr" ready false sweep 0 0 "sweep could not merge (rc=$mrc): ${MERGE_NOTE:-unknown}" ok >> "$SCORECARD" ;;
    esac
  done
}

# recover_pr_cmd <code> <pr> [reviewer_vendor] -> STANDALONE recovery of ONE
# orphaned PR: bring a BEHIND branch up to date, run the genuine cross-vendor
# recovery review, and (on PASS + green CI) merge it -- no coordinator session,
# no re-implementation. First-class entry point for the failure the 2026-07-14
# overnight run exposed: a GitHub-infra CI flake outlasted recovery's reruns and
# buried 3 CI-green PRs as `escalated`, orphaning a 4th. A plain re-queue does
# NOT fix that class: the coordinator's opening step would no-op ("a sibling PR
# already did it" -> leaves the PR unmerged) or re-implement it (duplicate PR);
# this adopts and lands the EXISTING PR. The runner posts
# bircher/cross-review=success ONLY after a real review PASS, and merges WITHOUT
# --admin (the branch is up to date by then) -- so no self-approval and no
# branch-protection bypass. rc mirrors merge_ready_pr (0 = merged or left open
# with a marker; 2 = merged but main-CI HALT). Reviewer defaults to claude_code
# (the overnight implementer was codex; the reviewer must be the opposite vendor).
recover_pr_cmd() {
  local code="${1:?usage: --recover-pr <code> <pr> [reviewer_vendor]}"
  local pr="${2:?usage: --recover-pr <code> <pr> [reviewer_vendor]}"
  RECOVERY_REVIEWER="${3:-claude_code}"
  local item="recover-$code"
  echo "[batch:recover-pr] $code: adopting PR #$pr (reviewer=$RECOVERY_REVIEWER)" >&2
  # Strict branch protection blocks a BEHIND branch from merging. Normal runs
  # dodge this by creating PRs sequentially off fresh main; a stale orphan must
  # be brought up to date first. update-branch re-triggers the required checks on
  # the new head, which recover_from_ground_truth's CI-wait then settles before
  # the review -- so the subsequent (non-admin) merge sees a green, up-to-date PR.
  local mss
  mss=$(gh pr view "$pr" --repo "$REPO" --json mergeStateStatus -q '.mergeStateStatus' 2>/dev/null)
  if [ "$mss" = "BEHIND" ]; then
    echo "[batch:recover-pr] $code: PR #$pr is BEHIND main -> update-branch" >&2
    gh api "repos/$REPO/pulls/$pr/update-branch" -X PUT >/dev/null 2>&1 \
      || echo "[batch:recover-pr] WARN $code: update-branch call failed (already updating or up to date)" >&2
  fi
  # Operator identity for the (rare) revert-worktree path inside merge_ready_pr.
  _install_work_git_config "$WORKDIR" >/dev/null 2>&1 || true
  local rec r_outcome r_review r_note
  rec=$(recover_from_ground_truth "$item" "$code" "$pr")
  IFS='|' read -r r_outcome r_review r_note <<EOF
$rec
EOF
  echo "[batch:recover-pr] $code: review -> outcome=$r_outcome review=$r_review note=$r_note" >&2
  if [ "$r_outcome" = "ready" ]; then
    merge_ready_pr "$item" "$pr"; local mrc=$?
    echo "[batch:recover-pr] $code: merge_ready_pr rc=$mrc${MERGE_NOTE:+ note=\"$MERGE_NOTE\"}" >&2
    return $mrc
  fi
  echo "[batch:recover-pr] $code: NOT ready (outcome=$r_outcome) -> PR left open with marker for human" >&2
  return 0
}

# _recovery_review_prompt <pr> -> the read-only reviewer sub-agent input.
# Mirrors the cross-review skill's reviewer template: fetch the PR branch,
# read whole files, run gates each with an inline PATH export, end with an
# exact VERDICT line (findings above it).
_recovery_review_prompt() {
  local pr="$1"
  cat <<EOF
Review PR #$pr in $REPO as an INDEPENDENT, READ-ONLY reviewer. Do NOT edit, commit, or open/update any PR.
First: export PATH=/root/bin:\$PATH; git fetch origin pull/$pr/head; git worktree add --detach /tmp/review-$pr FETCH_HEAD; cd /tmp/review-$pr.
READ the changed files AND enough surrounding code to verify correctness -- do NOT judge from the diff alone.
Run the gates you can, EACH as ONE command prefixed with 'export PATH=/root/bin:\$PATH &&' (e.g. 'export PATH=/root/bin:\$PATH && go build ./...', '... && go vet ./...', client '... && npm run typecheck' / '... && npx vitest run', plugin '... && pytest'); DB-backed 'go test' needs a DB the runner lacks, so trust the PR's green CI for those.
Report blocking / non-blocking / suggestion findings, then a FINAL LINE that is EXACTLY 'VERDICT: PASS' or 'VERDICT: FAIL'. Put findings BEFORE the verdict so the verdict is the last line even if output is long.
EOF
}

# _reconcile_item_pr <code> <tracked_pr> -> the open PR number to act on.
# A coordinator that opens a fresh branch/PR for a CI-red retry (run #20: item
# i141 left #178 red + #179 green, both open) leaves run-queue tracking only the
# FIRST PR it discovered, so recovery buried a green fix as failed. Re-scan every
# open PR whose head branch carries the item code; if a DIFFERENT one is CI-green
# adopt it and close the non-adopted siblings as superseded (so they do not
# orphan). Prints the tracked pr unchanged when there is nothing better to pick.
_reconcile_item_pr() {
  local code="$1" tracked="$2" matches count m green=""
  local chosen="$tracked"
  [ -n "$code" ] || { echo "$tracked"; return; }
  matches=$(gh pr list --repo "$REPO" --state open --json number,headRefName \
    -q "[.[] | select(.headRefName | ascii_downcase | test(\"(^|[^a-z0-9])${code}([^a-z0-9]|\$)\"))] | .[].number" 2>/dev/null)
  count=$(printf '%s\n' "$matches" | grep -c .)
  [ "${count:-0}" -le 1 ] && { echo "$tracked"; return; }
  for m in $matches; do
    if [ "$(_normalize_ci "$(gh pr checks "$m" --repo "$REPO" --json bucket -q '.[].bucket' 2>/dev/null)")" = green ]; then
      green="$m"; break
    fi
  done
  # Only reshuffle when a CI-green sibling exists; if all are red leave them for
  # the normal failed/re-queue path (never close a PR we did not supersede).
  [ -z "$green" ] && { echo "$tracked"; return; }
  chosen="$green"
  for m in $matches; do
    [ "$m" = "$chosen" ] && continue
    gh pr close "$m" --repo "$REPO" \
      --comment "Superseded by #$chosen (Bircher recovery: item $code opened multiple PRs after a CI-red retry; adopting the CI-green one)." >/dev/null 2>&1 || true
  done
  echo "$chosen"
}

# recover_from_ground_truth <item> <code> <pr> [issue]
# Called when a coordinator ended (idle-reaper ~30 min) before posting its
# marker. Derives a truthful outcome from the PR and, for a CI-green PR, an
# out-of-band cross-vendor review. Posts a self-describing bircher-status
# marker to the PR and prints "outcome|review|note" for the scorecard row.
# `issue` (optional) enables the issue-linkage PR fallback when both signal and
# branch-code discovery miss (run #24 a06-vs-i230); standalone --recover-pr omits it.
recover_from_ground_truth() {
  local item="$1" code="$2" pr="$3" issue="${4:-}"
  local ci="na" verdict="" reviewer_out=""
  # If discovery missed the PR (coordinator opened it, then died before run-queue
  # saw it -- overnight i230 opened #250 but was recorded "no PR"), re-scan for an
  # open PR whose head branch carries the item code before concluding "no PR".
  if [ -z "$pr" ] && [ -n "$code" ]; then
    local _disc
    _disc=$(gh pr list --repo "$REPO" --state open --json number,headRefName \
      -q "[.[] | select(.headRefName | ascii_downcase | test(\"(^|[^a-z0-9])${code}([^a-z0-9]|\$)\"))] | (.[0].number // empty)" 2>/dev/null)
    if [ -n "$_disc" ]; then
      echo "[batch:recover] $item: discovery had no PR; found open PR #$_disc by code -> adopting" >&2
      pr="$_disc"
    fi
  fi
  # Branch-code discovery ALSO missed it -> fall back to issue linkage (`Closes
  # #N` in the PR body). Only a SINGLE unambiguous match auto-adopts; 2+ matches
  # are left for a human (recovery has no live escalation channel like the poll
  # loop). Recovers the run #24 a06-vs-i230 class where branch AND signal used
  # the wrong code but the body write-back was still correct.
  if [ -z "$pr" ] && [ -n "$issue" ]; then
    local _im _ic
    _im=$(_discover_pr_by_issue "$issue")
    _ic=$(printf '%s\n' "$_im" | grep -c .)
    if [ "${_ic:-0}" -eq 1 ]; then
      pr="$_im"
      echo "[batch:recover] $item: no PR by code; found #$pr via issue #$issue linkage -> adopting" >&2
    elif [ "${_ic:-0}" -gt 1 ]; then
      echo "[batch:recover] $item: multiple PRs link issue #$issue ($_im) -- leaving for a human (ambiguous)" >&2
    fi
  fi
  # Reconcile a CI-red-retry that opened a second branch/PR before the
  # coordinator died (run #20 #141): adopt the CI-green sibling if there is one.
  if [ -n "$pr" ]; then
    local _rp; _rp=$(_reconcile_item_pr "$code" "$pr")
    if [ -n "$_rp" ] && [ "$_rp" != "$pr" ]; then
      echo "[batch:recover] $item: adopted CI-green sibling PR #$_rp (was tracking #$pr)" >&2
      pr="$_rp"
    fi
  fi
  if [ -n "$pr" ]; then
    local buckets
    buckets=$(gh pr checks "$pr" --repo "$REPO" --json bucket -q '.[].bucket' 2>/dev/null)
    ci=$(_normalize_ci "$buckets")
    # B-5 part 2: the coordinator often DIES (runner_error) while CI is still
    # running -- CI queue delays (degraded GitHub runner capacity) pushed CI to
    # ~12min+ and the coordinator can't survive that wait. run-queue CAN, so wait
    # for CI to settle instead of escalating on 'pending' (that "pending at
    # timeout" was a coordinator death, not a real timeout).
    if [ "$ci" = pending ]; then
      echo "[batch:recover] $item: PR #$pr CI still running at coordinator death -> waiting for CI to settle" >&2
      ci=$(_wait_ci "$pr")
    fi
    # B-5 part 1: a red CI may be a transient GitHub infra failure (runner not
    # acquired / cancelled with no real failed step) rather than a real test
    # failure. Classify and re-run rather than burying a green PR as failed.
    # 2026-07-14: an overnight GitHub-infra flake outlasted 2 reruns and buried 3
    # green PRs as failed -> default raised to 4 (each rerun already waits for CI).
    local _rr=0 _rrmax="${BIRCHER_CI_RERUN_MAX:-4}"
    while [ "$ci" = red ] && [ "$_rr" -lt "$_rrmax" ] && [ "$(_ci_failure_kind "$pr")" = infra ]; do
      _rr=$((_rr + 1))
      echo "[batch:recover] $item: PR #$pr CI red but INFRA (no failed step) -> re-running CI (attempt $_rr/$_rrmax)" >&2
      ci=$(_rerun_and_wait_ci "$pr")
    done
    if [ "$ci" = green ]; then
      echo "[batch:recover] $item: PR #$pr CI green, no marker -> $RECOVERY_REVIEWER recovery review" >&2
      local prompt rlog
      prompt=$(_recovery_review_prompt "$pr")
      rlog="/tmp/recover-$item.log"
      ( cd "$BUNDLE_DIR" && omnigent run "agents/$RECOVERY_REVIEWER" \
          --server "$SERVER" -p "$prompt" ) >"$rlog" 2>&1 || true
      reviewer_out=$(cat "$rlog" 2>/dev/null)
      verdict=$(_extract_verdict "$reviewer_out")
    fi
  fi

  local tuple r_outcome r_review r_ci r_note
  tuple=$(classify_recovery "$pr" "$ci" "$verdict")
  IFS='|' read -r r_outcome r_review r_ci r_note <<EOF
$tuple
EOF

  # Post a self-describing marker to the PR (reviewer findings above it, if any).
  if [ -n "$pr" ]; then
    local marker_line body
    marker_line="bircher-status: outcome=$r_outcome ci=$r_ci ci_first=false review=$r_review rounds=0 note=\"$r_note\""
    if [ -n "$reviewer_out" ]; then
      body="Recovery cross-vendor review (coordinator session ended before posting a marker):

$reviewer_out

$marker_line"
    else
      body="Recovery (coordinator session ended before posting a marker; outcome derived from ground truth).

$marker_line"
    fi
    gh pr comment "$pr" --repo "$REPO" --body "$body" >/dev/null 2>&1 \
      || echo "[batch:recover] WARN $item: failed to post recovery marker to PR #$pr" >&2
  fi

  echo "$r_outcome|$r_review|$r_note"
}

# _is_blank <text> -> rc 0 if text is empty or whitespace-only.
# RC-1 guard helper: a missing/empty queue file must never launch a task-less
# session (an empty -p prompt produced coordinators that idled the full
# ITEM_TIMEOUT). Kept tiny and pure so --self-test can exercise it.
_is_blank() { [ -z "${1//[[:space:]]/}" ]; }

# _render_issue_item <number> <title> <body> -> the queue-file text for a GitHub
# issue. First line is the task heading (code i<number>); an `Issue: #<number>`
# header lets run-queue write back + the coordinator emit `Closes #<number>`.
_render_issue_item() {
  local n="$1" title="$2" body="$3"
  printf '# i%s: %s\n\nIssue: #%s\n\n%s\n' "$n" "$title" "$n" "$body"
}

# _main_ci_verdict <first-state> <second-state> -> continue|revert-halt|halt
# first/second are _checkrun_state outputs (green|red|pending). A non-green FIRST
# is re-checked once (SECOND); only a still-bad SECOND acts. Pending==unresolved.
_main_ci_verdict() {
  case "$1" in
    green) echo continue ;;
    red)   [ "${2:-}" = green ] && echo continue || echo "revert-halt" ;;
    *)     [ "${2:-}" = green ] && echo continue || echo halt ;;
  esac
}

# _revert_git_args <sha> <parent_count> -> the arg string for `git revert`, or "" when
# unrevertable (empty sha -> caller must NOT run a bare `git revert`; the 2026-07-10 run
# did exactly that and left main red). A MERGE commit (parents>1) needs `-m 1` (mainline);
# a normal/squash commit (1 parent) does not. PURE + self-tested (#359).
_revert_git_args() {
  local sha="$1" parents="${2:-1}"
  [ -n "$sha" ] || { echo ""; return; }
  if [ "${parents:-1}" -gt 1 ] 2>/dev/null; then
    echo "--no-edit -m 1 -q $sha"
  else
    echo "--no-edit -q $sha"
  fi
}

# _rerun_main_ci <sha> -> green|red|pending. Re-runs the failed jobs of main's CI
# run for the merge commit ONCE, then re-polls the commit's check-runs. Used to
# distinguish a flaky red/hung main from a genuine one before reverting/halting.
_rerun_main_ci() {
  local sha="$1" rid w=0 lines st
  rid=$(gh run list --repo "$REPO" --branch main --limit 10 --json databaseId,headSha \
        -q ".[] | select(.headSha==\"$sha\") | .databaseId" 2>/dev/null | head -1)
  [ -n "$rid" ] || { echo red; return; }
  gh run rerun "$rid" --repo "$REPO" --failed >/dev/null 2>&1 \
    || gh run rerun "$rid" --repo "$REPO" >/dev/null 2>&1 || { echo red; return; }
  sleep 20
  while [ "$w" -lt "$MAIN_CI_TIMEOUT" ]; do
    lines=$(gh api "repos/$REPO/commits/$sha/check-runs" \
      -q '.check_runs[] | "\(.status)|\(.conclusion // "")"' 2>/dev/null)
    st=$(_checkrun_state "$lines")
    [ "$st" != pending ] && { echo "$st"; return; }
    sleep 30; w=$((w + 30))
  done
  echo pending
}

# _manifest_items <manifest-file> <queue-dir> -> prints "<queue-dir>/<basename>" for
# each non-empty manifest line, IN ORDER (the shim wrote them in priority order).
_manifest_items() {
  local mf="$1" qdir="$2" b
  [ -f "$mf" ] || return 0
  while IFS= read -r b; do [ -n "$b" ] && printf '%s\n' "$qdir/$b"; done < "$mf"
}

# _pr_signal <code> -> the PR number the coordinator recorded for this item in
# $NOOP_DIR/<code>.pr (digits only), or "" if none (B-6). Deterministic PR<->item
# mapping that does not depend on the implementer's branch name.
_pr_signal() {
  [ -f "$NOOP_DIR/$1.pr" ] || return 0
  head -c 20 "$NOOP_DIR/$1.pr" 2>/dev/null | tr -cd '0-9'
}

# _select_pr_candidate <signal_pr> <matching_prs_string> -> one of
# use-signal|<pr>, use-the-one-match|<pr>, no-match|, ambiguous/escalate|<prs>.
# Pure selection only: the caller owns any gh query and the .escalated write.
_select_pr_candidate() {
  local signal="$1" matches="$2"
  if [ -n "$signal" ]; then
    printf 'use-signal|%s\n' "$signal"
    return 0
  fi
  set -- $matches
  case "$#" in
    0) printf 'no-match|\n' ;;
    1) printf 'use-the-one-match|%s\n' "$1" ;;
    *) printf 'ambiguous/escalate|%s\n' "$*" ;;
  esac
}

# _item_issue <prompt-text> -> the issue number from an `Issue: #<n>` header, or empty.
_item_issue() {
  printf '%s\n' "$1" | grep -iE '^Issue:[[:space:]]*#[0-9]+' | head -1 | grep -oE '[0-9]+' | head -1
}

# _discover_pr_by_issue <issue_num> -> open-PR number(s), one per line ("" if none).
# LAST-RESORT PR->item mapping: fires ONLY when neither the coordinator's explicit
# <code>.pr signal nor a branch-name code match found the PR. Every issue-driven
# item is REQUIRED (muesli-loop step 3) to put `Closes #N` (or Fixes/Resolves #N)
# in its PR body, so this recovers a PR whose branch AND signal were named after
# the WRONG code -- run #24 (2026-07-14): item i230's implementer branched
# `a06-release-assets-v2` and wrote `a06.pr` after the "A6" epic tag in the title,
# so `_pr_signal i230` + the i230 branch match both missed the (green, ready) PR
# and the run stalled ~45min. Matching by the issue linkage is code-name-agnostic.
_discover_pr_by_issue() {
  local issue="$1"
  [ -n "$issue" ] || return 0
  gh pr list --repo "$REPO" --state open --search "$issue in:body" \
    --json number,body 2>/dev/null | python3 -c '
import json, re, sys
try: prs = json.load(sys.stdin)
except Exception: sys.exit(0)
issue = sys.argv[1]
pat = re.compile(r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s*:?\s*#" + re.escape(issue) + r"\b")
for pr in prs:
    if pat.search(pr.get("body") or ""):
        print(pr["number"])
' "$issue"
}

# _writeback_plan <outcome> -> "add_label|remove_label|verb" for the issue write-back.
# ready/noop close via the PR's `Closes #N`; we only clear bircher:running.
# escalated/failed/timeout keep the issue OPEN and flag it.
_writeback_plan() {
  case "$1" in
    ready)              echo "|bircher:running|done" ;;
    noop|skipped)       echo "|bircher:running|noop" ;;
    escalated)          echo "bircher:escalated|bircher:running|escalated" ;;
    failed|timeout)     echo "bircher:escalated|bircher:running|failed" ;;
    *)                  echo "|bircher:running|$1" ;;
  esac
}

# _issue_writeback <issue> <outcome> <pr> <review> <rounds>: comment the scorecard
# line on the issue and set/clear status labels. No-op if issue empty or writeback off.
_issue_writeback() {
  local issue="$1" outcome="$2" pr="$3" review="$4" rounds="$5" ci_first="$6"
  [ -n "$issue" ] || return 0
  [ "${BIRCHER_ISSUE_WRITEBACK:-1}" = "1" ] || return 0
  local plan add rm; plan=$(_writeback_plan "$outcome"); IFS='|' read -r add rm _ <<EOF
$plan
EOF
  # #6: build the comment from only the fields that have a value, so a noop/
  # escalated write-back reads "bircher: outcome=noop" instead of a malformed
  # "... rounds=? pr=" with bare/empty tails.
  local body="bircher: outcome=$outcome"
  [ -n "$ci_first" ] && body="$body ci_first=$ci_first"
  [ -n "$review" ]   && body="$body review=$review"
  [ -n "$rounds" ]   && body="$body rounds=$rounds"
  [ -n "$pr" ]       && body="$body pr=#$pr"
  gh issue comment "$issue" --repo "$REPO" --body "$body" >/dev/null 2>&1 || true
  [ -n "$rm" ]  && gh issue edit "$issue" --repo "$REPO" --remove-label "$rm"  >/dev/null 2>&1 || true
  [ -n "$add" ] && gh issue edit "$issue" --repo "$REPO" --add-label "$add"    >/dev/null 2>&1 || true
}

# _ensure_issue_closed <issue> <pr>: safety-net for the `Closes #N` auto-close
# (bircher #3). After a CONFIRMED PR merge, GitHub normally closes the linked
# issue via `Closes #N` in the PR body -- but occasionally it does not fire
# (observed on muesli #33/#35). Wait a grace period for GitHub's own close, then
# close the issue ourselves if it is still open. Idempotent, and gated on the PR
# actually being MERGED so it never closes a deferred/failed item.
_ensure_issue_closed() {
  local issue="$1" pr="$2"
  [ -n "$issue" ] && [ -n "$pr" ] || return 0
  [ "${BIRCHER_ISSUE_WRITEBACK:-1}" = "1" ] || return 0
  [ "$(gh pr view "$pr" --repo "$REPO" --json state -q '.state' 2>/dev/null)" = "MERGED" ] || return 0
  sleep "${BIRCHER_AUTOCLOSE_GRACE_S:-5}"
  [ "$(gh issue view "$issue" --repo "$REPO" --json state -q '.state' 2>/dev/null)" = "OPEN" ] || return 0
  gh issue close "$issue" --repo "$REPO" \
    --comment "Safety-net close: PR #$pr merged but GitHub did not auto-close this issue via \`Closes #$issue\`; the work is on main (bircher #3)." >/dev/null 2>&1 || true
  echo "[batch] safety-net: closed issue #$issue after PR #$pr merged (auto-close missed)" >&2
}

# preflight_auth -> rc 0 if BOTH providers respond to a trivial call; rc 1 else.
# The 2026-06-22 run wasted ~30h after codex's /root/.codex/auth.json went
# 7-days stale mid-run (ops runner-resilience findings) -> every codex
# reviewer hit "Timed out waiting for Codex app-server socket". Fail fast HERE,
# before launching the queue, instead of letting every item time out.
# Skip with SKIP_PREFLIGHT=1; tune the per-probe timeout with PREFLIGHT_TIMEOUT.
preflight_auth() {
  # #5: honor SKIP_PREFLIGHT only for an ATTENDED (interactive TTY) invocation.
  # An unattended/detached run (no controlling TTY -- the overnight launch runs
  # with stdin </dev/null and stdout to a log) MUST probe: a stale codex/claude
  # auth would otherwise silently waste the whole batch (the 2026-06-22 ~30h loss).
  if [ -n "${SKIP_PREFLIGHT:-}" ]; then
    if [ -t 0 ] || [ -t 1 ]; then
      echo "[batch] preflight: skipped (SKIP_PREFLIGHT set, attended TTY)"; return 0
    fi
    echo "[batch] preflight: SKIP_PREFLIGHT IGNORED on an unattended run (no TTY) -> probing anyway" >&2
  fi
  local t="${PREFLIGHT_TIMEOUT:-60}" ok=1
  echo "[batch] preflight: probing claude + codex auth (timeout ${t}s each)..."
  # Claude (claude-sdk coordinator brain + worker): trivial headless call.
  if timeout "$t" claude -p "Reply with the single word READY." >/tmp/preflight-claude.txt 2>&1 \
     && grep -qi 'ready' /tmp/preflight-claude.txt; then
    echo "[batch] preflight: claude OK"
  else
    echo "[batch] preflight: !!! CLAUDE auth/health FAILED (tail of /tmp/preflight-claude.txt):" >&2
    tail -n 3 /tmp/preflight-claude.txt >&2 2>/dev/null; ok=0
  fi
  # Codex (codex worker): file-based ChatGPT OAuth, expires ~7d, silently.
  # --skip-git-repo-check: codex refuses to run outside a trusted git dir.
  if timeout "$t" codex exec --skip-git-repo-check "Reply with the single word READY." >/tmp/preflight-codex.txt 2>&1 \
     && grep -qi 'ready' /tmp/preflight-codex.txt; then
    echo "[batch] preflight: codex OK"
  else
    echo "[batch] preflight: !!! CODEX auth/health FAILED -- likely stale /root/.codex/auth.json; run 'codex login' on the runner (tail of /tmp/preflight-codex.txt):" >&2
    tail -n 3 /tmp/preflight-codex.txt >&2 2>/dev/null; ok=0
  fi
  [ "$ok" = 1 ] || { echo "[batch] preflight FAILED -> refusing to start the queue; fix auth then re-run" >&2; return 1; }
  echo "[batch] preflight OK -> both providers healthy"
}

# json_row item pr outcome ci_first review rounds wall note bound [implementer]
# #4: implementer is optional (last arg) so pre-launch call sites can omit it;
# recording it makes the cross-vendor pairing (implementer vs review) auditable.
json_row() {
  python3 - "$@" <<'PY'
import json,sys,datetime
a=sys.argv[1:]
item,pr,outcome,ci_first,review,rounds,wall,note,bound=a[:9]
implementer=a[9] if len(a)>9 else ""
print(json.dumps({
 "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
 "item": item, "pr": int(pr) if pr.isdigit() else None, "outcome": outcome,
 "implementer": implementer or None, "review": review or None,
 "ci_pass_first_try": ci_first=="true",
 "rounds": int(rounds) if rounds.isdigit() else None,
 "wall_seconds": int(wall) if wall.isdigit() else None, "cost": None,
 "bound": bound or None, "note": note}))
PY
}

# _local_host_id
# Read the bircher runner's stable host_id from /root/.omnigent/config.yaml.
# This script runs inside the omnigent-runner-bircher container, so that file
# holds exactly the bircher runner's host_id (e.g. host_83b59621...).
_local_host_id() {
  local cfg="/root/.omnigent/config.yaml"
  [ -f "$cfg" ] || return 1
  # host_id: is indented under the top-level "host:" key, so do NOT anchor with ^.
  grep -m1 'host_id:' "$cfg" | awk '{print $2}'
}

run_item() {
  local f="$1"; local item; item=$(basename "$f" .md)
  local code; code=$(printf '%s' "$item" | cut -d- -f1 | tr 'A-Z' 'a-z')  # item code, e.g. a06

  # RC-1 guard: NEVER launch a session with an empty prompt. The 2026-06-22 run
  # lost ~30h because a vanished queue file (a second instance moved it) made
  # `cat` return nothing, so `omnigent run -p ""` spawned a task-less coordinator
  # that idled the full ITEM_TIMEOUT. A missing file is recorded and skipped (no
  # `mv` -- there is nothing to move); a present-but-blank file is moved aside.
  if [ ! -f "$f" ]; then
    echo "[batch] SKIP $item: queue file vanished before processing (concurrent instance?); not launching" >&2
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "" "skipped" "false" "" "" 0 "queue file missing at read time; not launched" "n/a" >> "$SCORECARD"
    return 0
  fi
  local prompt; prompt=$(cat "$f")
  if _is_blank "$prompt"; then
    echo "[batch] SKIP $item: queue file is empty/blank; not launching" >&2
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "" "skipped" "false" "" "" 0 "empty/blank prompt; not launched" "n/a" >> "$SCORECARD"
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/" 2>/dev/null || true
    return 0
  fi
  echo "[batch] === $item ==="
  local _iss; _iss=$(_item_issue "$prompt")
  [ -n "$_iss" ] && gh issue edit "$_iss" --repo "$REPO" --add-label bircher:running --remove-label bircher:queued >/dev/null 2>&1 || true
  # B-3 vendor dispatch: resolve THIS item's implementer and flip the reviewer to
  # the opposite vendor (cross-vendor integrity is invariant). A per-item queue tag
  # `bircher-implementer: <vendor>` wins over the runner-level PICKED_VENDOR (set by
  # the usage-aware gate / BIRCHER_IMPLEMENTER). muesli-loop step 3 honors the
  # directive line; the reviewer agent is selected via RECOVERY_REVIEWER below.
  local vendor tag
  tag=$(printf '%s\n' "$prompt" | grep -iE '^[[:space:]]*bircher-implementer:' | head -1 \
        | sed -E 's/.*:[[:space:]]*//' | tr -d '[:space:]' | tr 'A-Z' 'a-z')
  case "$tag" in
    claude_code|claude) vendor=claude_code ;;
    codex)              vendor=codex ;;
    *)                  vendor="${PICKED_VENDOR:-$IMPLEMENTER}" ;;
  esac
  [ "$vendor" = auto ] && vendor=claude_code   # never dispatch the literal 'auto'
  if [ "$vendor" = codex ]; then RECOVERY_REVIEWER=claude_code; else RECOVERY_REVIEWER=codex; fi
  prompt="IMPLEMENTER VENDOR DIRECTIVE: dispatch the implement sub-agent to ${vendor}; the cross-vendor reviewer MUST be the opposite vendor (${RECOVERY_REVIEWER}). Do not set any model or model_override.

${prompt}"
  echo "[batch] $item: implementer=$vendor reviewer=$RECOVERY_REVIEWER" >&2
  # REST launch: create the run session bound to the bircher host (deterministic
  # conv_id from the create response - no discovery heuristic), then send the
  # prompt. (omnigent/server/API.md: Create From Existing Agent + Post Event.)
  local host_id conv_id bound_outcome="ok"
  host_id=$(_local_host_id 2>/dev/null) || host_id=""
  conv_id=$(_create_session "$AGENT_ID" "$host_id" "$WORKDIR")
  if [ -z "$conv_id" ]; then
    echo "[batch] $item: REST session create FAILED; recording failed" >&2
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "" "failed" "false" "" "" 0 "REST session create failed" "failed" >> "$SCORECARD"
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
    return 0
  fi
  echo "[batch] $item: session $conv_id (agent $AGENT_ID)"
  # Binding check: we set host_id in create; confirm the session bound to THIS runner.
  local sess_host; sess_host=$(_http_json GET "/v1/sessions/$conv_id" | _json_get host_id)
  if [ -n "$host_id" ] && [ "$sess_host" != "$host_id" ]; then
    bound_outcome="failed"
    echo "[batch] !!!! BINDING MISMATCH for $item: session host_id='${sess_host:-<empty>}' != local='$host_id'" >&2
  fi
  _send_prompt "$conv_id" "$prompt" || echo "[batch] WARN $item: send_prompt failed" >&2

  local start; start=$(date +%s); local pr="" marker="" elapsed=0 polls=0
  while [ "$elapsed" -lt "$ITEM_TIMEOUT" ]; do
    sleep "$POLL"; elapsed=$(( $(date +%s) - start )); polls=$((polls + 1))
    # B-2 within-item fast limit check: only in the early window (first 2 polls).
    # If the coordinator's opening turn is a provider "hit your limit" message the
    # vendor window is exhausted -- stop the session, DO NOT consume the queue
    # file, and return rc 3 so main re-gates and retries the SAME item on the
    # other vendor (or after the reset). Never fires later (a mid-run limit
    # mention in normal output would false-positive).
    if [ "$polls" -le 2 ] && [ -n "$conv_id" ]; then
      if [ "$(_is_limit_message "$(_last_assistant_text "$conv_id" 3)")" = yes ]; then
        echo "[batch] $item: provider limit message in opening turn -> stop + re-gate (rc 3)" >&2
        _stop_session "$conv_id"
        return 3
      fi
    fi
    if [ -z "$pr" ]; then
      # B-6: prefer the coordinator's EXPLICIT PR signal -- the coordinator knows
      # THIS item's code (from the prompt) and writes the PR number to
      # <code>.pr, so mapping is deterministic and immune to an implementer that
      # names its branch after the skill's EXAMPLE code (CAL06 #277 branch
      # 'a06-...' vs code 'cal06' broke the branch match below and stalled a
      # coupled wave). Prefer the signal outright; otherwise inspect the
      # branch-prefix matches and either take the single match, keep polling, or
      # escalate on ambiguity.
      local pr_signal pr_matches pr_decision
      pr_signal=$(_pr_signal "$code")
      if [ -n "$pr_signal" ]; then
        pr="$pr_signal"
        rm -f "$NOOP_DIR/$code.pr" 2>/dev/null  # consume-once: a stale signal must not misattribute to a later same-coded item
      else
        # Match THIS item's PR by its code in the head branch - STRICT match only.
        # The old "newest new PR" fallback misattributed twice (2026-06-28 H03
        # latched #64; 2026-07-05 SUM02 credited to SUM03), so it was removed.
        pr_matches=$(gh pr list --repo "$REPO" --state open --json number,headRefName \
          -q "[.[] | select(.headRefName | ascii_downcase | test(\"(^|[^a-z0-9])${code}([^a-z0-9]|\$)\"))] | .[].number" 2>/dev/null)
        # Branch match empty too -> last-resort issue-linkage fallback (run #24
        # a06-vs-i230: branch+signal named after the epic tag, not the item code).
        if [ -z "$pr_matches" ] && [ -n "$_iss" ]; then
          pr_matches=$(_discover_pr_by_issue "$_iss")
          [ -n "$pr_matches" ] && echo "[batch] $item: no signal/branch code match; mapped PR via issue #$_iss linkage (Closes #$_iss)" >&2
        fi
        pr_decision=$(_select_pr_candidate "" "$pr_matches")
        case "$pr_decision" in
          use-the-one-match\|*) pr=${pr_decision#use-the-one-match|} ;;
          no-match\|*) ;;
          ambiguous/escalate\|*)
            mkdir -p "$NOOP_DIR"
            printf '%s\n' "multiple open PRs match branch prefix $code: ${pr_decision#ambiguous/escalate|} and no .pr signal to disambiguate" \
              > "$NOOP_DIR/$code.escalated"
            break
            ;;
        esac
      fi
    fi
    if [ -n "$pr" ]; then
      local body; body=$(gh pr view "$pr" --repo "$REPO" --json comments -q '.comments[].body' 2>/dev/null)
      if printf '%s' "$body" | grep -q 'bircher-status:'; then marker=$(parse_marker "$body"); break; fi
    fi
    # No-op signal: the coordinator decided the item is already satisfied (gap #3)
    # and dropped a marker here instead of forcing a (garbage) PR.
    [ -f "$NOOP_DIR/$code.noop" ] && break
    # Escalation-without-PR signal: the coordinator escalated (confidence gate /
    # unmet dependency) and there is no PR to carry a marker (run #13 SRC01b
    # burned its whole cap invisibly). Stop waiting and record it honestly.
    [ -f "$NOOP_DIR/$code.escalated" ] && break
    # Session-aware completion: stop waiting if the SERVER session has DIED.
    # idle is NOT death (coordinator awaiting a sub-agent wake), so only an
    # error/failed/cancelled session ends the wait here; a healthy long run
    # continues to the cap.
    if [ -n "$conv_id" ]; then
      local _ss; _ss=$(_session_state "$conv_id")
      if [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = died ]; then
        echo "[batch] $item: session $conv_id died (state=$_ss) -> stop waiting" >&2
        break
      fi
    fi
  done
  # If we exited the loop without a marker/noop and the server session is still
  # ALIVE (cap reached, not a death), cancel it via the API so it actually
  # stops -- killing the local client alone does NOT stop the server-side
  # session, and a live coordinator would otherwise race the recovery review.
  if [ -z "$marker" ] && [ ! -f "$NOOP_DIR/$code.noop" ] && [ ! -f "$NOOP_DIR/$code.escalated" ] && [ -n "$conv_id" ]; then
    local _ss; _ss=$(_session_state "$conv_id")
    if [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = alive ]; then
      echo "[batch] $item: cap reached, session $conv_id still alive -> cancelling" >&2
      _stop_session "$conv_id"
      local _w=0
      while [ "$_w" -lt 30 ]; do
        sleep 3; _w=$((_w + 3))
        _ss=$(_session_state "$conv_id")
        [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = died ] && break
      done
    fi
  fi
  # Teardown: the session is server-side (no local client to kill). If it is
  # still alive here (e.g. we broke early), stop it so it can't run on untracked.
  if [ -n "$conv_id" ]; then
    local _ss; _ss=$(_session_state "$conv_id")
    [ "$(_session_died "${_ss%%|*}" "${_ss#*|}")" = alive ] && _stop_session "$conv_id"
  fi

  # No-op exit: the coordinator signalled "already satisfied; no change needed" ->
  # record a noop (not a false timeout) and advance without forcing a PR (gap #3).
  if [ -f "$NOOP_DIR/$code.noop" ]; then
    local nnote; nnote=$(head -c 300 "$NOOP_DIR/$code.noop" 2>/dev/null | tr '\n' ' ')
    rm -f "$NOOP_DIR/$code.noop"
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "" "noop" "" "" "" "$elapsed" "${nnote:-already satisfied; no product change needed}" "$bound_outcome" "$vendor" >> "$SCORECARD"
    echo "[batch] $item -> outcome=noop (no change needed)"
    _issue_writeback "$(_item_issue "$prompt")" "noop" "" "" "" ""
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
    return 0
  fi

  # Escalated-without-PR exit: the coordinator hit the confidence gate or an
  # unmet dependency and there is no PR to carry a marker. Record an honest
  # `escalated` row (not a false timeout) and advance (run #13 SRC01b).
  if [ -f "$NOOP_DIR/$code.escalated" ]; then
    local enote; enote=$(head -c 300 "$NOOP_DIR/$code.escalated" 2>/dev/null | tr '\n' ' ')
    rm -f "$NOOP_DIR/$code.escalated"
    mkdir -p "$(dirname "$SCORECARD")"
    json_row "$item" "${pr:-}" "escalated" "false" "" "" "$elapsed" "${enote:-coordinator escalated without a PR}" "$bound_outcome" "$vendor" >> "$SCORECARD"
    echo "[batch] $item -> outcome=escalated (no PR; reason: ${enote:-n/a})"
    _issue_writeback "$(_item_issue "$prompt")" "escalated" "${pr:-}" "" "" ""
    mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
    return 0
  fi

  # Final marker re-check: a coordinator marker may have landed between the last
  # poll and now (or as the session ended). Prefer it over recovery so a
  # converged coordinator always wins and we never post a conflicting marker.
  if [ -z "$marker" ] && [ -n "$pr" ]; then
    local _fb; _fb=$(gh pr view "$pr" --repo "$REPO" --json comments -q '.comments[].body' 2>/dev/null)
    printf '%s' "$_fb" | grep -q 'bircher-status:' && marker=$(parse_marker "$_fb")
  fi

  local outcome ci_first review rounds note
  if [ -n "$marker" ]; then
    # Coordinator posted its own marker -> trust it (existing path).
    local _ci
    IFS='|' read -r outcome _ci ci_first review rounds note <<EOF
$marker
EOF
    : "${outcome:=timeout}" "${ci_first:=false}"
  else
    # No marker: the session died or was cancelled at the cap without posting a
    # marker. Recover from ground truth -- the implementer's
    # PR usually exists and is CI-green; complete or truthfully label the item
    # here instead of recording a bare timeout that re-balloons the item.
    echo "[batch] $item: no marker at timeout -> ground-truth recovery" >&2
    local rec
    rec=$(recover_from_ground_truth "$item" "$code" "$pr" "$_iss")
    IFS='|' read -r outcome review note <<EOF
$rec
EOF
    ci_first="false"; rounds="0"
  fi

  # B-1 in-run merge: merge a ready PR now so the NEXT item builds on it
  # (eliminates the merge-order conflict class). Deferral appends to the note;
  # a red/unresolved main after merge HALTS the run (rc 2 propagates to main).
  local merge_rc=0
  if [ "$INRUN_MERGE" != "0" ] && [ "$outcome" = "ready" ] && [ -n "${pr:-}" ]; then
    merge_ready_pr "$item" "$pr"; merge_rc=$?
    [ -n "$MERGE_NOTE" ] && note="${note:+$note; }$MERGE_NOTE"
    _record_deferred_ready "$item" "$pr" "$merge_rc"
  fi

  mkdir -p "$(dirname "$SCORECARD")"
  json_row "$item" "${pr:-}" "$outcome" "$ci_first" "${review:-}" "${rounds:-}" "$elapsed" "$note" "$bound_outcome" "$vendor" >> "$SCORECARD"
  _issue_writeback "$(_item_issue "$prompt")" "$outcome" "${pr:-}" "${review:-}" "${rounds:-}" "${ci_first:-}"
  # #3: guarantee the issue closes when its PR actually merged (backstops a
  # missed GitHub `Closes #N` auto-close). No-op unless outcome=ready + PR merged.
  [ "$outcome" = "ready" ] && _ensure_issue_closed "$(_item_issue "$prompt")" "${pr:-}"
  echo "[batch] $item -> outcome=$outcome pr=${pr:-none} review=${review:-na} rounds=${rounds:-?} bound=$bound_outcome"
  mkdir -p "$PROCESSED" && mv -f "$f" "$PROCESSED/"
  return "$merge_rc"
}

self_test() {
  local m
  m=$(parse_marker $'some prose\nbircher-status: outcome=ready ci=green ci_first=true review=codex:pass rounds=0 note="wired it in"')
  [ "$m" = "ready|green|true|codex:pass|0|wired it in" ] || { echo "FAIL parse: '$m'"; exit 1; }
  # Regression (EXP02, 2026-07-08): marker posted with a LITERAL backslash-n before
  # it (no real newline) must still parse -- single quotes keep \n literal here.
  m=$(parse_marker 'Ready for merge.\nbircher-status: outcome=ready ci=green ci_first=true review=claude_code:pass rounds=1 note="txt export"')
  [ "$m" = "ready|green|true|claude_code:pass|1|txt export" ] || { echo "FAIL parse literal-\\n: '$m'"; exit 1; }
  m=$(parse_marker "no marker here") && { echo "FAIL: expected rc1"; exit 1; }
  local row; row=$(json_row demo 7 ready true codex:pass 0 800 "ok" ok codex)
  printf '%s' "$row" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["item"]=="demo" and d["pr"]==7 and d["ci_pass_first_try"] is True and d["cost"] is None and d["bound"]=="ok" and d["implementer"]=="codex", d; print("json_row OK (incl. #4 implementer)")'
  local row2; row2=$(json_row demo2 "" timeout false "" "" 900 "" failed)
  printf '%s' "$row2" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["bound"]=="failed" and d["pr"] is None, d; print("json_row bound=failed OK")'
  # RC-1: _is_blank gates the empty-prompt guard.
  _is_blank ""        || { echo "FAIL _is_blank: empty"; exit 1; }
  _is_blank $'  \n\t' || { echo "FAIL _is_blank: whitespace-only"; exit 1; }
  _is_blank "x"       && { echo "FAIL _is_blank: non-blank treated as blank"; exit 1; }
  _is_blank $' # A01 ' && { echo "FAIL _is_blank: real prompt treated as blank"; exit 1; }
  echo "_is_blank OK"
  # RC-1: a skipped item produces a valid scorecard row.
  local row3; row3=$(json_row demo3 "" skipped false "" "" 0 "empty/blank prompt; not launched" "n/a")
  printf '%s' "$row3" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["outcome"]=="skipped" and d["pr"] is None and d["wall_seconds"]==0 and d["bound"]=="n/a" and d["implementer"] is None, d; print("json_row skipped OK (implementer omitted -> null)")'
  # --- Layer-2 recovery: pure decision helpers -------------------------------
  RECOVERY_REVIEWER=codex   # make the asserts deterministic regardless of env
  [ "$(_extract_verdict $'note\nVERDICT: FAIL\nmore prose\nVERDICT: PASS')" = "PASS" ] \
    || { echo "FAIL _extract_verdict: last match should win"; exit 1; }
  [ "$(_extract_verdict 'no verdict token here')" = "" ] \
    || { echo "FAIL _extract_verdict: empty when absent"; exit 1; }
  [ "$(_normalize_ci $'pass\npass')"    = "green"   ] || { echo "FAIL _normalize_ci green"; exit 1; }
  [ "$(_normalize_ci $'pass\nfail')"    = "red"     ] || { echo "FAIL _normalize_ci red"; exit 1; }
  [ "$(_normalize_ci $'pass\npending')" = "pending" ] || { echo "FAIL _normalize_ci pending"; exit 1; }
  [ "$(_normalize_ci '')"               = "pending" ] || { echo "FAIL _normalize_ci empty->pending"; exit 1; }
  [ "$(_classify_ci_failure 0)" = infra ]   || { echo "FAIL _classify_ci_failure 0->infra"; exit 1; }
  [ "$(_classify_ci_failure 3)" = genuine ] || { echo "FAIL _classify_ci_failure 3->genuine"; exit 1; }
  [ "$(_classify_ci_failure '')" = infra ]  || { echo "FAIL _classify_ci_failure empty->infra"; exit 1; }
  local _std="${TMPDIR:-/tmp}/bircher-st-pr-$$"; mkdir -p "$_std"; NOOP_DIR="$_std"
  printf '279\n' > "$_std/cal08.pr"
  [ "$(_pr_signal cal08)" = "279" ] || { echo "FAIL _pr_signal read"; exit 1; }
  [ -z "$(_pr_signal absent)" ]     || { echo "FAIL _pr_signal absent->empty"; exit 1; }
  [ "$(_select_pr_candidate '' '297 298')" = "ambiguous/escalate|297 298" ] \
    || { echo "FAIL _select_pr_candidate ambiguous"; exit 1; }
  rm -rf "$_std"
  [ "$(classify_recovery '' green PASS)" = "timeout|na|na|no PR at timeout (reaped before implement delivered)" ] \
    || { echo "FAIL classify no-pr"; exit 1; }
  [ "$(classify_recovery 7 red '')" = "failed|na|red|RECOVERED: PR up, CI red, coordinator died before fix" ] \
    || { echo "FAIL classify red"; exit 1; }
  [ "$(classify_recovery 7 pending '')" = "escalated|na|pending|RECOVERED: CI still pending at timeout" ] \
    || { echo "FAIL classify pending"; exit 1; }
  [ "$(classify_recovery 7 green PASS)" = "ready|codex:pass|green|RECOVERED: coordinator reaped; out-of-band review PASS" ] \
    || { echo "FAIL classify green+pass"; exit 1; }
  [ "$(classify_recovery 7 green FAIL)" = "failed|codex:fail|green|RECOVERED: out-of-band review FAIL" ] \
    || { echo "FAIL classify green+fail"; exit 1; }
  [ "$(classify_recovery 7 green '')" = "escalated|codex:na|green|RECOVERED: review produced no verdict; needs human" ] \
    || { echo "FAIL classify green+noverdict"; exit 1; }
  echo "classify_recovery OK"
  # --- Layer-2 recovery: wrapper end-to-end with fake gh/omnigent on PATH -----
  local shimdir; shimdir=$(mktemp -d)
  cat >"$shimdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh: `pr checks ... --json bucket` -> two passing checks;
#          `pr comment ... --body X` -> write X to $GH_COMMENT_OUT
sub="$2"
if [ "$sub" = "checks" ]; then printf 'pass\npass\n'; exit 0; fi
if [ "$sub" = "comment" ]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--body" ]; then printf '%s' "$2" > "$GH_COMMENT_OUT"; break; fi
    shift
  done
  # real `gh pr comment` prints the created comment URL to stdout:
  echo "https://github.com/demo/demo/pull/7#issuecomment-123456"
  exit 0
fi
exit 0
SH
  cat >"$shimdir/omnigent" <<'SH'
#!/usr/bin/env bash
# fake omnigent: emit findings ending in the required verdict line
printf 'Reviewed build + tests, all good.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$shimdir/gh" "$shimdir/omnigent"
  local rec_out
  rec_out=$(PATH="$shimdir:$PATH" WORKDIR="$shimdir" REPO=demo/demo SERVER=http://x \
            GH_COMMENT_OUT="$shimdir/comment.txt" RECOVERY_REVIEWER=codex \
            recover_from_ground_truth demo demo 7)
  [ "$rec_out" = "ready|codex:pass|RECOVERED: coordinator reaped; out-of-band review PASS" ] \
    || { echo "FAIL recover happy-path tuple: '$rec_out'"; exit 1; }
  grep -q '^bircher-status: outcome=ready ci=green ' "$shimdir/comment.txt" \
    || { echo "FAIL recover: marker not posted to PR"; exit 1; }
  grep -q 'VERDICT: PASS' "$shimdir/comment.txt" \
    || { echo "FAIL recover: reviewer findings not included in comment"; exit 1; }
  local rec_nopr
  rec_nopr=$(PATH="$shimdir:$PATH" WORKDIR="$shimdir" REPO=demo/demo SERVER=http://x \
             recover_from_ground_truth demo demo "")
  [ "$rec_nopr" = "timeout|na|no PR at timeout (reaped before implement delivered)" ] \
    || { echo "FAIL recover no-pr tuple: '$rec_nopr'"; exit 1; }
  rm -rf "$shimdir"
  echo "recover_from_ground_truth OK"
  # --- Fix 1b: recovery re-discovers the PR when it was recorded "no PR" -------
  local ddir; ddir=$(mktemp -d)
  cat >"$ddir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh: an open PR #300 for the item; CI green; record the marker comment.
case "$2" in
  list)    printf '%s\n' '300' ;;
  checks)  printf 'pass\npass\n' ;;
  comment) echo "https://github.com/demo/demo/pull/300#issuecomment-1" ;;
esac
exit 0
SH
  cat >"$ddir/omnigent" <<'SH'
#!/usr/bin/env bash
printf 'Recovery review of the adopted PR.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$ddir/gh" "$ddir/omnigent"
  local rec_disc
  rec_disc=$(PATH="$ddir:$PATH" WORKDIR="$ddir" REPO=demo/demo SERVER=http://x \
             RECOVERY_REVIEWER=codex recover_from_ground_truth i300 i300 "")
  [ "$rec_disc" = "ready|codex:pass|RECOVERED: coordinator reaped; out-of-band review PASS" ] \
    || { echo "FAIL recover discovery-adopt (1b): '$rec_disc'"; rm -rf "$ddir"; exit 1; }
  rm -rf "$ddir"
  echo "recover discovery-adopt (1b) OK"
  # --- issue-linkage fallback: branch AND signal used the WRONG code (run #24
  #     a06-vs-i230), but the PR body carries Closes #N -> map/adopt by issue ----
  local idir; idir=$(mktemp -d)
  cat >"$idir/gh" <<'SH'
#!/usr/bin/env bash
# branch-code discovery + reconcile -> NO match (empty); the --search issue query
# returns PR #305 whose body carries "Closes #307"; CI green.
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf '%s ' "$@" | grep -q -- '--search' && { printf '[{"number":305,"body":"Impl. Closes #307 done"}]\n'; exit 0; }
  exit 0
fi
[ "$2" = "checks" ]  && { printf 'pass\npass\n'; exit 0; }
[ "$2" = "comment" ] && { echo "https://x/pull/305#c1"; exit 0; }
exit 0
SH
  cat >"$idir/omnigent" <<'SH'
#!/usr/bin/env bash
printf 'Recovery review.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$idir/gh" "$idir/omnigent"
  [ "$(PATH="$idir:$PATH" REPO=demo/demo _discover_pr_by_issue 307)" = "305" ] \
    || { echo "FAIL _discover_pr_by_issue: expected 305"; rm -rf "$idir"; exit 1; }
  [ -z "$(PATH="$idir:$PATH" REPO=demo/demo _discover_pr_by_issue '')" ] \
    || { echo "FAIL _discover_pr_by_issue: empty issue must return nothing"; rm -rf "$idir"; exit 1; }
  # a body that mentions #307 but does NOT close it must NOT match (search returns it; regex rejects)
  cat >"$idir/gh" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf '%s ' "$@" | grep -q -- '--search' && { printf '[{"number":999,"body":"see also #307 for context"}]\n'; exit 0; }
fi
exit 0
SH
  chmod +x "$idir/gh"
  [ -z "$(PATH="$idir:$PATH" REPO=demo/demo _discover_pr_by_issue 307)" ] \
    || { echo "FAIL _discover_pr_by_issue: bare #307 mention must not match (needs a closing keyword)"; rm -rf "$idir"; exit 1; }
  # end-to-end: recover with a wrong code (no branch match) but the issue param adopts #305
  cat >"$idir/gh" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf '%s ' "$@" | grep -q -- '--search' && { printf '[{"number":305,"body":"Impl. Closes #307 done"}]\n'; exit 0; }
  exit 0
fi
[ "$2" = "checks" ]  && { printf 'pass\npass\n'; exit 0; }
[ "$2" = "comment" ] && { echo "https://x/pull/305#c1"; exit 0; }
exit 0
SH
  chmod +x "$idir/gh"
  local rec_iss
  rec_iss=$(PATH="$idir:$PATH" WORKDIR="$idir" REPO=demo/demo SERVER=http://x \
            RECOVERY_REVIEWER=codex recover_from_ground_truth iwrong iwrong "" 307)
  [ "$rec_iss" = "ready|codex:pass|RECOVERED: coordinator reaped; out-of-band review PASS" ] \
    || { echo "FAIL recover issue-linkage adopt: '$rec_iss'"; rm -rf "$idir"; exit 1; }
  rm -rf "$idir"
  echo "issue-linkage fallback (_discover_pr_by_issue + recover) OK"
  # --- Fix C: _reconcile_item_pr adopts a CI-green sibling + closes the loser --
  local rdir; rdir=$(mktemp -d)
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh: two open PRs for the item; 179 green, 178 red; record closes.
case "$2" in
  list)   printf '178\n179\n' ;;
  checks) case "$3" in 179) printf 'pass\npass\n' ;; *) printf 'fail\npass\n' ;; esac ;;
  close)  echo "$3" >> "$GH_CLOSED" ;;
esac
exit 0
SH
  chmod +x "$rdir/gh"
  : > "$rdir/closed.txt"
  local rchosen
  rchosen=$(PATH="$rdir:$PATH" REPO=demo/demo GH_CLOSED="$rdir/closed.txt" _reconcile_item_pr i141 178)
  [ "$rchosen" = 179 ]                       || { echo "FAIL reconcile chose '$rchosen' not 179"; rm -rf "$rdir"; exit 1; }
  grep -qx 178 "$rdir/closed.txt"            || { echo "FAIL reconcile did not close loser 178"; rm -rf "$rdir"; exit 1; }
  grep -qx 179 "$rdir/closed.txt" 2>/dev/null && { echo "FAIL reconcile closed winner 179"; rm -rf "$rdir"; exit 1; }
  # single match -> unchanged, closes nothing
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
case "$2" in list) printf '200\n' ;; checks) printf 'pass\npass\n' ;; close) echo "$3" >> "$GH_CLOSED" ;; esac
exit 0
SH
  : > "$rdir/closed.txt"
  rchosen=$(PATH="$rdir:$PATH" REPO=demo/demo GH_CLOSED="$rdir/closed.txt" _reconcile_item_pr i200 200)
  [ "$rchosen" = 200 ]        || { echo "FAIL reconcile single-match '$rchosen'"; rm -rf "$rdir"; exit 1; }
  [ ! -s "$rdir/closed.txt" ] || { echo "FAIL reconcile single-match closed something"; rm -rf "$rdir"; exit 1; }
  # two matches, both red -> keep tracked, close nothing
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
case "$2" in list) printf '301\n302\n' ;; checks) printf 'fail\npass\n' ;; close) echo "$3" >> "$GH_CLOSED" ;; esac
exit 0
SH
  : > "$rdir/closed.txt"
  rchosen=$(PATH="$rdir:$PATH" REPO=demo/demo GH_CLOSED="$rdir/closed.txt" _reconcile_item_pr i301 301)
  [ "$rchosen" = 301 ]        || { echo "FAIL reconcile all-red '$rchosen'"; rm -rf "$rdir"; exit 1; }
  [ ! -s "$rdir/closed.txt" ] || { echo "FAIL reconcile all-red closed something"; rm -rf "$rdir"; exit 1; }
  rm -rf "$rdir"
  echo "_reconcile_item_pr OK"
  # --- RC2: _session_died (idle is NOT death) --------------------------------
  [ "$(_session_died running '')"   = "alive" ] || { echo "FAIL _session_died running";  exit 1; }
  [ "$(_session_died idle '')"      = "alive" ] || { echo "FAIL _session_died idle";     exit 1; }
  [ "$(_session_died '' '')"        = "alive" ] || { echo "FAIL _session_died empty";    exit 1; }
  [ "$(_session_died failed '')"    = "died"  ] || { echo "FAIL _session_died failed";   exit 1; }
  [ "$(_session_died error '')"     = "died"  ] || { echo "FAIL _session_died error";    exit 1; }
  [ "$(_session_died cancelled '')" = "died"  ] || { echo "FAIL _session_died cancelled";exit 1; }
  [ "$(_session_died idle 'ReadError')" = "died" ] || { echo "FAIL _session_died errcode"; exit 1; }
  echo "_session_died OK"
  # --- RC2: _session_state parse, via fake curl -------
  local ssdir; ssdir=$(mktemp -d)
  cat >"$ssdir/curl" <<'SH'
#!/usr/bin/env bash
# fake curl: print a session JSON built from $FAKE_STATUS / $FAKE_ERR.
printf '{"status":"%s","labels":{"omnigent.last_task_error_code":"%s"}}' "${FAKE_STATUS:-running}" "${FAKE_ERR:-}"
SH
  chmod +x "$ssdir/curl"
  local ss
  ss=$(PATH="$ssdir:$PATH" SERVER=http://x FAKE_STATUS=running _session_state conv_t)
  [ "$ss" = "running|" ] || { echo "FAIL _session_state running: '$ss'"; exit 1; }
  ss=$(PATH="$ssdir:$PATH" SERVER=http://x FAKE_STATUS=failed FAKE_ERR=ReadError _session_state conv_t)
  [ "$ss" = "failed|ReadError" ] || { echo "FAIL _session_state failed: '$ss'"; exit 1; }
  rm -rf "$ssdir"
  echo "session helpers OK"
  # --- REST launch helpers, via fake curl on PATH -----------------------------
  local rdir; rdir=$(mktemp -d)
  cat >"$rdir/curl" <<'SH'
#!/usr/bin/env bash
# fake curl: record the invocation to $CURL_LOG; emit canned JSON by endpoint.
printf '%s\n' "$*" >> "${CURL_LOG:-/dev/null}"
last=""; for a in "$@"; do last="$a"; done
if printf '%s\n' "$@" | grep -q '/events'; then exit 0; fi          # message/stop -> 2xx, no body
if printf '%s\n' "$@" | grep -q -- '-F'; then echo '{"session_id":"conv_holder1"}'; exit 0; fi  # multipart upload
if printf '%s\n' "$@" | grep -q '/v1/sessions/conv_run1'; then echo '{"id":"conv_run1","agent_id":"ag_x","host_id":"host_local","status":"running"}'; exit 0; fi
if printf '%s\n' "$@" | grep -q '/v1/sessions/conv_holder1'; then echo '{"id":"conv_holder1","agent_id":"ag_x"}'; exit 0; fi
if printf '%s\n' "$@" | grep -q 'POST'; then echo '{"id":"conv_run1"}'; exit 0; fi  # JSON create
echo '{}'
SH
  chmod +x "$rdir/curl"
  local got
  got=$(PATH="$rdir:$PATH" SERVER=http://x _upload_bundle "$rdir" "t")
  [ "$got" = "conv_holder1" ] || { echo "FAIL _upload_bundle: '$got'"; exit 1; }
  got=$(PATH="$rdir:$PATH" SERVER=http://x _get_agent_id conv_holder1)
  [ "$got" = "ag_x" ] || { echo "FAIL _get_agent_id: '$got'"; exit 1; }
  got=$(PATH="$rdir:$PATH" SERVER=http://x _create_session ag_x host_local /workspaces/muesli)
  [ "$got" = "conv_run1" ] || { echo "FAIL _create_session: '$got'"; exit 1; }
  PATH="$rdir:$PATH" SERVER=http://x CURL_LOG="$rdir/log" _send_prompt conv_run1 $'a "quoted" prompt\nline2'
  grep -q '/v1/sessions/conv_run1/events' "$rdir/log" || { echo "FAIL _send_prompt endpoint"; exit 1; }
  PATH="$rdir:$PATH" SERVER=http://x CURL_LOG="$rdir/log2" _stop_session conv_run1
  grep -q 'stop_session' "$rdir/log2" || { echo "FAIL _stop_session payload"; exit 1; }
  rm -rf "$rdir"
  echo "REST helpers OK"
  # --- B-1: _checkrun_state (main-CI classification) --------------------------
  [ "$(_checkrun_state $'completed|success\ncompleted|success')" = "green" ]   || { echo "FAIL _checkrun_state green"; exit 1; }
  [ "$(_checkrun_state $'completed|success\ncompleted|failure')" = "red" ]     || { echo "FAIL _checkrun_state red"; exit 1; }
  [ "$(_checkrun_state $'completed|success\nin_progress|')" = "pending" ]      || { echo "FAIL _checkrun_state pending"; exit 1; }
  [ "$(_checkrun_state '')" = "pending" ]                                      || { echo "FAIL _checkrun_state empty"; exit 1; }
  [ "$(_checkrun_state 'completed|action_required')" = "red" ]                 || { echo "FAIL _checkrun_state action_required"; exit 1; }
  [ "$(_checkrun_state $'completed|skipped\ncompleted|neutral')" = "green" ]   || { echo "FAIL _checkrun_state skipped/neutral"; exit 1; }
  echo "_checkrun_state OK"
  # --- B-2v2/B-3v2: limit-message matcher + usage-aware vendor pick -----------
  [ "$(_is_limit_message "You've hit your session limit - resets 6pm")" = "yes" ] || { echo "FAIL limitmsg session"; exit 1; }
  [ "$(_is_limit_message "weekly limit exceeded... hit your weekly limit")" = "yes" ] || { echo "FAIL limitmsg weekly"; exit 1; }
  [ "$(_is_limit_message "Implemented the rate limiter as specified")" = "no" ] || { echo "FAIL limitmsg falsepos"; exit 1; }
  #                      c5  c5r  c7  x5  x5r  x7  now
  FIVEH_MAX=92
  [ "$(_pick_implementer 10 100 50  10 200 30 1000)" = "codex" ]       || { echo "FAIL pick lower-weekly codex"; exit 1; }
  [ "$(_pick_implementer 10 100 20  10 200 60 1000)" = "claude_code" ] || { echo "FAIL pick lower-weekly claude"; exit 1; }
  [ "$(_pick_implementer 95 100 20  10 200 60 1000)" = "codex" ]       || { echo "FAIL pick claude-5h-excluded"; exit 1; }
  [ "$(_pick_implementer 10 100 80  97 200 5  1000)" = "claude_code" ] || { echo "FAIL pick codex-5h-excluded"; exit 1; }
  [ "$(_pick_implementer 95 1500 20 97 1200 5 1000)" = "wait:1200" ]   || { echo "FAIL pick both-excluded wait"; exit 1; }
  [ "$(_pick_implementer -  -   -   -  -   -  1000)" = "claude_code" ] || { echo "FAIL pick no-signal default"; exit 1; }
  [ "$(_pick_implementer 50 100 40  -  -   -  1000)" = "codex" ]       || { echo "FAIL pick missing-codex-eligible"; exit 1; }
  echo "_pick_implementer OK"
  # --- _parse_codex_rate_limits: current "prolite" weekly-only schema, legacy
  #     dual-window schema, and garbage all parse correctly (regression for the
  #     codex-cli 0.144.x schema drift that silently pinned every item to codex) -
  local pl lg
  pl='{"type":"token_count","rate_limits":{"limit_id":"codex","limit_name":null,"primary":{"used_percent":6.0,"window_minutes":10080,"resets_at":1784488480},"secondary":null,"credits":null,"plan_type":"prolite"}}'
  [ "$(printf '%s' "$pl" | _parse_codex_rate_limits)" = "-|-|6.0|1784488480" ] || { echo "FAIL codex prolite (weekly-only) parse"; exit 1; }
  lg='{"rate_limits":{"primary":{"used_percent":30,"window_minutes":300,"resets_at":111},"secondary":{"used_percent":55,"window_minutes":10080,"resets_at":222}}}'
  [ "$(printf '%s' "$lg" | _parse_codex_rate_limits)" = "30|111|55|222" ] || { echo "FAIL codex legacy dual-window parse"; exit 1; }
  [ -z "$(printf '%s' 'not json at all' | _parse_codex_rate_limits)" ]    || { echo "FAIL codex garbage -> empty"; exit 1; }
  echo "_codex_usage parse OK"
  # --- commit-msg hook: strips AI-attribution trailers, keeps body + human co-authors
  local hook="$BUNDLE_DIR/githooks/commit-msg" hmf
  if [ -x "$hook" ]; then
    hmf=$(mktemp)
    printf 'feat: real change\n\nExplains the change.\nCo-authored-by: Real Human <human@team.org>\nCo-authored-by: Codex <codex@example.com>\n' > "$hmf"
    "$hook" "$hmf"
    grep -q 'Explains the change.' "$hmf" || { echo "FAIL hook dropped the body"; rm -f "$hmf"; exit 1; }
    grep -q 'Real Human' "$hmf"           || { echo "FAIL hook dropped a human co-author"; rm -f "$hmf"; exit 1; }
    grep -qi 'codex' "$hmf"               && { echo "FAIL hook kept the codex trailer"; rm -f "$hmf"; exit 1; }
    rm -f "$hmf"; echo "commit-msg hook OK"
  else
    echo "WARN: commit-msg hook not executable at $hook" >&2
  fi
  # --- _install_work_git_config forces the operator identity over codex's Codex
  #     author (the real source of the squash Co-authored-by trailer) ------------
  local gdir; gdir=$(mktemp -d)
  ( cd "$gdir" && git init -q && git config user.name Codex && git config user.email codex@example.com )
  _install_work_git_config "$gdir"
  [ "$(git -C "$gdir" config user.name)"  = "Abedegno" ]               || { echo "FAIL identity name not overridden"; rm -rf "$gdir"; exit 1; }
  [ "$(git -C "$gdir" config user.email)" = "jon@jonwilliams.org.uk" ] || { echo "FAIL identity email not overridden"; rm -rf "$gdir"; exit 1; }
  BIRCHER_GIT_AUTHOR_NAME=Custom BIRCHER_GIT_AUTHOR_EMAIL=c@x.io _install_work_git_config "$gdir"
  [ "$(git -C "$gdir" config user.name)"  = "Custom" ]                 || { echo "FAIL identity env override"; rm -rf "$gdir"; exit 1; }
  rm -rf "$gdir"; echo "_install_work_git_config OK"
  # --- Layer-1: _post_cross_review_status retry + verify -----------------------
  local pdir; pdir=$(mktemp -d)
  cat >"$pdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh for _post_cross_review_status. CNT counts statuses POSTs; the posted
# context only "lands" (becomes visible to the read-back) from POST attempt >= $LAND_AT.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then echo "headsha1234567"; exit 0; fi
if [ "$1" = "api" ]; then
  if printf '%s\n' "$@" | grep -q '/statuses/'; then
    n=$(cat "$CNT" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$CNT"
    [ "$n" -ge "${LAND_AT:-1}" ] && printf 'bircher/cross-review\n' >> "$STORE"
    exit 0
  fi
  if printf '%s\n' "$@" | grep -q '/status'; then cat "$STORE" 2>/dev/null; exit 0; fi
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$pdir/gh"
  # retry-then-success: lands only on POST attempt 2 -> rc 0, verified on attempt 2
  ( PATH="$pdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      CNT="$pdir/cnt" STORE="$pdir/store" LAND_AT=2 \
      _post_cross_review_status demo 7 2>"$pdir/err"; rc=$?; [ $rc -eq 0 ] ) \
    && grep -q 'posted+verified .* (attempt 2)' "$pdir/err" \
    || { echo "FAIL _post retry-then-success"; cat "$pdir/err"; rm -rf "$pdir"; exit 1; }
  # never-confirms (covers both a hard POST failure and a 2xx that never persists,
  # since _post trusts the read-back, not the POST rc): rc 1 + ESCALATE line
  : > "$pdir/cnt"; : > "$pdir/store"
  ( PATH="$pdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      CNT="$pdir/cnt" STORE="$pdir/store" LAND_AT=999 \
      _post_cross_review_status demo 7 2>"$pdir/err"; rc=$?; [ $rc -eq 1 ] ) \
    && grep -q 'ESCALATE (ready, needs human merge)' "$pdir/err" \
    || { echo "FAIL _post never-confirms -> rc1+escalate"; cat "$pdir/err"; rm -rf "$pdir"; exit 1; }
  rm -rf "$pdir"; echo "_post_cross_review_status OK (retry+verify)"
  # --- B-1: merge_ready_pr via fake gh (merged + deferred paths) ---------------
  local mdir; mdir=$(mktemp -d)
  cat >"$mdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh for merge_ready_pr: $FAKE_MERGEABLE controls mergeability; FAKE_GH_LOG records status posts.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  if printf '%s\n' "$@" | grep -q 'headRefOid'; then echo "headsha1234567"
  elif printf '%s\n' "$@" | grep -q 'mergeCommit'; then echo "deadbeefsha"
  else echo "${FAKE_MERGEABLE:-MERGEABLE}"; fi
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then exit 0; fi
if [ "$1" = "api" ]; then
  # a statuses POST -> record it (log + make it visible to the read-back);
  # a commits/<sha>/status GET -> return the recorded contexts (verify);
  # a check-runs GET -> report main CI green.
  if printf '%s\n' "$@" | grep -q '/statuses/'; then
    echo "$@" >> "${FAKE_GH_LOG:-/dev/null}"
    printf 'bircher/cross-review\n' >> "${FAKE_STATUS_STORE:-/dev/null}"
    exit 0
  fi
  if printf '%s\n' "$@" | grep -q '/status'; then cat "${FAKE_STATUS_STORE:-/dev/null}" 2>/dev/null; exit 0; fi
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$mdir/gh"
  # happy path: mergeable -> merged -> main CI green -> rc 0, empty MERGE_NOTE
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_STATUS_STORE="$mdir/s1" merge_ready_pr demo 7 >/dev/null 2>&1
    rc=$?; [ $rc -eq 0 ] && [ -z "$MERGE_NOTE" ] ) || { echo "FAIL merge_ready_pr happy path"; exit 1; }
  # deferred path: CONFLICTING -> rc 0 with a deferral note
  ( PATH="$mdir:$PATH" REPO=demo/demo FAKE_MERGEABLE=CONFLICTING FAKE_STATUS_STORE="$mdir/s2" merge_ready_pr demo 7 >/dev/null 2>&1
    rc=$?; [ $rc -eq 0 ] && [ "$MERGE_NOTE" = "merge deferred: mergeable=CONFLICTING" ] && [ "${MERGE_RETRY_ELIGIBLE:-0}" != 1 ] ) \
    || { echo "FAIL merge_ready_pr deferred path"; exit 1; }
  # #10 cross-review status: a ready item posts bircher/cross-review=success before merging
  local slog="$mdir/status.log"; : >"$slog"
  ( PATH="$mdir:$PATH" REPO=demo/demo MAIN_CI_TIMEOUT=31 FAKE_GH_LOG="$slog" FAKE_STATUS_STORE="$mdir/s3" merge_ready_pr demo 7 >/dev/null 2>&1 )
  grep -q 'repos/demo/demo/statuses/headsha' "$slog" \
    && grep -q 'state=success' "$slog" \
    && grep -q 'context=bircher/cross-review' "$slog" \
    || { echo "FAIL merge_ready_pr: cross-review status not posted"; exit 1; }
  rm -rf "$mdir"
  echo "merge_ready_pr OK (incl. #10 cross-review status)"
  # Task 3: status-post gives up -> retry-eligible defer, NO merge attempt
  local sdir; sdir=$(mktemp -d)
  cat >"$sdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh where the cross-review status NEVER verifies (read-back empty); a merge
# would be logged if (wrongly) attempted.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  printf '%s\n' "$@" | grep -q 'headRefOid' && { echo "headsha1234567"; exit 0; }
  echo "${FAKE_MERGEABLE:-MERGEABLE}"; exit 0
fi
[ "$1" = "pr" ] && [ "$2" = "merge" ] && { echo "merge $3" >> "${PMLOG:-/dev/null}"; exit 0; }
if [ "$1" = "api" ]; then
  printf '%s\n' "$@" | grep -q '/statuses/' && exit 0   # POST "ok" but never persists
  printf '%s\n' "$@" | grep -q '/status'    && exit 0   # read-back empty
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$sdir/gh"; : > "$sdir/pmlog"
  ( PATH="$sdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 PMLOG="$sdir/pmlog" \
      merge_ready_pr demo 7 >/dev/null 2>&1
    rc=$?
    [ $rc -eq 0 ] && [ "${MERGE_RETRY_ELIGIBLE:-x}" = 1 ] && case "$MERGE_NOTE" in "ready but"*) true;; *) false;; esac ) \
    || { echo "FAIL merge_ready_pr: status-post fail not retry-eligible"; rm -rf "$sdir"; exit 1; }
  [ ! -s "$sdir/pmlog" ] || { echo "FAIL merge_ready_pr: merged despite unposted status"; rm -rf "$sdir"; exit 1; }
  rm -rf "$sdir"; echo "merge_ready_pr status-post-fail OK (retry-eligible, no merge)"
  # --- Task 4: _record_deferred_ready + reconcile_deferred_ready ----------------
  local rdir; rdir=$(mktemp -d)
  DEFERRED_READY_FILE="$rdir/deferred.tsv" MERGE_NOTE="ready but cross-review status post failed -> human merge" MERGE_RETRY_ELIGIBLE=1 \
    _record_deferred_ready itemA 11 0
  DEFERRED_READY_FILE="$rdir/deferred.tsv" MERGE_NOTE="" MERGE_RETRY_ELIGIBLE=0 \
    _record_deferred_ready itemB 12 0
  DEFERRED_READY_FILE="$rdir/deferred.tsv" MERGE_NOTE="merge deferred: mergeable=CONFLICTING" MERGE_RETRY_ELIGIBLE=0 \
    _record_deferred_ready itemC 13 0
  [ "$(cat "$rdir/deferred.tsv")" = "$(printf 'itemA\t11')" ] \
    || { echo "FAIL _record_deferred_ready: wrong contents"; cat "$rdir/deferred.tsv"; rm -rf "$rdir"; exit 1; }
  echo "_record_deferred_ready OK"
  cat >"$rdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh for the sweep. MSSDIR/<pr> drives mergeStateStatus (default CLEAN);
# STATEDIR/<pr> drives pr view state (default OPEN); empty mergeCommit skips the
# main-CI watch; STORE models the post->read-back for _post; `pr checks` -> green
# so _wait_ci settles instantly.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  # 'mergeStateStatus' contains 'state', so it MUST be matched before 'state'.
  printf '%s\n' "$@" | grep -q 'mergeStateStatus' && { pr=""; for a in "$@"; do case "$a" in [0-9]*) pr="$a";; esac; done; cat "$MSSDIR/$pr" 2>/dev/null || echo CLEAN; exit 0; }
  printf '%s\n' "$@" | grep -q 'state'       && { pr=""; for a in "$@"; do case "$a" in [0-9]*) pr="$a";; esac; done; cat "$STATEDIR/$pr" 2>/dev/null || echo OPEN; exit 0; }
  printf '%s\n' "$@" | grep -q 'headRefOid'  && { echo "headsha1234567"; exit 0; }
  printf '%s\n' "$@" | grep -q 'mergeCommit' && { echo ""; exit 0; }
  echo "${FAKE_MERGEABLE:-MERGEABLE}"; exit 0
fi
[ "$1" = "pr" ] && [ "$2" = "checks" ] && { printf 'pass\npass\n'; exit 0; }
[ "$1" = "pr" ] && [ "$2" = "merge" ]  && { echo "merge $3" >> "$PMLOG"; exit 0; }
if [ "$1" = "api" ]; then
  printf '%s\n' "$@" | grep -q 'update-branch' && { printf 'update-branch %s\n' "$*" >> "$PMLOG"; exit 0; }
  printf '%s\n' "$@" | grep -q '/statuses/' && { printf 'bircher/cross-review\n' >> "$STORE"; exit 0; }
  printf '%s\n' "$@" | grep -q '/status'    && { cat "$STORE" 2>/dev/null; exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  chmod +x "$rdir/gh"
  mkdir -p "$rdir/states" "$rdir/mss"; echo OPEN > "$rdir/states/7"; echo MERGED > "$rdir/states/8"
  printf 'sweepA\t7\nsweepB\t8\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -qx 'merge 7' "$rdir/pmlog"            || { echo "FAIL sweep: OPEN PR #7 not merged"; rm -rf "$rdir"; exit 1; }
  grep -q  'merge 8' "$rdir/pmlog"            && { echo "FAIL sweep: non-OPEN PR #8 was merged"; rm -rf "$rdir"; exit 1; }
  grep -q 'reconciliation sweep' "$rdir/scorecard.jsonl" || { echo "FAIL sweep: no merged scorecard row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  # Task 4c: a PR that won't merge is escalated (NOT merged), sweep continues (rc 0)
  echo OPEN > "$rdir/states/9"
  printf 'sweepC\t9\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 FAKE_MERGEABLE=CONFLICTING \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -q 'merge 9' "$rdir/pmlog"                         && { echo "FAIL sweep-escalate: CONFLICTING PR #9 was merged"; rm -rf "$rdir"; exit 1; }
  grep -q 'sweep could not merge' "$rdir/scorecard.jsonl" || { echo "FAIL sweep-escalate: no escalation scorecard row"; cat "$rdir/scorecard.jsonl"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready escalate OK"
  # Task 4d (codex P2-2): a BEHIND-but-ready PR is brought up to date then merged
  echo OPEN > "$rdir/states/10"; echo BEHIND > "$rdir/mss/10"
  printf 'sweepD\t10\n' > "$rdir/deferred.tsv"
  : > "$rdir/pmlog"; : > "$rdir/store"; : > "$rdir/scorecard.jsonl"
  ( PATH="$rdir:$PATH" REPO=demo/demo BIRCHER_STATUS_BACKOFF=0 \
      DEFERRED_READY_FILE="$rdir/deferred.tsv" SCORECARD="$rdir/scorecard.jsonl" \
      STATEDIR="$rdir/states" MSSDIR="$rdir/mss" PMLOG="$rdir/pmlog" STORE="$rdir/store" \
      reconcile_deferred_ready >/dev/null 2>&1 )
  grep -q 'pulls/10/update-branch' "$rdir/pmlog" || { echo "FAIL sweep-behind: BEHIND PR #10 not update-branched"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  grep -qx 'merge 10' "$rdir/pmlog"              || { echo "FAIL sweep-behind: BEHIND PR #10 not merged after update"; cat "$rdir/pmlog"; rm -rf "$rdir"; exit 1; }
  echo "reconcile_deferred_ready behind OK"
  rm -rf "$rdir"; echo "reconcile_deferred_ready OK"
  # --- --recover-pr: standalone adopt+review+merge of one orphaned PR ----------
  local prdir; prdir=$(mktemp -d)
  cat >"$prdir/gh" <<'SH'
#!/usr/bin/env bash
# fake gh for recover_pr_cmd end-to-end (recovery review + merge_ready_pr).
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  printf '%s\n' "$@" | grep -q 'mergeStateStatus' && { echo "${FAKE_MSS:-CLEAN}"; exit 0; }
  printf '%s\n' "$@" | grep -q 'headRefOid'       && { echo "headsha1234567"; exit 0; }
  printf '%s\n' "$@" | grep -q 'mergeCommit'      && { echo ""; exit 0; }  # empty sha -> merge_ready_pr skips the slow main-CI watch (covered by the merge_ready_pr test)
  echo "${FAKE_MERGEABLE:-MERGEABLE}"; exit 0
fi
[ "$1" = "pr" ] && [ "$2" = "checks" ]  && { printf 'pass\npass\n'; exit 0; }
[ "$1" = "pr" ] && [ "$2" = "comment" ] && { echo "https://x/pull/9#c1"; exit 0; }
[ "$1" = "pr" ] && [ "$2" = "list" ]    && { exit 0; }
[ "$1" = "pr" ] && [ "$2" = "merge" ]   && { echo "merge $3" >> "${PR_LOG:-/dev/null}"; exit 0; }
if [ "$1" = "api" ]; then
  printf '%s\n' "$@" | grep -q 'update-branch' && { echo "update-branch" >> "${PR_LOG:-/dev/null}"; exit 0; }
  if printf '%s\n' "$@" | grep -q '/statuses/'; then echo "status $*" >> "${PR_LOG:-/dev/null}"; printf 'bircher/cross-review\n' >> "${STORE:-/dev/null}"; exit 0; fi
  printf '%s\n' "$@" | grep -q '/status' && { cat "${STORE:-/dev/null}" 2>/dev/null; exit 0; }
  printf 'completed|success\ncompleted|success\n'; exit 0
fi
exit 0
SH
  cat >"$prdir/omnigent" <<'SH'
#!/usr/bin/env bash
printf 'Recovery review of the adopted PR.\nVERDICT: PASS\n'
exit 0
SH
  chmod +x "$prdir/gh" "$prdir/omnigent"
  # up-to-date green PR: review PASS -> cross-review status + NON-admin merge; no update-branch
  ( PATH="$prdir:$PATH" REPO=demo/demo SERVER=http://x WORKDIR="$prdir" \
      MAIN_CI_TIMEOUT=31 PR_LOG="$prdir/log" STORE="$prdir/store" FAKE_MSS=CLEAN \
      recover_pr_cmd rdemo 9 codex >/dev/null 2>&1
    rc=$?; [ $rc -eq 0 ] ) || { echo "FAIL recover_pr_cmd happy rc"; rm -rf "$prdir"; exit 1; }
  grep -q 'context=bircher/cross-review' "$prdir/log" || { echo "FAIL recover_pr_cmd: cross-review status not posted"; rm -rf "$prdir"; exit 1; }
  grep -qx 'merge 9' "$prdir/log" || { echo "FAIL recover_pr_cmd: PR not merged"; rm -rf "$prdir"; exit 1; }
  grep -q 'update-branch' "$prdir/log" && { echo "FAIL recover_pr_cmd: update-branch run for an up-to-date PR"; rm -rf "$prdir"; exit 1; }
  # BEHIND PR: update-branch FIRST, then review + merge
  : > "$prdir/log"; : > "$prdir/store"
  ( PATH="$prdir:$PATH" REPO=demo/demo SERVER=http://x WORKDIR="$prdir" \
      MAIN_CI_TIMEOUT=31 PR_LOG="$prdir/log" STORE="$prdir/store" FAKE_MSS=BEHIND \
      recover_pr_cmd rdemo 9 codex >/dev/null 2>&1 )
  grep -q 'update-branch' "$prdir/log" || { echo "FAIL recover_pr_cmd: BEHIND did not update-branch"; rm -rf "$prdir"; exit 1; }
  grep -qx 'merge 9' "$prdir/log" || { echo "FAIL recover_pr_cmd: BEHIND path did not merge"; rm -rf "$prdir"; exit 1; }
  rm -rf "$prdir"
  echo "recover_pr_cmd (--recover-pr) OK"
  # --- _render_issue_item: pure queue-file renderer ---------------------------
  r=$(_render_issue_item 301 "People / attendees" $'## Summary\nDo the thing.\n## Verify\nno db tests')
  printf '%s\n' "$r" | grep -q '^Issue: #301$'            || { echo "FAIL render: Issue header"; exit 1; }
  printf '%s\n' "$r" | grep -q '^## Summary$'             || { echo "FAIL render: body copied"; exit 1; }
  printf '%s\n' "$r" | head -1 | grep -q '^# i301: People / attendees$' || { echo "FAIL render: title heading"; exit 1; }
  echo "_render_issue_item OK"
  # --- Task 4: _item_issue + _writeback_plan pure helpers ----------------------
  [ "$(_item_issue $'# i301: x\n\nIssue: #301\n\nbody')" = "301" ] || { echo "FAIL _item_issue read"; exit 1; }
  [ -z "$(_item_issue 'no issue header here')" ]                   || { echo "FAIL _item_issue absent"; exit 1; }
  [ "$(_writeback_plan ready)"     = "|bircher:running|done" ]     || { echo "FAIL wbplan ready"; exit 1; }
  [ "$(_writeback_plan escalated)" = "bircher:escalated|bircher:running|escalated" ] || { echo "FAIL wbplan esc"; exit 1; }
  [ "$(_writeback_plan failed)"    = "bircher:escalated|bircher:running|failed" ]    || { echo "FAIL wbplan failed"; exit 1; }
  echo "_item_issue + _writeback_plan OK"
  # --- #6 + #3: write-back comment shape + safety-net issue close --------------
  local wbdir; wbdir=$(mktemp -d)
  cat >"$wbdir/gh" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "issue" ] && [ "$2" = "comment" ]; then
  while [ $# -gt 0 ]; do [ "$1" = "--body" ] && { echo "$2" >> "${WB_LOG:-/dev/null}"; break; }; shift; done; exit 0; fi
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then echo "${FAKE_PR_STATE:-MERGED}"; exit 0; fi
if [ "$1" = "issue" ] && [ "$2" = "view" ]; then echo "${FAKE_ISSUE_STATE:-OPEN}"; exit 0; fi
if [ "$1" = "issue" ] && [ "$2" = "close" ]; then echo "CLOSED $3" >> "${WB_CLOSE_LOG:-/dev/null}"; exit 0; fi
exit 0
SH
  chmod +x "$wbdir/gh"
  local wbc="$wbdir/comment.log"; : >"$wbc"
  ( PATH="$wbdir:$PATH" REPO=demo/demo WB_LOG="$wbc" _issue_writeback 42 noop "" "" "" "" )
  grep -qx 'bircher: outcome=noop' "$wbc" || { echo "FAIL #6 noop comment: [$(cat "$wbc")]"; exit 1; }
  : >"$wbc"
  ( PATH="$wbdir:$PATH" REPO=demo/demo WB_LOG="$wbc" _issue_writeback 42 ready 7 codex:pass 1 true )
  { grep -q 'outcome=ready' "$wbc" && grep -q 'review=codex:pass' "$wbc" && grep -q 'pr=#7' "$wbc"; } \
    || { echo "FAIL #6 ready comment: [$(cat "$wbc")]"; exit 1; }
  echo "_issue_writeback comment (#6) OK"
  local wbcl="$wbdir/close.log"; : >"$wbcl"
  ( PATH="$wbdir:$PATH" REPO=demo/demo BIRCHER_AUTOCLOSE_GRACE_S=0 FAKE_PR_STATE=MERGED FAKE_ISSUE_STATE=OPEN WB_CLOSE_LOG="$wbcl" _ensure_issue_closed 42 7 )
  grep -q 'CLOSED 42' "$wbcl" || { echo "FAIL #3: merged+open issue not closed"; exit 1; }
  : >"$wbcl"
  ( PATH="$wbdir:$PATH" REPO=demo/demo BIRCHER_AUTOCLOSE_GRACE_S=0 FAKE_PR_STATE=OPEN FAKE_ISSUE_STATE=OPEN WB_CLOSE_LOG="$wbcl" _ensure_issue_closed 42 7 )
  [ -s "$wbcl" ] && { echo "FAIL #3: closed issue for an unmerged PR"; exit 1; }
  : >"$wbcl"
  ( PATH="$wbdir:$PATH" REPO=demo/demo BIRCHER_AUTOCLOSE_GRACE_S=0 FAKE_PR_STATE=MERGED FAKE_ISSUE_STATE=CLOSED WB_CLOSE_LOG="$wbcl" _ensure_issue_closed 42 7 )
  [ -s "$wbcl" ] && { echo "FAIL #3: redundant close on already-closed issue"; exit 1; }
  rm -rf "$wbdir"
  echo "_ensure_issue_closed (#3) OK"
  # --- Task 2 (#346): _main_ci_verdict pure re-run/decision helper ------------
  [ "$(_main_ci_verdict green "")"     = continue ]    || { echo "FAIL verdict green"; exit 1; }
  [ "$(_main_ci_verdict red green)"    = continue ]    || { echo "FAIL verdict red,green"; exit 1; }
  [ "$(_main_ci_verdict red red)"      = revert-halt ] || { echo "FAIL verdict red,red"; exit 1; }
  [ "$(_main_ci_verdict red pending)"  = revert-halt ] || { echo "FAIL verdict red,pending"; exit 1; }
  [ "$(_main_ci_verdict pending green)" = continue ]   || { echo "FAIL verdict pending,green"; exit 1; }
  [ "$(_main_ci_verdict pending red)"  = halt ]        || { echo "FAIL verdict pending,red"; exit 1; }
  [ "$(_main_ci_verdict pending pending)" = halt ]     || { echo "FAIL verdict pending,pending"; exit 1; }
  echo "_main_ci_verdict OK"
  # --- #359: _revert_git_args guards empty sha + adds -m 1 for merge commits -----
  [ "$(_revert_git_args '' 1)" = "" ]                        || { echo "FAIL revert empty-sha (must be blank -> no bare git revert)"; exit 1; }
  [ "$(_revert_git_args abc123 1)" = "--no-edit -q abc123" ] || { echo "FAIL revert single-parent"; exit 1; }
  [ "$(_revert_git_args abc123 2)" = "--no-edit -m 1 -q abc123" ] || { echo "FAIL revert merge-parent (needs -m 1)"; exit 1; }
  [ "$(_revert_git_args abc123 '')" = "--no-edit -q abc123" ] || { echo "FAIL revert default-parent"; exit 1; }
  echo "_revert_git_args OK"
  # --- Task 3 (#347): _manifest_items preserves priority-manifest line order --
  local mdir2; mdir2=$(mktemp -d)
  printf '%s\n' "i2-b.md" "i10-a.md" "i1-c.md" > "$mdir2/.manifest"
  local out; out=$(_manifest_items "$mdir2/.manifest" "$mdir2")
  [ "$(printf '%s\n' "$out" | sed -n '1p')" = "$mdir2/i2-b.md" ]  || { echo "FAIL manifest order 1"; exit 1; }
  [ "$(printf '%s\n' "$out" | sed -n '2p')" = "$mdir2/i10-a.md" ] || { echo "FAIL manifest order 2"; exit 1; }
  [ "$(printf '%s\n' "$out" | sed -n '3p')" = "$mdir2/i1-c.md" ]  || { echo "FAIL manifest order 3 (must preserve file order, NOT sort)"; exit 1; }
  rm -rf "$mdir2"
  echo "_manifest_items OK"
  # --- decoupling: BUNDLE_DIR derivation (from script location) + path defaults ---
  local bdt; bdt=$(mktemp -d); mkdir -p "$bdt/batch"; : > "$bdt/batch/run-queue.sh"
  [ "$(_derive_bundle_dir "$bdt/batch/run-queue.sh")" = "$bdt" ] || { echo "FAIL bundle-dir derive"; exit 1; }
  # QUEUE/SCORECARD are already-bound globals (set at top of file), so a subshell
  # inherits them; `unset` them here so the ${VAR:-default} expansions actually
  # exercise the DEFAULT. Check the subshell exit status so a failure aborts
  # self_test (a bare `( ... )` would swallow the inner `exit 1`).
  ( unset QUEUE SCORECARD; BUNDLE_DIR=/tmp/xbundle
    [ "${QUEUE:-$BUNDLE_DIR/queue}" = "/tmp/xbundle/queue" ] || exit 1
    [ "${SCORECARD:-$BUNDLE_DIR/.run/scorecard.jsonl}" = "/tmp/xbundle/.run/scorecard.jsonl" ] || exit 1
    QUEUE=/tmp/override
    [ "${QUEUE:-$BUNDLE_DIR/queue}" = "/tmp/override" ] || exit 1
  ) || { echo "FAIL bundle-dir path defaults/override"; exit 1; }
  rm -rf "$bdt"
  echo "_bundle_dir OK"
  echo "self-test OK"
}

# _install_work_git_config <workdir>: prepare the work repo before a run so no AI
# attribution reaches muesli/bircher/homelab history.
# (1) Commit identity: codex writes user.name=Codex / user.email=codex@example.com
#     into the work repo's LOCAL git config, and the squash merge turns that
#     branch-commit AUTHOR into a "Co-authored-by: Codex <...>" trailer on main.
#     Force the operator identity (matching the merge author, so GitHub derives no
#     co-author). Env-overridable via BIRCHER_GIT_AUTHOR_NAME/_EMAIL.
# (2) core.hooksPath -> the bundle commit-msg hook (defense in depth against any
#     message-level AI trailer). Absolute path so it covers every worktree.
_install_work_git_config() {
  local wd="$1"
  git -C "$wd" config user.name  "${BIRCHER_GIT_AUTHOR_NAME:-Abedegno}"                2>/dev/null || true
  git -C "$wd" config user.email "${BIRCHER_GIT_AUTHOR_EMAIL:-jon@jonwilliams.org.uk}" 2>/dev/null || true
  if [ -x "$BUNDLE_DIR/githooks/commit-msg" ]; then
    git -C "$wd" config core.hooksPath "$BUNDLE_DIR/githooks" 2>/dev/null || true
  fi
}

main() {
  [ "${1:-}" = "--self-test" ] && { self_test; exit 0; }
  # Standalone auth check (no queue run): verify both providers, then exit.
  [ "${1:-}" = "--preflight" ] && { preflight_auth; exit $?; }
  # Standalone usage readout (no queue run): print both providers' live signals
  # and the vendor _pick_implementer would choose right now, then exit. Operator
  # sanity check for the B-2/B-3 gate (5h_pct|5h_reset|7d_pct|7d_reset).
  if [ "${1:-}" = "--usage" ]; then
    local cu xu now
    cu=$(_claude_usage) || cu="-|-|-|-"; [ -z "$cu" ] && cu="-|-|-|-"
    xu=$(_codex_usage)  || xu="-|-|-|-"; [ -z "$xu" ] && xu="-|-|-|-"
    now=$(date +%s)
    echo "claude: $cu"
    echo "codex : $xu"
    echo "pick  : $(_pick_implementer "$(echo "$cu" | cut -d'|' -f1)" "$(echo "$cu" | cut -d'|' -f2)" "$(echo "$cu" | cut -d'|' -f3)" \
                                      "$(echo "$xu" | cut -d'|' -f1)" "$(echo "$xu" | cut -d'|' -f2)" "$(echo "$xu" | cut -d'|' -f3)" "$now") (FIVEH_MAX=$FIVEH_MAX)"
    exit 0
  fi

  # Standalone single-PR recovery (no queue run): adopt an existing orphaned PR,
  # run the cross-vendor recovery review, and merge on PASS. See recover_pr_cmd.
  #   run-queue.sh --recover-pr <code> <pr> [reviewer_vendor]
  if [ "${1:-}" = "--recover-pr" ]; then
    recover_pr_cmd "${2:-}" "${3:-}" "${4:-}"; exit $?
  fi

  # RC-1 singleton: only one batch may drain the queue at a time. The 2026-06-22
  # run had a second/restarted instance racing the same queue dir and moving
  # files out from under this loop. flock is advisory and released when this
  # process exits (FD 9 closes). If flock is unavailable, warn and proceed
  # rather than abort the whole run.
  local lock="${BIRCHER_BATCH_LOCK:-/tmp/bircher-batch.lock}"
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$lock" || { echo "[batch] cannot open lock file $lock" >&2; exit 1; }
    if ! flock -n 9; then
      echo "[batch] another run-queue.sh already holds $lock; refusing to start a second instance" >&2
      exit 1
    fi
  else
    echo "[batch] WARN: flock not found; running without singleton protection" >&2
  fi

  # Item 2: fail fast if either provider's auth is dead/stale before we launch.
  preflight_auth || exit 2

  # Clear stale no-op signals from any prior run (gap #3).
  mkdir -p "$NOOP_DIR"; rm -f "$NOOP_DIR"/*.noop "$NOOP_DIR"/*.escalated "$NOOP_DIR"/*.pr 2>/dev/null

  # REST launch: upload the agent bundle ONCE to mint a fresh session-scoped
  # agent (config edits activate here); every item's run session binds to it.
  local holder
  holder=$(_upload_bundle "$BUNDLE_DIR" "bircher bundle upload")
  [ -n "$holder" ] || { echo "[batch] FATAL: bundle upload failed" >&2; exit 3; }
  AGENT_ID=$(_get_agent_id "$holder")
  [ -n "$AGENT_ID" ] || { echo "[batch] FATAL: no agent_id from holder $holder" >&2; _prune_session "$holder"; exit 3; }
  echo "[batch] uploaded bundle -> agent=$AGENT_ID (holder $holder)"

  # Force the operator commit identity (codex's default Codex author otherwise
  # becomes a squash Co-authored-by trailer) + install the attribution-strip
  # commit-msg hook. No AI attribution in muesli/bircher/homelab.
  _install_work_git_config "$WORKDIR"
  echo "[batch] work-repo git identity + attribution hook set on $WORKDIR (author=${BIRCHER_GIT_AUTHOR_NAME:-Abedegno})" >&2

  shopt -s nullglob
  if [ "${BIRCHER_SOURCE:-queue}" = "issues" ]; then
    echo "[batch] source=issues: generating queue from bircher:queued issues" >&2
    bash "$BUNDLE_DIR/batch/issues-to-queue.sh" || { echo "[batch] issue->queue generation failed" >&2; exit 3; }
  fi
  local items
  if [ "${BIRCHER_SOURCE:-queue}" = "issues" ] && [ -f "$QUEUE/.manifest" ]; then
    local mout line; mout=$(_manifest_items "$QUEUE/.manifest" "$QUEUE")
    items=(); while IFS= read -r line; do [ -n "$line" ] && items+=("$line"); done <<< "$mout"
  else
    items=("$QUEUE"/*.md)
  fi
  if [ ${#items[@]} -eq 0 ]; then echo "[batch] queue empty"; exit 0; fi
  mkdir -p "$(dirname "$DEFERRED_READY_FILE")"; : > "$DEFERRED_READY_FILE"
  for f in "${items[@]}"; do
    local halt=0
    while :; do
      # B-2 quota gate: start-of-run preflight cannot protect a long run
      # (run #11). Probe BOTH providers before each launch (the probes also
      # FRESHEN both usage signals: any codex exec writes a rollout with
      # rate_limits; the claude probe updates the statusLine cache where
      # configured); on failure pause-and-reprobe without consuming items.
      local qwait=0 qmax="${BIRCHER_QUOTA_MAX_WAIT:-21600}"
      until SKIP_PREFLIGHT= PREFLIGHT_TIMEOUT=60 preflight_auth >/dev/null 2>&1; do
        if [ "$qwait" -ge "$qmax" ]; then
          echo "[batch] \!\!\!\! HALT: provider quota/auth still unhealthy after ${qmax}s - stopping (queue preserved) \!\!\!\!" >&2
          exit 4
        fi
        echo "[batch] quota gate: a provider is unhealthy (likely usage-window exhaustion); pausing 15m before reprobe (waited ${qwait}s)" >&2
        sleep 900; qwait=$((qwait + 900))
      done
      # B-3 usage-aware vendor pick. wait:<epoch> = both 5h windows hot ->
      # sleep until the sooner reset (+60s skew), bounded, then re-gate.
      PICKED_VENDOR="$IMPLEMENTER"
      if [ "$IMPLEMENTER" = "auto" ]; then
        local cu xu now pick
        cu=$(_claude_usage) || cu="-|-|-|-"
        xu=$(_codex_usage)  || xu="-|-|-|-"
        [ -z "$cu" ] && cu="-|-|-|-"; [ -z "$xu" ] && xu="-|-|-|-"
        now=$(date +%s)
        pick=$(_pick_implementer "$(echo "$cu" | cut -d"|" -f1)" "$(echo "$cu" | cut -d"|" -f2)" "$(echo "$cu" | cut -d"|" -f3)" \
                                 "$(echo "$xu" | cut -d"|" -f1)" "$(echo "$xu" | cut -d"|" -f2)" "$(echo "$xu" | cut -d"|" -f3)" "$now")
        if [ "${pick#wait:}" \!= "$pick" ]; then
          local dur=$(( ${pick#wait:} + 60 - now ))
          if [ "$dur" -gt "$qmax" ] || [ "$dur" -le 0 ]; then dur=900; fi
          echo "[batch] usage gate: both 5h windows >= ${FIVEH_MAX}% - sleeping ${dur}s until the sooner reset" >&2
          sleep "$dur"; continue
        fi
        PICKED_VENDOR="$pick"
        echo "[batch] usage gate: claude[$cu] codex[$xu] -> implementer=$PICKED_VENDOR" >&2
      fi
      run_item "$f"
      case $? in
        2) halt=1; break ;;
        3) echo "[batch] usage limit hit at item start; re-gating and retrying $f" >&2; continue ;;
        *) break ;;
      esac
    done
    if [ "$halt" = 1 ]; then
      echo "[batch] \!\!\!\! HALT: main CI red/unresolved after an in-run merge - not launching further items (queue preserved for resume) \!\!\!\!" >&2
      break
    fi
  done
  if [ "${halt:-0}" != 1 ] && [ -s "$DEFERRED_READY_FILE" ]; then
    reconcile_deferred_ready
  fi
  # Deliberately NO holder prune here: deleting the holder cascade-deletes the
  # whole run's sessions (#1388) - run #11b's history was destroyed this way.
  # Holders accumulate (one per run) and are pruned manually only when a run's
  # history is disposable.
  echo "[batch] done; scorecard: $SCORECARD (holder $holder kept - owns this run's session history)"
}
main "$@"
