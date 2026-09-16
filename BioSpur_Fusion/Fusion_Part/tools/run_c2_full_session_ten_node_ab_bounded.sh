#!/usr/bin/env bash
set -u

if (( $# < 1 )); then
    echo "usage: $0 NEW_OUTPUT_DIR [-- CHILD [ARG...]]" >&2
    exit 64
fi
output=$1
shift
[[ ! -e "$output" ]] || { echo "output target already exists" >&2; exit 65; }
mkdir -- "$output" || exit 65
if [[ ${1-} == -- ]]; then
    shift
    (( $# > 0 )) || exit 64
    child=("$@")
else
    child=(.venv-v0/bin/python tools/build_c2_full_session_ten_node_ab.py
           --execute --output "$output/RESULT.json")
fi
run_seconds=${C2_BOUNDED_TIMEOUT_SECONDS:-2400}
[[ $run_seconds =~ ^[1-9][0-9]*$ ]] || exit 64

printf '%q ' "${child[@]}" > "$output/COMMAND.txt"
printf '\n' >> "$output/COMMAND.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$output/UTC_START.txt"
mapfile -d '' source_inputs < <(
    find src/biospur_fusion -type f -name '*.py' -print0 | LC_ALL=C sort -z
)
(( ${#source_inputs[@]} > 0 )) || exit 66
hash_inputs=(
    tools/run_c2_full_session_ten_node_ab_bounded.sh
    tools/build_c2_full_session_ten_node_ab.py
    tests/test_c2_full_session_ten_node_factory.py
    tests/test_c2_full_session_ten_node_ab.py
    tests/test_c2_articulated_rejection_prefix.py
    tests/test_c2_continuous_group_epoch_owner.py
    tests/test_c2_authoritative_articulated_fusion.py
    tests/test_c2_articulated_range.py
    "${source_inputs[@]}"
)
for input in "${hash_inputs[@]}"; do
    [[ -f "$input" && ! -L "$input" ]] || exit 66
done
sha256sum "${hash_inputs[@]}" > "$output/START_HASHES.txt" || exit 66

ulimit -v 1048576 || exit 67
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPATH=src:../B306_Part/tools:tests
export C2_BOUNDED_OUTPUT=$output
setsid /usr/bin/time -v -o "$output/RUNTIME.txt" \
    timeout --foreground --signal=TERM --kill-after=5s "$run_seconds" "${child[@]}" \
    > "$output/STDOUT.txt" 2> "$output/STDERR.txt" &
child_group=$!
wait "$child_group"
child_status=$?

ps -eo pid=,pgid=,ppid=,stat=,args= | awk -v group="$child_group" \
    '$2 == group { print }' > "$output/PROCESS_FINAL.txt"
sha256sum "${hash_inputs[@]}" > "$output/END_HASHES.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$output/UTC_END.txt"
hash_gate=PASS
cmp -s "$output/START_HASHES.txt" "$output/END_HASHES.txt" || hash_gate=FAIL
residual_gate=PASS
if [[ -s "$output/PROCESS_FINAL.txt" ]]; then
    residual_gate=FAIL
    kill -TERM -- "-$child_group" 2>/dev/null || true
    for _attempt in {1..20}; do
        ps -eo pgid= | awk -v group="$child_group" '$1 == group { found=1 } END { exit !found }' \
            || break
        sleep 0.05
    done
    kill -KILL -- "-$child_group" 2>/dev/null || true
fi
bytes=$(du -sb -- "$output" | cut -f1)
size_gate=PASS
if (( bytes > 52428800 )); then
    size_gate=FAIL
fi
structure_gate=FAIL
checkpoint="$output/RESULT.checkpoints.json"
if [[ -s "$output/RESULT.json" && -s "$checkpoint" ]] && \
    .venv-v0/bin/python - "$output/RESULT.json" "$checkpoint" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
checkpoint = json.load(open(sys.argv[2], encoding="utf-8"))
if result.get("status") != "RUNNABLE_DIAGNOSTIC" or checkpoint.get("status") != "COMPLETE":
    raise SystemExit(1)
rows = result.get("whole_session_checkpoints")
if rows != checkpoint.get("checkpoints") or not isinstance(rows, list) or not rows:
    raise SystemExit(1)
if sum(item.get("final") is True for item in rows) != 1 or rows[-1].get("final") is not True:
    raise SystemExit(1)
for key in ("stream_audit", "coordinator_audit", "body_audit", "a", "b"):
    if result.get(key) is None:
        raise SystemExit(1)
roots = rows[-1].get("roots")
if not isinstance(roots, dict) or roots.get("a") is None or roots.get("b") is None:
    raise SystemExit(1)
common = {
    "prepared_accepted", "prepared_rejected", "credible_node_x_of_10_histogram",
    "primary_articulated_accepted", "accepted_root_fallback", "diagnostic_count",
}
if not common <= roots["a"].keys() or not common <= roots["b"].keys():
    raise SystemExit(1)
if not {"commit_intent", "commit_attempted", "commit_succeeded", "commit_outcomes"} <= roots["b"].keys():
    raise SystemExit(1)
PY
then
    structure_gate=PASS
fi

if [[ $hash_gate == FAIL ]]; then
    finalization=HASH_INPUT_CHANGED; final_status=90
elif [[ $residual_gate == FAIL ]]; then
    finalization=RESIDUAL_PROCESS_GROUP; final_status=91
elif [[ $size_gate == FAIL ]]; then
    finalization=EVIDENCE_SIZE_EXCEEDED; final_status=92
elif [[ $child_status == 0 && $structure_gate == FAIL ]]; then
    finalization=RESULT_STRUCTURE_INVALID; final_status=93
elif [[ $child_status != 0 ]]; then
    finalization=CHILD_FAILED; final_status=$child_status
else
    finalization=PASS; final_status=0
fi
printf 'child_exit_status=%s\nfinal_exit_status=%s\nfinalization=%s\nevidence_bytes=%s\nchild_process_group=%s\nhash_gate=%s\nresidual_gate=%s\nsize_gate=%s\nstructure_gate=%s\n' \
    "$child_status" "$final_status" "$finalization" "$bytes" "$child_group" \
    "$hash_gate" "$residual_gate" "$size_gate" "$structure_gate" \
    > "$output/STATUS.txt"

if [[ $hash_gate == PASS && $residual_gate == PASS && $size_gate == PASS ]]; then
    (
        cd "$output" || exit 94
        find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%f\n' \
            | LC_ALL=C sort | xargs -r sha256sum > SHA256SUMS
        sha256sum -c SHA256SUMS >/dev/null
    ) || exit 94
    chmod 444 "$output"/* || exit 95
    chmod 555 "$output" || exit 95
fi
exit "$final_status"
