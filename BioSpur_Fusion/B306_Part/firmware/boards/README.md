# Board definitions

`biospur_fusion_nrf52840/` is the Fusion PCB definition for the
NINA-B306-01B. Build it as `biospur_fusion_nrf52840/nrf52840`.

The module pad numbers are not arithmetic nRF GPIO numbers. The board definition
uses the u-blox NINA-B3 pad lookup table and the Fusion PCB schematic:

| Net | NINA pad | nRF52840 | Direction |
|---|---:|---:|---|
| `UWB_RX1` | GPIO_35 | P1.01 | B306 RX from DWM1001C UART_TX |
| `UWB_TX1` | GPIO_36 | P1.02 | B306 TX to DWM1001C UART_RX |
| `UWB_RDY` | GPIO_37 | P1.03 | B306 strobe input |
| JY61P SDA | GPIO_42 | P0.26 | B306 I2C |
| JY61P SCL | GPIO_44 | P0.27 | B306 I2C |
| `BUTTON_1` | GPIO_32 | P0.11 | active-low input |

The status LED is NINA GPIO_1 / nRF P0.13 and is active low. UART1 and I2C0
carry their physical pin control but are disabled in the first DFU image.

NINA-B306-01B has no 32.768 kHz crystal. The board therefore selects the
500 ppm RC LF source and enables periodic calibration. The NCS v2.8 Kconfig
name for that calibration is
`CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y`.
