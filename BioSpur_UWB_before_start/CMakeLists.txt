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

project(BioSpur_UWB_before_start)

target_include_directories(app PRIVATE include)
target_include_directories(app PRIVATE drivers/dw1000/include)
target_include_directories(app PRIVATE apps/anchor/src)

target_sources(app PRIVATE
    apps/anchor/src/main.c
    apps/anchor/src/anchor_app.c
    src/uwb_bringup.c
    src/uwb_port.c
    src/uwb_ss_twr_shared.c
    src/uwb_range_tracker.c
    src/ss_twr_resp.c
    drivers/dw1000/src/deca_device.c
    drivers/dw1000/src/deca_params_init.c
    drivers/dw1000/src/deca_range_tables.c
)
