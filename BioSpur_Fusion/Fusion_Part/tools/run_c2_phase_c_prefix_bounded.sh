#!/usr/bin/env bash
set -u

if (( $# != 1 )); then
    echo "usage: $0 NEW_OUTPUT_DIR" >&2
    exit 64
fi
output=$1
[[ ! -e "$output" ]] || { echo "output target already exists" >&2; exit 65; }
mkdir -- "$output" || exit 65

preregistered=tools/c2_phase_c_prefix_preregistered.sha256
sha256sum -c "$preregistered" > "$output/PREREGISTERED_HASH_CHECK.txt" || exit 66
diagnostic=tools/diagnose_c2_phase_c_prefix.py
diagnostic_sha=$(sha256sum "$diagnostic" | cut -d' ' -f1) || exit 66
child=(.venv-v0/bin/python "$diagnostic"
       --output "$output/RESULT.json"
       --expected-self-sha256 "$diagnostic_sha")
printf '%q ' "${child[@]}" > "$output/COMMAND.txt"
printf '\n' >> "$output/COMMAND.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$output/UTC_START.txt"
mapfile -d '' sources < <(
    find src/biospur_fusion -type f -name '*.py' -print0 | LC_ALL=C sort -z
)
hash_inputs=(
    tools/run_c2_phase_c_prefix_bounded.sh
    tools/diagnose_c2_phase_c_prefix.py
    tools/build_c2_full_session_ten_node_ab.py
    tools/build_c2_calibration_continuous_consensus_ab.py
    "$preregistered"
    tests/test_c2_phase_c_prefix.py
    tests/test_c2_full_session_ten_node_factory.py
    tests/test_c2_prospective_action00_initializer.py
    "${sources[@]}"
)
for input in "${hash_inputs[@]}"; do
    [[ -f "$input" && ! -L "$input" ]] || exit 66
done
sha256sum "${hash_inputs[@]}" > "$output/START_HASHES.txt" || exit 66

ulimit -v 1048576 || exit 67
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPATH=src:../B306_Part/tools:tests
setsid /usr/bin/time -v -o "$output/RUNTIME.txt" \
    timeout --foreground --signal=TERM --kill-after=5s 1560 "${child[@]}" \
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
    kill -KILL -- "-$child_group" 2>/dev/null || true
fi
bytes=$(du -sb -- "$output" | cut -f1)
size_gate=PASS
(( bytes <= 5242880 )) || size_gate=FAIL
structure_gate=FAIL
if [[ -s "$output/RESULT.json" ]] && .venv-v0/bin/python - "$output/RESULT.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
if result.get("status") != "PHASE_C_PREFIX_MECHANISM_PASS":
    raise SystemExit(1)
if result.get("authorization") != "GO_FULL_CONFIG_WORK":
    raise SystemExit(1)
if result.get("product_ready") is not False or result.get("scientific_pass") is not False:
    raise SystemExit(1)
if result.get("boundary", {}).get("events") != 268749:
    raise SystemExit(1)
if result.get("boundary", {}).get("records") != 36549:
    raise SystemExit(1)
if not result.get("gates") or not all(result["gates"].values()):
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
