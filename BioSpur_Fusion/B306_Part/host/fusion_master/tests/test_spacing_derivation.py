#!/usr/bin/env python3
"""Contract for connection-spacing correctness (batch spacing_default_20260807).

Spacing has been wrong for a whole capture window at least once, and it fired
twice more in a single night. It never fails loudly: boards still connect, still
deliver, and the capture still runs. The only symptom is that the connection
schedule is wrong for the entire window.

Three layers defend it, and this test pins all three, because each alone leaks:
a corrected boot default is bypassed by an old image, corrected tooling is
bypassed by a manual flash, and the pre-window assertion alone means it stays a
matter of somebody remembering to check.
"""
import re
from pathlib import Path

root = Path(__file__).resolve().parents[3]
dk = (root / "host/fusion_master/src/main.c").read_text()


def strip_c_comments(src):
    """Negative assertions must not match prose.

    The first run of this test failed on its own explanatory comment, which
    quotes the very call it forbids ("this used to be spacing_apply(OFF)").
    A source contract that greps comments forbids documenting the thing it
    protects against, which is backwards.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


dk_code = strip_c_comments(dk)
tx = (root / "tools/v32_ota_board_transaction.py").read_text()
sp = (root / "tools/fusion_spacing.py").read_text()

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# --- LAYER 1: the firmware boots correct, BY DERIVATION -------------------
check("#define SPACING_ON_US (FUSION_CONN_INTERVAL_US / MAX_FUSION_PEERS)" in dk,
      "spacing must be DERIVED from interval and peer count, never a literal: "
      "5000 is only correct for 10 nodes at 50 ms, and the 20-node expansion "
      "would make the literal silently wrong in exactly the same way")
check(re.search(r"#define\s+SPACING_ON_US\s+5000", dk_code) is None,
      "SPACING_ON_US must not be written as the literal 5000")
check("spacing_apply(SPACING_MODE_ON)" in dk_code,
      "the boot path must apply the derived spacing. It used to apply "
      "SPACING_MODE_OFF over a controller whose Kconfig default was already "
      "correct -- the DK did not lose spacing on a reflash, it actively set it "
      "wrong on every boot")
check("spacing_apply(SPACING_MODE_OFF)" not in dk_code,
      "nothing may apply the 7500 us baseline at boot")
check("BUILD_ASSERT(FUSION_CONN_INTERVAL_US % MAX_FUSION_PEERS == 0u" in dk,
      "the build must fail if the interval does not divide evenly by the peer "
      "count -- there is no correct spacing value in that case")
check("BUILD_ASSERT(CONFIG_BT_CTLR_SDC_CENTRAL_ACL_EVENT_SPACING_DEFAULT == SPACING_ON_US"
      in dk,
      "the controller Kconfig default and the derived value must be pinned "
      "together, or a node-count change fixes one and forgets the other")
# the interval literal must exist in exactly one place
check(dk.count("#define FUSION_CONN_INTERVAL_UNITS") == 1,
      "the connection interval must have exactly one definition")
check(dk_code.count(".interval_min = 40") == 0 and
      dk_code.count(".interval_max = 40") == 0,
      "connection params must use FUSION_CONN_INTERVAL_UNITS, not the literal 40, "
      "or the interval and the spacing derivation can drift apart")

# derivation actually evaluates to the intended value
m_i = re.search(r"#define FUSION_CONN_INTERVAL_UNITS (\d+)u", dk)
m_p = re.search(r"#define MAX_FUSION_PEERS (\d+)", dk)
check(m_i is not None and m_p is not None, "derivation inputs not found")
if m_i and m_p:
    derived = int(m_i.group(1)) * 1250 // int(m_p.group(1))
    check(derived == 5000,
          f"derivation currently yields {derived} us, not the 5000 us the fleet "
          f"was validated at -- if that is intentional, update this test and "
          f"re-validate the schedule on hardware")

# --- LAYER 2: the rebuild is INSIDE the restore --------------------------
check("def restore_master(" in tx,
      "there must be a single restore helper; two open-coded restore sites is "
      "how one of them ends up without the rebuild")
check("flash(restore_script, args.out_dir / \"restore_v28_jlink.log\")" not in tx,
      "the normal restore path must go through restore_master(), not bare flash()")
check(tx.count("restore_master(") >= 3,
      "every restore site (normal and emergency) must use the helper")
check("ensure_spacing" in tx,
      "the restore must rebuild spacing itself, not leave it to the caller")
check(tx.index("def restore_master(") < tx.index("def main("),
      "restore_master must be defined before use")

# the helper's contract is on STATE, not on a generation bump
check('res["action"] = "none_already_correct"' in sp,
      "the helper must accept an already-correct DK: dk-v36 boots correct, so "
      "SPACING ON answers UNCHANGED and does NOT bump the generation. "
      "Requiring an increase would fail the correct image.")
check("_resolve_with_retry" in sp,
      "the helper must wait for the CDC to re-enumerate: its first caller is "
      "the restore step, which has just reset the DK over J-Link. The first "
      "hardware run failed with 'found []' for exactly this reason.")
check("EXPECTED_SPACING_US = FUSION_CONN_INTERVAL_US // FUSION_PEERS" in sp,
      "the host-side expectation must be derived too, not pasted")

# --- LAYER 3: the pre-window assertion stays -----------------------------
ops = root.parent / "UWB_Part/logs/deploy_20260806/b_fusion_ops.py"
if ops.exists():
    check("def spacing_contract(" in ops.read_text(),
          "the pre-window spacing assertion must NOT be removed -- it is the "
          "only layer that has ever actually caught this, twice in one night. "
          "Layers 1 and 2 make it a backstop, not a replacement.")

if fails:
    print("spacing derivation contract: FAIL")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("spacing derivation contract: PASS")
