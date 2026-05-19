# BioSpur Gesture Recognition Implementation Plan

## 1. System Understanding

The target system has two firmware sides:

- **GR module / BLE Peripheral:** u-blox **B306 / nRF52840**
  - Reads **ADS1298** biosignal data.
  - Reads **JY61P** IMU data.
  - Performs capture, timestamping, optional gesture preprocessing/recognition.
  - Streams data over BLE to the Central.
  - Supports **DFU + OTA** so normal firmware updates do not require J-Link.
  - BLE device name format: **`GRXXXX`**.

- **BLE Central / USB bridge:** u-blox **NORA-B120**
  - Uses **internal LF RC oscillator**, no external 32.768 kHz crystal.
  - Connects to the B306 GR module over BLE.
  - Receives sensor/gesture packets.
  - Connects to the computer by **USB CDC ACM**.
  - Forwards captured data, status, logs, and gesture results to the computer over USB CDC.
  - Sends control commands from the computer to the GR module.
  - Provides or assists OTA update flow for the GR module.
  - BLE/device role name: **`GR-Master`**.

Reference projects:

- `../Gesture_Recognition`
  - Old capture method.
  - Useful BLE NUS framing, timestamp sync, packet style, JY61P/WT IMU task, static BLE TX FIFO.

- `../BioSpur_UWB_before_start`
  - OTA/DFU material.
  - Useful `apps/tag_ota` pattern for MCUboot + BLE SMP server.
  - Useful `apps/master_ota` pattern for BLE Central OTA/SMP client.
  - Useful scripts for packaging/deploying OTA images.

## 2. Proposed Workspace Structure

```text
BioSpur_Gesture_Recognition/
  docs/
    IMPLEMENTATION_PLAN.md
  shared/
    include/
      gr_protocol.h
      gr_sensor_frame.h
  gr_module/
    CMakeLists.txt
    prj.conf
    sysbuild.conf
    pm_static.yml
    boards/
      b306_nrf52840.overlay
    src/
      main.c
      ble_peripheral.c
      ads1298_task.c
      jy61p_task.c
      gesture_task.c
      device_id.c
  central_b120/
    CMakeLists.txt
    prj.conf
    boards/
      nora_b120.overlay
      nora_b120.conf
    src/
      main.c
      ble_central.c
      usb_cdc_bridge.c
      gr_packet_parser.c
      ota_client.c
  tools/
    ota/
      build_gr_ota_package.sh
      deploy_gr_ota.py
```

## 3. Communication Architecture

### BLE Device Naming

There may be other UWB systems in the same room advertising names such as `BSXXXX`
and `Anchor-X-BSXXXX`. The gesture recognition system must use a separate naming
scheme so the B120 Central does not accidentally connect to UWB devices.

Gesture device names:

- **B306 GR module:** `GRXXXX`
  - Example: `GR20AC`, `GR9336`, `GRF66F`
  - `XXXX` should be derived from the device identity, for example the lower
    16 bits of the BLE address or a stored device ID.

- **B120 Central / USB bridge:** `GR-Master`
  - This identifies the Central side when logs, USB CDC output, or optional BLE
    identity are shown.

B120 scan filtering rules:

- Accept devices whose name starts with `GR`.
- Prefer/require gesture-specific manufacturer data when available.
- Reject or ignore UWB names such as `BSXXXX` and `Anchor-X-BSXXXX`.

### BLE Link

Use BLE NUS first because the old project already uses it successfully and it is simple for sensor streaming.

GR module advertises as a BLE Peripheral with:

- NUS service for data/control.
- SMP/DFU service for OTA.
- Device identity in name or manufacturer data.

B120 acts as BLE Central:

- Scans for GR module.
- Connects and subscribes to NUS notifications.
- Receives sensor frames.
- Sends control frames.
- Starts OTA flow when commanded from computer.

### USB CDC Link

B120 must expose a USB CDC ACM serial port to the computer.

Computer side can use the USB serial port for:

- Live ADS1298 sample stream.
- Live JY61P IMU stream.
- Recognized gesture events.
- Device status and errors.
- Start/stop capture commands.
- Sensor rate/config commands.
- OTA command trigger and OTA progress reporting.

Recommended CDC protocol:

- Start with readable line protocol for bring-up.
- Move to binary framed protocol once data rate is confirmed.
- Keep packet framing compatible with BLE packet types where practical.

## 4. Packet Plan

Common packet header:

```text
0xAA | type | seq_u16 | device_id_u16 | timestamp_ms_u32 | payload...
```

Packet types:

- `'A'`: ADS1298 sample bundle.
- `'I'`: JY61P IMU sample bundle.
- `'G'`: gesture recognition result.
- `'S'`: status/health.
- `'K'`: app key / identity.
- `'T'`: time sync.
- `'C'`: command/config.
- `'O'`: OTA status/progress.

ADS1298 frame payload should include:

- sample count.
- channel mask.
- sample rate.
- 24-bit channel data, packed compactly.
- optional status bytes from ADS1298.

JY61P frame payload should include:

- sample count.
- accel.
- gyro.
- quaternion or angle data, depending on confirmed JY61P register mode.

## 5. Phase Plan

### Phase 0: Workspace Setup

Goal: create a clean, buildable two-app workspace.

Tasks:

- Add `gr_module`, `central_b120`, `shared`, `docs`, and `tools` folders.
- Add initial CMake and Zephyr config.
- Confirm target board names for B306 and B120.
- Add B120 internal oscillator config.
- Add USB CDC config to B120 app.

Exit criteria:

- Both apps configure with CMake/west.
- No sensor logic yet, but project structure is stable.

### Phase 1: BLE Peripheral + Central Skeleton

Goal: B306 and B120 connect over BLE.

Tasks:

- Port BLE Peripheral/NUS logic from `../Gesture_Recognition`.
- Implement B120 BLE Central scan/connect/NUS client.
- Add simple heartbeat/status packet.
- Add connection LEDs/logs.

Exit criteria:

- B120 connects to B306.
- B120 receives heartbeat packets.
- B120 reports connection state over USB CDC.

### Phase 2: USB CDC Computer Bridge

Goal: computer can see and control the B120.

Tasks:

- Enable USB device stack and CDC ACM on B120.
- Create `usb_cdc_bridge.c`.
- Forward BLE packets to USB CDC.
- Accept simple commands from computer:
  - `scan`
  - `connect`
  - `start`
  - `stop`
  - `status`

Exit criteria:

- Computer sees B120 as serial port.
- BLE status and received frames appear on computer.
- Computer command can start/stop B306 streaming.

### Phase 3: JY61P Capture

Goal: reuse the old IMU capture path.

Tasks:

- Port JY61P/WT IMU task from `../Gesture_Recognition`.
- Confirm I2C pins and address.
- Pack IMU samples as `'I'` frames.
- Forward frames B306 -> BLE -> B120 -> USB CDC -> computer.

Exit criteria:

- Stable IMU stream visible on computer.
- Sequence counter and timestamp are valid.
- Packet loss can be measured.

### Phase 4: ADS1298 Bring-Up

Goal: read ADS1298 reliably on B306.

Tasks:

- Add SPI pin overlay for ADS1298.
- Implement ADS1298 reset/start/config sequence.
- Use `DRDY` interrupt for sample timing.
- Read status + 8 channels.
- Pack ADS1298 data as `'A'` frames.

Exit criteria:

- ADS1298 device ID/register read works.
- Continuous sample stream works.
- Data reaches computer through B120 USB CDC.

### Phase 5: Combined Sensor Streaming

Goal: stream ADS1298 + JY61P together.

Tasks:

- Merge ADS1298 and IMU streams into one BLE TX path.
- Tune BLE MTU, connection interval, data length, FIFO sizes.
- Add rate control commands.
- Add drop counters and backpressure handling.

Exit criteria:

- Combined stream runs without FIFO explosion.
- Computer receives synchronized ADS1298 and IMU data.
- Drop counters are visible over USB CDC.

### Phase 6: DFU + OTA on GR Module

Goal: update B306 GR module without J-Link after initial provisioning.

Tasks:

- Integrate MCUboot into `gr_module`.
- Add partition layout based on `../BioSpur_UWB_before_start/apps/tag_ota/pm_static.yml`.
- Add BLE SMP/MCUmgr transport to GR module.
- Keep NUS sensor service and SMP DFU service coexisting.
- Port/adapt OTA packaging scripts from `../BioSpur_UWB_before_start`.

Exit criteria:

- B306 boots MCUboot + GR app.
- OTA image can be built and signed.
- OTA update can be transferred over BLE.
- New image boots and confirms.

Important:

- The first MCUboot-capable image usually still needs initial flashing by J-Link or factory method.
- After that, normal updates use OTA.

### Phase 7: B120 OTA Controller

Goal: B120 can initiate and report GR OTA updates.

Tasks:

- Adapt OTA Central/client logic from `../BioSpur_UWB_before_start/apps/master_ota`.
- Decide where OTA image is stored:
  - embedded in B120 firmware, or
  - streamed from computer over USB CDC to B120, then BLE to B306.
- Report OTA progress to computer over USB CDC.
- Add failure recovery/status messages.

Exit criteria:

- Computer triggers OTA through B120.
- B120 updates B306 over BLE.
- Computer receives OTA progress/result over USB CDC.

### Phase 8: Gesture Recognition

Goal: produce gesture output from ADS1298 + IMU data.

Tasks:

- Start with raw capture dataset pipeline.
- Define window size, features, labels, and output classes.
- Decide recognition location:
  - on computer first for training/debug,
  - then optionally on B306 or B120 for embedded inference.
- Add `'G'` gesture result packets.

Exit criteria:

- Gesture events are visible on computer through USB CDC.
- Recognition latency and confidence are measurable.

## 6. Main Technical Risks

- ADS1298 data rate may exceed BLE throughput if packets are not bundled compactly.
- USB CDC must not block BLE receive path on B120.
- BLE NUS and BLE SMP must coexist cleanly on B306.
- B120 internal RC oscillator config must be correct for stable BLE.
- OTA image size must fit nRF52840 flash partition layout.
- If B120 streams OTA image from computer, CDC protocol needs flow control.

## 7. Immediate Next Steps

1. Create the workspace skeleton.
2. Bring up B120 USB CDC first, because it is the computer-facing debug/control path.
3. Bring up BLE heartbeat from B306 to B120.
4. Forward heartbeat to computer over USB CDC.
5. Port JY61P capture.
6. Add ADS1298 SPI capture.
7. Integrate OTA/DFU.

## 8. First Flash Targets

The first bring-up images are:

- `gr_module`: B306/nRF52840 BLE Peripheral advertising as `GRXXXX`.
- `central_b120`: B120/nRF5340 BLE Central + USB CDC bridge named `GR-Master`.

Initial flashing should be done by J-Link. After the OTA-capable GR module image is
integrated and flashed once, normal B306 updates should move to BLE OTA.

Build commands:

```sh
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_Gesture_Recognition
source /home/zekaixiao/ncs/v2.8.0/zephyr/zephyr-env.sh

PYTHONPATH=/usr/lib/python3/dist-packages west build -b nrf52840dk/nrf52840 gr_module -d build/gr_module -p always
PYTHONPATH=/usr/lib/python3/dist-packages west build -b nrf5340dk/nrf5340/cpuapp central_b120 -d build/central_b120 -p always
```

Flash commands, after the J-Link probe is connected:

```sh
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_Gesture_Recognition
source /home/zekaixiao/ncs/v2.8.0/zephyr/zephyr-env.sh

# Flash B306 / nRF52840 GR module.
west flash -d build/gr_module

# Flash B120 / NORA-B120 GR-Master.
# This sysbuild image includes both CPUAPP and CPUNET/ipc_radio.
west flash -d build/central_b120
```

If more than one J-Link probe is connected, list them first and then pass the
serial number to `west flash`:

```sh
nrfjprog --ids
west flash -d build/gr_module --dev-id <B306_JLINK_SERIAL>
west flash -d build/central_b120 --dev-id <B120_JLINK_SERIAL>
```
