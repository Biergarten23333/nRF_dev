#!/usr/bin/env python3
"""E2/F3 — the ECDSA signed-hash rule must stay written down and enforced.

The failure this guards against is a future round "tidying up" the build gate
into a rebuild-and-compare, or reading a marker-guard mismatch as image
corruption. Both are one-line changes that look like improvements.
"""
from pathlib import Path

root = Path(__file__).resolve().parents[2]
readme = (root / "firmware/README.md").read_text()
guard = (root / "tools/check_deployed_marker.py").read_text()
build = (root / "logs/onset_ring_20260806/build_e1.sh").read_text()
checker = (root / "logs/onset_ring_20260806/check_signed_payload.py").read_text()

# --- half one: reproducibility is judged on the unsigned app and MCUboot only
assert "Standing rule: what a signed hash is, and what it is not" in readme
assert "ECDSA" in readme and "fresh random\nnonce" in readme
assert "unsigned application and on MCUboot" in readme
# and the build script must actually do that, not merely say it
assert "check unsigned_app firmware/zephyr/zephyr.bin" in build
assert "check mcuboot      mcuboot/zephyr/zephyr.bin" in build
assert "check signed_app" not in build, \
    "the signed image must never be gated on byte equality"
assert "check_signed_payload.py" in build, \
    "the signed image is checked for payload equality up to the trailer instead"
assert "MAX_TRAILER = 160" in checker

# --- half two: the canonical signed hash is an artifact identity
assert "artifact* identity" in readme or "ARTIFACT identity" in readme
assert 'verify by rebuilding' in readme
assert "cannot be regenerated" in readme, \
    "the README must say a lost signed artifact means a new marker"
assert "cut a new marker" in readme.lower()

# --- corollary: no deploy step may rebuild what it gates on (N6)
assert "never let a deploy step rebuild what it is gating on" in readme.lower()
assert "--skip-rebuild" in readme, \
    "the corollary must name the flag that prevents the implicit rebuild"
flash_scripts = sorted(root.glob("logs/*/flash_*.sh")) + \
    sorted(root.parent.glob("UWB_Part/logs/*/flash_*.sh"))
assert flash_scripts, "no flash script found to check"
for f in flash_scripts:
    body = f.read_text()
    if "west flash" not in body:
        continue
    assert "--skip-rebuild" in body, \
        (f"{f} runs `west flash` without --skip-rebuild: it would silently "
         f"regenerate the artifacts whose hashes the deploy just verified")

# --- and the guard the next executor will actually hit must say so in place
assert "STANDING RULE" in guard
assert "does NOT mean the image is corrupt" in guard
assert "never to update the registry to" in guard
assert "cut_a_new_marker_instead_of_editing_the_registry" in guard, \
    "the failure message itself must carry the explanation"
assert "NOT reproducible" in guard

# --- the two registries have different disciplines; keep them straight
dk = (root / "host/fusion_master/marker_registry.json").read_text()
b306 = (root / "firmware/deployed_markers.json").read_text()
assert '"artifact": "zephyr.bin"' in dk, \
    "the DK registry pins the UNSIGNED app, which is reproducible"
assert '"artifact": "firmware/zephyr/zephyr.signed.bin"' in b306, \
    "the B306 registry pins the SIGNED artifact, which is not"
assert "signed_sha256" in b306

print("signed-hash standing rule: PASS")
