#!/usr/bin/env python3
"""Overnight interleaved TX-power sweep — fully autonomous, wand fixed, DIAG OFF.

Per cell: set TXPWR (persists across master reset) -> settle -> 180s capture ->
extract ge7/ge8/valid%/per-anchor range + DW1000 temp (;T trailer) -> embed the
power label INSIDE the data. Interleaved rounds [MAX,M3,M6,M12,POR] until target.
No firmware change; only TXPWR runtime commands. EVC not runtime-readable -> miss
derived from TR per-anchor status. Robust: a bad cell never aborts the run;
restores MAX in finally.
"""
import serial, time, re, json, os, sys, subprocess, glob, statistics, signal

BCAST = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast"
TPORT = "/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00"
MASTER_TAG_SNR = "1050070698"
TARGETS = "BS9336,BS955A,BSCCF4"
ORDER = ["MAX", "M3", "M6", "M12", "POR"]
PRESET_HEX = {"MAX": "0x25456585", "M3": "0x05254565", "M6": "0x00052545",
              "M12": "0x00000005", "POR": "0x0E080222"}
DWELL = int(os.environ.get("OVN_DWELL", "180"))    # 3 min capture per cell
SETTLE = int(os.environ.get("OVN_SETTLE", "10"))   # s stabilization after TXPWR
TARGET_SECONDS = int(float(os.environ.get("OVN_HOURS", "9.5")) * 3600)
OUT_ROOT = os.environ.get("OVN_OUT", os.path.join(BCAST, "logs", "overnight_power_20260714"))

os.makedirs(OUT_ROOT, exist_ok=True)
RUN_LOG = os.path.join(OUT_ROOT, "run_log.txt")

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(RUN_LOG, "a") as f:
        f.write(line + "\n")

def set_txpwr(preset, tries=4):
    """Send cmd_all TXPWR <preset>, return dict of acks. Never raises."""
    acks = {}
    for _ in range(tries):
        try:
            s = serial.Serial(TPORT, 115200, timeout=0.3, write_timeout=2); time.sleep(0.3)
            s.reset_input_buffer(); s.write(f"cmd_all TXPWR {preset}\n".encode()); s.flush()
            t = time.time(); buf = ""
            while time.time() - t < 4:
                buf += s.read(4096).decode(errors="replace")
            for m in re.finditer(r"(BS[0-9A-F]{4}) notify: TXPWR_OK VAL=(0x[0-9A-Fa-f]+)", buf):
                acks[m.group(1)] = m.group(2)
            s.close()
        except Exception as e:
            time.sleep(1.0)
        if len(acks) >= 3:
            break
        time.sleep(1.0)
    return acks

def check_links():
    """Return sorted list of linked tags (read-only)."""
    try:
        s = serial.Serial(TPORT, 115200, timeout=0.3, write_timeout=2); time.sleep(0.3)
        s.reset_input_buffer(); s.write(b"conn\n"); s.flush()
        t = time.time(); buf = ""
        while time.time() - t < 3:
            buf += s.read(4096).decode(errors="replace")
        s.close()
        return sorted(set(re.findall(r"(BS[0-9A-F]{4}) notify: TR;", buf)) |
                      set(re.findall(r"MSTAT peer=\d+ name=(BS[0-9A-F]{4}) conn=1 ready=1", buf)))
    except Exception:
        return []

def run_capture(cell_dir):
    """Run the proven capture; return session dir (with data) or None."""
    cmd = ["python3", "-u", "scripts/run_recv_tdma_capture.py", "--port", TPORT,
           "--controller-reset-snr", MASTER_TAG_SNR, "--skip-anchor-preflight",
           "--targets", TARGETS, "--tr-hz", "10", "--tdma-profile", "motion",
           "--duration", str(DWELL), "--out-dir", cell_dir]
    logf = open(os.path.join(cell_dir, "_caplog.txt"), "w")
    try:
        p = subprocess.Popen(cmd, cwd=BCAST, stdout=logf, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        p.wait(timeout=DWELL + 150)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
    except Exception as e:
        log(f"  capture exception: {e}")
    finally:
        logf.close()
    # find the session dir with actual data. run_recv_tdma_capture appends a
    # _YYYYMMDD_HHMMSS suffix to --out-dir, creating a SIBLING dir (not a child).
    cands = sorted(glob.glob(cell_dir + "*/raw.log") +
                   glob.glob(os.path.join(cell_dir, "**", "raw.log"), recursive=True))
    return os.path.dirname(cands[-1]) if cands else None

def parse_cell(session_dir, preset, hexv, rnd):
    """Extract metrics from a cell's data; embed power label in a header + labeled TR."""
    out = {"preset": preset, "txpwr_hex": hexv, "round": rnd,
           "epoch": time.time(), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    raw = os.path.join(session_dir, "raw.log")
    temps, tr_rows, per_anchor_valid, per_anchor_total = [], 0, [0]*8, [0]*8
    ge7 = ge8 = ntot = 0
    tags_seen = set()
    per_link_ranges = {}  # (tag,anchor) -> [range_mm]
    labeled = os.path.join(session_dir, "tr_labeled.csv")
    lab = open(labeled, "w")
    lab.write(f"# TXPWR={hexv} PRESET={preset} ROUND={rnd} TS={out['ts']}\n")
    lab.write("txpwr_hex,preset,round,tag,sweep,valid_mask,nvalid,ranges_mm,temp_raw\n")
    try:
        with open(raw, errors="replace") as fh:
            for ln in fh:
                m = re.search(r"(BS[0-9A-F]{4}) notify: (TR;.*)", ln)
                if not m:
                    continue
                tag, tr = m.group(1), m.group(2).strip()
                tags_seen.add(tag)
                parts = tr.split(";")
                if len(parts) < 10:
                    continue
                try:
                    sweep = parts[2]
                    valid_mask = int(parts[6], 16)
                    ranges = parts[8].split(",")
                except Exception:
                    continue
                nvalid = bin(valid_mask).count("1")
                ntot += 1; tr_rows += 1
                if nvalid >= 7: ge7 += 1
                if nvalid >= 8: ge8 += 1
                temp_raw = ""
                tm = re.search(r";T,(\d+),(\d+)", tr)
                if tm:
                    temp_raw = tm.group(1); temps.append(int(tm.group(1)))
                for a in range(8):
                    bit = (valid_mask >> a) & 1
                    per_anchor_total[a] += 1
                    if bit:
                        per_anchor_valid[a] += 1
                        if a < len(ranges):
                            try:
                                r = float(ranges[a])
                                if r > 0:
                                    per_link_ranges.setdefault((tag, a), []).append(r)
                            except Exception:
                                pass
                lab.write(f"{hexv},{preset},{rnd},{tag},{sweep},0x{valid_mask:02x},{nvalid},{parts[8]},{temp_raw}\n")
    except Exception as e:
        log(f"  parse exception: {e}")
    lab.close()
    out["tags_present"] = sorted(tags_seen)
    out["n_tr_rows"] = tr_rows
    out["ratio_ge7"] = round(ge7 / ntot, 4) if ntot else 0.0
    out["ratio_ge8"] = round(ge8 / ntot, 4) if ntot else 0.0
    out["per_anchor_valid_pct"] = {str(a): (round(100*per_anchor_valid[a]/per_anchor_total[a]) if per_anchor_total[a] else 0) for a in range(8)}
    out["valid_pct_overall"] = round(100 * sum(per_anchor_valid) / sum(per_anchor_total), 1) if sum(per_anchor_total) else 0.0
    if temps:
        out["temp_raw_start"] = temps[0]; out["temp_raw_end"] = temps[-1]
        out["temp_raw_mean"] = round(statistics.mean(temps), 1)
    out["per_link"] = {f"{t}_a{a}": {"mean_mm": round(statistics.mean(v), 1),
                                     "std_mm": round(statistics.pstdev(v), 1) if len(v) > 1 else 0.0,
                                     "n": len(v)}
                       for (t, a), v in per_link_ranges.items()}
    with open(os.path.join(session_dir, "cell_meta.json"), "w") as f:
        json.dump(out, f, indent=1)
    return out

def main():
    log(f"=== OVERNIGHT POWER SWEEP START target={TARGET_SECONDS}s order={ORDER} dwell={DWELL}s ===")
    start = time.time()
    rnd = 0
    consec_empty = 0
    try:
        while time.time() - start < TARGET_SECONDS:
            rnd += 1
            round_dir = os.path.join(OUT_ROOT, f"round_{rnd:02d}")
            os.makedirs(round_dir, exist_ok=True)
            for preset in ORDER:
                if time.time() - start >= TARGET_SECONDS:
                    break
                hexv = PRESET_HEX[preset]
                cell_dir = os.path.join(round_dir, f"{preset}_3min")
                os.makedirs(cell_dir, exist_ok=True)
                acks = set_txpwr(preset)
                time.sleep(SETTLE)
                sess = run_capture(cell_dir)
                if sess is None:
                    consec_empty += 1
                    log(f"round {rnd:02d} {preset:>3} NO DATA (acks={len(acks)}) consec_empty={consec_empty}")
                    if consec_empty >= 2:
                        links = check_links()
                        log(f"  ranging may be down; links={links}")
                        if len(links) == 0:
                            log("  0 tags linked; one reconnect attempt via next capture reset")
                            if consec_empty >= 4:
                                log("  STOPPING: ranging failed 4 consecutive cells, preserving data")
                                return
                    continue
                consec_empty = 0
                m = parse_cell(sess, preset, hexv, rnd)
                log(f"round {rnd:02d} {preset:>3} hex={hexv} ge7={m['ratio_ge7']} ge8={m['ratio_ge8']} "
                    f"valid%={m['valid_pct_overall']} tags={len(m['tags_present'])} "
                    f"temp_raw={m.get('temp_raw_mean','?')} rows={m['n_tr_rows']}")
        log(f"=== TARGET REACHED: {rnd} rounds in {(time.time()-start)/3600:.2f}h ===")
    except Exception as e:
        log(f"FATAL loop exception: {e}")
    finally:
        r = set_txpwr("MAX")
        log(f"=== RESTORED TXPWR MAX acks={len(r)} ; DONE rounds={rnd} ===")

if __name__ == "__main__":
    main()
