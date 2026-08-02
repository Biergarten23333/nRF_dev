cmake_minimum_required(VERSION 3.20.0)

if(NOT ZEPHYR_MODULES)
  execute_process(
    COMMAND west list --format={abspath}
    WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
    OUTPUT_VARIABLE WEST_MODULES_RAW
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE WEST_LIST_RESULT
  )
  if(WEST_LIST_RESULT EQUAL 0 AND WEST_MODULES_RAW)
    string(REPLACE "\n" ";" ZEPHYR_MODULES "${WEST_MODULES_RAW}")
  endif()
endif()

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(BioSpur_UWB_tag)

if(DEFINED ENV{APP_TAG_PRELOAD_FILE} AND EXISTS $ENV{APP_TAG_PRELOAD_FILE})
    include($ENV{APP_TAG_PRELOAD_FILE})
endif()

set(APP_ROOT ${CMAKE_CURRENT_LIST_DIR}/../..)
if(NOT DEFINED APP_TAG_ID OR APP_TAG_ID STREQUAL "")
set(APP_TAG_ID "0" CACHE STRING "Tag node ID")
endif()
set(APP_TAG_USB_DIAG_TRACE "0" CACHE STRING "Enable low-level console trace for bringup debugging")
set(APP_TAG_ANCHOR_COUNT "8" CACHE STRING "Number of anchors to range against")
set(APP_TAG_ANCHOR_0_ID "0" CACHE STRING "Anchor slot 0 ID")
set(APP_TAG_ANCHOR_1_ID "1" CACHE STRING "Anchor slot 1 ID")
set(APP_TAG_ANCHOR_2_ID "2" CACHE STRING "Anchor slot 2 ID")
set(APP_TAG_ANCHOR_3_ID "3" CACHE STRING "Anchor slot 3 ID")
set(APP_TAG_ANCHOR_4_ID "4" CACHE STRING "Anchor slot 4 ID")
set(APP_TAG_ANCHOR_5_ID "5" CACHE STRING "Anchor slot 5 ID")
set(APP_TAG_ANCHOR_6_ID "6" CACHE STRING "Anchor slot 6 ID")
set(APP_TAG_ANCHOR_7_ID "7" CACHE STRING "Anchor slot 7 ID")
set(APP_TAG_RNG_DELAY_MS "0" CACHE STRING "Delay between anchor polls in milliseconds")
set(APP_TAG_CAL_RNG_SETTLE_US "0" CACHE STRING "Optional settle time between anchor polls in microseconds")
set(APP_TAG_TX_TO_RX_DLY_UUS "140" CACHE STRING "Initiator RX enable delay after poll TX in UWB usec")
set(APP_TAG_RESP_RX_TIMEOUT_UUS "1800" CACHE STRING "Initiator response RX timeout in UWB usec")
set(APP_TAG_NORMAL_OUTPUT_ENABLE "1" CACHE STRING "Emit normal Tag range output such as TR/CM")
set(APP_TAG_CIR_FEATURE_OUTPUT_ENABLE "0" CACHE STRING "Emit lightweight DW1000 CIR/first-path features for received response frames")
set(APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE "1" CACHE STRING "Forward lightweight CIR feature lines over BLE/NUS when enabled")
set(APP_TAG_CIR_FULL_OUTPUT_ENABLE "0" CACHE STRING "Emit full DW1000 accumulator bytes for received response frames")
set(APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE "1" CACHE STRING "Emit full CIR accumulator chunks to console/CDC")
set(APP_TAG_CIR_FULL_CHUNK_BYTES "48" CACHE STRING "Payload bytes per full-CIR text chunk")
set(APP_TAG_CIR_FULL_PRIORITY_MASK "0" CACHE STRING "Bit mask of anchors to prioritize for full CIR capture, or 0 for round robin")
set(APP_TAG_CIR_COMPACT_SAMPLE_PERIOD "8" CACHE STRING "Compact CIR samples one sweep every N sweeps; 1 samples every sweep")
set(APP_TAG_RF_DIAG_OUTPUT_ENABLE "0" CACHE STRING "Emit per-anchor RF diagnostics parsed from v2 responses and Tag RX diagnostics")
set(APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE "1" CACHE STRING "Forward RF diagnostic lines over BLE/NUS when RF diag output is enabled")
set(APP_TAG_RF_DIAG_OUTPUT_PERIOD "1" CACHE STRING "Emit one RF diagnostic row every N sweeps when enabled")
set(APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE "0" CACHE STRING "Emit legacy per-anchor RFD text rows; keep disabled for normal ranging captures")
set(APP_TAG_TR_RF_DIAG_COMPACT_ENABLE "1" CACHE STRING "Append compact RF diagnostics to the normal TR range summary when RF diag is enabled")
set(APP_TAG_RF_DIAG_TAG_RX_ENABLE "0" CACHE STRING "Read Tag-side RX diagnostics in the response hot path; disabled by default to protect ranging success")
set(APP_TAG_VERBOSE_RANGING "0" CACHE STRING "Print per-anchor ranging logs")
set(APP_TAG_VERBOSE_MEASUREMENTS "0" CACHE STRING "Print per-anchor measurement logs")
set(APP_TAG_USB_MIRROR_BLE_STATUS "0" CACHE STRING "Mirror BLE status summaries to USB serial for debug")
set(APP_TAG_MULTITAG_PLAN_MODE "0" CACHE STRING "Enable active/standby/reserve multi-tag anchor plan")
set(APP_TAG_ACTIVE_ANCHOR_0_ID "0" CACHE STRING "Multi-tag active anchor slot 0 ID")
set(APP_TAG_ACTIVE_ANCHOR_1_ID "1" CACHE STRING "Multi-tag active anchor slot 1 ID")
set(APP_TAG_ACTIVE_ANCHOR_2_ID "4" CACHE STRING "Multi-tag active anchor slot 2 ID")
set(APP_TAG_ACTIVE_ANCHOR_3_ID "5" CACHE STRING "Multi-tag active anchor slot 3 ID")
set(APP_TAG_STANDBY_ANCHOR_0_ID "2" CACHE STRING "Multi-tag standby anchor slot 0 ID")
set(APP_TAG_STANDBY_ANCHOR_1_ID "6" CACHE STRING "Multi-tag standby anchor slot 1 ID")
set(APP_TAG_RESERVE_ANCHOR_0_ID "3" CACHE STRING "Multi-tag reserve anchor slot 0 ID")
set(APP_TAG_RESERVE_ANCHOR_1_ID "7" CACHE STRING "Multi-tag reserve anchor slot 1 ID")
set(APP_TAG_REFRESH_ANCHOR_BUDGET "1" CACHE STRING "Additional refresh anchors measured on refresh sweeps")
set(APP_TAG_REFRESH_INTERVAL "10" CACHE STRING "Run one refresh sweep every N sweeps in multi-tag mode")
set(APP_TAG_MAINTENANCE_FULL_INTERVAL "100" CACHE STRING "Run one full 8-anchor maintenance sweep every N sweeps in multi-tag mode")
set(APP_TAG_TDMA_ENABLE "0" CACHE STRING "Enable TDMA slot gating for this tag")
set(APP_TAG_TDMA_SLOT_INDEX "0" CACHE STRING "Zero-based TDMA slot index for this tag")
set(APP_TAG_TDMA_SLOT_COUNT "1" CACHE STRING "Total TDMA slot count in the cycle")
set(APP_TAG_TDMA_SLOT_PERIOD_MS "10" CACHE STRING "TDMA slot period in milliseconds")
set(APP_TAG_TDMA_SLOT_ACTIVE_MS "9" CACHE STRING "Usable active time within each slot in milliseconds")
set(APP_TAG_TDMA_SLOT_ACTIVE_US "0" CACHE STRING "Optional usable active time within each slot in microseconds; 0 uses ACTIVE_MS")
set(APP_TAG_LEGACY_STATIC_SLOT_DIVIDER "1" CACHE STRING "Legacy grouped-static slot divider; normal captures keep this at 1")
set(APP_TAG_TR_BCAST_V2_ENABLE "1" CACHE STRING "Emit broadcast-rich TR;2 range summaries with rx/timing fields")
set(APP_TAG_BCAST_SUMMARY_ENABLE "0" CACHE STRING "Emit 1Hz broadcast SSTWR summary lines")
set(APP_TAG_BCAST_SUMMARY_PERIOD_MS "1000" CACHE STRING "Broadcast SSTWR summary period in milliseconds")
set(APP_TAG_SWEEP_DIAG_ENABLE "0" CACHE STRING "Emit per-sweep timing diagnostic lines")
set(APP_TAG_SWEEP_DIAG_PERIOD "10" CACHE STRING "Emit one sweep timing diagnostic every N sweeps")
set(APP_TAG_BLE_ENABLE "1" CACHE STRING "Enable BLE/NUS and OTA transport on the tag")
set(APP_TAG_BLE_OTA_ENABLE "1" CACHE STRING "Enable OTA command and bootloader-advertising BLE payloads on the tag")
set(APP_TAG_BLE_SETTINGS_ENABLE "1" CACHE STRING "Enable Bluetooth settings persistence on the tag")
set(APP_TAG_MCUBOOT_ENABLE "1" CACHE STRING "Enable MCUboot confirm/OTA plumbing on the tag")
set(APP_TAG_SELF_CONFIRM_MODE "0" CACHE STRING "MCUboot self-confirm mode: 0=normal, 1=proof no-confirm, 2=proof timeout")
set(APP_TAG_SELF_CONFIRM_TIMEOUT_MS "10000" CACHE STRING "Maximum unconfirmed BLE bring-up time before rollback reset")
set(APP_TAG_UWB_ENABLE "1" CACHE STRING "Enable UWB ranging runtime on the tag (0 keeps BLE/OTA online only)")
set(APP_TAG_BLE_COMPACT_STATUS "1" CACHE STRING "Enable compact BLE TagSummary payloads on the tag")
set(APP_TAG_BLE_PACKET_BUNDLE_RECORDS "8" CACHE STRING "Bundle this many TagSummary records into one BLE NUS payload")
set(APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS "100" CACHE STRING "Flush a partial BLE bundle after this many milliseconds (0 disables)")
set(APP_TAG_BLE_TX_ITEM_COUNT "16" CACHE STRING "BLE NUS transmit queue item count")
set(APP_TAG_BLE_UWB_DIAG_OUTPUT_ENABLE "0" CACHE STRING "Forward UWB diagnostic status lines over BLE")
set(APP_TAG_BLE_NAME_PREFIX "" CACHE STRING "Optional BLE name prefix prepended before BSxxxx")
set(APP_TAG_STREAM_FORCE_OFF_AT_BOOT "0" CACHE STRING "Force STREAM OFF on every boot regardless of persisted setting")
set(APP_TAG_FW_MARKER "unified-default" CACHE STRING "Runtime firmware marker printed at boot for OTA validation")
set(APP_ALT_SS_TWR_ENABLE "1" CACHE STRING "Enable experimental burst-poll SS-TWR protocol")
set(APP_ALT_SS_TWR_BCAST_ENABLE "1" CACHE STRING "Enable clean broadcast Alt SS-TWR path")
set(APP_ALT_SS_TWR_MODE "2" CACHE STRING "Alt SS-TWR mode: 1=unicast burst, 2=broadcast mask")
set(APP_ALT_SS_TWR_POLL_SPACING_US "200" CACHE STRING "Alt SS-TWR spacing between burst polls in microseconds")
set(APP_ALT_SS_TWR_GUARD_US "1200" CACHE STRING "Alt SS-TWR guard time after poll burst in microseconds")
set(APP_ALT_SS_TWR_RESP_SPACING_US "1000" CACHE STRING "Alt SS-TWR spacing between anchor response slots in microseconds")
set(APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP "1" CACHE STRING "Force broadcast Alt SS-TWR sweeps to use all configured anchors")
set(APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE "1" CACHE STRING "Use broadcast-dedicated lightweight TDMA scheduler")
set(APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE "0" CACHE STRING "Use immediate TX for broadcast Alt SS-TWR poll frames")
set(APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE "0" CACHE STRING "Prewrite broadcast Alt SS-TWR poll TX buffer before the TDMA slot")
set(APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE "255" CACHE STRING "Broadcast responder rank offset override, 0-7. 255 keeps the normal automatic policy")
set(APP_TAG_ALT_POLL_DIAG_PERIOD_MS "5000" CACHE STRING "Alt SS-TWR poll timing diagnostic period in milliseconds")
set(APP_TAG_ALT_RXG_BLE_DIAG_ENABLE "0" CACHE STRING "Forward broadcast RX gap timing diagnostics over BLE")
set(APP_TAG_RELAY6_COUNTER_ENABLE "1" CACHE STRING "Use a tag-owned monotonic public sweep counter")
set(APP_TAG_RELAY6_BEACON_STATUS_ENABLE "1" CACHE STRING "Enable bounded BEACON_STATUS reply")
set(APP_TAG_RELAY6_DW_ANCHOR_ENABLE "1" CACHE STRING "Compile volatile DW-domain slot anchoring support")
if(NOT DEFINED APP_TAG_BLE_TOKEN_ID OR APP_TAG_BLE_TOKEN_ID STREQUAL "")
set(APP_TAG_BLE_TOKEN_ID "${APP_TAG_ID}" CACHE STRING "BLE identity token advertised in manufacturer data")
endif()
set(APP_UWB_CHANNEL "5" CACHE STRING "DW1000 UWB channel number")
set(APP_UWB_PAN_ID "0xDECA" CACHE STRING "DW1000 PAN ID")
# MAC/PAN/address frame acceptance only; this does not filter range values.
set(APP_UWB_HW_FRAME_FILTER_ENABLE "1" CACHE STRING "Enable DW1000 hardware frame filtering")

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)
target_compile_definitions(app PRIVATE
    APP_TAG_ID=${APP_TAG_ID}
    APP_TAG_ANCHOR_COUNT=${APP_TAG_ANCHOR_COUNT}
    APP_TAG_ANCHOR_0_ID=${APP_TAG_ANCHOR_0_ID}
    APP_TAG_ANCHOR_1_ID=${APP_TAG_ANCHOR_1_ID}
    APP_TAG_ANCHOR_2_ID=${APP_TAG_ANCHOR_2_ID}
    APP_TAG_ANCHOR_3_ID=${APP_TAG_ANCHOR_3_ID}
    APP_TAG_ANCHOR_4_ID=${APP_TAG_ANCHOR_4_ID}
    APP_TAG_ANCHOR_5_ID=${APP_TAG_ANCHOR_5_ID}
    APP_TAG_ANCHOR_6_ID=${APP_TAG_ANCHOR_6_ID}
    APP_TAG_ANCHOR_7_ID=${APP_TAG_ANCHOR_7_ID}
    APP_TAG_RNG_DELAY_MS=${APP_TAG_RNG_DELAY_MS}
    APP_TAG_CAL_RNG_SETTLE_US=${APP_TAG_CAL_RNG_SETTLE_US}
    APP_TAG_TX_TO_RX_DLY_UUS=${APP_TAG_TX_TO_RX_DLY_UUS}
    APP_TAG_RESP_RX_TIMEOUT_UUS=${APP_TAG_RESP_RX_TIMEOUT_UUS}
    APP_TAG_NORMAL_OUTPUT_ENABLE=${APP_TAG_NORMAL_OUTPUT_ENABLE}
    APP_TAG_CIR_FEATURE_OUTPUT_ENABLE=${APP_TAG_CIR_FEATURE_OUTPUT_ENABLE}
    APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE=${APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE}
    APP_TAG_CIR_FULL_OUTPUT_ENABLE=${APP_TAG_CIR_FULL_OUTPUT_ENABLE}
    APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE=${APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE}
    APP_TAG_CIR_FULL_CHUNK_BYTES=${APP_TAG_CIR_FULL_CHUNK_BYTES}
    APP_TAG_CIR_FULL_PRIORITY_MASK=${APP_TAG_CIR_FULL_PRIORITY_MASK}
    APP_TAG_CIR_COMPACT_SAMPLE_PERIOD=${APP_TAG_CIR_COMPACT_SAMPLE_PERIOD}
    APP_TAG_RF_DIAG_OUTPUT_ENABLE=${APP_TAG_RF_DIAG_OUTPUT_ENABLE}
    APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE=${APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE}
    APP_TAG_RF_DIAG_OUTPUT_PERIOD=${APP_TAG_RF_DIAG_OUTPUT_PERIOD}
    APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE=${APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE}
    APP_TAG_TR_RF_DIAG_COMPACT_ENABLE=${APP_TAG_TR_RF_DIAG_COMPACT_ENABLE}
    APP_TAG_RF_DIAG_TAG_RX_ENABLE=${APP_TAG_RF_DIAG_TAG_RX_ENABLE}
    APP_TAG_VERBOSE_RANGING=${APP_TAG_VERBOSE_RANGING}
    APP_TAG_VERBOSE_MEASUREMENTS=${APP_TAG_VERBOSE_MEASUREMENTS}
    APP_TAG_USB_MIRROR_BLE_STATUS=${APP_TAG_USB_MIRROR_BLE_STATUS}
    APP_TAG_MULTITAG_PLAN_MODE=${APP_TAG_MULTITAG_PLAN_MODE}
    APP_TAG_ACTIVE_ANCHOR_0_ID=${APP_TAG_ACTIVE_ANCHOR_0_ID}
    APP_TAG_ACTIVE_ANCHOR_1_ID=${APP_TAG_ACTIVE_ANCHOR_1_ID}
    APP_TAG_ACTIVE_ANCHOR_2_ID=${APP_TAG_ACTIVE_ANCHOR_2_ID}
    APP_TAG_ACTIVE_ANCHOR_3_ID=${APP_TAG_ACTIVE_ANCHOR_3_ID}
    APP_TAG_STANDBY_ANCHOR_0_ID=${APP_TAG_STANDBY_ANCHOR_0_ID}
    APP_TAG_STANDBY_ANCHOR_1_ID=${APP_TAG_STANDBY_ANCHOR_1_ID}
    APP_TAG_RESERVE_ANCHOR_0_ID=${APP_TAG_RESERVE_ANCHOR_0_ID}
    APP_TAG_RESERVE_ANCHOR_1_ID=${APP_TAG_RESERVE_ANCHOR_1_ID}
    APP_TAG_REFRESH_ANCHOR_BUDGET=${APP_TAG_REFRESH_ANCHOR_BUDGET}
    APP_TAG_REFRESH_INTERVAL=${APP_TAG_REFRESH_INTERVAL}
    APP_TAG_MAINTENANCE_FULL_INTERVAL=${APP_TAG_MAINTENANCE_FULL_INTERVAL}
    APP_TAG_TDMA_ENABLE=${APP_TAG_TDMA_ENABLE}
    APP_TAG_TDMA_SLOT_INDEX=${APP_TAG_TDMA_SLOT_INDEX}
    APP_TAG_TDMA_SLOT_COUNT=${APP_TAG_TDMA_SLOT_COUNT}
    APP_TAG_TDMA_SLOT_PERIOD_MS=${APP_TAG_TDMA_SLOT_PERIOD_MS}
    APP_TAG_TDMA_SLOT_ACTIVE_MS=${APP_TAG_TDMA_SLOT_ACTIVE_MS}
    APP_TAG_TDMA_SLOT_ACTIVE_US=${APP_TAG_TDMA_SLOT_ACTIVE_US}
    APP_TAG_LEGACY_STATIC_SLOT_DIVIDER=${APP_TAG_LEGACY_STATIC_SLOT_DIVIDER}
    APP_TAG_TR_BCAST_V2_ENABLE=${APP_TAG_TR_BCAST_V2_ENABLE}
    APP_TAG_BCAST_SUMMARY_ENABLE=${APP_TAG_BCAST_SUMMARY_ENABLE}
    APP_TAG_BCAST_SUMMARY_PERIOD_MS=${APP_TAG_BCAST_SUMMARY_PERIOD_MS}
    APP_TAG_SWEEP_DIAG_ENABLE=${APP_TAG_SWEEP_DIAG_ENABLE}
    APP_TAG_SWEEP_DIAG_PERIOD=${APP_TAG_SWEEP_DIAG_PERIOD}
    APP_TAG_BLE_ENABLE=${APP_TAG_BLE_ENABLE}
    APP_TAG_BLE_OTA_ENABLE=${APP_TAG_BLE_OTA_ENABLE}
    APP_TAG_USB_DIAG_TRACE=${APP_TAG_USB_DIAG_TRACE}
    APP_TAG_BLE_SETTINGS_ENABLE=${APP_TAG_BLE_SETTINGS_ENABLE}
    APP_TAG_UWB_ENABLE=${APP_TAG_UWB_ENABLE}
    APP_TAG_BLE_COMPACT_STATUS=${APP_TAG_BLE_COMPACT_STATUS}
    APP_TAG_BLE_PACKET_BUNDLE_RECORDS=${APP_TAG_BLE_PACKET_BUNDLE_RECORDS}
    APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS=${APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS}
    APP_TAG_BLE_TX_ITEM_COUNT=${APP_TAG_BLE_TX_ITEM_COUNT}
    APP_TAG_BLE_UWB_DIAG_OUTPUT_ENABLE=${APP_TAG_BLE_UWB_DIAG_OUTPUT_ENABLE}
    APP_TAG_BLE_NAME_PREFIX=\"${APP_TAG_BLE_NAME_PREFIX}\"
    APP_TAG_STREAM_FORCE_OFF_AT_BOOT=${APP_TAG_STREAM_FORCE_OFF_AT_BOOT}
    APP_TAG_FW_MARKER=\"${APP_TAG_FW_MARKER}\"
    APP_ALT_SS_TWR_ENABLE=${APP_ALT_SS_TWR_ENABLE}
    APP_ALT_SS_TWR_BCAST_ENABLE=${APP_ALT_SS_TWR_BCAST_ENABLE}
    APP_ALT_SS_TWR_MODE=${APP_ALT_SS_TWR_MODE}
    APP_ALT_SS_TWR_POLL_SPACING_US=${APP_ALT_SS_TWR_POLL_SPACING_US}
    APP_ALT_SS_TWR_GUARD_US=${APP_ALT_SS_TWR_GUARD_US}
    APP_ALT_SS_TWR_RESP_SPACING_US=${APP_ALT_SS_TWR_RESP_SPACING_US}
    APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP=${APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP}
    APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE=${APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE}
    APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE=${APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE}
    APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE=${APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE}
    APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE=${APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE}
    APP_TAG_ALT_POLL_DIAG_PERIOD_MS=${APP_TAG_ALT_POLL_DIAG_PERIOD_MS}
    APP_TAG_ALT_RXG_BLE_DIAG_ENABLE=${APP_TAG_ALT_RXG_BLE_DIAG_ENABLE}
    APP_TAG_RELAY6_COUNTER_ENABLE=${APP_TAG_RELAY6_COUNTER_ENABLE}
    APP_TAG_RELAY6_BEACON_STATUS_ENABLE=${APP_TAG_RELAY6_BEACON_STATUS_ENABLE}
    APP_TAG_RELAY6_DW_ANCHOR_ENABLE=${APP_TAG_RELAY6_DW_ANCHOR_ENABLE}
    APP_TAG_BLE_TOKEN_ID=${APP_TAG_BLE_TOKEN_ID}
    APP_TAG_MCUBOOT_ENABLE=${APP_TAG_MCUBOOT_ENABLE}
    APP_TAG_SELF_CONFIRM_MODE=${APP_TAG_SELF_CONFIRM_MODE}
    APP_TAG_SELF_CONFIRM_TIMEOUT_MS=${APP_TAG_SELF_CONFIRM_TIMEOUT_MS}
    APP_UWB_CHANNEL=${APP_UWB_CHANNEL}
    APP_UWB_PAN_ID=${APP_UWB_PAN_ID}
    APP_UWB_HW_FRAME_FILTER_ENABLE=${APP_UWB_HW_FRAME_FILTER_ENABLE}
)

target_sources(app PRIVATE
    src/main.c
    src/tag_app.c
    ${APP_ROOT}/src/uwb_bringup.c
    ${APP_ROOT}/src/uwb_port.c
    ${APP_ROOT}/src/uwb_ss_twr_shared.c
    ${APP_ROOT}/src/biospur_uart_link.c
    ${APP_ROOT}/src/uwb_beacon.c
    ${APP_ROOT}/src/uwb_tdma.c
    ${APP_ROOT}/src/broadcast_tdma.c
    ${APP_ROOT}/src/uwb_range_tracker.c
    ${APP_ROOT}/src/ss_twr_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_device.c
    ${APP_ROOT}/drivers/dw1000/src/deca_params_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_range_tables.c
)

if(APP_TAG_BLE_ENABLE)
    target_sources(app PRIVATE
        ${APP_ROOT}/apps/tag/src/uwb_tag_ble.c
    )
endif()
