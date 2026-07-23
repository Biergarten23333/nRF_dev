# Development tools

This directory is reserved for small, reproducible helpers local to the B306
workspace: build wrappers, pin/cadence validation, RTT capture, UART contract
tests, and provenance checks.

Tools must write runtime output under `../logs/<name>_YYYYMMDD_HHMMSS/`, accept
explicit device identities, and avoid hard-coded `/dev/ttyACM*` numbers. Hardware
mutation tools must verify the target before flashing or erasing it.

`build_firmware.sh [build-name]` is the canonical pristine B306 build entry
point. It centralizes output under `B306_Part/builds/`, isolates the NCS Python
environment, and fails unless FLASH <=95%, RAM <=85%, and malloc-arena sizing
is explicit.

`analyze_dsview_logic.py` streams a one-channel DSView `.dsl` archive and
decodes its LSB-first packed samples into pulse CSV and summary files. Its edge,
width, interval, and loss results reproduce the accepted 2026-07-20 30-minute
DSView report exactly.

`analyze_strobe_attribution.py` uniquely aligns the DSView pulse cadence with
Fusion Master protocol-v2 RTT records, extracts a fixed-duration window, emits
an auditable joined CSV, and reports all four `STROBE_SENT`/edge cases plus
orphan, pairing-window, sweep-gap, and logger counters.

`analyze_clock_ratio.py` compares consecutive raw 40-bit DW1000 poll-TX
timestamps against B306 strobe-capture timestamps. It handles the roughly
17.2-second DW1000 wrap, reports interval and aggregate ratios in ppm, and can
read either an aligned CSV or raw Fusion Master RTT output.

`analyze_sweep_loss.py` measures a fixed-duration B306 RTT window and an
independent same-duration DSView window, then joins those loss counts with the
capture-mode `BSLSTAT` pre/post counter deltas. It discards short stale RTT
prefixes by selecting the longest contiguous strobe segment.
