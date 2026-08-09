# Part 0 — BSF6C53 captured before the OTA. 2026-08-09

## V45 STATUS, full (read from the raw CDC log, not the reply)

```
V45 present=1 seq=1 cause=3 len=29752 pages=136 core=1020 ch=4 ring=510 flash=0
    armed=0 blind_ms=6163269 blind_ticks=6160 blind_discards=0
    dog=0 dog_dwell=0 dog_age_ms=0 dog_tick_ms=0 rcv=1 rcv_cause=
STATUS fw=b306-imu-relay-v45 id=6C53 up_ms=6166958 frames=51379 imu=0/200Hz/N10 verify=PASS
STALL e=165454 x=165452 rc=0 rcc=0/0/0/0 alarm=0/0
RING boot=3 init=retained count=510/510 frozen=1 reason=1 fidx=510 fms=100784
CORPSE present=0 seq=0 len=840 rr=00000000 reboot_owner=3
```

**The status reply is TRUNCATED BY THE FIRMWARE at 200 characters**, mid-token
at `rcv_cause=`. Every guard field after that point was unreadable, and v46r2
adds four more. Fixed in this round by splitting the guard fields into a new
`V45 GUARD` command; `V45 STATUS` is trimmed to fit. A status line that
silently loses its tail is an instrument that lies by omission.

## Corpse — collected, decoded, ACKed

`BSF6C53_v45corpse_seq1_20260809T212104Z.bin`, 29 752 B / 136 pages,
crc32 `f74240c4`. Decoded clean:

| field | value |
|---|---|
| cause | `BOTH_FROZEN` |
| trigger | #1 this power cycle, `reboot_taken=1 owner=3` |
| uptime at capture | 285.757 s, epoch 1, `reset_reason=0x00000004` (SREQ) |
| notify_exit age | 12 007 ms |
| ncp_packet age | 12 007 ms |
| link | connected=1 data_sub=1 tele_sub=1 ota=0 |
| verdict | notify ENTER > EXIT, everything upstream healthy — blockage in the app/ATT/TX path |

**`MPSL_RX enter=33696 exit=33696 (delta 0)`.** MPSL Work entered and exited in
equal numbers: it never parked on a pool. That is consistent with `V45 LEAK`
starving the singleton `sync_evt_pool` rather than `hci_rx_pool`, and it is
independent evidence that this injection does **not** exercise the B1 path.

The 12 007 ms ages confirm `BSF_V45_FREEZE_MS = 12000` is live and working.

After ACK: `present=0 armed=1 blind_ms=0` — detector re-armed. The board had
been blind for 102 minutes on an uncollected corpse.

## Guard and reset counters

`rcv=1`, cause 1 (`NOTIFY_FROZEN`) — the guard has fired once, during the
earlier C2 re-run, and that reset is accounted for.

**The unattributed `rr=4` cannot be re-examined from the board.** It happened
two boots ago; `RING boot=3` and the retained regions carry only the current
and immediately preceding boot. Whatever the board knew about it is already
gone. That is precisely why §1.3's `reset_intent` exists, and why it had to be
built rather than investigated.
