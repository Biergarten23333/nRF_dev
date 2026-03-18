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
set(APP_ANCHOR_ID "4" CACHE STRING "Anchor node ID")
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

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)
target_compile_definitions(app PRIVATE
    APP_ANCHOR_ID=${APP_ANCHOR_ID}
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
)

target_sources(app PRIVATE
    src/main.c
    src/anchor_app.c
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
