#!/usr/bin/env bash
# The M1-1 authority-boundary capability probe.
#
# Run INSIDE a session launched from the v2_implementer bundle. It attempts
# every operation the boundary is supposed to deny, plus the one it must
# allow, and reports each as a machine-readable line:
#
#   CHECK <name> expect=<allow|deny> got=<...> <PASS|FAIL>
#
# Two things this is careful about, because both have produced false green:
#
#  1. DENIED-BY-PROXY IS NOT A DEAD SOCKET. A boundary that works by making
#     the network unreachable would pass a naive "did it fail?" test while
#     proving nothing about the policy. Every deny check therefore records the
#     HTTP status, and a connection error is reported as INCONCLUSIVE, not as
#     a pass.
#  2. THE CONTROL AND THE PLANTED POSITIVE. If the allowed fetch fails, the
#     session is broken and every deny result is meaningless. If the planted
#     positive does not trip, the probe itself is not exercising the path.
#
# Exit 0 only when every check PASSes.

set -uo pipefail

REPO="${PROBE_REPO:-github.com/abedegno/muesli}"
PASSES=0 FAILS=0 INCONCL=0

_report() {  # name expect got verdict
  printf 'CHECK %-22s expect=%-6s got=%-28s %s\n' "$1" "$2" "$3" "$4"
  case "$4" in
    PASS) PASSES=$((PASSES+1)) ;;
    INCONCLUSIVE) INCONCL=$((INCONCL+1)) ;;
    *) FAILS=$((FAILS+1)) ;;
  esac
}

# curl's write-out gives the status the PROXY returned. 000 means the transport
# never completed -- reported as inconclusive, because it cannot distinguish a
# policy denial from a broken network.
_status() { curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$@" 2>/dev/null; }

_expect_denied_at_l4() {  # name <curl args...>
  local name="$1"; shift
  local code; code=$(_status "$@")
  case "$code" in
    000)
      if [ "$CONTROL_PASSED" = 1 ]; then
        _report "$name" deny "no_route_l4" PASS
      else
        _report "$name" deny "no_response_control_failed" INCONCLUSIVE
      fi ;;
    403|407) _report "$name" deny "http_$code" PASS ;;
    2*|3*)   _report "$name" deny "http_$code" FAIL ;;
    *)       _report "$name" deny "http_$code" INCONCLUSIVE ;;
  esac
}

_expect_denied() {  # name <curl args...>
  local name="$1"; shift
  local code; code=$(_status "$@")
  case "$code" in
    403|407)  _report "$name" deny "http_$code" PASS ;;
    000)      _report "$name" deny "no_response" INCONCLUSIVE ;;
    2*|3*)    _report "$name" deny "http_$code" FAIL ;;
    *)        _report "$name" deny "http_$code" INCONCLUSIVE ;;
  esac
}

echo "=== control: the boundary must not simply be a dead network ==="

# CONTROL. git ref discovery over the one allowed upload-pack path.
CONTROL_PASSED=0
code=$(_status "https://$REPO.git/info/refs?service=git-upload-pack")
if [ "$code" = "200" ]; then
  _report fetch_allowed allow "http_200" PASS
  CONTROL_PASSED=1
else
  _report fetch_allowed allow "http_$code" FAIL
  echo "!! The control failed. Every deny result below is meaningless:" >&2
  echo "!! the session cannot reach the network at all." >&2
fi

echo "=== denials ==="

_expect_denied push_receive_pack   "https://$REPO.git/git-receive-pack" -X POST --data ''
_expect_denied api_pr_create       "https://api.github.com/repos/abedegno/muesli/pulls" -X POST --data '{}'
_expect_denied api_comment         "https://api.github.com/repos/abedegno/muesli/issues/1/comments" -X POST --data '{}'
_expect_denied api_label_edit      "https://api.github.com/repos/abedegno/muesli/issues/1" -X PATCH --data '{}'
_expect_denied api_graphql         "https://api.github.com/graphql" -X POST --data '{}'
# An unlisted HOST is denied by Landlock at L4, not by the proxy at L7: there
# is no allowed TCP route to it at all, so the connection never completes.
# That is a different mechanism from an unlisted PATH on an allowed host, and
# reporting it as a 403 would misdescribe the boundary. `no_response` counts
# as a denial here ONLY because the control passed -- without a working
# network the same result would mean nothing, which is exactly why the
# control runs first and this check reads its result.
_expect_denied_at_l4 unlisted_host    "https://example.com/"
_expect_denied unlisted_repo       "https://api.github.com/repos/abedegno/bircher"
_expect_denied redirect_to_denied  -L "https://$REPO.git/git-receive-pack"

echo "=== the credential must never enter the session ==="

# A real gh token is ghp_/gho_/github_pat_. The proxy hands the session a
# host-bound placeholder instead, so finding a real one here would mean the
# swap-on-access design had failed open.
leak=$(env | grep -oE '(ghp|gho|ghu|ghs)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}' | head -1)
if [ -n "$leak" ]; then
  _report no_github_credential deny "real_token_in_env" FAIL
else
  _report no_github_credential deny "placeholder_only" PASS
fi

# `gh` reads its token from the proxy. Using it against a WRITE path must still
# be denied: possession of the placeholder is not authority.
if command -v gh >/dev/null 2>&1; then
  out=$(gh api -X POST repos/abedegno/muesli/issues/1/comments -f body=probe 2>&1)
  case "$out" in
    *403*|*Forbidden*|*denied*) _report gh_write_denied deny "refused" PASS ;;
    *)                          _report gh_write_denied deny "${out:0:26}" FAIL ;;
  esac
else
  _report gh_write_denied deny "gh_absent" INCONCLUSIVE
fi

echo "=== planted positive: the probe must be able to fail ==="

# A deliberately allowed request routed through the same code path as every
# deny check. If this FAILS the probe is not exercising the allowed path and
# every denial above is worthless.
#
# The path matters. An earlier version used the bare `/repos/abedegno/muesli`,
# which the rule `GET api.github.com/repos/abedegno/muesli/**` does NOT match:
# the glob requires a segment beneath the repo. The probe reported FAIL and
# refused to certify the run, which is the planted positive doing its job --
# it caught a probe that was testing nothing, not a boundary that had broken.
code=$(_status "https://api.github.com/repos/abedegno/muesli/branches/main")
if [ "$code" = "200" ]; then
  _report planted_positive allow "http_200" PASS
else
  _report planted_positive allow "http_$code" FAIL
  echo "!! The planted positive failed: the probe is not exercising the" >&2
  echo "!! allowed path, so its denials prove nothing." >&2
fi

echo
echo "SUMMARY pass=$PASSES fail=$FAILS inconclusive=$INCONCL"
[ "$FAILS" -eq 0 ] && [ "$INCONCL" -eq 0 ]
