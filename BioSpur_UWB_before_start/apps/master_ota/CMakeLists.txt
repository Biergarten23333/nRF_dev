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

project(BioSpur_UWB_master_ota)

set(APP_MASTER_OTA_TARGET_NAME "" CACHE STRING
    "Exact BLE device name to OTA; empty accepts any DFU SMP advertiser")
set(APP_MASTER_OTA_TARGET_NAME_PREFIX "BS" CACHE STRING
    "BLE device-name prefix to OTA when no exact name is provided")
set(APP_MASTER_OTA_TARGET_TOKEN_ID "-1" CACHE STRING
    "BioSpur manufacturer token tag ID to OTA; -1 accepts any token")

get_filename_component(NCS_BASE ${ZEPHYR_BASE} DIRECTORY)

target_include_directories(app PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
    ${CMAKE_CURRENT_LIST_DIR}/generated
    ${NCS_BASE}/modules/lib/zcbor/include
    ${ZEPHYR_BASE}/subsys/mgmt/mcumgr/util/include
)

target_compile_definitions(app PRIVATE
    ZCBOR_CANONICAL
    APP_MASTER_OTA_TARGET_NAME=\"${APP_MASTER_OTA_TARGET_NAME}\"
    APP_MASTER_OTA_TARGET_NAME_PREFIX=\"${APP_MASTER_OTA_TARGET_NAME_PREFIX}\"
    APP_MASTER_OTA_TARGET_TOKEN_ID=${APP_MASTER_OTA_TARGET_TOKEN_ID}
)

target_sources(app PRIVATE
    src/main.c
    ${NCS_BASE}/modules/lib/zcbor/src/zcbor_common.c
    ${NCS_BASE}/modules/lib/zcbor/src/zcbor_encode.c
    ${NCS_BASE}/modules/lib/zcbor/src/zcbor_decode.c
    ${ZEPHYR_BASE}/subsys/mgmt/mcumgr/util/src/zcbor_bulk.c
)
