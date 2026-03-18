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
target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src)

target_sources(app PRIVATE
    src/main.c
    src/master_app.c
)
