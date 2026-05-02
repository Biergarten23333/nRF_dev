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

project(BioSpur_UWB_anchor)

set(APP_ROOT ${CMAKE_CURRENT_LIST_DIR}/../..)
function(set_cache_from_env name default type doc)
  if(DEFINED ENV{${name}} AND NOT "$ENV{${name}}" STREQUAL "")
    set(_value "$ENV{${name}}")
  else()
    set(_value "${default}")
  endif()
  set(${name} "${_value}" CACHE ${type} "${doc}")
endfunction()

set_cache_from_env(APP_ANCHOR_ID "4" STRING "Anchor node ID (0..7 legacy fallback only)")
set_cache_from_env(APP_ANCHOR_ROLE "0" STRING "Anchor role: 0=legacy,1=master,2=matrix,3=responder")
set_cache_from_env(APP_ANCHOR_FORCE_BOOT_ROLE "0" STRING "Force runtime boot role when nonzero: 1=master,2=matrix,3=responder")
set_cache_from_env(APP_ANCHOR_CONFIG_ADDR "0x0007E000" STRING "Flash address of anchor_config_t provisioning block")
set_cache_from_env(APP_ANCHOR_CONFIG_PAGE_SIZE "4096" STRING "Flash erase page size for anchor config")
set_cache_from_env(APP_ANCHOR_BLE_ID_ENABLE "1" STRING "Enable BLE identification advertising")
set_cache_from_env(APP_ANCHOR_SERIAL_CMD_ENABLE "1" STRING "Enable UART role/config command handler")
set_cache_from_env(APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS "5000" STRING "Boot window for serial config before ranging starts")
set_cache_from_env(APP_ANCHOR_FW_MARKER "anchor-unified" STRING "Firmware marker/version string for startup/control readback")
set_cache_from_env(APP_ANCHOR_MASTER "0" STRING "Whether this anchor actively ranges peers")
set_cache_from_env(APP_ANCHOR_PEER_COUNT "3" STRING "Number of peer anchors for master mode")
set_cache_from_env(APP_ANCHOR_PEER_0_ID "5" STRING "Peer slot 0")
set_cache_from_env(APP_ANCHOR_PEER_1_ID "6" STRING "Peer slot 1")
set_cache_from_env(APP_ANCHOR_PEER_2_ID "7" STRING "Peer slot 2")
set_cache_from_env(APP_ANCHOR_USE_AUTO_SCHEDULE "1" STRING "Use built-in upper-triangle peer schedule")
set_cache_from_env(APP_ANCHOR_SCHEDULE_MODE "2" STRING "1=upper triangle, 2=all except self")
set_cache_from_env(APP_ANCHOR_ALLOW_TAG_POLLS "1" STRING "Worker accepts tag polls")
set_cache_from_env(APP_ANCHOR_RESP_DELAY_UUS "1200" STRING "Responder poll-to-response delay in UWB usec")
set_cache_from_env(APP_ANCHOR_RESPONDER_COOP_SLEEP_MS "0" STRING "Responder cooperative sleep (ms) to reduce CPU starvation under OTA/BLE load")
set_cache_from_env(APP_ANCHOR_RESPONDER_ADV_INT_MIN_MS "5000" STRING "Responder-role connectable BLE advertising minimum interval in ms")
set_cache_from_env(APP_ANCHOR_RESPONDER_ADV_INT_MAX_MS "10000" STRING "Responder-role connectable BLE advertising maximum interval in ms")
set_cache_from_env(APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS "5000" STRING "Responder diagnostic print period in ms")
set_cache_from_env(APP_ANCHOR_VERBOSE_RESPONDER "0" STRING "Print responder success logs")
set_cache_from_env(APP_ANCHOR_VERBOSE_RESPONDER_ERRORS "0" STRING "Print responder RX/TX error logs")
set_cache_from_env(APP_ANCHOR_RESPONDER_PRINTK_ENABLE "0" STRING "Enable low-rate responder-path printk output; keep disabled outside debug builds")
set_cache_from_env(APP_ANCHOR_RESPONDER_PROFILE_ENABLE "0" STRING "Enable low-rate responder fast-path timing profile output")
set_cache_from_env(APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE "0" STRING "Pulse DWM1001C blue LED while a responder TX is active")
set_cache_from_env(APP_ANCHOR_RESPONDER_BLUE_LED_PIN "31" STRING "GPIO pin for responder activity LED")
set_cache_from_env(APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW "1" STRING "Responder activity LED active-low polarity")
set_cache_from_env(APP_ANCHOR_DIAG_BLE_BEFORE_UWB "0" STRING "Diagnostic: start BLE/control before UWB bringup")
set_cache_from_env(APP_ANCHOR_DIAG_CONTINUE_ON_UWB_FAIL "0" STRING "Diagnostic: keep BLE/control alive if UWB bringup fails")
set_cache_from_env(APP_ALT_SS_TWR_ENABLE "0" STRING "Enable experimental burst-poll SS-TWR responder scheduling")
set_cache_from_env(APP_ALT_SS_TWR_BCAST_ENABLE "0" STRING "Enable clean broadcast Alt SS-TWR responder path")
set_cache_from_env(APP_ALT_SS_TWR_POLL_SPACING_US "200" STRING "Alt SS-TWR spacing between burst polls in microseconds")
set_cache_from_env(APP_ALT_SS_TWR_GUARD_US "400" STRING "Alt SS-TWR guard time after poll burst in microseconds")
set_cache_from_env(APP_ALT_SS_TWR_RESP_SPACING_US "1000" STRING "Alt SS-TWR spacing between anchor response slots in microseconds")
set_cache_from_env(APP_ALT_SS_TWR_UNICAST_POLL_REARM_US "0" STRING "Extra responder-side allowance per remaining unicast poll for tag TX rearm overhead")
set_cache_from_env(APP_UWB_CHANNEL "5" STRING "DW1000 UWB channel number")
set_cache_from_env(APP_UWB_PAN_ID "0xDECA" STRING "DW1000 PAN ID")
set_cache_from_env(APP_UWB_HW_FRAME_FILTER_ENABLE "1" STRING "Enable DW1000 hardware frame filtering")

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)
target_include_directories(app PRIVATE ${APP_ROOT}/src/anchors/unified)
target_compile_definitions(app PRIVATE
    APP_ANCHOR_ID=${APP_ANCHOR_ID}
    APP_ANCHOR_ROLE=${APP_ANCHOR_ROLE}
    APP_ANCHOR_FORCE_BOOT_ROLE=${APP_ANCHOR_FORCE_BOOT_ROLE}
    APP_ANCHOR_CONFIG_ADDR=${APP_ANCHOR_CONFIG_ADDR}
    APP_ANCHOR_CONFIG_PAGE_SIZE=${APP_ANCHOR_CONFIG_PAGE_SIZE}
    APP_ANCHOR_BLE_ID_ENABLE=${APP_ANCHOR_BLE_ID_ENABLE}
    APP_ANCHOR_SERIAL_CMD_ENABLE=${APP_ANCHOR_SERIAL_CMD_ENABLE}
    APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS=${APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS}
    APP_ANCHOR_FW_MARKER=\"${APP_ANCHOR_FW_MARKER}\"
    APP_ANCHOR_MASTER=${APP_ANCHOR_MASTER}
    APP_ANCHOR_PEER_COUNT=${APP_ANCHOR_PEER_COUNT}
    APP_ANCHOR_PEER_0_ID=${APP_ANCHOR_PEER_0_ID}
    APP_ANCHOR_PEER_1_ID=${APP_ANCHOR_PEER_1_ID}
    APP_ANCHOR_PEER_2_ID=${APP_ANCHOR_PEER_2_ID}
    APP_ANCHOR_USE_AUTO_SCHEDULE=${APP_ANCHOR_USE_AUTO_SCHEDULE}
    APP_ANCHOR_SCHEDULE_MODE=${APP_ANCHOR_SCHEDULE_MODE}
    APP_ANCHOR_ALLOW_TAG_POLLS=${APP_ANCHOR_ALLOW_TAG_POLLS}
    APP_ANCHOR_RESP_DELAY_UUS=${APP_ANCHOR_RESP_DELAY_UUS}
    APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=${APP_ANCHOR_RESPONDER_COOP_SLEEP_MS}
    APP_ANCHOR_RESPONDER_ADV_INT_MIN_MS=${APP_ANCHOR_RESPONDER_ADV_INT_MIN_MS}
    APP_ANCHOR_RESPONDER_ADV_INT_MAX_MS=${APP_ANCHOR_RESPONDER_ADV_INT_MAX_MS}
    APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS=${APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS}
    APP_ANCHOR_VERBOSE_RESPONDER=${APP_ANCHOR_VERBOSE_RESPONDER}
    APP_ANCHOR_VERBOSE_RESPONDER_ERRORS=${APP_ANCHOR_VERBOSE_RESPONDER_ERRORS}
    APP_ANCHOR_RESPONDER_PRINTK_ENABLE=${APP_ANCHOR_RESPONDER_PRINTK_ENABLE}
    APP_ANCHOR_RESPONDER_PROFILE_ENABLE=${APP_ANCHOR_RESPONDER_PROFILE_ENABLE}
    APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE=${APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE}
    APP_ANCHOR_RESPONDER_BLUE_LED_PIN=${APP_ANCHOR_RESPONDER_BLUE_LED_PIN}
    APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW=${APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW}
    APP_ANCHOR_DIAG_BLE_BEFORE_UWB=${APP_ANCHOR_DIAG_BLE_BEFORE_UWB}
    APP_ANCHOR_DIAG_CONTINUE_ON_UWB_FAIL=${APP_ANCHOR_DIAG_CONTINUE_ON_UWB_FAIL}
    APP_ALT_SS_TWR_ENABLE=${APP_ALT_SS_TWR_ENABLE}
    APP_ALT_SS_TWR_BCAST_ENABLE=${APP_ALT_SS_TWR_BCAST_ENABLE}
    APP_ALT_SS_TWR_POLL_SPACING_US=${APP_ALT_SS_TWR_POLL_SPACING_US}
    APP_ALT_SS_TWR_GUARD_US=${APP_ALT_SS_TWR_GUARD_US}
    APP_ALT_SS_TWR_RESP_SPACING_US=${APP_ALT_SS_TWR_RESP_SPACING_US}
    APP_ALT_SS_TWR_UNICAST_POLL_REARM_US=${APP_ALT_SS_TWR_UNICAST_POLL_REARM_US}
    APP_UWB_CHANNEL=${APP_UWB_CHANNEL}
    APP_UWB_PAN_ID=${APP_UWB_PAN_ID}
    APP_UWB_HW_FRAME_FILTER_ENABLE=${APP_UWB_HW_FRAME_FILTER_ENABLE}
)

target_sources(app PRIVATE
    src/main.c
    src/anchor_app.c
    ${APP_ROOT}/src/anchors/unified/anchor_config.c
    ${APP_ROOT}/src/anchors/unified/anchor_ble_id.c
    ${APP_ROOT}/src/anchors/unified/anchor_ble_ctrl.c
    ${APP_ROOT}/src/anchors/unified/anchor_runtime_control.c
    ${APP_ROOT}/src/anchors/unified/anchor_mcumgr_diag.c
    ${APP_ROOT}/src/anchors/unified/uart_role_switch.c
    ${APP_ROOT}/src/uwb_bringup.c
    ${APP_ROOT}/src/uwb_port.c
    ${APP_ROOT}/src/uwb_anchor_topology.c
    ${APP_ROOT}/src/uwb_anchor_matrix.c
    ${APP_ROOT}/src/uwb_ss_twr_shared.c
    ${APP_ROOT}/src/uwb_range_tracker.c
    ${APP_ROOT}/src/ss_twr_anchor_init.c
    ${APP_ROOT}/src/ss_twr_resp.c
    ${APP_ROOT}/drivers/dw1000/src/deca_device.c
    ${APP_ROOT}/drivers/dw1000/src/deca_params_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_range_tables.c
)
