# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION 3.5)

file(MAKE_DIRECTORY
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/tag"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/tag-prefix"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/tag-prefix/tmp"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/tag-prefix/src/tag-stamp"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/tag-prefix/src"
  "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/tag-prefix/src/tag-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/tag-prefix/src/tag-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/127/_sysbuild/sysbuild/images/tag-prefix/src/tag-stamp${cfgdir}") # cfgdir has leading slash
endif()
