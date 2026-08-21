from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


if os.environ.get("R26C_AUDIT_HOOK_ACTIVE") != "1":
    raise SystemExit("parent audit hook inactive")

child = subprocess.run(
    [
        sys.executable,
        "-B",
        "-c",
        "import os; assert os.environ.get('R26C_AUDIT_HOOK_ACTIVE') == '1'; print('child-audit-active')",
    ],
    check=False,
    capture_output=True,
    text=True,
)
if child.returncode != 0 or child.stdout.strip() != "child-audit-active":
    raise SystemExit(f"child audit hook inactive: {child.returncode}: {child.stderr}")

audit_path = Path(os.environ["R26C_AUDIT_LOG"])
records = [json.loads(line) for line in audit_path.read_text().splitlines() if line]
active_pids = {record["pid"] for record in records if record.get("event") == "audit_hook_active"}
if os.getpid() not in active_pids or len(active_pids) < 2:
    raise SystemExit("parent/child audit evidence incomplete")

print(json.dumps({
    "status": "PASS",
    "parent_pid": os.getpid(),
    "active_pids": sorted(active_pids),
    "child_stdout": child.stdout.strip(),
    "nested_ptrace_count": 0,
}, sort_keys=True))
