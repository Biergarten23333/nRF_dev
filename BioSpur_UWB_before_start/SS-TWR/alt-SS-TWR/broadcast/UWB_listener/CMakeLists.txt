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

project(BioSpur_UWB_listener)

set(APP_ROOT ${CMAKE_CURRENT_LIST_DIR}/..)

set(APP_UWB_CHANNEL "5" CACHE STRING "DW1000 UWB channel number")
set(APP_UWB_PAN_ID "0xDECA" CACHE STRING "DW1000 PAN ID")
set(APP_LISTENER_ID "255" CACHE STRING "Logical listener id 0..7, or 255 when host-mapped")
set(APP_LISTENER_NEAR_ANCHOR_ID "255" CACHE STRING "Near-anchor id 0..7, or 255 to accept every broadcast poll")
# Position-calibration tag mode (MODE_TAG). TAG_ADDR is the ON-AIR 16-bit source
# address: it MUST be in the tag range 0xB100..0xB1FF or the anchors reject the
# poll (ss_twr_resp.c uwb_short_addr_is_ranging_initiator). TAG_LABEL is only the
# host-facing logical id echoed in "MODE=TAG src=" / "LTAG;src=". Per-listener map:
#   LB 0xB1C1/0xc001  LE 0xB1C2/0xc002  LF 0xB1C3/0xc003  LA 0xB1C4/0xc004
#   LCCF4 0xB1C5/0xc005  L9336 0xB1C6/0xc006  L955A 0xB1C7/0xc007
set(APP_LISTENER_TAG_ADDR "0xB1C0" CACHE STRING "On-air tag src addr in MODE_TAG (0xB100..0xB1FF)")
set(APP_LISTENER_TAG_LABEL "0xC000" CACHE STRING "Host-facing logical listener id (0xc0xx) printed in MODE_TAG/LTAG")
set(APP_LISTENER_POLL_DIAG_ENABLE "1" CACHE STRING "Emit LPD poll diagnostic rows")
set(APP_LISTENER_CIR_CAPTURE_ENABLE "0" CACHE STRING "Read ACC_MEM for accepted poll frames")
set(APP_LISTENER_CIR_SAMPLE_PERIOD "10" CACHE STRING "Capture one full CIR every N accepted poll frames")
set(APP_LISTENER_CIR_CHUNK_BYTES "48" CACHE STRING "Hex bytes per LCIRD chunk")
set(APP_LISTENER_POST_CIR_IDLE_MS "12" CACHE STRING "Idle after full CIR capture so same-burst responses cannot overwrite accumulator")
set(APP_LISTENER_STATUS_PRINT_ENABLE "1" CACHE STRING "Print low-rate listener status")

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)

target_compile_definitions(app PRIVATE
    APP_UWB_CHANNEL=${APP_UWB_CHANNEL}
    APP_UWB_PAN_ID=${APP_UWB_PAN_ID}
    APP_LISTENER_ID=${APP_LISTENER_ID}
    APP_LISTENER_NEAR_ANCHOR_ID=${APP_LISTENER_NEAR_ANCHOR_ID}
    APP_LISTENER_TAG_ADDR=${APP_LISTENER_TAG_ADDR}
    APP_LISTENER_TAG_LABEL=${APP_LISTENER_TAG_LABEL}
    APP_LISTENER_POLL_DIAG_ENABLE=${APP_LISTENER_POLL_DIAG_ENABLE}
    APP_LISTENER_CIR_CAPTURE_ENABLE=${APP_LISTENER_CIR_CAPTURE_ENABLE}
    APP_LISTENER_CIR_SAMPLE_PERIOD=${APP_LISTENER_CIR_SAMPLE_PERIOD}
    APP_LISTENER_CIR_CHUNK_BYTES=${APP_LISTENER_CIR_CHUNK_BYTES}
    APP_LISTENER_POST_CIR_IDLE_MS=${APP_LISTENER_POST_CIR_IDLE_MS}
    APP_LISTENER_STATUS_PRINT_ENABLE=${APP_LISTENER_STATUS_PRINT_ENABLE}
    APP_TAG_USB_DIAG_TRACE=0
)

target_sources(app PRIVATE
    src/main.c
    ${APP_ROOT}/src/uwb_bringup.c
    ${APP_ROOT}/src/uwb_port.c
    ${APP_ROOT}/src/uwb_ss_twr_shared.c
    ${APP_ROOT}/drivers/dw1000/src/deca_device.c
    ${APP_ROOT}/drivers/dw1000/src/deca_params_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_range_tables.c
)
