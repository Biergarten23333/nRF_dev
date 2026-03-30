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
set(APP_ANCHOR_ID "4" CACHE STRING "Anchor node ID (0..7 legacy fallback only)")
set(APP_ANCHOR_ROLE "0" CACHE STRING "Anchor role: 0=legacy,1=master,2=matrix,3=responder")
set(APP_ANCHOR_CONFIG_ADDR "0x0007E000" CACHE STRING "Flash address of anchor_config_t provisioning block")
set(APP_ANCHOR_CONFIG_PAGE_SIZE "4096" CACHE STRING "Flash erase page size for anchor config")
set(APP_ANCHOR_BLE_ID_ENABLE "1" CACHE STRING "Enable BLE identification advertising")
set(APP_ANCHOR_SERIAL_CMD_ENABLE "1" CACHE STRING "Enable UART role/config command handler")
set(APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS "5000" CACHE STRING "Boot window for serial config before ranging starts")
set(APP_ANCHOR_MASTER "0" CACHE STRING "Whether this anchor actively ranges peers")
set(APP_ANCHOR_PEER_COUNT "3" CACHE STRING "Number of peer anchors for master mode")
set(APP_ANCHOR_PEER_0_ID "5" CACHE STRING "Peer slot 0")
set(APP_ANCHOR_PEER_1_ID "6" CACHE STRING "Peer slot 1")
set(APP_ANCHOR_PEER_2_ID "7" CACHE STRING "Peer slot 2")
set(APP_ANCHOR_USE_AUTO_SCHEDULE "1" CACHE STRING "Use built-in upper-triangle peer schedule")
set(APP_ANCHOR_SCHEDULE_MODE "1" CACHE STRING "1=upper triangle, 2=all except self")
set(APP_ANCHOR_ALLOW_TAG_POLLS "1" CACHE STRING "Worker accepts tag polls")
set(APP_ANCHOR_RESP_DELAY_UUS "900" CACHE STRING "Responder poll-to-response delay in UWB usec")
set(APP_ANCHOR_VERBOSE_RESPONDER "0" CACHE STRING "Print responder success logs")
set(APP_ANCHOR_VERBOSE_RESPONDER_ERRORS "0" CACHE STRING "Print responder RX/TX error logs")
set(APP_UWB_CHANNEL "5" CACHE STRING "DW1000 UWB channel number")
set(APP_UWB_PAN_ID "0xDECA" CACHE STRING "DW1000 PAN ID")

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)
target_include_directories(app PRIVATE ${APP_ROOT}/src/anchors/unified)
target_compile_definitions(app PRIVATE
    APP_ANCHOR_ID=${APP_ANCHOR_ID}
    APP_ANCHOR_ROLE=${APP_ANCHOR_ROLE}
    APP_ANCHOR_CONFIG_ADDR=${APP_ANCHOR_CONFIG_ADDR}
    APP_ANCHOR_CONFIG_PAGE_SIZE=${APP_ANCHOR_CONFIG_PAGE_SIZE}
    APP_ANCHOR_BLE_ID_ENABLE=${APP_ANCHOR_BLE_ID_ENABLE}
    APP_ANCHOR_SERIAL_CMD_ENABLE=${APP_ANCHOR_SERIAL_CMD_ENABLE}
    APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS=${APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS}
    APP_ANCHOR_MASTER=${APP_ANCHOR_MASTER}
    APP_ANCHOR_PEER_COUNT=${APP_ANCHOR_PEER_COUNT}
    APP_ANCHOR_PEER_0_ID=${APP_ANCHOR_PEER_0_ID}
    APP_ANCHOR_PEER_1_ID=${APP_ANCHOR_PEER_1_ID}
    APP_ANCHOR_PEER_2_ID=${APP_ANCHOR_PEER_2_ID}
    APP_ANCHOR_USE_AUTO_SCHEDULE=${APP_ANCHOR_USE_AUTO_SCHEDULE}
    APP_ANCHOR_SCHEDULE_MODE=${APP_ANCHOR_SCHEDULE_MODE}
    APP_ANCHOR_ALLOW_TAG_POLLS=${APP_ANCHOR_ALLOW_TAG_POLLS}
    APP_ANCHOR_RESP_DELAY_UUS=${APP_ANCHOR_RESP_DELAY_UUS}
    APP_ANCHOR_VERBOSE_RESPONDER=${APP_ANCHOR_VERBOSE_RESPONDER}
    APP_ANCHOR_VERBOSE_RESPONDER_ERRORS=${APP_ANCHOR_VERBOSE_RESPONDER_ERRORS}
    APP_UWB_CHANNEL=${APP_UWB_CHANNEL}
    APP_UWB_PAN_ID=${APP_UWB_PAN_ID}
)

target_sources(app PRIVATE
    src/main.c
    src/anchor_app.c
    ${APP_ROOT}/src/anchors/unified/anchor_config.c
    ${APP_ROOT}/src/anchors/unified/anchor_ble_id.c
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
