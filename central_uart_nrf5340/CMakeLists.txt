cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(central_uart_nrf5340)

target_sources(app PRIVATE
  central_main.c
  app_ble.c
  app_cmd.c
)

# 让 app_ble.h / app_cmd.h 能被 include（当前目录）
zephyr_library_include_directories(${CMAKE_CURRENT_SOURCE_DIR})
