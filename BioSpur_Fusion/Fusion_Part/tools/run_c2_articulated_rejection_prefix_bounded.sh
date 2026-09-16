#!/usr/bin/env bash
set -u

if (( $# < 1 )); then
    echo "usage: $0 OUTPUT_DIR [-- CHILD [ARG...]]" >&2
    exit 64
fi
output=$1
shift
mkdir -- "$output" || exit 65
if [[ ${1-} == -- ]]; then
    shift
    (( $# > 0 )) || exit 64
    child=("$@")
else
    child=(.venv-v0/bin/python tools/diagnose_c2_articulated_rejection_prefix.py
           --output "$output/RESULT.json")
fi

printf '%q ' "${child[@]}" > "$output/COMMAND.txt"
printf '\n' >> "$output/COMMAND.txt"
hash_inputs=(
    tools/diagnose_c2_articulated_rejection_prefix.py
    tools/run_c2_articulated_rejection_prefix_bounded.sh
    tests/test_c2_articulated_rejection_prefix.py
    tools/build_c2_full_session_ten_node_ab.py
    src/biospur_fusion/c2_coupled_progressive/continuous_full_session_reader.py
    src/biospur_fusion/c2_coupled_progressive/full_session_ten_node_ab.py
    src/biospur_fusion/c2_coupled_progressive/continuous_group_epoch_owner.py
    src/biospur_fusion/c2_coupled_progressive/prospective_action00_initializer.py
    src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py
    src/biospur_fusion/c2_uwb_root_world/full_session_body_pose.py
    src/biospur_fusion/c2_uwb_root_world/authoritative_articulated_fusion.py
    src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py
    src/biospur_fusion/c2_uwb_root_world/causal_update_guard.py
    src/biospur_fusion/c2_uwb_root_world/causal_update_transaction.py
    src/biospur_fusion/c2_articulated_biomechanics/orientation_ik.py
    src/biospur_fusion/root_r3/estimator.py
    tests/root_r3/test_deferred_uwb_transactions.py
    tests/test_c2_causal_update_guard.py
    tests/test_c2_causal_update_transaction.py
    tests/test_c2_continuous_group_epoch_owner.py
    tests/test_c2_continuous_full_session_delivery.py
    tests/test_c2_full_session_body_pose.py
    tests/test_c2_full_session_ten_node_ab.py
    tests/test_c2_prospective_action00_initializer.py
    tests/test_c2_authoritative_articulated_fusion.py
    tests/test_c2_articulated_biomechanics.py
    tests/test_c2_owner_bound_async_worker.py
)
sha256sum "${hash_inputs[@]}" > "$output/START_HASHES.txt" || exit 66

ulimit -v 1048576 || exit 67
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPATH=src:../B306_Part/tools:tests
setsid /usr/bin/time -v -o "$output/RUNTIME.txt" \
    timeout --foreground --signal=TERM --kill-after=5s 300s "${child[@]}" \
    > "$output/STDOUT.txt" 2> "$output/STDERR.txt" &
child_group=$!
wait "$child_group"
child_status=$?

ps -eo pid=,pgid=,ppid=,stat=,args= | awk -v group="$child_group" \
    '$2 == group { print }' > "$output/PROCESS_FINAL.txt"
sha256sum "${hash_inputs[@]}" > "$output/END_HASHES.txt"
final_status=$child_status
finalization=PASS
if ! cmp -s "$output/START_HASHES.txt" "$output/END_HASHES.txt"; then
    finalization=HASH_INPUT_CHANGED
    (( final_status != 0 )) || final_status=90
fi
if [[ -s "$output/PROCESS_FINAL.txt" ]]; then
    finalization=RESIDUAL_PROCESS_GROUP
    (( final_status != 0 )) || final_status=91
    kill -TERM -- "-$child_group" 2>/dev/null || true
    for _attempt in {1..20}; do
        ps -eo pgid= | awk -v group="$child_group" '$1 == group { found=1 } END { exit !found }' \
            || break
        sleep 0.05
    done
    kill -KILL -- "-$child_group" 2>/dev/null || true
fi
bytes=$(du -sb -- "$output" | cut -f1)
if (( bytes > 1048576 )); then
    finalization=EVIDENCE_SIZE_EXCEEDED
    (( final_status != 0 )) || final_status=92
fi
printf 'child_exit_status=%s\nfinal_exit_status=%s\nfinalization=%s\nevidence_bytes=%s\nchild_process_group=%s\n' \
    "$child_status" "$final_status" "$finalization" "$bytes" "$child_group" \
    > "$output/STATUS.txt"

if [[ $finalization == PASS ]]; then
    (
        cd "$output" || exit 93
        find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%f\n' \
            | LC_ALL=C sort | xargs -r sha256sum > SHA256SUMS
        sha256sum -c SHA256SUMS >/dev/null
    ) || exit 93
    chmod 444 "$output"/* || exit 94
    chmod 555 "$output" || exit 94
fi
exit "$final_status"
