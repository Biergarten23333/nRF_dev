#!/usr/bin/env python3
"""STEP 0 — parse + index the 6 listener logs.

Field order is taken verbatim from the firmware format strings in
SS-TWR/alt-SS-TWR/broadcast/UWB_listener/src/main.c (grepped, not guessed):

  LPD:   LPD;1;lid;near;now_ms;seq_count;seq;peer_id;src;dst;rx_ts;carrier;
         fp_index;fp1;fp2;fp3;cir;rxpacc;stdnoise;frame_len;mask;
         rcph=..;rxtofs=..;ttcki=..;agc=..            (tags only, has mask)
  LRD:   same as LPD minus the `mask` field           (anchors only)
  LCIRM: LCIRM;1;lid;near;accepted_polls;seq;tag_id;mask;resp_rx_ts;carrier;
         fp_index;fp1;fp2;fp3;maxGrowthCIR;rxpacc;acc_len(4064)
  LCIRD: LCIRD;1;accepted_polls;offset;len;<hex>      (48B/line, 85 lines/frame)
  LCIRE: LCIRE;1;accepted_polls;acc_len
  LSTAT: LSTAT;1;lid;near;good_frames;accepted_polls;ignored_nonpoll;
         ignored_poll_mask;bad_header;too_long;rx_errors;full_cir_captures;
         last_status;last_src;last_dst;last_code;ring_drops;self_recover;
         rx_enable_failures;fps;evc_fcg=..;evc_fce=..;evc_ovr=..;evc_sto=..

KEY REALITIES (verified against source + data, differ from the analysis brief):
- CIR (LCIRM/LCIRD/LCIRE) exists ONLY for tag polls (main.c:552 `if(is_poll)`).
  Anchors (LRD) have scalar diagnostics only, no waveform.
- LCIRD is chunked 48B/line; a full 4064B CIR = 85 lines, reassembled by
  (accepted_polls, offset). NOT one line per frame.
- The listener is passive: there is NO range field anywhere.
- CIR->TX: LCIRM carries tag_id directly; now_ms/src/rcph/rxtofs/ttcki for a
  CIR frame come from the LPD line with seq_count == LCIRM.accepted_polls
  (joined post-parse, since that LPD may print AFTER the CIR block).

Output (dependency-free; pyarrow unavailable on this box):
  parsed/{L}_scalar.npz   column arrays for LPD+LRD rows
  parsed/{L}_cir.npy      complex64 (n_frames, 1016)
  parsed/{L}_cir_index.npz  per-frame metadata incl. joined now_ms/src/...
  parsed/{L}_lstat.npz    LSTAT rows (for steps 8/9)
  parsed/step0_summary.json
"""
import os, sys, json, gc, time
import numpy as np

RAW_DIR = os.environ.get("BIOSPUR_OVERNIGHT_OUT",
                         os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "raw"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parsed")
os.makedirs(OUT, exist_ok=True)

LISTENERS = ["LB", "LE", "LF", "L9336", "L955A", "LCCF4"]  # LCCF4 last (dirty)
CLEAN = {"LB", "LE", "LF", "L9336", "L955A"}
ACC_LEN = 4064          # bytes per full CIR (1016 complex int16)
NTAP = 1016

# scalar column order (all numeric; src/dst/mask parsed from hex to int)
SCAL_COLS = ["kind", "now_ms", "seq_count", "seq", "peer_id", "src", "dst",
             "rx_ts", "carrier", "fp_index", "fp1", "fp2", "fp3", "cir",
             "rxpacc", "stdnoise", "frame_len", "mask", "rcph", "rxtofs",
             "ttcki", "agc"]
NO_MASK = 0xFFFF        # sentinel for LRD (no mask field)


def _kv(tok):
    # "rcph=123" -> 123 ; tolerant of trailing junk
    try:
        return int(tok.split("=", 1)[1])
    except Exception:
        return None


def _hx(s):
    return int(s, 16)


def parse_scalar_line(p, is_lpd):
    """p = line.split(';'). Returns list matching SCAL_COLS, or None if malformed."""
    try:
        if is_lpd:
            # 21 base fields (0..20) + 4 kv
            base = p
            mask = _hx(base[20])
            kvs = base[21:]
            kind = 0
        else:
            mask = NO_MASK
            kvs = p[20:]
            kind = 1
        rcph = rxtofs = ttcki = agc = None
        for t in kvs:
            if t.startswith("rcph="):
                rcph = _kv(t)
            elif t.startswith("rxtofs="):
                rxtofs = _kv(t)
            elif t.startswith("ttcki="):
                ttcki = _kv(t)
            elif t.startswith("agc="):
                agc = _kv(t)
        if None in (rcph, rxtofs, ttcki, agc):
            return None
        return [kind, int(p[4]), int(p[5]), int(p[6]), int(p[7]),
                _hx(p[8]), _hx(p[9]), int(p[10]), int(p[11]), int(p[12]),
                int(p[13]), int(p[14]), int(p[15]), int(p[16]), int(p[17]),
                int(p[18]), int(p[19]), mask, rcph, rxtofs, ttcki, agc]
    except (ValueError, IndexError):
        return None


def valid_scalar(row, prev_now):
    """LCCF4 strict validation: monotone-ish now_ms + plausible field ranges."""
    now = row[1]
    if not (0 <= now < 4_294_967_296):
        return False
    if prev_now is not None:
        d = now - prev_now
        if d < -1000 or d > 60000:      # backward >1s or forward >60s = corruption
            return False
    # physically plausible ranges for the diagnostic fields
    if not (0 <= row[9] <= 0xFFFF):     # fp_index (10.6 fixed)
        return False
    if not (0 <= row[14] <= 0xFFFF):    # rxpacc
        return False
    if not (0 <= row[18] <= 127):       # rcph 7-bit
        return False
    if not (0 <= row[21] <= 0x7FF):     # agc EDG1 11-bit
        return False
    if row[5] not in (0xa100, 0xa101, 0xa102, 0xa103, 0xa104, 0xa105, 0xa106,
                      0xa107, 0xb136, 0xb15a, 0xb1f4):  # src must be a real node
        return False
    return True


def parse_listener(name, path, strict):
    t0 = time.time()
    scal = []                       # list of rows
    cir_frames = []                 # list of complex64[1016]
    cir_idx = []                    # per-frame meta dicts
    lstat = []                      # LSTAT rows
    # current CIR block state
    cur_ap = None
    cur_meta = None
    cur_chunks = {}
    prev_now = None
    n_lines = 0
    n_bad_cir = 0
    counts = {"LPD": 0, "LRD": 0, "LCIRM": 0, "LCIRD": 0, "LCIRE": 0,
              "LSTAT": 0, "dropped_scalar": 0}

    def finalize_frame():
        nonlocal n_bad_cir
        if cur_ap is None or cur_meta is None:
            return
        # assemble
        buf = bytearray(ACC_LEN)
        covered = 0
        ok = True
        for off, hx in cur_chunks.items():
            try:
                b = bytes.fromhex(hx)
            except ValueError:
                ok = False
                break
            end = off + len(b)
            if end > ACC_LEN:
                ok = False
                break
            buf[off:end] = b
            covered += len(b)
        if not ok or covered != ACC_LEN:
            n_bad_cir += 1
            return
        arr = np.frombuffer(bytes(buf), dtype="<i2")   # 2032 int16
        cplx = (arr[0::2].astype(np.float32) + 1j * arr[1::2].astype(np.float32)).astype(np.complex64)
        if cplx.shape[0] != NTAP:
            n_bad_cir += 1
            return
        cir_frames.append(cplx)
        m = cur_meta
        m["frame_row"] = len(cir_frames) - 1
        cir_idx.append(m)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            n_lines += 1
            if not line.startswith("L"):
                continue
            line = line.rstrip("\n").rstrip("\r")
            # fast prefix dispatch
            if line.startswith("LPD;1;"):
                p = line.split(";")
                row = parse_scalar_line(p, True)
                if row is None:
                    counts["dropped_scalar"] += 1
                    continue
                if strict and not valid_scalar(row, prev_now):
                    counts["dropped_scalar"] += 1
                    continue
                prev_now = row[1]
                scal.append(row)
                counts["LPD"] += 1
            elif line.startswith("LRD;1;"):
                p = line.split(";")
                row = parse_scalar_line(p, False)
                if row is None:
                    counts["dropped_scalar"] += 1
                    continue
                if strict and not valid_scalar(row, prev_now):
                    counts["dropped_scalar"] += 1
                    continue
                prev_now = row[1]
                scal.append(row)
                counts["LRD"] += 1
            elif line.startswith("LCIRD;1;"):
                p = line.split(";")
                try:
                    ap = int(p[2]); off = int(p[3]); ln = int(p[4]); hx = p[5]
                except (ValueError, IndexError):
                    continue
                counts["LCIRD"] += 1
                if ap == cur_ap and len(hx) == 2 * ln:
                    cur_chunks[off] = hx
            elif line.startswith("LCIRM;1;"):
                p = line.split(";")
                counts["LCIRM"] += 1
                # close any in-progress (shouldn't happen for clean nodes)
                try:
                    ap = int(p[4])
                    cur_ap = ap
                    cur_chunks = {}
                    cur_meta = {"accepted_polls": ap, "tag_id": int(p[6]),
                                "resp_rx_ts": int(p[8]), "carrier": int(p[9]),
                                "fp_index": int(p[10]), "rxpacc": int(p[15])}
                except (ValueError, IndexError):
                    cur_ap = None; cur_meta = None; cur_chunks = {}
            elif line.startswith("LCIRE;1;"):
                p = line.split(";")
                counts["LCIRE"] += 1
                try:
                    ap = int(p[2])
                except (ValueError, IndexError):
                    ap = None
                if ap == cur_ap:
                    finalize_frame()
                cur_ap = None; cur_meta = None; cur_chunks = {}
            elif line.startswith("LSTAT;1;"):
                p = line.split(";")
                try:
                    # p4..p11 = 8 numeric counters (good_frames..full_cir_captures);
                    # p12 last_status,p13 last_src,p14 last_dst,p15 last_code are HEX (skip);
                    # p16..p19 = ring_drops,self_recover,rx_enable_failures,fps
                    base = [int(p[2]), int(p[3])] + [int(x) for x in p[4:12]]
                    tail = [int(p[16]), int(p[17]), int(p[18]), int(p[19])]
                    ev = {}
                    for t in p[20:]:   # evc_fcg starts at p[20] (fps is p[19])
                        if "=" in t:
                            k, v = t.split("=", 1); ev[k] = int(v)
                    lstat.append(base + tail + [ev.get("evc_fcg", -1), ev.get("evc_fce", -1),
                                               ev.get("evc_ovr", -1), ev.get("evc_sto", -1)])
                    counts["LSTAT"] += 1
                except (ValueError, IndexError):
                    pass

    # ---- save scalar ----
    scal_arr = np.array(scal, dtype=np.int64) if scal else np.zeros((0, len(SCAL_COLS)), np.int64)
    np.savez_compressed(os.path.join(OUT, f"{name}_scalar.npz"),
                        data=scal_arr, cols=np.array(SCAL_COLS))
    # ---- build LPD seq_count -> (now_ms, src, rcph, rxtofs, ttcki) join map ----
    join = {}
    if scal_arr.shape[0]:
        lpd_mask = scal_arr[:, 0] == 0
        for r in scal_arr[lpd_mask]:
            join[int(r[2])] = (int(r[1]), int(r[5]), int(r[18]), int(r[19]), int(r[20]))
    # ---- attach join + save CIR ----
    if cir_frames:
        cir_np = np.stack(cir_frames).astype(np.complex64)
    else:
        cir_np = np.zeros((0, NTAP), np.complex64)
    np.save(os.path.join(OUT, f"{name}_cir.npy"), cir_np)
    # cir index arrays
    n = len(cir_idx)
    idx_ap = np.array([m["accepted_polls"] for m in cir_idx], np.int64)
    idx_tag = np.array([m["tag_id"] for m in cir_idx], np.int64)
    idx_row = np.array([m["frame_row"] for m in cir_idx], np.int64)
    idx_rxts = np.array([m["resp_rx_ts"] for m in cir_idx], np.int64)
    idx_carr = np.array([m["carrier"] for m in cir_idx], np.int64)
    idx_fp = np.array([m["fp_index"] for m in cir_idx], np.int64)
    idx_rxpacc = np.array([m["rxpacc"] for m in cir_idx], np.int64)
    j_now = np.full(n, -1, np.int64); j_src = np.full(n, -1, np.int64)
    j_rcph = np.full(n, -1, np.int64); j_rxtofs = np.full(n, 0, np.int64); j_ttcki = np.full(n, -1, np.int64)
    matched = 0
    for i, m in enumerate(cir_idx):
        v = join.get(m["accepted_polls"])
        if v is not None:
            matched += 1
            j_now[i], j_src[i], j_rcph[i], j_rxtofs[i], j_ttcki[i] = v
    np.savez_compressed(os.path.join(OUT, f"{name}_cir_index.npz"),
                        accepted_polls=idx_ap, tag_id=idx_tag, frame_row=idx_row,
                        resp_rx_ts=idx_rxts, carrier=idx_carr, fp_index=idx_fp,
                        rxpacc=idx_rxpacc, now_ms=j_now, src=j_src, rcph=j_rcph,
                        rxtofs=j_rxtofs, ttcki=j_ttcki)
    # LSTAT cols (18): lid,near,good_frames,accepted_polls,ignored_nonpoll,
    #   ignored_poll_mask,bad_header,too_long,rx_errors,full_cir_captures,
    #   ring_drops,self_recover,rx_enable_failures,fps,evc_fcg,evc_fce,evc_ovr,evc_sto
    np.savez_compressed(os.path.join(OUT, f"{name}_lstat.npz"),
                        data=np.array(lstat, dtype=np.int64) if lstat else np.zeros((0, 18), np.int64))

    # ---- per-src distribution ----
    src_dist = {}
    if scal_arr.shape[0]:
        for s in np.unique(scal_arr[:, 5]):
            src_dist[hex(int(s))] = int((scal_arr[:, 5] == s).sum())
    dt = time.time() - t0
    summary = {"listener": name, "lines": n_lines, "strict": strict,
               "counts": counts, "cir_frames_ok": int(cir_np.shape[0]),
               "cir_frames_bad": n_bad_cir, "cir_join_matched": matched,
               "scalar_rows": int(scal_arr.shape[0]), "src_dist": src_dist,
               "seconds": round(dt, 1)}
    print(f"[Step0] {name}: {n_lines:,} lines -> scalar={scal_arr.shape[0]:,} "
          f"CIR_ok={cir_np.shape[0]:,} CIR_bad={n_bad_cir:,} join={matched:,} "
          f"({dt:.1f}s)", flush=True)
    del scal, scal_arr, cir_frames, cir_np, cir_idx, lstat
    gc.collect()
    return summary


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    summaries = []
    for name in LISTENERS:
        if only and name != only:
            continue
        path = os.path.join(RAW_DIR, f"{name}.log")
        if not os.path.exists(path):
            print(f"[Step0] {name}: MISSING {path}", flush=True)
            continue
        summaries.append(parse_listener(name, path, strict=(name not in CLEAN)))
    # merge summary (append if partial run)
    sp = os.path.join(OUT, "step0_summary.json")
    existing = {}
    if os.path.exists(sp):
        try:
            existing = {s["listener"]: s for s in json.load(open(sp))["listeners"]}
        except Exception:
            existing = {}
    for s in summaries:
        existing[s["listener"]] = s
    with open(sp, "w") as f:
        json.dump({"listeners": [existing[k] for k in existing]}, f, indent=2)
    print(f"[Step0] wrote {sp}", flush=True)


if __name__ == "__main__":
    main()
