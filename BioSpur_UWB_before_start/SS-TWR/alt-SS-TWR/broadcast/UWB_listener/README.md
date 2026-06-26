# UWB Listener

Universal co-located passive listener firmware.

This app is for DWM1001C boards placed near anchors as RX-only poll diagnostics
or poll-CIR proxy nodes. It must stay generic: one firmware image should work for
Listener A-H, instead of producing one build per physical listener.

Do not hardcode a specific listener letter, anchor letter, J-Link SNR, or USB
path in the firmware. The listener identity and its near-anchor assignment
should come from runtime configuration, host-side inventory, or a small persisted
setting, just like anchors are treated as Anchor X identities rather than unique
firmware forks.

Expected generic runtime fields:

- listener id: `A`-`H` or `unknown`
- near anchor id: `A`-`H` or `unknown`
- J-Link SNR / USB path: host inventory only
- output role: poll diagnostics, sampled poll CIR, or both

The first deployment may use the board currently recorded as Listener E beside
Anchor E, but that is only a lab inventory mapping, not a firmware specialization.

The legacy UF/UL air-monitor firmware was moved to `../UWB_listener_old/` and
remains associated with the historical `760185886` listener. Do not copy the old
air-monitor behavior into this app by default; this firmware should software
filter broadcast poll frames and capture poll-side diagnostics/CIR for the
co-located listener experiment.
