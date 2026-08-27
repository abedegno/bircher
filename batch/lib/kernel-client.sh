# The coordinator's interface to the v2 kernel.
#
# EVERY CALL HERE IS ADVISORY. A non-zero exit, a missing database, a Python
# traceback, an absent interpreter, A HUNG PROCESS -- none of it may change
# what the run does. The kernel records; the coordinator decides. If that is
# ever not true, this file is where it broke.
#
# A hang is the sharpest version of that failure: every OTHER failure still
# lets the caller move on with a non-zero exit code to react to. A process
# that never returns turns "the run completes" into "the run stalls forever
# at this line", which changes a run's outcome more completely than any exit
# code could. Both functions below are bounded by `_net_run` for exactly
# this reason -- see `_kernel_net_cap`.
#
# stdout is reserved for values the caller consumes (`_kernel_dispatch` echoes
# a generation). Diagnostics go to stderr, because call sites capture stdout
# and a stray line would corrupt whatever they were reading.

_kernel_warn() { echo "[batch:kernel] $*" >&2; }

# The v2 checkout, so `-m kernel.cli` and `_kernel_dispatch`'s python source
# below resolve `import kernel...` regardless of the coordinator's cwd. Both
# functions read the SAME variable so they agree on where the kernel package
# lives; the fallback is $BUNDLE_DIR/v2 because kernel-client.sh is sourced
# from inside run-queue.sh, which has already derived BUNDLE_DIR by the time
# it sources this file, and v2/ sits alongside batch/ in every checkout
# layout `_derive_bundle_dir` supports (flattened and nested).
#
# Without this, `python3 -m kernel.cli ...` fails "No module named kernel" on
# EVERY call from the coordinator's actual working directory -- and because
# every call here is advisory, that failure is swallowed exactly like a
# genuinely broken kernel would be. A green suite over that bug would prove
# nothing except that a broken client is easy to build.
_kernel_pythonpath() { printf '%s' "${BIRCHER_V2_DIR:-$BUNDLE_DIR/v2}"; }

# The wall-clock bound on every kernel call, in seconds. This is a local
# sqlite write with no network I/O -- a cold interpreter start on a loaded
# box should still finish in well under a second -- but it is still a
# BOUND: a hung python now returns control to the coordinator in this many
# seconds instead of never.
#
# 5, not 20 (fix round 2): this task's wiring added four more kernel calls
# (_kernel_put_artifact, _kernel_submit_spec, _kernel_submit_plan,
# _kernel_start_implementation) into the window between session creation
# and _send_prompt, each bounded by this same cap -- so the 20s default,
# never itself reasoned about, had widened the worst-case delay to prompt
# delivery from ~20s to ~100s when the kernel database is unresponsive. 5s
# still leaves three orders of magnitude of headroom over the millisecond
# typical case and bounds the new worst case at ~25s. Raise it only with a
# reason that outweighs that delay -- the sqlite operations behind this cap
# do not need it.
# Overridable so a caller (or a test) can shrink it rather than wait out
# the default.
_kernel_net_cap() { printf '%s' "${BIRCHER_KERNEL_TIMEOUT:-5}"; }

# _kernel <subcommand> <args...>  -- always returns 0
#
# Routed through `_net_run` -- batch/run-queue.sh's existing network-call
# bound, already wrapping every other kernel-owned effect via `_effect` in
# effect-adapter.sh -- rather than a second bounding mechanism invented here.
# `_net_run` is not defined in this file; it resolves at call time, exactly
# like `_effect`'s own use of it, so kernel-client.sh being sourced before
# `_net_run` is defined later in run-queue.sh does not matter.
#
# A timeout is not a distinct case: `_net_run` returns non-zero exactly like
# a crashed python would, so it falls straight into the same failure handling
# below -- warn (unless it's a shadow refusal), return 0, change nothing.
#
# A shadow-mode refusal (exit 87, message prefixed "shadow-refused:") is a
# NORMAL outcome, not a failure: BIRCHER_KERNEL_MODE defaults to shadow, so
# most refusals take this path rather than raising, and it means the kernel
# evaluated the command exactly as enforcement would and recorded the
# refusal -- nothing failed. It is treated identically to success: no
# warning, nothing changed, rc 0. Every OTHER non-zero exit -- a missing
# database, a missing interpreter, an uncaught traceback, a timeout, a real
# enforce-mode refusal -- warns, because those are exactly the states an
# operator needs to see the recorder is unhealthy in.
_kernel() {
  local sub="$1"; shift
  local out
  out=$( PYTHONPATH="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -m kernel.cli "$sub" \
           --db "${BIRCHER_KERNEL_DB:-}" "$@" 2>&1 >/dev/null ) && return 0
  case "$out" in
    shadow-refused:*) ;;
    *) _kernel_warn "call failed (advisory): $sub $* -- $out" ;;
  esac
  return 0
}

# _kernel_dispatch <actor> <role> -- echoes the new generation, or nothing.
#
# A role change is a NEW dispatch and a dispatch re-fences the generation, so
# every caller re-reads it. Reusing a stale generation would fence the run out
# of its own kernel record.
#
# The python source is passed as a `-c` ARGUMENT, not piped in over stdin via
# a heredoc as an earlier draft did. Two independent reasons, either one
# sufficient on its own:
#
#   1. Bash drops a heredoc's content when the command it feeds is
#      BACKGROUNDED from inside a function (or any separate process) rather
#      than run in the foreground of the current shell -- the backgrounded
#      reader sees immediate EOF instead of the heredoc body. `_net_run`
#      backgrounds its command internally (real GNU `timeout`, or the
#      fork/kill stub tests use in place of it), so a heredoc here would
#      silently execute an EMPTY python program under `_net_run` -- dispatch
#      would always fail, quietly, only once bounded.
#   2. This sandbox refuses to create the temp file a heredoc needs at all
#      ("cannot create temp file for here document: Operation not
#      permitted"), independent of backgrounding.
#
# An argv has neither problem: it survives being backgrounded through any
# wrapper, and needs no temp file.
# _kernel_adopt_run <code> <repo> <base_sha> <reviewer> -- exports
# BIRCHER_RUN_ID and BIRCHER_GENERATION, echoes the run id (empty on failure).
#
# THE GAP THIS CLOSES. `BIRCHER_RUN_ID` was assigned in exactly one place --
# inside `run_item` -- so every path that does not go through it performed
# effects with no run and no generation. In kernel mode those abort on
# `${BIRCHER_RUN_ID:?}` and are swallowed by the redirects around them: a live
# `--recover-pr` produced an empty kernel database, no PR comment and no
# cross-review status, while reporting success. The documented path for landing
# a human PR was silently inert in the mode v2 is meant to run in.
#
# ADOPT, don't mint. Recovery and the end-of-run sweep are continuations of an
# item's lifecycle, not new ones: a ledger that splits them tells you less, and
# the run already holds the spec, the plan and the implementation output this
# recovery is deciding about. Run ids are `<item>-<epoch>` and the item begins
# with the code, so the item's own run is discoverable by prefix -- newest
# first, because a re-queued item has several and the live one is the last.
#
# Minting is the FALLBACK, for a PR that never came from the queue. It is not
# the default, because a fresh run would present an empty history to a merge
# gate whose whole job is to check history.
_kernel_adopt_run() {  # <code> <repo> <base_sha> <reviewer>
  local code="$1" repo="$2" base="$3" reviewer="$4" found=""
  local src='
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
code = os.environ["K_CODE"]
runs = [r for r in s.all_run_ids() if r.startswith(code + "-")]
print(runs[-1] if runs else "")
'
  found=$( K_CODE="$code"            BIRCHER_V2_DIR="$(_kernel_pythonpath)"            _net_run "$(_kernel_net_cap)"            "${BIRCHER_PY:-python3}" -c "$src" 2>/dev/null
  ) || found=""

  if [ -n "$found" ]; then
    BIRCHER_RUN_ID="$found"
  else
    BIRCHER_RUN_ID="${code}-adopted-$(date +%s)"
    _kernel_warn "no existing run for '$code' -- minting $BIRCHER_RUN_ID"
    _kernel_run_start "$BIRCHER_RUN_ID" "$repo" "$base"
  fi
  export BIRCHER_RUN_ID

  # A recovery reviews; the reviewer role is what validate_review requires, and
  # the role is assigned with the fence so it cannot be elected later.
  BIRCHER_GENERATION=$(_kernel_dispatch "$reviewer" reviewer)
  export BIRCHER_GENERATION
  [ -n "$BIRCHER_GENERATION" ] || _kernel_warn "adopt: no generation for $BIRCHER_RUN_ID"
  printf '%s' "$BIRCHER_RUN_ID"
}

_kernel_dispatch() {  # <actor> <role>
  local actor="$1" role="$2" gen=""
  local src='
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.dispatch import dispatch
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
print(dispatch(s, os.environ["BIRCHER_RUN_ID"],
               actor=os.environ["K_ACTOR"], role=os.environ["K_ROLE"]).generation)
'
  gen=$( K_ACTOR="$actor" K_ROLE="$role" \
         BIRCHER_V2_DIR="$(_kernel_pythonpath)" \
         _net_run "$(_kernel_net_cap)" \
         "${BIRCHER_PY:-python3}" -c "$src" 2>/dev/null
  ) || gen=""
  [ -n "$gen" ] || _kernel_warn "dispatch failed (advisory): $actor/$role"
  printf '%s' "$gen"
  return 0
}

# --- Lifecycle wiring -------------------------------------------------------
#
# The functions below are what `run_item` actually calls at each stage
# transition. They exist so the wiring can be tested by EXECUTION against a
# real database, not only by grepping run_item's source for a command name --
# see v2/tests/execution/test_lifecycle_functions.py. Splitting them out of
# run_item also means a payload shape only has to be gotten right in one
# place per stage.
#
# Every one of them is exactly as advisory as `_kernel` itself: each ends in
# a call to `_kernel` (or, for `_kernel_run_start`/`_kernel_put_artifact`,
# the same bounded-python-via-_net_run shape `_kernel_dispatch` already
# uses), never branches on its result, and always returns 0.

# _kernel_run_start <run_id> <base_repo> <base_sha> -- creates this run's row.
#
# The plan's Step 3 called this `_kernel command --name enqueue ...`, routed
# through submit()'s COMMAND_NAMES set. That does not work: "enqueue" is not
# one of those names (kernel/commands.py's COMMAND_NAMES omits it on
# purpose -- see kernel/enqueue.py's docstring, which reserves `enqueue()`
# for a human-approved spec+plan+grill workflow this batch queue does not
# have). Verified directly against the CLI: `--name enqueue` exits 2,
# "unknown command: enqueue" -- not a shadow-refusal, a usage error, and the
# run's row is never created. Every later command for the run then fails too:
# `store.run_version()` reads `.fetchone()[0]` on a run that was never
# inserted and raises an uncaught TypeError. `_kernel` treats that exactly
# like a healthy shadow-refusal -- warn (or not, if it happened to look like
# one) and move on -- so the whole lifecycle would record NOTHING, silently,
# behind the same "advisory, so nothing breaks" cover every other failure
# here legitimately uses. That is the exact near-miss this file's own header
# warns about, one call earlier than Task 3's.
#
# The fix mirrors `_kernel_dispatch` rather than `_kernel`: `dispatch()` is
# ALSO not a submit() command (a worker cannot CAS a version against a run
# that has no version yet), and it is called directly, by name, against the
# store. Creating the run's row is the same shape of foundational operation,
# so it is called the same way. `kernel.enqueue.enqueue()` itself is not
# reused here -- it persists a spec/plan/bundle this call site does not have
# and does not need; `store.create_run()` is the whole of what a fresh run
# requires to become a legal target for every other command below.
#
# Idempotent: a duplicate run_id (there should never be one -- BIRCHER_RUN_ID
# carries the attempt epoch) hits the `runs` table's primary key and is
# swallowed rather than warned about, the same way `kernel.enqueue.enqueue`
# treats a repeat as a safe retry rather than a failure.
_kernel_run_start() {  # <run_id> <base_repo> <base_sha>
  local run_id="$1" base_repo="$2" base_sha="$3"
  local src='
import os, sqlite3, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
try:
    s.create_run(run_id=os.environ["K_RUN_ID"],
                 base_repo=os.environ["K_BASE_REPO"],
                 base_sha=os.environ["K_BASE_SHA"])
except sqlite3.IntegrityError:
    pass  # already created -- idempotent, same run_id retried
'
  K_RUN_ID="$run_id" K_BASE_REPO="$base_repo" K_BASE_SHA="$base_sha" \
    BIRCHER_V2_DIR="$(_kernel_pythonpath)" \
    _net_run "$(_kernel_net_cap)" \
    "${BIRCHER_PY:-python3}" -c "$src" >/dev/null 2>&1 \
    || _kernel_warn "run start failed (advisory): $run_id"
  return 0
}

# _kernel_put_artifact <data> -- PUTs *data* into the store and echoes its
# content hash, or nothing on failure.
#
# `record_implementation_output`'s authorization (kernel/authz.py) refuses
# any artifact_hash the store does not already hold via `store.has_artifact`
# -- so the hash a command names has to be the result of an actual PUT, not
# merely a value computed and asserted alongside it. Hashing happens on the
# PYTHON side (`kernel.artifacts.put_artifact`, plain sha256 over the exact
# bytes stored) rather than shelling out to `shasum` on a separately
# `printf`'d copy of the same text: one computation, so the hash returned
# here is BY CONSTRUCTION the hash of what got stored, not two independent
# renderings of the same string that happen to agree.
#
# *data* travels as an env var, the same choice `_kernel_dispatch` made for
# actor/role: env vars never touch the `-c` program text, so nothing in a
# marker body pulled from an untrusted PR can inject into the script that
# reads it.
_kernel_put_artifact() {  # <data>
  local data="$1" hash=""
  local src='
import os, sys
sys.path.insert(0, os.environ.get("BIRCHER_V2_DIR", "v2"))
from kernel.artifacts import put_artifact
from kernel.store import Store
s = Store.open(os.environ["BIRCHER_KERNEL_DB"])
print(put_artifact(s, os.environ["K_ARTIFACT_DATA"].encode("utf-8")))
'
  hash=$( K_ARTIFACT_DATA="$data" \
          BIRCHER_V2_DIR="$(_kernel_pythonpath)" \
          _net_run "$(_kernel_net_cap)" \
          "${BIRCHER_PY:-python3}" -c "$src" 2>/dev/null
  ) || hash=""
  [ -n "$hash" ] || _kernel_warn "artifact put failed (advisory)"
  printf '%s' "$hash"
  return 0
}

# _kernel_submit_spec <run_id> <generation> <spec_hash> -- records
# submit_spec (queued -> specified). *spec_hash* must already be PUT (see
# _kernel_put_artifact) -- the same PUT-before-reference discipline as
# record_implementation_output, even though authorize() does not currently
# check it for this command: this is the run's recorded INPUT, and an
# unverifiable hash here would be as wrong as one on the output.
_kernel_submit_spec() {  # <run_id> <generation> <spec_hash>
  local run_id="$1" generation="$2" hash="$3"
  # submit_spec
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name submit_spec --payload-json "{\"artifact_hash\":\"$hash\"}"
}

# _kernel_submit_plan <run_id> <generation> <plan_hash> -- records submit_plan
# (specified -> planned). v1 has no separate plan document, so the caller
# passes the SAME hash `_kernel_submit_spec` was given -- the queue item's
# prompt stands in for a plan artifact until a real one exists, exactly as
# the marker body stands in for a real implementation-output artifact in
# `_kernel_record_output`.
_kernel_submit_plan() {  # <run_id> <generation> <plan_hash>
  local run_id="$1" generation="$2" hash="$3"
  # submit_plan
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name submit_plan --payload-json "{\"artifact_hash\":\"$hash\"}"
}

# _kernel_start_implementation <run_id> <generation> -- records
# start_implementation (planned -> implementing). Requires *generation* to be
# dispatched in the implementer role (kernel/authz.py); called right after
# the implementer dispatch in run_item, so it always is.
_kernel_start_implementation() {  # <run_id> <generation>
  local run_id="$1" generation="$2"
  # start_implementation
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name start_implementation --payload-json "{}"
}

# _kernel_record_output <run_id> <generation> <body> -- records
# record_implementation_output for *body*, PUTting it first so the hash the
# command names is one the kernel actually holds. If the PUT fails, there is
# no hash to name and no run_implementation_output call is made at all (a
# missing artifact is warned about once, by `_kernel_put_artifact`, not
# twice by also sending a command that names an empty hash).
# ECHOES the artifact hash it PUT, on stdout, so the caller can bind a review
# to it. Without that the coordinator has no way to name the object under
# review: `validate_review` requires the binding's artifact_hash to equal
# `store.current_artifact(run_id)`, which is exactly what this PUT set, and a
# hash the caller invented would be refused (rightly).
_kernel_record_output() {  # <run_id> <generation> <body> -- echoes the hash
  local run_id="$1" generation="$2" body="$3" hash
  hash=$(_kernel_put_artifact "$body")
  [ -n "$hash" ] || return 0
  # record_implementation_output
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name record_implementation_output \
    --payload-json "{\"artifact_hash\":\"$hash\"}"
  printf '%s' "$hash"
}

# The policy version every binding this coordinator produces carries. An int,
# because the kernel refuses anything else (`type(...) is int`: int(1.9) == 1
# would silently bind a review recorded under a different policy).
_KERNEL_POLICY_VERSION=1

# _kernel_verdict <coordinator_verdict> -- echoes the KERNEL's word, or empty.
#
# The two vocabularies were never reconciled and the mismatch was invisible
# until a real run produced it: the coordinator says `<vendor>:pass`, the
# kernel accepts only {accept, reject, request_revision}, so every
# record_review was refused -- and because the run then never left
# `implementing`, request_merge and record_merge_outcome were refused too. One
# unmapped word collapsed the whole merge-authorisation chain.
#
# `fail` maps to request_revision, not reject: in this pipeline a failed
# review sends the work back for another round, and `request_revision` is the
# verdict that returns the run to `planned` so implementation can start again.
# `reject` would leave it in `reviewing` with nowhere to go.
#
# `na` maps to NOTHING and is not submitted. It means no review happened (the
# blind-teardown path records it), and a run that was never reviewed must not
# carry a verdict fact saying otherwise -- that would be a claim with no
# observation behind it, in the one place this design exists to prevent that.
_kernel_verdict() {
  case "$1" in
    *:pass) printf 'accept' ;;
    *:fail) printf 'request_revision' ;;
    *)      printf '' ;;
  esac
}

# _kernel_record_ci <run_id> <generation> <status> <head_git_sha> -- records
# record_ci_observation.
# _kernel_ci_status <coordinator_status> -- echoes the KERNEL's word.
#
# The SECOND untranslated vocabulary, found the same way as the first: by
# running the thing. The marker says `ci=green` / `ci=red`; the kernel's merge
# gate asks `status == "success"` and nothing else. So CI was recorded
# faithfully and counted for nothing -- request_merge refused with "no
# successful CI observation for the head being merged" even on a green run,
# one link further along the same chain the verdict mismatch used to break.
#
# Unknown values PASS THROUGH unchanged rather than being coerced or dropped.
# The kernel does not police this vocabulary, so an unrecognised status is
# recorded honestly and simply never counts as green -- which is the
# fail-closed direction. Coercing it to "success" would be the one unsafe
# choice available here.
_kernel_ci_status() {
  case "$1" in
    green|success) printf 'success' ;;
    red|failure|failed) printf 'failure' ;;
    *) printf '%s' "$1" ;;
  esac
}

_kernel_record_ci() {  # <run_id> <generation> <status> <head_git_sha>
  local run_id="$1" generation="$2" status="$3" head="$4"
  status=$(_kernel_ci_status "$status")
  # record_ci_observation
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name record_ci_observation \
    --payload-json "{\"status\":\"$status\",\"head_git_sha\":\"$head\"}"
}

# _kernel_record_review <run_id> <generation> <verdict> <artifact_hash>
#                       <base_sha> <context_hash>
#
# A verdict alone is not a review. `validate_review` requires the BINDING --
# which artifact, which base, which context bundle, under which policy version
# -- because an approval that names nothing approves everything, and the merge
# gate compares this tuple against the one presented at request_merge.
#
# Sending only {"verdict": ...} was therefore refused twice over: first for the
# untranslated verdict word, and behind that for `malformed verdict binding:
# 'policy_version'`. Fixing only the word would have moved the refusal without
# moving the outcome -- and read like progress.
#
# The values are the coordinator's OWN: the hash it PUT, the base it resolved,
# the prompt it sent. Deliberately not read back from the kernel: a binding the
# caller fetches from the mechanism and hands straight back makes the
# mechanism's check compare a value against itself.
_kernel_record_review() {  # <run_id> <generation> <verdict> <artifact> <base> <context>
  local run_id="$1" generation="$2" raw="$3" artifact="$4" base="$5" context="$6"
  local verdict; verdict=$(_kernel_verdict "$raw")
  if [ -z "$verdict" ]; then
    _kernel_warn "no kernel verdict for '$raw' -- not recording a review"
    return 0
  fi
  if [ -z "$artifact" ] || [ -z "$base" ] || [ -z "$context" ]; then
    _kernel_warn "incomplete verdict binding (artifact='$artifact' base='$base' context='$context') -- not recording a review"
    return 0
  fi
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name record_review \
    --payload-json "{\"verdict\":\"$verdict\",\"artifact_hash\":\"$artifact\",\"base_sha\":\"$base\",\"context_bundle_hash\":\"$context\",\"policy_version\":$_KERNEL_POLICY_VERSION}"
}

# _kernel_request_merge <run_id> <generation> <pr> <repo> <head_git_sha>
#                       <artifact_hash> <base_sha> <context_hash>
#
# Carries the SAME binding the review carried. `_merge_is_authorized` hashes
# this tuple and looks for a kernel-recorded `accept` against that exact hash,
# so a merge request whose binding differs by one field -- a superseded
# artifact, a different base -- finds no approval and is refused. That is the
# gate working; presenting no binding at all just fails it earlier and less
# informatively.
_kernel_request_merge() {  # <run_id> <generation> <pr> <repo> <head> <artifact> <base> <context>
  local run_id="$1" generation="$2" pr="$3" repo="$4" head="$5"
  local artifact="$6" base="$7" context="$8"
  # Same guard record_review carries. The no-marker recovery path reaches this
  # with an EMPTY artifact -- nothing recorded an implementation output, so
  # there is nothing to bind -- and sending a binding with a hole in it asks
  # the kernel to refuse something the caller already knew was incomplete.
  if [ -z "$artifact" ] || [ -z "$base" ] || [ -z "$context" ]; then
    _kernel_warn "incomplete merge binding (artifact='$artifact' base='$base' context='$context') -- not requesting a merge"
    return 0
  fi
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name request_merge \
    --payload-json "{\"pr\":\"$pr\",\"repo\":\"$repo\",\"head_git_sha\":\"$head\",\"artifact_hash\":\"$artifact\",\"base_sha\":\"$base\",\"context_bundle_hash\":\"$context\",\"policy_version\":$_KERNEL_POLICY_VERSION}"
}

# _kernel_record_run_outcome <run_id> <generation> <outcome>
#
# The TERMINAL record: "this run is over, and here is what the coordinator
# decided". Distinct from _kernel_record_outcome, which reports what a MERGE
# did and is reachable only from merge_requested -- so before this existed,
# every run that escalated, timed out, went noop or was skipped simply stopped
# emitting facts and sat in `implementing` forever, indistinguishable from one
# still running.
#
# The payload is built by a CASE over the closed vocabulary, not by shelling
# out to python and not by interpolating $outcome into JSON.
#
# Interpolating is unsafe: a `"` in $outcome produces malformed JSON the CLI
# rejects -- a silent advisory failure at the one point in the run that records
# how it ended. But the first fix for that was worse than the problem. It ran a
# BARE `python3`, which (a) sat OUTSIDE `_net_run`, so the one call this file
# exists to keep bounded was unbounded -- a hung interpreter blocks the
# coordinator, measured at 8s against a 2s cap -- and (b) hardcoded `python3`
# while every other call here honours `BIRCHER_PY`, so a deployment pointing at
# a different interpreter would use two.
#
# A case statement needs neither. The vocabulary is closed and known here, so
# the JSON is a literal and cannot be malformed. An unrecognised outcome maps to
# a value the kernel REFUSES rather than one it silently accepts: drift between
# this list and kernel/authz.py's shows up as a recorded refusal instead of a
# run whose ledger quietly disagrees with its scorecard.
_kernel_record_run_outcome() {  # <run_id> <generation> <outcome>
  local run_id="$1" generation="$2" outcome="$3" payload
  case "$outcome" in
    merged|ready|escalated|noop|skipped|failed|timeout)
      payload="{\"outcome\":\"$outcome\"}" ;;
    *)
      payload='{"outcome":"unrecognised"}' ;;
  esac
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name record_run_outcome --payload-json "$payload"
}

# _kernel_record_outcome <run_id> <generation> <outcome> -- records
# record_merge_outcome. *outcome* is "merged" or "failed". NOT the terminal
# record; that is _kernel_record_run_outcome above. This comment previously sat
# above THAT function by accident, mislabelling both at the one place the two
# must not be confused.

_kernel_record_outcome() {  # <run_id> <generation> <outcome>
  local run_id="$1" generation="$2" outcome="$3"
  # record_merge_outcome
  _kernel command --run-id "$run_id" --generation "$generation" \
    --name record_merge_outcome --payload-json "{\"outcome\":\"$outcome\"}"
}
