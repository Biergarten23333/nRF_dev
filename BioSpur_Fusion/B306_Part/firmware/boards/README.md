# Board definitions

This directory will hold the NINA-B306/custom-PCB devicetree overlay after the
PCB pin assignment is confirmed.

The nRF52840 DK scaffold currently uses the upstream board definition unchanged.
No custom overlay is supplied because the B306 I2C, UART, and ready-capture pins
are still `UNKNOWN`; inventing them here would turn an unverified wiring guess
into a build-time hardware contract.
