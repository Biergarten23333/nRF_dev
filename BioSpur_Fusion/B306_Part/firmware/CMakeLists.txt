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

set(BSF_FW_MARKER "b306-imu-relay-v42")
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

target_sources(app PRIVATE
  src/main.c
  src/imu.c
  src/strobe_capture.c
)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../include)
target_compile_definitions(app PRIVATE
	BSF_TIMER_COUNTER_BITS=${BSF_TIMER_COUNTER_BITS}
	BSF_FW_MARKER="${BSF_FW_MARKER}"
	BSF_BOOT_CONFIRM_ENABLED=${BSF_BOOT_CONFIRM_ENABLED}
)
