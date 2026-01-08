cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(central_uart)

# NORDIC SDK APP START
target_sources(app PRIVATE
  src/central_main.c
  src/app_ble.c
  src/app_cmd.c
  src/cdc_async.c
)
# NORDIC SDK APP END

target_include_directories(app PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)

zephyr_library_include_directories(${ZEPHYR_BASE}/subsys/bluetooth/services)
