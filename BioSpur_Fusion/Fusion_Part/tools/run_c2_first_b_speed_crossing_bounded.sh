#!/usr/bin/env bash
set -u

if (( $# != 1 )); then
    echo "usage: $0 NEW_OUTPUT_DIR" >&2
    exit 64
fi

output=$1
[[ ! -e "$output" ]] || { echo "output target already exists" >&2; exit 65; }
mkdir -- "$output" || exit 65

manifest=tools/c2_first_b_speed_crossing_preregistered.sha256
sha256sum -c "$manifest" > "$output/PREREGISTERED_HASH_CHECK.txt" || exit 66
child=(.venv-v0/bin/python -m tools.diagnose_c2_first_b_speed_crossing
       --output "$output/RESULT.json")
printf '%q ' "${child[@]}" > "$output/COMMAND.txt"
printf '\n' >> "$output/COMMAND.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$output/UTC_START.txt"

mapfile -d '' sources < <(
    find src/biospur_fusion -type f -name '*.py' -print0 | LC_ALL=C sort -z
)
inputs=("$manifest" tools/run_c2_first_b_speed_crossing_bounded.sh
 tools/diagnose_c2_first_b_speed_crossing.py
 tools/diagnose_c2_full_session_first_divergence.py
 tools/diagnose_c2_phase_c_prefix.py tools/build_c2_full_session_ten_node_ab.py
 tests/test_c2_first_b_speed_crossing.py "${sources[@]}")
sha256sum "${inputs[@]}" > "$output/START_HASHES.txt" || exit 66

ulimit -v 1048576 || exit 67
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPATH=.:src:../B306_Part/tools:tests
setsid /usr/bin/time -v -o "$output/RUNTIME.txt" \
 timeout --foreground --signal=TERM --kill-after=5s 1860 "${child[@]}" \
 > "$output/STDOUT.txt" 2> "$output/STDERR.txt" &
group=$!
printf '%s\n' "$group" > "$output/PROCESS_GROUP.txt"
wait "$group"
child_status=$?

ps -eo pid=,pgid=,ppid=,stat=,args= | \
 awk -v group="$group" '$2 == group {print}' > "$output/PROCESS_FINAL.txt"
sha256sum "${inputs[@]}" > "$output/END_HASHES.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$output/UTC_END.txt"

hash_gate=PASS
cmp -s "$output/START_HASHES.txt" "$output/END_HASHES.txt" || hash_gate=FAIL
residual_gate=PASS
if [[ -s "$output/PROCESS_FINAL.txt" ]]; then
    residual_gate=FAIL
    kill -TERM -- "-$group" 2>/dev/null || true
    kill -KILL -- "-$group" 2>/dev/null || true
fi
bytes=$(du -sb -- "$output" | cut -f1)
size_gate=PASS
(( bytes <= 5242880 )) || size_gate=FAIL
structure_gate=FAIL
if [[ -s "$output/RESULT.json" ]] && .venv-v0/bin/python - "$output/RESULT.json" <<'PY'
import json
import sys
from tools.diagnose_c2_first_b_speed_crossing import validate_result

with open(sys.argv[1], encoding="utf-8") as source:
    validate_result(json.load(source))
PY
then
    structure_gate=PASS
fi

if [[ $hash_gate == FAIL ]]; then final=HASH_INPUT_CHANGED; status=90
elif [[ $residual_gate == FAIL ]]; then final=RESIDUAL_PROCESS_GROUP; status=91
elif [[ $size_gate == FAIL ]]; then final=EVIDENCE_SIZE_EXCEEDED; status=92
elif [[ $child_status != 0 ]]; then final=CHILD_FAILED; status=$child_status
elif [[ $structure_gate == FAIL ]]; then final=RESULT_STRUCTURE_INVALID; status=93
else final=PASS; status=0
fi
printf 'child_exit_status=%s\nfinal_exit_status=%s\nfinalization=%s\nevidence_bytes=%s\nchild_process_group=%s\nhash_gate=%s\nresidual_gate=%s\nsize_gate=%s\nstructure_gate=%s\n' \
 "$child_status" "$status" "$final" "$bytes" "$group" "$hash_gate" \
 "$residual_gate" "$size_gate" "$structure_gate" > "$output/STATUS.txt"

if [[ $hash_gate == PASS && $residual_gate == PASS && $size_gate == PASS ]]; then
    (cd "$output" && find . -maxdepth 1 -type f ! -name SHA256SUMS \
      -printf '%f\n' | LC_ALL=C sort | xargs -r sha256sum > SHA256SUMS \
      && sha256sum -c SHA256SUMS >/dev/null) || exit 94
    chmod 444 "$output"/* || exit 95
    chmod 555 "$output" || exit 95
fi
exit "$status"
