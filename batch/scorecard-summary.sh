#!/usr/bin/env bash
# Summarize a scorecard.jsonl: counts by outcome, CI-pass-first-try rate, mean
# review rounds, mean wall time. Usage: scorecard-summary.sh [path] | --self-test
set -uo pipefail
SCORECARD="${1:-/workspaces/muesli/docs/agent-runs/scorecard.jsonl}"

summarize() {
  python3 - "$1" <<'PY'
import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
n=len(rows) or 1
from collections import Counter
oc=Counter(r.get("outcome") for r in rows)
first=sum(1 for r in rows if r.get("ci_pass_first_try"))
rounds=[r["rounds"] for r in rows if isinstance(r.get("rounds"),int)]
wall=[r["wall_seconds"] for r in rows if isinstance(r.get("wall_seconds"),int)]
print(f"runs: {len(rows)}")
print("outcomes: " + ", ".join(f"{k}={v}" for k,v in sorted(oc.items())))
print(f"ci_pass_first_try: {first}/{len(rows)} ({100*first//n}%)")
print(f"mean_review_rounds: {sum(rounds)/len(rounds):.2f}" if rounds else "mean_review_rounds: n/a")
print(f"mean_wall_seconds: {sum(wall)//len(wall)}" if wall else "mean_wall_seconds: n/a")
PY
}

if [ "${1:-}" = "--self-test" ]; then
  tmp=$(mktemp)
  printf '%s\n' \
    '{"item":"a","pr":5,"outcome":"ready","ci_pass_first_try":true,"rounds":0,"wall_seconds":800}' \
    '{"item":"b","pr":6,"outcome":"ready","ci_pass_first_try":false,"rounds":1,"wall_seconds":1200}' \
    '{"item":"c","pr":null,"outcome":"timeout","ci_pass_first_try":false,"rounds":null,"wall_seconds":1800}' > "$tmp"
  out=$(summarize "$tmp"); echo "$out"
  echo "$out" | grep -q "runs: 3" || { echo FAIL; exit 1; }
  echo "$out" | grep -q "ci_pass_first_try: 1/3" || { echo FAIL; exit 1; }
  echo "$out" | grep -q "ready=2" || { echo FAIL; exit 1; }
  rm -f "$tmp"; echo "self-test OK"; exit 0
fi
summarize "$SCORECARD"
