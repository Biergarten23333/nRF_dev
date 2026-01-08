# cmake/toolchain.cmake
set(CMAKE_OBJCOPY arm-zephyr-eabi-objcopy)
set(CMAKE_OBJCOPY_WORKS TRUE)
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(timer_blink)

target_sources(app PRIVATE src/main.c)
