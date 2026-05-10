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

project(BioSpur_UWB_master)

set(APP_ROOT ${CMAKE_CURRENT_LIST_DIR}/../..)
set(APP_MASTER_ONE_SHOT_CMD "" CACHE STRING "Optional single NUS command sent once after link ready")
set(APP_MASTER_TAG_NAME_PREFIX "BS" CACHE STRING "BLE device-name prefix for receiver mode")
set(APP_MASTER_TDMA_SLOT_PERIOD_MS "40" CACHE STRING "Master TDMA slot period in milliseconds")
set(APP_MASTER_TDMA_SLOT_ACTIVE_MS "24" CACHE STRING "Master TDMA slot active window in milliseconds")
set(APP_MASTER_TDMA_LEGACY_SINGLE_SLOT_ENABLE "0" CACHE STRING "Force one TDMA slot per connected tag in a fixed 10-slot roster")
set(APP_MASTER_TDMA_LEGACY_SLOT_COUNT "10" CACHE STRING "Slot count used when legacy single-slot TDMA mode is enabled")
set(APP_MASTER_SCAN_DEBUG_ALL_ENABLE "0" CACHE STRING "Print all connectable BLE scan candidates before BioSpur filtering")
set(APP_MASTER_TAG_UUID_ONLY_RECOVERY_ENABLE "0" CACHE STRING "Allow an explicit ota_target uuid to connect to a tag advertiser even when BS token is missing")

# Allow operators/scripts to inject one-shot behavior under sysbuild via env.
if(DEFINED ENV{APP_MASTER_ONE_SHOT_CMD})
  set(APP_MASTER_ONE_SHOT_CMD "$ENV{APP_MASTER_ONE_SHOT_CMD}" CACHE STRING
      "Optional single NUS command sent once after link ready" FORCE)
endif()

if(DEFINED ENV{APP_MASTER_TAG_NAME_PREFIX})
  set(APP_MASTER_TAG_NAME_PREFIX "$ENV{APP_MASTER_TAG_NAME_PREFIX}" CACHE STRING
      "BLE device-name prefix for receiver mode" FORCE)
endif()

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)

target_compile_definitions(app PRIVATE
    APP_MASTER_TAG_NAME_PREFIX="${APP_MASTER_TAG_NAME_PREFIX}"
    APP_MASTER_ONE_SHOT_CMD="${APP_MASTER_ONE_SHOT_CMD}"
    APP_MASTER_TDMA_SLOT_PERIOD_MS=${APP_MASTER_TDMA_SLOT_PERIOD_MS}
    APP_MASTER_TDMA_SLOT_ACTIVE_MS=${APP_MASTER_TDMA_SLOT_ACTIVE_MS}
    APP_MASTER_TDMA_LEGACY_SINGLE_SLOT_ENABLE=${APP_MASTER_TDMA_LEGACY_SINGLE_SLOT_ENABLE}
    APP_MASTER_TDMA_LEGACY_SLOT_COUNT=${APP_MASTER_TDMA_LEGACY_SLOT_COUNT}
    APP_MASTER_SCAN_DEBUG_ALL_ENABLE=${APP_MASTER_SCAN_DEBUG_ALL_ENABLE}
    APP_MASTER_TAG_UUID_ONLY_RECOVERY_ENABLE=${APP_MASTER_TAG_UUID_ONLY_RECOVERY_ENABLE}
)

target_sources(app PRIVATE
    src/main.c
    src/master_multi_app.c
)
