#!/usr/bin/env bash
# What every open run is doing, from the kernel journal (coordinator.cli
# status). Read-only.  Usage: batch/status.sh [--all] [--db <path>]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
db="${BIRCHER_KERNEL_DB:-$HERE/../.run/kernel-muesli.db}"
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --db) db="$2"; shift 2 ;;
    --all) args+=(--all); shift ;;
    *) echo "usage: $0 [--all] [--db <path>]" >&2; exit 2 ;;
  esac
done
PYTHONPATH="$HERE/../v2" exec "${BIRCHER_PY:-python3}" -m coordinator.cli status --db "$db" ${args[@]+"${args[@]}"}
