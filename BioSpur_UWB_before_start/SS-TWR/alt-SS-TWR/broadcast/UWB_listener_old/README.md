# UWB Listener Old

Legacy passive DWM1001-DEV air monitor for the 74HC595 LED bar.

This app is kept for the old listener board, historically SNR `760185886`.
Do not use this path for the co-located Listener E poll-CIR proxy; the new
Listener E firmware lives under `../UWB_listener/`.

This app does not transmit UWB frames. On boot it only runs a 74HC595 LED chase.
After the first active-low button press, it starts the DW1000 receiver, listens
for BioSpur frames on PAN `0xDECA`, filters frames by anchor source address, and
uses the 74HC595 LED bar to show the selected anchor's received signal level.
The first button press starts listening on anchor A; later button presses switch
the selected anchor A-H.

Anchor short addresses are:

| Anchor | Short address |
| --- | --- |
| A | `0xA100` |
| B | `0xA101` |
| C | `0xA102` |
| D | `0xA103` |
| E | `0xA104` |
| F | `0xA105` |
| G | `0xA106` |
| H | `0xA107` |

## Wiring Used

| DWM1001-DEV J10 | nRF GPIO | 74HC595 / Button |
| --- | --- | --- |
| pin 19 `SPI1_MOSI` | `P0.06` | `SER` |
| pin 23 `SPI1_CLK` | `P0.04` normally, `P0.08` also mirrored by this app | `SRCLK` |
| pin 24 `CS_RPI` | `P0.03` | `RCLK` |
| pin 21 `SPI1_MISO` | `P0.07` | `RCLK` is also mirrored here for the current wiring |
| pin 15 `GPIO_RPI/READY` | `P0.26` | button to GND, pull-up, active-low |
| pin 3 `SDA_RPI` | `P0.15` | active buzzer through 220 ohm to GND |

74HC595 `VCC` must be 3.3 V. `OE` goes to GND, `SRCLR` goes to 3.3 V.
On boot the firmware flashes all LEDs on/off three times before entering the
running chase test.

The selected anchor's buzzer output uses Geiger-counter style clicks: stronger
received signal levels produce shorter intervals between clicks. The LED bar is
calibrated for this listener's observed `q` range, with level 8 reached around
`q >= 22000`. Very weak signals blink `QA`; very strong signals blink `QA`-`QH`.

## Build

```sh
west build -p always -b decawave_dwm1001_dev UWB_listener_old -d build-uwb-listener
```

## Flash

```sh
west flash -d build-uwb-listener --dev-id 760185886
```

Or use:

```sh
BIOSPUR_LISTENER_SN=760185886 scripts/flash_uwb_listener_jlink.sh build-uwb-listener/merged.hex
```
