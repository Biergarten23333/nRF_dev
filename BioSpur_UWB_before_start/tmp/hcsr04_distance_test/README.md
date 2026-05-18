# HC-SR04 Distance Test on DWM1001-DEV

Minimal Zephyr app for checking an HC-SR04 ultrasonic module on one
DWM1001-DEV board. It prints distance records on the serial console.

## Wiring

Use the DWM1001-DEV J10 header:

| HC-SR04 | DWM1001-DEV | nRF52832 |
| --- | --- | --- |
| VCC | stable 5V rail after the Schottky protection path | - |
| GND | GND | - |
| TRIG | J10 pin 19, `SPI1_MOSI` | `P0.06` |
| ECHO | J10 pin 21, `SPI1_MISO`, through divider | `P0.07` |

The standard HC-SR04 ECHO pin is 5V. Do not connect it directly to the
DWM1001 GPIO. Use a divider:

```text
HC-SR04 ECHO ---- 10kΩ ----+---- J10 pin 21 / P0.07
                           |
                          20kΩ
                           |
                          GND
```

## Build, Flash, Monitor

From the repository root:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
chmod +x tmp/hcsr04_distance_test/build_flash_monitor.sh
tmp/hcsr04_distance_test/build_flash_monitor.sh 760184500
```

Then open the serial port for the flashed DWM1001-DEV:

```bash
ls -l /dev/serial/by-id/
picocom -b 115200 /dev/serial/by-id/<PORT_FOR_SNR_760184500>
```

Expected output:

```text
HCSR04;BOOT;trig=P0.06;echo=P0.07;period_ms=500
DIST;0;echo_us=583;distance_mm=100
DIST;1;echo_us=590;distance_mm=101
```

If no echo is received:

```text
DIST;0;ERR;timeout_or_gpio;-116
```

Then check HC-SR04 power, common GND, TRIG/ECHO wiring, and the ECHO divider.
