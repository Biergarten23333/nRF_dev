#!/usr/bin/env python3
"""Offline yield ladder and event log for a v43 run.

Reads the run's raw hourly archive. Computed offline, never live, so heavy
analysis cannot perturb or fall behind the capture it is measuring.

ARITHMETIC RULES (brief section 13), each of which has cost a session before:

  * Every ratio carries its numerator and denominator. A bare percentage hides
    whether it is 9/10 or 9000/10000.
  * Every rate is (records - 1) / (last_ts - first_ts). Never records/window:
    that silently divides by a nominal duration the capture did not have.
  * A locked COUNT=12 x PERIOD=10 schedule CANNOT exceed 8.3333 Hz. A computed
    rate above that is an arithmetic bug, not a fast board, and is flagged.
  * Captures open with a short stale block drained at attachment, then jump to
    live records. Using those as endpoints understates the rate by ~10%. The
    series is split at timestamp discontinuities and the stale prefix dropped.
  * Losses are counted from SEQUENCE JUMPS, not q_drop -- q_drop understates by
    about tenfold.
  * IMU seq is 16-bit and wraps every 327.68 s at 200 Hz, so a modular gap must
    be unwrapped against elapsed time:
        real_gap = mod_gap + 65536 * round((elapsed_s*200 - mod_gap) / 65536)
  * Freshness is INSUFFICIENT on a static bench. It is labelled, never numbered.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TDMA_CEILING_HZ = 8.3333          # COUNT=12 x PERIOD=10
CEILING_TOL = 1.02                # host ARRIVAL timestamps carry CDC jitter;
                                  # only flag a rate that exceeds the schedule
                                  # by more than the jitter can explain
GAP_TOL = 1.5                     # a gap must be consistent with elapsed time
                                  # to count as loss rather than a discontinuity
IMU_HZ = 200.0
IMU_SEQ_MOD = 65536
STALE_GAP_S = 5.0                 # a jump this large splits the series

UWB_RE = re.compile(r"FUSION_UWB .*?name=(\S+).*?\bsweep=(\d+).*?\bvalid=0x([0-9a-fA-F]+)")
IMU_RE = re.compile(r"FUSION_IMU .*?name=(\S+).*?\bseq=(\d+).*?\bn=(\d+)")


def popcount(x):
    return bin(x).count("1")


def split_stale(series):
    """Drop the stale prefix: keep the last contiguous block."""
    if len(series) < 3:
        return series, 0
    blocks, cur = [], [series[0]]
    for prev, cur_pt in zip(series, series[1:]):
        if cur_pt[0] - prev[0] > STALE_GAP_S:
            blocks.append(cur)
            cur = []
        cur.append(cur_pt)
    blocks.append(cur)
    keep = max(blocks, key=len)
    dropped = len(series) - len(keep)
    return keep, dropped


def rate(series):
    """(records - 1) / (last - first). None when it cannot be computed."""
    pts, _ = split_stale(series)
    if len(pts) < 2:
        return None, len(pts), None
    span = pts[-1][0] - pts[0][0]
    if span <= 0:
        return None, len(pts), span
    return (len(pts) - 1) / span, len(pts), span


def analyse(paths, hours):
    uwb = defaultdict(list)     # node -> [(t, sweep, valid)]
    imu = defaultdict(list)     # node -> [(t, seq, n)]
    for p in paths:
        with open(p, "r", errors="replace") as fh:
            for line in fh:
                sp = line.split(" ", 2)
                if len(sp) < 3:
                    continue
                try:
                    t = float(sp[0])
                except ValueError:
                    continue
                rest = sp[2]
                m = UWB_RE.search(rest)
                if m:
                    uwb[m.group(1)].append((t, int(m.group(2)), int(m.group(3), 16)))
                    continue
                m = IMU_RE.search(rest)
                if m:
                    imu[m.group(1)].append((t, int(m.group(2)), int(m.group(3))))

    nodes = sorted(set(uwb) | set(imu))
    t0 = min([s[0][0] for s in list(uwb.values()) + list(imu.values()) if s] or [0])
    out = {"nodes": {}, "t0_epoch": t0}

    for n in nodes:
        u = sorted(uwb[n])
        i = sorted(imu[n])
        rec = {}

        # --- UWB ladder -------------------------------------------------
        r, kept, span = rate(u)
        rec["uwb"] = {
            "delivered": len(u), "kept_after_stale_split": kept,
            "span_s": round(span, 3) if span else None,
            "rate_hz": round(r, 4) if r else None,
            "rate_over_ceiling": bool(r and r > TDMA_CEILING_HZ * CEILING_TOL),
        }
        if u:
            hist = defaultdict(int)
            for _t, _s, v in u:
                hist[popcount(v)] += 1
            tot = len(u)
            rec["uwb"]["valid_link_histogram"] = {str(k): hist[k] for k in sorted(hist)}
            rec["uwb"]["ge8"] = f"{hist[8]}/{tot}"
            rec["uwb"]["ge8_pct"] = round(100.0 * hist[8] / tot, 4)
            ge7 = sum(v for k, v in hist.items() if k >= 7)
            rec["uwb"]["ge7"] = f"{ge7}/{tot}"
            rec["uwb"]["ge7_pct"] = round(100.0 * ge7 / tot, 4)
            # loss from sweep-number jumps, not q_drop
            pts, _ = split_stale(u)
            gaps, lost, disc = 0, 0, 0
            for a, b in zip(pts, pts[1:]):
                d = b[1] - a[1]
                if d <= 1:
                    # <=0 is a counter reset (reboot). Not loss.
                    if d <= 0:
                        disc += 1
                    continue
                # A real gap must be consistent with the time that actually
                # elapsed at the schedule rate. A jump far larger than elapsed
                # time allows is a resync or a reboot, NOT 2685 lost sweeps --
                # counting it as loss is how a healthy board reads as 38%.
                allowed = (b[0] - a[0]) * TDMA_CEILING_HZ * GAP_TOL + 2
                if d - 1 > allowed:
                    disc += 1
                    continue
                gaps += 1
                lost += d - 1
            expect = lost + len(pts)
            rec["uwb"]["sweep_gaps"] = gaps
            rec["uwb"]["sweeps_lost"] = lost
            rec["uwb"]["discontinuities"] = disc
            rec["uwb"]["delivered_of_expected"] = f"{len(pts)}/{expect}"
            rec["uwb"]["delivered_pct"] = round(100.0 * len(pts) / expect, 4) if expect else None

        # --- IMU ladder ---------------------------------------------------
        if i:
            pts, dropped = split_stale(i)
            samples = sum(x[2] for x in pts)
            r2, kept2, span2 = rate(i)
            rec["imu"] = {
                "records": len(i), "kept_after_stale_split": kept2,
                "samples_delivered": samples,
                "span_s": round(span2, 3) if span2 else None,
                "record_rate_hz": round(r2, 4) if r2 else None,
                "stale_prefix_records_dropped": dropped,
            }
            gaps, lost = 0, 0
            disc = 0
            for a, b in zip(pts, pts[1:]):
                mod_gap = (b[1] - a[1]) % IMU_SEQ_MOD
                elapsed = b[0] - a[0]
                # unwrap the 16-bit sequence against elapsed time
                k = round((elapsed * IMU_HZ - mod_gap) / IMU_SEQ_MOD)
                real_gap = mod_gap + IMU_SEQ_MOD * k
                expect_step = a[2]          # n samples in the previous record
                if real_gap <= expect_step:
                    continue
                # Same discipline as UWB: a jump the elapsed time cannot
                # account for is a sequence RESET (the board rebooted and
                # restarted at 0), not loss. BSFAA61 rebooted twice during
                # Stage 2; scoring its restart as loss read as 75% missing.
                allowed = elapsed * IMU_HZ * GAP_TOL + expect_step + IMU_HZ
                if real_gap > allowed:
                    disc += 1
                    continue
                gaps += 1
                lost += real_gap - expect_step
            expect = samples + lost
            rec["imu"]["discontinuities"] = disc
            rec["imu"]["seq_gaps"] = gaps
            rec["imu"]["samples_lost"] = lost
            rec["imu"]["delivered_of_expected"] = f"{samples}/{expect}"
            rec["imu"]["delivered_pct"] = round(100.0 * samples / expect, 4) if expect else None
            rec["imu"]["no_sequence_gap"] = (gaps == 0)
            rec["imu"]["freshness"] = "INSUFFICIENT (static bench)"

        out["nodes"][n] = rec

    # --- hourly ladder ---------------------------------------------------
    if hours:
        hourly = {}
        for n in nodes:
            per = defaultdict(lambda: {"uwb": 0, "ge8": 0, "imu_samples": 0})
            for t, _s, v in uwb[n]:
                h = int((t - t0) // 3600)
                per[h]["uwb"] += 1
                if popcount(v) == 8:
                    per[h]["ge8"] += 1
            for t, _q, nn in imu[n]:
                h = int((t - t0) // 3600)
                per[h]["imu_samples"] += nn
            hourly[n] = {str(h): dict(v, ge8_of_uwb=f"{v['ge8']}/{v['uwb']}")
                         for h, v in sorted(per.items())}
        out["hourly"] = hourly
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--hours", action="store_true", help="also emit the hourly ladder")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    paths = sorted(Path(a.run_dir).glob("fusion_h*.log"))
    if not paths:
        sys.exit(f"no fusion_h*.log under {a.run_dir}")
    res = analyse(paths, a.hours)

    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    print(f"archives: {len(paths)}   nodes: {len(res['nodes'])}")
    print(f"{'node':<9} {'uwb':>7} {'rate_hz':>8} {'8/8':>16} {'7+/8':>16} "
          f"{'uwb_deliv':>14} {'imu_samp':>9} {'imu_deliv':>16}")
    for n, r in sorted(res["nodes"].items()):
        u = r.get("uwb", {})
        i = r.get("imu", {})
        flag = " !OVER-CEILING" if u.get("rate_over_ceiling") else ""
        print(f"{n:<9} {u.get('delivered', 0):>7} {str(u.get('rate_hz')):>8} "
              f"{u.get('ge8', '-'):>16} {u.get('ge7', '-'):>16} "
              f"{u.get('delivered_of_expected', '-'):>14} "
              f"{i.get('samples_delivered', 0):>9} "
              f"{i.get('delivered_of_expected', '-'):>16}{flag}")
    print("\nfreshness: INSUFFICIENT (static bench) -- labelled, not numbered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
