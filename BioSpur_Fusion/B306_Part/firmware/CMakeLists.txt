cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(biospur_fusion_b306)

set(BSF_TIMER_COUNTER_BITS 32)
if(DEFINED ENV{BSF_TIMER_COUNTER_BITS})
  set(BSF_TIMER_COUNTER_BITS "$ENV{BSF_TIMER_COUNTER_BITS}")
endif()
if(NOT BSF_TIMER_COUNTER_BITS MATCHES "^(16|24|32)$")
  message(FATAL_ERROR
          "BSF_TIMER_COUNTER_BITS must be exactly 16, 24, or 32")
endif()

set(BSF_FW_MARKER "b306-imu-relay-v43")
if(DEFINED ENV{BSF_FW_MARKER})
  set(BSF_FW_MARKER "$ENV{BSF_FW_MARKER}")
endif()

set(BSF_BOOT_CONFIRM_ENABLED "1")
if(DEFINED ENV{BSF_BOOT_CONFIRM_ENABLED})
  set(BSF_BOOT_CONFIRM_ENABLED "$ENV{BSF_BOOT_CONFIRM_ENABLED}")
endif()
if(NOT BSF_BOOT_CONFIRM_ENABLED MATCHES "^[01]$")
  message(FATAL_ERROR "BSF_BOOT_CONFIRM_ENABLED must be exactly 0 or 1")
endif()


# ---------------------------------------------------------------------------
# v43: the Bluetooth host carries stage instrumentation applied as a patch to
# the SHARED SDK at $ENV{ZEPHYR_BASE}. Refuse to build unless it is applied and
# hashes exactly. An image built against an unpatched host would look normal,
# link (the capture symbol would be missing -- but only at link time, and only
# if something references it), and silently produce blank corpses in the field.
# Gate before compiling, not after.
execute_process(
  COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/patches/host_patch.sh verify
  RESULT_VARIABLE BSF_HOST_PATCH_RC
  OUTPUT_VARIABLE BSF_HOST_PATCH_OUT
  ERROR_VARIABLE  BSF_HOST_PATCH_ERR)
if(NOT BSF_HOST_PATCH_RC EQUAL 0)
  message(FATAL_ERROR
    "BT host stage patch is not applied or does not hash correctly.\n"
    "  ${BSF_HOST_PATCH_ERR}\n"
    "  Run: B306_Part/firmware/patches/host_patch.sh apply")
endif()
message(STATUS "BSF host patch: ${BSF_HOST_PATCH_OUT}")

target_sources(app PRIVATE
  src/main.c
  src/imu.c
  src/strobe_capture.c
)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../include)
# Global, not app-private: subsys/bluetooth/host/conn.c is built as part of
# a Zephyr library and resolves <bsf_bt_stage.h> through zephyr_interface.
zephyr_include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src)
zephyr_include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../include)
target_compile_definitions(app PRIVATE
	BSF_TIMER_COUNTER_BITS=${BSF_TIMER_COUNTER_BITS}
	BSF_FW_MARKER="${BSF_FW_MARKER}"
	BSF_BOOT_CONFIRM_ENABLED=${BSF_BOOT_CONFIRM_ENABLED}
)
