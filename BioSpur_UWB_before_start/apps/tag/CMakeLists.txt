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

project(BioSpur_UWB_tag)

set(APP_ROOT ${CMAKE_CURRENT_LIST_DIR}/../..)
set(APP_TAG_ID "0" CACHE STRING "Tag node ID")
set(APP_TAG_ANCHOR_COUNT "8" CACHE STRING "Number of anchors to range against")
set(APP_TAG_ANCHOR_0_ID "0" CACHE STRING "Anchor slot 0 ID")
set(APP_TAG_ANCHOR_1_ID "1" CACHE STRING "Anchor slot 1 ID")
set(APP_TAG_ANCHOR_2_ID "2" CACHE STRING "Anchor slot 2 ID")
set(APP_TAG_ANCHOR_3_ID "3" CACHE STRING "Anchor slot 3 ID")
set(APP_TAG_ANCHOR_4_ID "4" CACHE STRING "Anchor slot 4 ID")
set(APP_TAG_ANCHOR_5_ID "5" CACHE STRING "Anchor slot 5 ID")
set(APP_TAG_ANCHOR_6_ID "6" CACHE STRING "Anchor slot 6 ID")
set(APP_TAG_ANCHOR_7_ID "7" CACHE STRING "Anchor slot 7 ID")
set(APP_TAG_RNG_DELAY_MS "100" CACHE STRING "Delay between anchor polls in milliseconds")
set(APP_TAG_VERBOSE_RANGING "0" CACHE STRING "Print per-anchor ranging logs")
set(APP_TAG_VERBOSE_MEASUREMENTS "0" CACHE STRING "Print per-anchor measurement logs")

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)
target_compile_definitions(app PRIVATE
    APP_TAG_ID=${APP_TAG_ID}
    APP_TAG_ANCHOR_COUNT=${APP_TAG_ANCHOR_COUNT}
    APP_TAG_ANCHOR_0_ID=${APP_TAG_ANCHOR_0_ID}
    APP_TAG_ANCHOR_1_ID=${APP_TAG_ANCHOR_1_ID}
    APP_TAG_ANCHOR_2_ID=${APP_TAG_ANCHOR_2_ID}
    APP_TAG_ANCHOR_3_ID=${APP_TAG_ANCHOR_3_ID}
    APP_TAG_ANCHOR_4_ID=${APP_TAG_ANCHOR_4_ID}
    APP_TAG_ANCHOR_5_ID=${APP_TAG_ANCHOR_5_ID}
    APP_TAG_ANCHOR_6_ID=${APP_TAG_ANCHOR_6_ID}
    APP_TAG_ANCHOR_7_ID=${APP_TAG_ANCHOR_7_ID}
    APP_TAG_RNG_DELAY_MS=${APP_TAG_RNG_DELAY_MS}
    APP_TAG_VERBOSE_RANGING=${APP_TAG_VERBOSE_RANGING}
    APP_TAG_VERBOSE_MEASUREMENTS=${APP_TAG_VERBOSE_MEASUREMENTS}
)

target_sources(app PRIVATE
    src/main.c
    src/tag_app.c
    ${APP_ROOT}/src/uwb_bringup.c
    ${APP_ROOT}/src/uwb_port.c
    ${APP_ROOT}/src/uwb_anchor_layout.c
    ${APP_ROOT}/src/uwb_imu.c
    ${APP_ROOT}/src/uwb_motion.c
    ${APP_ROOT}/src/uwb_ss_twr_shared.c
    ${APP_ROOT}/src/uwb_range_tracker.c
    ${APP_ROOT}/src/uwb_tag_loc.c
    ${APP_ROOT}/src/ss_twr_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_device.c
    ${APP_ROOT}/drivers/dw1000/src/deca_params_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_range_tables.c
)
