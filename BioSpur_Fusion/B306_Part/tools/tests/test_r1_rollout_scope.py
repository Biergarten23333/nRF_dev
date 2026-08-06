#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
confirm = (root / "confirm_b306_v32.py").read_text()
transaction = (root / "v32_ota_board_transaction.py").read_text()

assert '"--target-only"' in confirm
assert 'master = None if args.target_only else wait_master_status(channel)' in confirm
assert '"--target-only",' in transaction
assert '"--fleet-preflight-result"' in transaction
assert 'responders != FLEET_NODES' in transaction
assert 'status={preflight.get(\'status\')}' in transaction
print("R1 rollout scope contract: PASS")
