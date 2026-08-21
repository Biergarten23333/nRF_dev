#!/usr/bin/env bash
set -u

if [[ $# -lt 3 ]]; then
  echo "usage: run_traced.sh LABEL python3 -B ..." >&2
  exit 64
fi

label=$1
shift
if [[ ! "$label" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "invalid evidence label" >&2
  exit 64
fi
if [[ "${1##*/}" != "python3" || "${2:-}" != "-B" ]]; then
  echo "qualification commands must begin with python3 -B" >&2
  exit 64
fi

worktree_root=$(git rev-parse --show-toplevel)
fusion_root="$worktree_root/BioSpur_Fusion/Fusion_Part"
report_root="$fusion_root/reports/fusion_v2/phase3r26c_r2/phase3r26c_r2_psi_free_20260820T124251Z"
evidence_dir="$report_root/raw/$label"
hook_root="$fusion_root/tools/fusion_v2/phase3r26c_r2"
mkdir -p "$evidence_dir"
cd "$worktree_root" || exit 70

audit_log="$evidence_dir/audit.jsonl"
: > "$audit_log"
printf '%q ' "$@" > "$evidence_dir/command.txt"
printf '\n' >> "$evidence_dir/command.txt"
printf '%s\n' "$PWD" > "$evidence_dir/cwd.txt"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$evidence_dir/start_utc.txt"
git rev-parse HEAD > "$evidence_dir/head.txt"
git rev-parse 'HEAD^{tree}' > "$evidence_dir/tree.txt"
{
  printf 'PYTHONDONTWRITEBYTECODE=1\n'
  printf 'PYTHONPATH=%s:%s:%s:%s\n' "$hook_root" "$worktree_root" "$fusion_root" "$fusion_root/src"
  printf 'R26C_AUDIT_LOG=%s\n' "$audit_log"
} > "$evidence_dir/environment_allowlist.txt"

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$hook_root:$worktree_root:$fusion_root:$fusion_root/src" \
R26C_AUDIT_LOG="$audit_log" \
strace -ff -qq -e trace=%file,%process -o "$evidence_dir/strace" \
  "$@" > "$evidence_dir/stdout.txt" 2> "$evidence_dir/stderr.txt"
status=$?

printf '%s\n' "$status" > "$evidence_dir/exit_code.txt"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$evidence_dir/end_utc.txt"
exit "$status"
