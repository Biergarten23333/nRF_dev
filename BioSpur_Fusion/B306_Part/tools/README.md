# Development tools

This directory is reserved for small, reproducible helpers local to the B306
workspace: build wrappers, pin/cadence validation, RTT capture, UART contract
tests, and provenance checks.

Tools must write runtime output under `../logs/<name>_YYYYMMDD_HHMMSS/`, accept
explicit device identities, and avoid hard-coded `/dev/ttyACM*` numbers. Hardware
mutation tools must verify the target before flashing or erasing it.
