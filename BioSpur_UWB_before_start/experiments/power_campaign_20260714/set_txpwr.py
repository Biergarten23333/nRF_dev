#!/usr/bin/env python3
"""Set TX_POWER preset on all 3 wand tags via the Master_Tag CDC (cmd_all relay).
Robust: retries port-open and command until >=3 tags ack (or gives up cleanly).
Prints a JSON line {preset,val,epoch,acks,n_ack}. Never raises to the caller."""
import serial, time, re, json, sys

PORT = "/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00"
VAL = {"MAX": "0x25456585", "M3": "0x05254565", "M6": "0x00052545",
       "M12": "0x00000005", "POR": "0x0E080222"}

def open_port(attempts=10):
    for _ in range(attempts):
        try:
            s = serial.Serial(PORT, 115200, timeout=0.2, write_timeout=2)
            time.sleep(0.3)
            return s
        except Exception:
            time.sleep(1.5)
    return None

def main():
    preset = sys.argv[1].upper()
    if preset not in VAL:
        print(json.dumps({"error": f"bad preset {preset}"})); return 2
    acks = {}
    epoch = time.time()
    for attempt in range(5):
        ser = open_port()
        if ser is None:
            time.sleep(2); continue
        try:
            ser.reset_input_buffer()
            ser.write(f"cmd_all TXPWR {preset}\n".encode()); ser.flush()
            epoch = time.time()
            t = time.time()
            while time.time() - t < 5:
                try:
                    ln = ser.readline().decode(errors="replace").strip()
                except Exception:
                    break
                m = re.search(r"\[RECV\]\s+(BS\w+)\s+notify:\s*TXPWR_OK VAL=(0x[0-9A-Fa-f]+)", ln)
                if m:
                    acks[m.group(1)] = m.group(2)
        except Exception:
            pass
        finally:
            try: ser.close()
            except Exception: pass
        if len(acks) >= 3:
            break
        time.sleep(2)
    print(json.dumps({"preset": preset, "val": VAL[preset], "epoch": epoch,
                      "acks": acks, "n_ack": len(acks)}))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(json.dumps({"error": str(e)})); sys.exit(0)
