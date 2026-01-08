cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(ADS1298_Test)
target_include_directories(app PRIVATE include)

target_sources(app PRIVATE
  src/main.c
  src/ads1298.c
  src/imu_task.c
  src/ble_link.c
  src/device_id.c
)

