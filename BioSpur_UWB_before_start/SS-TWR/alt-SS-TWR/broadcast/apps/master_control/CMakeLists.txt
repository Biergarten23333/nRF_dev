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

project(BioSpur_UWB_master_control)

set(APP_ROOT ${CMAKE_CURRENT_LIST_DIR}/../..)
get_filename_component(NCS_BASE ${ZEPHYR_BASE} DIRECTORY)

set(APP_MASTER_TAG_NAME_PREFIX "BS" CACHE STRING
    "BLE device-name prefix for receiver mode")
set(APP_MASTER_ONE_SHOT_CMD "" CACHE STRING
    "Optional single NUS command sent once after link ready in receiver mode")
if(DEFINED ENV{APP_MASTER_ONE_SHOT_CMD})
  set(APP_MASTER_ONE_SHOT_CMD "$ENV{APP_MASTER_ONE_SHOT_CMD}" CACHE STRING
      "Optional single NUS command sent once after link ready in receiver mode" FORCE)
endif()
set(APP_MASTER_OTA_TARGET_NAME "" CACHE STRING
    "Exact BLE device name to OTA; empty accepts any DFU SMP advertiser")
set(APP_MASTER_OTA_TARGET_NAME_PREFIX "BS" CACHE STRING
    "BLE device-name prefix to OTA when no exact name is provided")
set(APP_MASTER_OTA_TARGET_TOKEN_ID "-1" CACHE STRING
    "BioSpur manufacturer token tag ID to OTA; -1 accepts any token")
set(APP_MASTER_BOOT_PROFILE "neutral" CACHE STRING
    "Boot profile: neutral, anchor, or tag")
set(APP_MASTER_BOOT_TAG_ALLOWLIST "" CACHE STRING
    "Comma-separated BS names accepted by the tag boot profile; empty accepts all BS*")
set(APP_MASTER_TDMA_SLOT_PERIOD_MS "10" CACHE STRING
    "Master TDMA slot period in milliseconds")
set(APP_MASTER_TDMA_SLOT_ACTIVE_MS "9" CACHE STRING
    "Master TDMA slot active window in milliseconds")
set(APP_MASTER_TDMA_SLOT_ACTIVE_US "0" CACHE STRING
    "Optional Master TDMA active window in microseconds; 0 sends ACTIVE_MS only")
set(APP_MASTER_TDMA_MOTION_DEFAULT_HZ "10" CACHE STRING
    "Default TDMA target Hz for motion profile")

target_include_directories(app PRIVATE
    ${APP_ROOT}/include
    ${APP_ROOT}/apps/master/src
    ${APP_ROOT}/apps/master_ota/src
    ${APP_ROOT}/apps/master_ota/generated
    ${NCS_BASE}/modules/lib/zcbor/include
    ${ZEPHYR_BASE}/subsys/mgmt/mcumgr/util/include
)

target_compile_definitions(app PRIVATE
    ZCBOR_CANONICAL
    APP_MASTER_TAG_NAME_PREFIX="${APP_MASTER_TAG_NAME_PREFIX}"
    APP_MASTER_ONE_SHOT_CMD="${APP_MASTER_ONE_SHOT_CMD}"
    APP_MASTER_OTA_TARGET_NAME="${APP_MASTER_OTA_TARGET_NAME}"
    APP_MASTER_OTA_TARGET_NAME_PREFIX="${APP_MASTER_OTA_TARGET_NAME_PREFIX}"
    APP_MASTER_OTA_TARGET_TOKEN_ID=${APP_MASTER_OTA_TARGET_TOKEN_ID}
    APP_MASTER_BOOT_PROFILE="${APP_MASTER_BOOT_PROFILE}"
    APP_MASTER_BOOT_TAG_ALLOWLIST="${APP_MASTER_BOOT_TAG_ALLOWLIST}"
    APP_MASTER_TDMA_SLOT_PERIOD_MS=${APP_MASTER_TDMA_SLOT_PERIOD_MS}
    APP_MASTER_TDMA_SLOT_ACTIVE_MS=${APP_MASTER_TDMA_SLOT_ACTIVE_MS}
    APP_MASTER_TDMA_SLOT_ACTIVE_US=${APP_MASTER_TDMA_SLOT_ACTIVE_US}
    APP_MASTER_TDMA_MOTION_DEFAULT_HZ=${APP_MASTER_TDMA_MOTION_DEFAULT_HZ}
)

set_source_files_properties(${APP_ROOT}/apps/master_ota/src/main.c PROPERTIES
    COMPILE_OPTIONS "-Dmain=master_ota_run"
)

target_sources(app PRIVATE
    src/main.c
    ${APP_ROOT}/apps/master/src/master_multi_app.c
    ${APP_ROOT}/apps/master_ota/src/main.c
    ${NCS_BASE}/modules/lib/zcbor/src/zcbor_common.c
    ${NCS_BASE}/modules/lib/zcbor/src/zcbor_encode.c
    ${NCS_BASE}/modules/lib/zcbor/src/zcbor_decode.c
    ${ZEPHYR_BASE}/subsys/mgmt/mcumgr/util/src/zcbor_bulk.c
)
