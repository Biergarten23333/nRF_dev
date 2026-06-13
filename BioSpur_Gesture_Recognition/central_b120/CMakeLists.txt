cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(biospur_gr_master)

get_filename_component(NCS_BASE ${ZEPHYR_BASE} DIRECTORY)

target_sources(app PRIVATE
  src/main.c
)

target_include_directories(app PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../shared/include
  ${CMAKE_CURRENT_SOURCE_DIR}/generated
  ${NCS_BASE}/modules/lib/zcbor/include
)

target_compile_definitions(app PRIVATE
  ZCBOR_CANONICAL
)
