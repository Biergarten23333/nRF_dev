# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION 3.5)

file(MAKE_DIRECTORY
  "/home/zekaixiao/ncs/v2.8.0/bootloader/mcuboot/boot/zephyr"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/mcuboot"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/bootloader/mcuboot-prefix"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/bootloader/mcuboot-prefix/tmp"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/bootloader/mcuboot-prefix/src/mcuboot-stamp"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/bootloader/mcuboot-prefix/src"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/bootloader/mcuboot-prefix/src/mcuboot-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/bootloader/mcuboot-prefix/src/mcuboot-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/bootloader/mcuboot-prefix/src/mcuboot-stamp${cfgdir}") # cfgdir has leading slash
endif()
