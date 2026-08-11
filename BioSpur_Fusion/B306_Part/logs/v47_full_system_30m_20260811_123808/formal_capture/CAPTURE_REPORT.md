# v47 30-minute full-system capture

Result: **SMOKE_BLOCKED** / **FAIL**. The single formal run started at 2026-08-11T12:40:13.214+02:00 and stopped after 121.643535 s. One raw COBS/CRC frame error and an approximately 61-second host-delivery gap after T0 were observed; 553 decoded consumer records also remained pending at close. All queue *drop* counters were zero and raw submitted/written bytes closed exactly, but corruption, a formal gap, or non-empty final queue forbids a lossless verdict. Per the prompt, collectors stopped after the 120-second smoke and no second run was started.

All ten nodes were present and subsequently delivered approximately 200 Hz IMU and 8.33 Hz Fusion UWB. Eight-slot UWB and strict IMU tuples were present, but the gap makes the partial dataset not replay-ready. Dynamic truth is `NOT_APPLICABLE_NO_TRUTH`. No prohibited hardware action or active formal diagnostic command occurred.

Sizes: raw 7379941 B; decoded text 21937405 B; Listener evidence 163801550 B.
