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

project(BioSpur_BLE_Listener_SoftDFU)

# Reuse the audited observer implementation without editing the frozen UWB
# workspace. This Fusion-local add-on replaces the lossy 100 ms polling
# assumption with an event-driven CDC SET_LINE_CODING callback and uses the
# PCA10059's P0.19-to-RESET self-reset wiring.
set(UPSTREAM_LISTENER_MAIN
    "${CMAKE_CURRENT_LIST_DIR}/../../../../BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/apps/ble_listener/src/main.c")

target_sources(app PRIVATE
  ${UPSTREAM_LISTENER_MAIN}
  src/soft_dfu.c
)
