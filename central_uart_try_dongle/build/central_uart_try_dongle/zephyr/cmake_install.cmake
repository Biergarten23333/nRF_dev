# Install script for directory: /home/zekaixiao/ncs/v2.8.0/zephyr

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "/usr/local")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "MinSizeRel")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "TRUE")
endif()

# Set default install directory permissions.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk/arm-zephyr-eabi/bin/arm-zephyr-eabi-objdump")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/arch/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/lib/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/soc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/boards/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/subsys/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/drivers/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/nrf/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/mcuboot/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/mbedtls/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/trusted-firmware-m/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/cjson/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/azure-sdk-for-c/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/cirrus-logic/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/openthread/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/suit-processor/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/memfault-firmware-sdk/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/canopennode/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/chre/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/lz4/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/nanopb/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/zscilib/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/cmsis/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/cmsis-dsp/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/cmsis-nn/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/fatfs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/hal_nordic/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/hal_st/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/hal_wurthelektronik/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/hostap/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/libmetal/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/liblc3/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/littlefs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/loramac-node/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/lvgl/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/mipi-sys-t/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/nrf_hw_models/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/open-amp/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/picolibc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/segger/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/tinycrypt/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/uoscore-uedhoc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/zcbor/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/nrfxlib/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/modules/connectedhomeip/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/kernel/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/cmake/flash/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/cmake/usage/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("/home/zekaixiao/Documents/nRF_dev/central_uart_try_dongle/build/central_uart_try_dongle/zephyr/cmake/reports/cmake_install.cmake")
endif()

