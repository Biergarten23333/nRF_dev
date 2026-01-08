cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(Zentral_Sensorhub)

# 源文件
target_sources(app PRIVATE
  src/main.c
  src/device_id.c
  src/ble_link.c
  src/imu_task.c
  src/uwb_task.c
  src/ecg_task.c
)

# 让编译器能找到你自己的 .h 文件
target_include_directories(app PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)
