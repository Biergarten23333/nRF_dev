# Reset attribution — complete call-site enumeration

## Application sites (all converted to `bsf_reset_now()`)

| # | site | intent |
|---|---|---|
| 1 | `bsf_recovery.c:130` guard cold reset | `RECOVERY_GUARD` |
| 2 | `bsf_v45.c:802` detector post-capture reboot | `V45_DETECTOR` |
| 3 | `main.c` stall-ring forward (from an ISR) | `RING_FWD` |
| 4 | `main.c` BT RX monitor wedge reboot | `BT_MONITOR` |
| 5 | `main.c` v41 stall recovery | `STALL_RECOVERY` |
| 6 | `main.c` `REBOOT` control command | `CMD_REBOOT` |
| 7 | `main.c` MCUboot confirmation timeout rollback | `BOOT_CONFIRM` |

Zero `sys_reboot()` / `NVIC_SystemReset()` calls remain outside
`bsf_reset_now()`, which seals the `.noinit` intent **before** resetting.
Zero such calls exist in any of the nine patched SDK files.

## The named gap

| # | site | status |
|---|---|---|
| 8 | mcumgr os-group reset (DFU) | **declared but NOT stampable** |

`CONFIG_MCUMGR_GRP_OS_RESET_HOOK` is not enabled, so the application receives
no callback before mcumgr resets the device. **A DFU-initiated reset will still
be counted as `UNKNOWN_SREQ`.** Enabling that hook is the fix; it is not done
here. Stated as a prediction so it can be falsified: the OTA in Part 2 should
raise `UNKNOWN_SREQ` by exactly one. More than one means this gap is not the
whole story.

## Mechanism

- raw `RESETREAS` latched at `PRE_KERNEL_1`, read without clearing (main.c
  still owns the clear);
- `SREQ` with no recorded intent → `UNKNOWN_SREQ`, cumulative in `.noinit`;
- witness is magic + CRC protected, validate-or-initialise on boot;
- reported by the new `V45 GUARD` command.

## Contract test, proven in both directions

Run against the tree **before** the call sites were converted:

```
v46r2 reset-intent contract: FAIL
  - 7 reset call site(s) bypass bsf_reset_now(): ['bsf_recovery.c:129',
    'bsf_v45.c:802', 'main.c:757', 'main.c:1392', 'main.c:1561',
    'main.c:2663', 'main.c:2677']
```

Then after conversion: `PASS`. A contract that has only ever passed is not a
gate.
