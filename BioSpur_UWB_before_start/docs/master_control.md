# External Master Plan

This project now includes a dedicated external-master app intended for the
`nrf54l15dk/nrf54l15/cpuapp` board.

## Current role split

- `apps/master/`: external control-plane entry point running on the nRF54L15 DK.
- `apps/anchor/`: UWB execution nodes running on DWM1001 anchors.
- `apps/tag/`: UWB ranging tag.

## Intended first wiring

- `nRF54L15 DK` to PC: USB / serial console.
- `nRF54L15 DK` to `Anchor E`: UART link.
- `Anchor E`: first UWB master anchor.

The current master app already builds command frames, but it does not yet send
them over UART. That transport link is the next integration step.

## Current shell commands

After flashing the master app to the nRF54L15 DK:

- `uwb ping`
- `uwb stop`
- `uwb single_range 4`
- `uwb sweep 4 5 6 7`
- `uwb autopos 4 5 6 7`

Each command prints the exact control frame that should later be transmitted to
Anchor E.

## Next step

Add a UART transport between:

- `apps/master/` on the nRF54L15 DK
- `apps/anchor/` on Anchor E

Then Anchor E can translate those control commands into UWB actions such as:

- start anchor sweep
- stop sweep
- request single-anchor measurement
- start auto-positioning matrix collection
