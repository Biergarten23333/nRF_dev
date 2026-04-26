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
set(APP_LISTENER_UF_TRACE_ENABLE "1" CACHE STRING "Emit compact UF; records for all BioSpur UWB frames")
set(APP_LISTENER_UF_TRACE_EVERY "1" CACHE STRING "Emit one compact UF record every N BioSpur frames")
set(APP_LISTENER_UL_TRACE_ENABLE "1" CACHE STRING "Emit compact UL; serial records for machine parsing")
set(APP_LISTENER_UL_TRACE_EVERY "1" CACHE STRING "Emit one compact UL record every N accepted anchor frames")
if(CONFIG_APP_LISTENER_UI_ENABLE)
  set(APP_LISTENER_UI_ENABLE 1)
else()
  set(APP_LISTENER_UI_ENABLE 0)
endif()
if(CONFIG_APP_LISTENER_STATUS_PRINT_ENABLE)
  set(APP_LISTENER_STATUS_PRINT_ENABLE 1)
else()
  set(APP_LISTENER_STATUS_PRINT_ENABLE 0)
endif()
if(CONFIG_APP_LISTENER_DEBUG_PRINT_ENABLE)
  set(APP_LISTENER_DEBUG_PRINT_ENABLE 1)
else()
  set(APP_LISTENER_DEBUG_PRINT_ENABLE 0)
endif()

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)

target_compile_definitions(app PRIVATE
    APP_UWB_CHANNEL=${APP_UWB_CHANNEL}
    APP_UWB_PAN_ID=${APP_UWB_PAN_ID}
    APP_LISTENER_UF_TRACE_ENABLE=${APP_LISTENER_UF_TRACE_ENABLE}
    APP_LISTENER_UF_TRACE_EVERY=${APP_LISTENER_UF_TRACE_EVERY}
    APP_LISTENER_UL_TRACE_ENABLE=${APP_LISTENER_UL_TRACE_ENABLE}
    APP_LISTENER_UL_TRACE_EVERY=${APP_LISTENER_UL_TRACE_EVERY}
    APP_LISTENER_UI_ENABLE=${APP_LISTENER_UI_ENABLE}
    APP_LISTENER_STATUS_PRINT_ENABLE=${APP_LISTENER_STATUS_PRINT_ENABLE}
    APP_LISTENER_DEBUG_PRINT_ENABLE=${APP_LISTENER_DEBUG_PRINT_ENABLE}
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
