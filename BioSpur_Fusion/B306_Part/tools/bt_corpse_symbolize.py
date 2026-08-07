#!/usr/bin/env python3
"""Symbolise a v43 corpse against the exact image that produced it.

Two jobs:

  collect   read a run's rings.jsonl, reassemble every corpse that was exported,
            and write ONE immutable record per corpse (board, firmware hash,
            sequence, reset reason, decoded stage, raw payload).
  symbolize resolve the raw addresses in a corpse against the v43 zephyr.elf.

THE GATE
--------
Symbolising against the wrong build produces confident nonsense, so the ELF is
gated before it is trusted. The gate is the FROZEN SIGNED FILE sitting beside it
in the build directory -- never a rebuild. The signed image is not byte
reproducible (imgtool draws a fresh ECDSA P-256 nonce every run), so "rebuild
and compare" would fail on a correct tree and, worse, invites someone to
regenerate the artifact they are supposed to be checking against. That is the
standing rule this project already learned once.

The corpse also carries an FNV-1a hash of its own BSF_FW_MARKER, which is
checked against the marker string found in the ELF. A mismatch is fatal.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bt_corpse_decode as bcd  # noqa: E402

# The canonical v43 signed artifact. Frozen: this is the deployed byte sequence.
V43_SIGNED_SHA = "52dfc9241844a48ddc21ad8c406a190070b02061a99d39b4dd29dbeac449d54d"
V43_UNSIGNED_SHA = "df7a543f1a9868cec2e985e52f827d6051dc994a58c35cfaabce9e440d33ceac"


def fnv1a(s):
    h = 2166136261
    for b in s.encode():
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def gate_elf(elf):
    """Refuse to symbolise unless this ELF belongs to the canonical v43 build."""
    elf = Path(elf)
    if not elf.exists():
        sys.exit(f"REFUSED: no ELF at {elf}")
    build = elf.parent
    unsigned = build / "zephyr.bin"
    signed = build / "zephyr.signed.bin"
    for path, want, label in ((unsigned, V43_UNSIGNED_SHA, "unsigned"),
                              (signed, V43_SIGNED_SHA, "signed")):
        if not path.exists():
            sys.exit(f"REFUSED: {label} artifact missing beside the ELF: {path}")
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            sys.exit(f"REFUSED: {label} artifact hashes {got}, canonical v43 is "
                     f"{want}. This is not the deployed image; symbolising "
                     f"against it would produce confident nonsense.")
    return elf


def load_symbols(elf):
    out = subprocess.run(["nm", "-S", "-C", str(elf)], capture_output=True,
                         text=True, check=True).stdout
    syms = []
    for line in out.splitlines():
        p = line.split()
        # ARM emits mapping symbols ($a code, $d data, $t thumb) at every
        # section transition. They are zero-sized, sit at the same addresses as
        # real symbols, and win a nearest-preceding lookup -- which is how every
        # address first resolved to "$d". They are not names; drop them.
        if p and p[-1] in ("$a", "$d", "$t") or (p and p[-1].startswith("$")):
            continue
        if len(p) == 4:
            try:
                syms.append((int(p[0], 16), int(p[1], 16), p[3]))
            except ValueError:
                continue
        elif len(p) == 3:
            try:
                syms.append((int(p[0], 16), 0, p[2]))
            except ValueError:
                continue
    syms.sort()
    return syms


def resolve(syms, addr):
    if not addr:
        return None
    best = None
    for a, size, name in syms:
        if a <= addr and (size == 0 or addr < a + size):
            if best is None or a > best[0]:
                best = (a, size, name)
    if best is None:
        return None
    off = addr - best[0]
    return f"{best[2]}+0x{off:x}" if off else best[2]


def marker_in_elf(elf):
    out = subprocess.run(["strings", str(elf)], capture_output=True, text=True).stdout
    m = re.findall(r"b306-imu-relay-v\d+", out)
    return sorted(set(m))


def cmd_symbolize(args):
    elf = gate_elf(args.elf)
    syms = load_symbols(elf)
    c = json.loads(Path(args.corpse).read_text()) if args.corpse.endswith(".json") \
        else bcd.decode(Path(args.corpse).read_bytes())
    if "classification" not in c:
        c["classification"] = bcd.classify(c)

    markers = marker_in_elf(elf)
    ok = [m for m in markers if f"{fnv1a(m):08x}" == c["fw_marker_hash"]]
    if not ok:
        sys.exit(f"REFUSED: corpse fw_marker_hash={c['fw_marker_hash']} matches "
                 f"none of the markers in this ELF {markers}. Wrong build.")
    c["fw_marker"] = ok[0]

    c["symbols"] = {
        "rx_thread": resolve(syms, int(c["rx_thread_addr"], 16)),
        "rx_thread_sp": resolve(syms, int(c["rx_thread_sp"], 16)),
        "conn": resolve(syms, int(c["conn"]["addr"], 16)),
        "conn_rx_buf": resolve(syms, int(c["conn"]["rx_ptr"], 16)),
    }
    print(json.dumps(c, indent=2) if args.json else _human(c))
    return 0


def _human(c):
    L = [f"CORPSE seq={c['corpse_seq']} node={c['node_identity']} "
         f"fw={c.get('fw_marker')} crc32_ok={c['crc32_ok']}",
         f"  classification : {c['classification']}",
         f"  stage          : {c['stage_name']} seq={c['stage_seq']} "
         f"age_ms={c['stage_age_ms']}",
         f"  BT RX thread   : {c['rx_thread_addr']} -> {c['symbols']['rx_thread']}",
         f"                   state={c['rx_thread_state_bits']} "
         f"sp={c['rx_thread_sp']} -> {c['symbols']['rx_thread_sp']}",
         f"  BT RX stack    : {c['rx_stack_size']} B, "
         f"{c['rx_stack_size'] - c['rx_stack_unused']} used, "
         f"{c['rx_stack_unused']} free",
         f"  conn           : {c['conn']['addr']} -> {c['symbols']['conn']} "
         f"state={c['conn']['state']} err={c['conn']['err']}",
         f"  tx_complete_work busy=[{c['conn']['tx_complete_busy_bits']}]  "
         f"deferred_work busy=[{c['conn']['deferred_busy_bits']}]",
         f"  tx_pending={c['conn']['tx_pending']} "
         f"tx_complete={c['conn']['tx_complete']} "
         f"pkts_avail={c['conn']['pkts_avail']}",
         f"  liveness       : wdt={c['wdt_feed_count']} notify_ok={c['notify_ok']} "
         f"producer={c['producer_seq']}",
         "  trace (oldest first):"]
    for t in c["trace"]:
        L.append(f"    {t['cycles']:>10} {t['stage_name']:<28} {t['event']}")
    return "\n".join(L)


def cmd_collect(args):
    """One immutable record per corpse, from a run's rings.jsonl."""
    by_node = {}
    for line in Path(args.rings).read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        raw = rec.get("raw", "")
        m = bcd.HEX_RE.search(raw)
        if not m:
            continue
        node, off, _n, hexs = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        by_node.setdefault(node, {}).setdefault(rec.get("t"), {})[off] = hexs

    out = []
    for node, frames in by_node.items():
        pages = []
        for _t, chunks in sorted(frames.items(), key=lambda kv: (kv[0] is None, kv[0])):
            blob = bytearray(bcd.PAGE_SIZE)
            got = 0
            for off, h in chunks.items():
                b = bytes.fromhex(h)
                if off + len(b) <= bcd.PAGE_SIZE:
                    blob[off:off + len(b)] = b
                    got += len(b)
            if got >= bcd.PAGE_SIZE:
                try:
                    pages.append(bcd.decode_page(bytes(blob)))
                except ValueError:
                    pass                       # a ring page, not a corpse page
        if not pages:
            continue
        for seq in sorted({p["seq"] for p in pages}):
            sel = [p for p in pages if p["seq"] == seq]
            blob, bad, missing = bcd.merge(sel)
            rec = {"node": node, "corpse_seq": seq,
                   "pages_seen": len(sel), "pages_bad": bad,
                   "pages_missing": missing,
                   "payload_sha256": hashlib.sha256(blob).hexdigest(),
                   "payload_hex": blob.hex()}
            try:
                c = bcd.decode(blob)
                c["classification"] = bcd.classify(c)
                rec.update(crc32_ok=c["crc32_ok"], stage=c["stage_name"],
                           trigger=c["trigger"],
                           boot_reset_reason=c["boot_reset_reason"],
                           fw_marker_hash=c["fw_marker_hash"],
                           classification=c["classification"], decoded=c)
            except ValueError as exc:
                rec.update(crc32_ok=False, error=str(exc))
            out.append(rec)

    dest = Path(args.out)
    with dest.open("w") as fh:
        for rec in out:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    print(f"{len(out)} corpse record(s) -> {dest}")
    for r in out:
        print(f"  {r['node']} seq={r['corpse_seq']} crc32_ok={r.get('crc32_ok')} "
              f"stage={r.get('stage')} class={r.get('classification')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("symbolize")
    s.add_argument("corpse", help="corpse.bin or a decoded .json")
    s.add_argument("--elf", required=True,
                   help="B306_Part/builds/b306-imu-relay-v43-a/firmware/zephyr/zephyr.elf")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_symbolize)
    c = sub.add_parser("collect")
    c.add_argument("rings", help="the run's rings.jsonl")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_collect)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
