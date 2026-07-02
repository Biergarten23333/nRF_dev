#!/usr/bin/env python3
"""Live L-B up-link CIR probe viewer.

Reads L-B serial (CIR-probe build), reassembles each accumulator dump
(LCIRM header -> N x LCIRD chunks -> LCIRE), decodes the DW1000 complex CIR,
and prints a per-capture first-path summary + leading-edge sparkline so you can
WATCH the first-path notch appear as you move the occluder.

Occlusion signatures to watch:
  - rxpow_dB drops (absolute attenuation: meat actually in the path)
  - FP/peak (dB) RISES and/or leading edge softens/shifts right (multipath NLOS:
    direct ray killed, energy arrives late) -> the "notch"
Both together = occluded. Flat = ray not intercepted (as we saw with on-antenna meat).
"""
import sys, time, math, argparse
import serial

TAG = {2: "BS9336", 3: "BS955A", 4: "BSCCF4"}
BARS = " .:-=+*#%@"

def decode_mag(accbytes):
    """DW1000 ACC_MEM -> per-sample magnitude. 4 bytes/sample: int16 re, int16 im (LE)."""
    n = len(accbytes) // 4
    mags = []
    for k in range(n):
        b = accbytes[4*k:4*k+4]
        re = int.from_bytes(b[0:2], "little", signed=True)
        im = int.from_bytes(b[2:4], "little", signed=True)
        mags.append(math.hypot(re, im))
    return mags

def spark(vals, vmax):
    if vmax <= 0:
        return ""
    out = []
    for v in vals:
        idx = int(round((v / vmax) * (len(BARS) - 1)))
        out.append(BARS[max(0, min(len(BARS)-1, idx))])
    return "".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=460800)
    ap.add_argument("--min-interval-s", type=float, default=0.8, help="rate-limit prints")
    ap.add_argument("--win-pre", type=int, default=6, help="samples before FP in sparkline")
    ap.add_argument("--win-post", type=int, default=28, help="samples after FP in sparkline")
    args = ap.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=1.0)
    print(f"[cir-view] {args.port} @ {args.baud}  (waiting for ranging traffic + LCIR dumps)", flush=True)
    cur = None          # (pollcount) accumulation state
    chunks = {}         # offset -> bytes
    hdr = None
    acc_len = 0
    last_print = 0.0
    captures = 0
    while True:
        try:
            raw = ser.readline().decode("ascii", "replace").strip()
        except serial.SerialException as e:
            print(f"[cir-view] serial error: {e}", flush=True); time.sleep(1.0); continue
        if not raw:
            continue
        if raw.startswith("LCIRM;"):
            f = raw.split(";")
            try:
                hdr = {
                    "poll": int(f[4]), "tag": int(f[6]), "mask": f[7],
                    "firstPath": int(f[10]), "fpAmp1": int(f[11]), "fpAmp2": int(f[12]),
                    "fpAmp3": int(f[13]), "maxGrowth": int(f[14]), "rxpacc": int(f[15]),
                    "acc_len": int(f[16]),
                }
                cur = hdr["poll"]; chunks = {}; acc_len = hdr["acc_len"]
            except (IndexError, ValueError):
                hdr = None; cur = None
        elif raw.startswith("LCIRD;") and cur is not None:
            f = raw.split(";")
            try:
                poll = int(f[2]); off = int(f[3]); ln = int(f[4]); hexs = f[5]
                if poll == cur and len(hexs) >= 2*ln:
                    chunks[off] = bytes.fromhex(hexs[:2*ln])
            except (IndexError, ValueError):
                pass
        elif raw.startswith("LCIRE;") and cur is not None and hdr is not None:
            # reassemble
            data = bytearray()
            off = 0
            ok = True
            while off < acc_len:
                c = chunks.get(off)
                if c is None:
                    ok = False; break
                data += c; off += len(c)
            captures += 1
            now = time.time()
            if ok and (now - last_print) >= args.min_interval_s:
                last_print = now
                mags = decode_mag(bytes(data))
                if mags:
                    peak = max(mags); peak_i = mags.index(peak)
                    fp_i = int(round(hdr["firstPath"] / 64.0))  # firstPath is 1/64-sample units
                    fp_i = max(0, min(len(mags)-1, fp_i))
                    fp_mag = mags[fp_i] if 0 <= fp_i < len(mags) else 0.0
                    # rxpacc-normalized RX power proxy from peak growth (rel dB, A dropped)
                    rp = hdr["rxpacc"] or 1
                    rxpow = 10*math.log10(hdr["maxGrowth"]*(2**17)/(rp*rp)) if hdr["maxGrowth"]>0 else float("nan")
                    fp_over_peak = 20*math.log10(peak/fp_mag) if (fp_mag>0 and peak>0) else float("nan")
                    lo = max(0, fp_i - args.win_pre); hi = min(len(mags), fp_i + args.win_post)
                    sk = spark(mags[lo:hi], peak)
                    tagn = TAG.get(hdr["tag"], f"id{hdr['tag']}")
                    print(f"[{time.strftime('%H:%M:%S')}] {tagn:7s} fp_idx={fp_i:4d} peak_idx={peak_i:4d} "
                          f"rxpow={rxpow:5.1f}dB  FP/peak={fp_over_peak:5.1f}dB  fpAmp1={hdr['fpAmp1']:6d}  "
                          f"|{sk}|  (n={captures})", flush=True)
            cur = None; hdr = None; chunks = {}

if __name__ == "__main__":
    main()
