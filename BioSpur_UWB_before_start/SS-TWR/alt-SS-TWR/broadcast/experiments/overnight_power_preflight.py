#!/usr/bin/env python3
"""Pre-flight 0.2 (tags linked 3/3) + 0.3 (TXPWR presets readback). Read-only + TXPWR."""
import serial, time, re, json, sys
PORT = "/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00"
PRESETS = {"MAX": "0x25456585", "M3": "0x05254565", "M6": "0x00052545",
           "M12": "0x00000005", "POR": "0x0E080222"}

def open_port():
    s = serial.Serial(PORT, 115200, timeout=0.3, write_timeout=2); time.sleep(0.4); return s

def drain(s, secs):
    t = time.time(); buf = ""
    while time.time() - t < secs:
        try: buf += s.read(4096).decode(errors="replace")
        except Exception: break
    return buf

# 0.2 tags linked
s = open_port(); s.reset_input_buffer(); s.write(b"conn\n"); s.flush()
data = drain(s, 3.0)
tags_seen = set(re.findall(r"(BS[0-9A-F]{4}) notify: TR;", data))
mstat = set(re.findall(r"MSTAT peer=\d+ name=(BS[0-9A-F]{4}) conn=1 ready=1", data))
linked = sorted(tags_seen | mstat)
print(json.dumps({"preflight_0.2_tags_linked": linked, "n_linked": len(linked)}))

# 0.3 TXPWR readback for each preset via cmd_all
results = {}
for name, hexv in [("MAX", PRESETS["MAX"]), ("M3", PRESETS["M3"]), ("M6", PRESETS["M6"]),
                   ("M12", PRESETS["M12"]), ("POR", PRESETS["POR"])]:
    s.reset_input_buffer(); s.write(f"cmd_all TXPWR {name}\n".encode()); s.flush()
    d = drain(s, 3.0)
    acks = dict(re.findall(r"(BS[0-9A-F]{4}) notify: TXPWR_OK VAL=(0x[0-9A-Fa-f]+)", d))
    ok = sum(1 for v in acks.values() if v.lower() == hexv.lower())
    results[name] = {"expected": hexv, "acks": acks, "n_correct": ok}
    print(json.dumps({name: results[name]}))
# restore MAX
s.reset_input_buffer(); s.write(b"cmd_all TXPWR MAX\n"); s.flush(); drain(s, 3.0)
s.close()
allok = all(r["n_correct"] >= 3 for r in results.values())
print(json.dumps({"preflight_0.3_all_presets_ok": allok, "restored": "MAX"}))
sys.exit(0 if (len(linked) >= 3 and allok) else 1)
