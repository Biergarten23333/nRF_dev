cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(biospur_fusion_b306)

set(BSF_TIMER_COUNTER_BITS 32)
if(DEFINED ENV{BSF_TIMER_COUNTER_BITS})
  set(BSF_TIMER_COUNTER_BITS "$ENV{BSF_TIMER_COUNTER_BITS}")
endif()
if(NOT BSF_TIMER_COUNTER_BITS MATCHES "^(16|24|32)$")
  message(FATAL_ERROR
          "BSF_TIMER_COUNTER_BITS must be exactly 16, 24, or 32")
endif()

set(BSF_FW_MARKER "b306-imu-relay-v45")
if(DEFINED ENV{BSF_FW_MARKER})
  set(BSF_FW_MARKER "$ENV{BSF_FW_MARKER}")
endif()

# ---------------------------------------------------------------------------
# v45 flash persistence (section 9).
#
# DEFAULT 0, and that is a finding, not an oversight. CONTEXT_AUDIT item 11:
# the deployed pm_static.yml tiles the whole 1 MiB with ZERO bytes free, and the
# only carve that overlaps nothing requires rebuilding and SWD-reflashing
# MCUboot on every board -- which Stage C, being OTA-only, cannot do. The
# implementation is complete and compiles; enabling it is a deliberate SWD
# campaign paired with pm_static_v45_corpse.yml, not a flag flip.
set(BSF_CORPSE_FLASH_ENABLED "0")
if(DEFINED ENV{BSF_CORPSE_FLASH_ENABLED})
  set(BSF_CORPSE_FLASH_ENABLED "$ENV{BSF_CORPSE_FLASH_ENABLED}")
endif()
if(NOT BSF_CORPSE_FLASH_ENABLED MATCHES "^[01]$")
  message(FATAL_ERROR "BSF_CORPSE_FLASH_ENABLED must be exactly 0 or 1")
endif()

# Fault injection has exactly ONE switch, and it is the Kconfig symbol
# CONFIG_BSF_V45_FAULT_INJECT (see Kconfig), set by overlay-validation.conf.
#
# It used to have two: a Kconfig symbol AND a CMake variable of the same name
# exported as a compile definition, with the C code guarding on the latter.
# Setting CONFIG_BSF_V45_FAULT_INJECT=y in a conf file therefore did NOTHING,
# silently, while looking exactly like it had worked. Two switches for one
# behaviour with only one of them wired is precisely the "looks configured,
# isn't" trap this project keeps paying for, so the CMake half is gone.
#
# The upside of keeping the Kconfig half rather than the CMake half: the
# setting lands in the build's own .config, so "production and validation
# differ only by the injection code" is a diffable fact rather than a claim.
if(CONFIG_BSF_V45_FAULT_INJECT)
  message(WARNING
    "CONFIG_BSF_V45_FAULT_INJECT=y: this image can DELIBERATELY INDUCE the "
    "wedge. Single-canary Stage B only -- never a fleet image.")
endif()

set(BSF_BOOT_CONFIRM_ENABLED "1")
if(DEFINED ENV{BSF_BOOT_CONFIRM_ENABLED})
  set(BSF_BOOT_CONFIRM_ENABLED "$ENV{BSF_BOOT_CONFIRM_ENABLED}")
endif()
if(NOT BSF_BOOT_CONFIRM_ENABLED MATCHES "^[01]$")
  message(FATAL_ERROR "BSF_BOOT_CONFIRM_ENABLED must be exactly 0 or 1")
endif()


# ---------------------------------------------------------------------------
# v43: the Bluetooth host carries stage instrumentation applied as a patch to
# the SHARED SDK at $ENV{ZEPHYR_BASE}. Refuse to build unless it is applied and
# hashes exactly. An image built against an unpatched host would look normal,
# link (the capture symbol would be missing -- but only at link time, and only
# if something references it), and silently produce blank corpses in the field.
# Gate before compiling, not after.
#
# v45 extends this to FIVE files across TWO roots (zephyr and nrf), so the
# manager moved to patches/sdk_patch.sh. The gate is unchanged in spirit: refuse
# to compile against an SDK that is neither exactly pristine nor exactly ours.
execute_process(
  COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/patches/sdk_patch.sh verify
  RESULT_VARIABLE BSF_HOST_PATCH_RC
  OUTPUT_VARIABLE BSF_HOST_PATCH_OUT
  ERROR_VARIABLE  BSF_HOST_PATCH_ERR)
if(NOT BSF_HOST_PATCH_RC EQUAL 0)
  message(FATAL_ERROR
    "SDK instrumentation patch is not applied or does not hash correctly.\n"
    "  ${BSF_HOST_PATCH_ERR}\n"
    "  Run: B306_Part/firmware/patches/sdk_patch.sh apply")
endif()
message(STATUS "BSF SDK patch: ${BSF_HOST_PATCH_OUT}")

target_sources(app PRIVATE
  src/main.c
  src/imu.c
  src/strobe_capture.c
  src/bsf_v45.c
  src/bsf_v45_pools.c
)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../include)
# Global, not app-private: subsys/bluetooth/host/conn.c is built as part of
# a Zephyr library and resolves <bsf_bt_stage.h> through zephyr_interface.
zephyr_include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src)
zephyr_include_directories(${CMAKE_CURRENT_SOURCE_DIR}/../include)
target_compile_definitions(app PRIVATE
	BSF_TIMER_COUNTER_BITS=${BSF_TIMER_COUNTER_BITS}
	BSF_FW_MARKER="${BSF_FW_MARKER}"
	BSF_BOOT_CONFIRM_ENABLED=${BSF_BOOT_CONFIRM_ENABLED}
	BSF_CORPSE_FLASH_ENABLED=${BSF_CORPSE_FLASH_ENABLED}
)
