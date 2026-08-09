#!/usr/bin/env python3
"""Every software reset must name itself.

A `sys_reboot()` or `NVIC_SystemReset()` that is not preceded by a recorded
intent produces a boot the system cannot attribute. That is not cosmetic: the
boot-loop protection counts guard resets, so a path outside the census is a
path the streak counter does not cover, and on 2026-08-09 exactly one such
reset appeared (`rr=4`, `rcv=0`, `dog=0`) and could not be explained.

THIS TEST IS REQUIRED TO FAIL FIRST. Run it against the tree before the call
sites are converted and it must report the unregistered ones. A contract that
has only ever passed cannot distinguish "the property holds" from "the check is
broken" -- eight false verdicts in this project say so, two of them caught
within an hour of that rule being written down.
"""
from __future__ import annotations
import re, sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
ALLOWED = {"bsf_reset_intent.c"}      # the only file that may call sys_reboot
failures: list[str] = []

def check(c, m):
    if not c: failures.append(m)
    return bool(c)

print("v46r2 reset-intent contract")

# 1. every reset call site outside the intent module must go through bsf_reset_now()
raw = []
for f in sorted(SRC.glob("*.c")):
    if f.name in ALLOWED:
        continue
    for i, line in enumerate(f.read_text().splitlines(), 1):
        if re.search(r"\b(sys_reboot|NVIC_SystemReset)\s*\(", line) and \
           not line.lstrip().startswith("*") and "//" not in line.split("sys_reboot")[0]:
            raw.append(f"{f.name}:{i}")
if check(not raw,
         f"{len(raw)} reset call site(s) bypass bsf_reset_now(): {raw}"):
    print("  ok   every reset goes through bsf_reset_now()")

# 2. the intent module must seal before resetting, not after
ri = (SRC / "bsf_reset_intent.c").read_text()
m = re.search(r"void bsf_reset_now\(uint8_t intent\)\s*\{(.*?)\n\}", ri, re.S)
if check(m is not None, "bsf_reset_now() not found"):
    body = m.group(1)
    mark = body.find("bsf_reset_intent_mark")
    boot = body.find("sys_reboot")
    if check(mark != -1 and boot != -1 and mark < boot,
             "bsf_reset_now() must seal the intent BEFORE sys_reboot(), not after"):
        print("  ok   intent is sealed before the reset")

# 3. RESETREAS must be read before anything clears it
if check("nrfx_reset_reason_get" in ri and "nrfx_reset_reason_clear" not in ri,
         "the intent module must READ RESETREAS and must not clear it"):
    print("  ok   raw RESETREAS captured without clearing")
if check(re.search(r"SYS_INIT\(bsf_reset_intent_early,\s*PRE_KERNEL_1", ri),
         "RESETREAS capture must run at PRE_KERNEL_1"):
    print("  ok   capture runs at PRE_KERNEL_1")

# 4. an SREQ with no intent must be counted, not ignored
if check(re.search(r"unknown_sreq\+\+", ri),
         "an SREQ with no recorded intent must increment unknown_sreq"):
    print("  ok   unattributed SREQ is counted")

# 5. every intent id used anywhere must be declared
hdr = (SRC / "bsf_reset_intent.h").read_text()
declared = set(re.findall(r"#define (BSF_RESET_INTENT_[A-Z_]+)\s", hdr))
used = set()
for f in SRC.glob("*.c"):
    used |= set(re.findall(r"\bBSF_RESET_INTENT_[A-Z_]+\b", f.read_text()))
undeclared = used - declared
if check(not undeclared, f"intent ids used but never declared: {sorted(undeclared)}"):
    print(f"  ok   all {len(used)} intent ids used are declared")

print("v46r2 reset-intent contract:", "FAIL" if failures else "PASS")
for f in failures: print("  -", f)
sys.exit(1 if failures else 0)
