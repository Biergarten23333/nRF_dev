# Canonical v47 observability compatibility audit

This is a field-level audit, not a firmware change. Master v36 treats a
`BSF#### <command>` as an opaque per-peer control write and emits its fixed
control reply as `FUSION_REPLY ... name=<peer> ... text=<raw reply>`. It is
lossless up to the B306 control-reply limit. The known approximately 200-byte
truncation occurs in B306's historical `V45 STATUS` formatting; the dedicated
short `V45 GUARD` was created specifically to avoid that tail.

| Evidence | Internal in v47 | Production external surface | Master v36 | Old formal collector | Raw overnight evidence | Post-run recovery | Classification / truncation |
|---|---|---|---|---|---|---|---|
| GUARD `rcv,cause,frozen_ms,streak,latched` | yes, `bsf_recovery_report` | `V45 GUARD` | opaque/lossless | not requested | no | only by a live later query; not historical | `EXPOSED_BUT_NOT_REQUESTED`; STATUS tail truncation avoided |
| reset `intent,unk_sreq,named_sreq,rr` | yes, retained intent/RESETREAS | `V45 GUARD` | opaque/lossless | not requested | telemetry retained `reset_reason` only | GUARD survives only as firmware-defined retained state | `EXPOSED_BUT_NOT_REQUESTED` |
| watchdog witness `dog,dog_dwell,dog_age_ms,dog_tick_ms` | yes | `V45 STATUS` | transports returned prefix | not requested | no | potentially live query only | `EXPOSED_BUT_NOT_REQUESTED`; STATUS can truncate later fields |
| uptime / boot / connection epoch | uptime and RESETREAS yes; no production boot counter | telemetry `node_ms,reset_reason`; Master connect/disconnect | binary telemetry and peer events lossless | yes | yes | uptime is not historical after power loss | uptime/epoch `AVAILABLE_AND_VALIDATED`; boot counter `FIRMWARE_NOT_EXPOSED` |
| `rx_retained` | yes, SDK-patch atomic | only fault-injection `V45 RXPOOL` reply; command absent in production build | transport capable | not requested | no | no | `FIRMWARE_NOT_EXPOSED` in canonical production v47 |
| HCI RX resubmit attempts / successful retained retry / failed retry | patch behavior exists; no complete exported counters | none | n/a | no | no | no | `FIRMWARE_NOT_EXPOSED` |
| MPSL work enter/exit, `msg_get_ok` | yes, trace/counters | corpse pages only after a captured corpse | page transport supported | not requested | no | only if corpse retained and collected | `EXPOSED_BUT_NOT_REQUESTED` conditionally |
| BT RX work enter/exit | trace channels exist | corpse pages only | page transport supported | not requested | no | conditional corpse | `EXPOSED_BUT_NOT_REQUESTED` conditionally |
| corpse present/sequence/cause/pages | yes | `V45 STATUS` and `CORPSE STATUS`; pages via explicit selection/read | supported | not requested | no | only while retained | `EXPOSED_BUT_NOT_REQUESTED`; presence prefix is before STATUS truncation |
| corpse CRC | yes in image/page formats | page payload/CRC | supported | not requested | no | conditional corpse | `EXPOSED_BUT_NOT_REQUESTED` |
| Fusion UWB/IMU progress | yes | data characteristic | lossless binary host records | yes | yes | raw archive | `AVAILABLE_AND_VALIDATED` |
| independent Listener UWB | DWM path, not B306 | five Listener LPD streams | independent of Master | yes | yes | raw archive | `AVAILABLE_AND_VALIDATED` |

No audited field was `REQUESTED_BUT_NOT_RETAINED` or
`RETAINED_BUT_NOT_PARSED` in the formal run. The main host omission was failure
to request the already-exposed GUARD/watchdog/corpse surfaces. B1 retry outcome
fields remain genuinely unavailable from production v47 and must never be
represented as explicit zero.

The corrected host schema is `biospur-v47-guard-evidence-v1`. Each append-only
record contains phase, host monotonic/wall timestamps, requested/responding
identity, raw full reply, parsed values, missing/malformed distinction, and
T0-relative deltas. Only `V45 GUARD` is allowed. Timeouts and per-node errors
are rows, not fleet aborts. It never issues corpse ACK.
