# Fusion tag command transport

The `tag-fusion-link-v2-relay1` command surface is the existing tag command
surface with two input transports:

- Path M: Nordic UART Service (BLE), unchanged from `v2-clean1`;
- Path R: the framed B306 UART downlink defined in `src/include/biospur_link.h`.

The parser receives `(line, source, reply_sink)`. A BLE-origin command replies
only through the existing NUS TX queue. A UART-origin command replies only
through a type-2 relay ACK frame carrying the command's original correlation.
The UART ISR only moves EasyDMA RX bytes into a ring; frame recognition, CRC,
command parsing, settings writes, and ACK construction run in threads below the
ranging priorities.

Path R adds no command words. It exposes the same commands already accepted on
Path M, including:

```text
PING
STATUS
BSL_STATUS
VERSION
TDMA_STATUS
CFG_STATUS
CFG TAG=<id> SLOT=<slot> COUNT=<count> [MASK=<hex>]
    PERIOD=<ms> ACTIVE=<ms> [ACTIVE_US=<us>] EPOCH=<ms>
    [GEN=<n>] [RUN=<0|1>] [PMODE=<0|3>]
CFG_RUN
CFG_STOP
TR?
TR ON|OFF
CAPTURE?
CAPTURE PARAM <interval_units> <timeout_units>
CAPTURE ON|OFF
MODE?
MODE RUN|IDLE
CIR?
CIR OFF|COMPACT|FULL
TXPWR MAX|M3|M6|M12|POR
DIAG ON|OFF
OTA_STATUS
OTA_PREPARE
OTA_BEGIN
OTA_CANCEL
REBOOT
HELP
```

`CFG_OK ... LIVE=1` means that the configuration was accepted/applied. It is
not proof of RF transmission; prove transmission from rising strobe and valid
UART data-frame counters.

APOS commands are deliberately absent in the Fusion fork: anchor layout is
host-owned. The frozen UWB fork retains APOS and remains the reference for
`push_apos_layout_verified.py`.

## Relay frame

Both command and ACK are:

```text
C3 6D | version=1 | type:u8 | len:u16 | correlation:u16
payload[len] | crc16_ccitt_false:u16
```

Type 1 is B306-to-tag command; type 2 is tag-to-B306 ACK. Payload is non-NUL
ASCII, 1–191 bytes. CRC covers the packed 8-byte header and payload. This magic
and variable length cannot be confused with the fixed `B5 9C`, 96-byte UWB
data frame.

The relay worker waits for a ranging-data TX completion and uses the quiet
post-frame window. A 250 ms bounded fallback serves commands when ranging is
stopped; it never parses or constructs frames in the ranging hot path. A Path
R `REBOOT` waits 1.2 s after queuing `REBOOTING`, while Path M retains its
existing 150 ms delay. Replies longer than 191 bytes become the explicit
`ERR:REPLY_TOO_LONG` ACK; the full command list is maintained in this file.
