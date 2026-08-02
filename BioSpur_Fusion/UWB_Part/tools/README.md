# UWB host tools

All control-port tools require a stable `/dev/serial/by-id/...` path and open
USB CDC with DTR/RTS inactive. Never pass a transient `/dev/ttyACM<n>` name.

`remote_dwm_reboot_preflight.py` performs an exact-name `VERSION` gate, sends
the tag's existing `REBOOT` command, then polls until the same exact tag returns
with the expected firmware marker. It is a pre-capture operation: never run it
while another process owns the Master Tag control port.

## Timed command sequence

`master_control_sequence.py` has two deliberately different schedule modes:

- `--step WAIT:COMMAND` sends the command and then waits. This is retained for
  short legacy setup sequences.
- `--at ELAPSED:COMMAND` sends at an absolute offset from port-open time. Use
  this for experiment deadlines.

For the one-hour REDO, the final freeze is an absolute 3,660-second event:

```bash
python3 UWB_Part/tools/master_control_sequence.py \
  --port /dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00 \
  --log UWB_Part/logs/<run>/raw/freeze-deadline.log \
  --initial-drain 0 \
  --at '3660:cmd BSL_LATE_FREEZE'
```

The log records requested time, actual send time, and deadline error. Because
the port is exclusive, one owner must schedule all commands needed during a
formal run; do not launch a second serial controller alongside it.

## Strict lateness-histogram read

`read_lateness_histogram.py` correlates each of pages 0–7 with its response,
retries a missing page, brackets every round with summaries, and accepts only
two consecutive complete identical rounds. It exits 0 on that condition, 2 on
a missing response after retries, and 3 when complete rounds keep changing.

The deployed `tag-fusion-link-v2-absdeadline3` image exposes a live cumulative
histogram and has no snapshot/resume command. Therefore strict reads while its
10 Hz schedule is active are expected to exit 3: the hot page changes between
rounds. This is a capability result, not a host retry failure. Do not call a
single complete live round an atomic 10-minute boundary snapshot.
