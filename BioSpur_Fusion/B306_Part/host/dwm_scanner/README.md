# DWM1001C read-only scanner

This nRF52840 DK diagnostic application performs active BLE observation and
prints named advertisements over RTT. It has no BLE central role and therefore
cannot connect to or modify a peer.

It was introduced to distinguish a silent Task A DWM1001C from a device running
under an unexpected BLE name. Flash it only to DK probe `683234364`, always
with explicit probe selection. Build output belongs under
`B306_Part/builds/dk-dwm-scanner-<version>/`.

The 2026-07-20 v2 scan observed B306 `BSF3C79` and unrelated nearby devices,
but no `BSxxxx`, NUS, `Zephyr`, or DWM-named device attributable to the Fusion
PCB DWM1001C. It never connected to BS9336, BS955A, or BSCCF4.
