cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
find_package(Python3 REQUIRED COMPONENTS Interpreter)
find_program(PATCH_EXECUTABLE patch REQUIRED)
project(biospur_fusion_fast_ota)

set(fusion_root "${CMAKE_CURRENT_SOURCE_DIR}/../../..")
set(freeze_ota_root
    "${fusion_root}/UWB_Part/2026-07-15-FREEZE/firmware/src/apps/master_ota")
set(freeze_main "${freeze_ota_root}/src/main.c")
set(freeze_header "${freeze_ota_root}/src/master_ota.h")

# The permanent B306 updater is a pinned port of the proven UWB fast updater,
# not an independent SMP implementation. Refuse an unnoticed source drift.
set(expected_freeze_main_sha256
    "9613d746a102afa9e0ea5943e1ea0074bd24b3445051be0fc2c2a51a1a880906")
set(expected_freeze_header_sha256
    "b30d1e3635b4ab1e00c2c3cad145564c5742f24c7cc6dbd194dfb488af611012")
file(SHA256 "${freeze_main}" actual_freeze_main_sha256)
file(SHA256 "${freeze_header}" actual_freeze_header_sha256)
if(NOT actual_freeze_main_sha256 STREQUAL expected_freeze_main_sha256)
  message(FATAL_ERROR
          "Frozen fast OTA source changed: expected ${expected_freeze_main_sha256}, got ${actual_freeze_main_sha256}")
endif()
if(NOT actual_freeze_header_sha256 STREQUAL expected_freeze_header_sha256)
  message(FATAL_ERROR
          "Frozen fast OTA header changed: expected ${expected_freeze_header_sha256}, got ${actual_freeze_header_sha256}")
endif()

# NCS sysbuild configures this application as a child image and does not
# forward arbitrary top-level -D cache entries. Environment values are
# inherited, so import the four mandatory destructive-operation inputs here.
if(DEFINED ENV{B306_OTA_IMAGE})
  set(B306_OTA_IMAGE "$ENV{B306_OTA_IMAGE}" CACHE FILEPATH "" FORCE)
endif()
if(DEFINED ENV{B306_OTA_IMAGE_SHA256})
  set(B306_OTA_IMAGE_SHA256 "$ENV{B306_OTA_IMAGE_SHA256}" CACHE STRING "" FORCE)
endif()
if(DEFINED ENV{B306_OTA_MARKER})
  set(B306_OTA_MARKER "$ENV{B306_OTA_MARKER}" CACHE STRING "" FORCE)
endif()
if(DEFINED ENV{B306_OTA_TARGET_NAME})
  set(B306_OTA_TARGET_NAME "$ENV{B306_OTA_TARGET_NAME}" CACHE STRING "" FORCE)
endif()
if(DEFINED ENV{B306_OTA_VERIFY_ONLY})
  set(B306_OTA_VERIFY_ONLY "$ENV{B306_OTA_VERIFY_ONLY}" CACHE BOOL "" FORCE)
endif()
if(DEFINED ENV{B306_OTA_RESET_ONLY})
  set(B306_OTA_RESET_ONLY "$ENV{B306_OTA_RESET_ONLY}" CACHE BOOL "" FORCE)
endif()

if(B306_OTA_RESET_ONLY)
  set(b306_ota_upload_enable 0)
  set(b306_ota_verify_only 0)
  set(b306_ota_reset_only 1)
elseif(B306_OTA_VERIFY_ONLY)
  set(b306_ota_upload_enable 0)
  set(b306_ota_verify_only 1)
  set(b306_ota_reset_only 0)
else()
  set(b306_ota_upload_enable 1)
  set(b306_ota_verify_only 0)
  set(b306_ota_reset_only 0)
endif()

foreach(required_var
        B306_OTA_IMAGE
        B306_OTA_IMAGE_SHA256
        B306_OTA_MARKER
        B306_OTA_TARGET_NAME)
  if(NOT DEFINED ${required_var} OR "${${required_var}}" STREQUAL "")
    message(FATAL_ERROR
            "${required_var} is required; destructive OTA builds must pin target, marker, image, and SHA")
  endif()
endforeach()

if(NOT EXISTS "${B306_OTA_IMAGE}")
  message(FATAL_ERROR "B306 OTA image does not exist: ${B306_OTA_IMAGE}")
endif()
string(TOLOWER "${B306_OTA_IMAGE_SHA256}" expected_ota_sha256)
file(SHA256 "${B306_OTA_IMAGE}" actual_ota_sha256)
if(NOT actual_ota_sha256 STREQUAL expected_ota_sha256)
  message(FATAL_ERROR
          "B306 OTA image SHA mismatch: expected ${expected_ota_sha256}, got ${actual_ota_sha256}")
endif()
if(NOT B306_OTA_TARGET_NAME
   MATCHES "^BSF[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]$")
  message(FATAL_ERROR
          "B306_OTA_TARGET_NAME must be an exact BSF%04X identity, got: ${B306_OTA_TARGET_NAME}")
endif()

set(generated_source_dir "${CMAKE_CURRENT_BINARY_DIR}/generated_source")
file(MAKE_DIRECTORY "${generated_source_dir}")
configure_file("${freeze_main}" "${generated_source_dir}/master_ota.c" COPYONLY)

execute_process(
  COMMAND
    "${PATCH_EXECUTABLE}" --batch --forward -p0
    -i "${CMAKE_CURRENT_SOURCE_DIR}/patches/b306_profile.patch"
  WORKING_DIRECTORY "${generated_source_dir}"
  RESULT_VARIABLE patch_result
  OUTPUT_VARIABLE patch_stdout
  ERROR_VARIABLE patch_stderr
)
if(NOT patch_result EQUAL 0)
  message(FATAL_ERROR
          "Failed to apply the B306 profile to the pinned fast OTA source:\n${patch_stdout}\n${patch_stderr}")
endif()

execute_process(
  COMMAND
    "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/apply_post_reset_verify.py"
    "${generated_source_dir}/master_ota.c"
  RESULT_VARIABLE verify_patch_result
  OUTPUT_VARIABLE verify_patch_stdout
  ERROR_VARIABLE verify_patch_stderr
)
if(NOT verify_patch_result EQUAL 0)
  message(FATAL_ERROR
          "Failed to apply post-reset verification to the pinned fast OTA source:\n${verify_patch_stdout}\n${verify_patch_stderr}")
endif()

execute_process(
  COMMAND
    "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/apply_idempotent_state_machine.py"
    "${generated_source_dir}/master_ota.c"
  RESULT_VARIABLE state_machine_patch_result
  OUTPUT_VARIABLE state_machine_patch_stdout
  ERROR_VARIABLE state_machine_patch_stderr
)
if(NOT state_machine_patch_result EQUAL 0)
  message(FATAL_ERROR
          "Failed to apply idempotent state machine to the pinned fast OTA source:\n${state_machine_patch_stdout}\n${state_machine_patch_stderr}")
endif()

execute_process(
  COMMAND
    "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/apply_graduated_read_retry.py"
    "${generated_source_dir}/master_ota.c"
  RESULT_VARIABLE read_retry_patch_result
  OUTPUT_VARIABLE read_retry_patch_stdout
  ERROR_VARIABLE read_retry_patch_stderr
)
if(NOT read_retry_patch_result EQUAL 0)
  message(FATAL_ERROR
          "Failed to apply graduated read retry to the pinned fast OTA source:\n${read_retry_patch_stdout}\n${read_retry_patch_stderr}")
endif()

execute_process(
  COMMAND
    "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/gen_ota_image_inc.py"
    "${B306_OTA_IMAGE}"
    "${generated_source_dir}/ota_image.inc"
    --symbol-prefix tag_ota_image
  RESULT_VARIABLE image_gen_result
  OUTPUT_VARIABLE image_gen_stdout
  ERROR_VARIABLE image_gen_stderr
)
if(NOT image_gen_result EQUAL 0)
  message(FATAL_ERROR
          "Failed to embed the pinned B306 image:\n${image_gen_stdout}\n${image_gen_stderr}")
endif()

get_filename_component(NCS_BASE "${ZEPHYR_BASE}" DIRECTORY)
target_include_directories(app PRIVATE
  "${generated_source_dir}"
  "${CMAKE_CURRENT_SOURCE_DIR}/src"
  "${freeze_ota_root}/src"
  "${NCS_BASE}/modules/lib/zcbor/include"
  "${ZEPHYR_BASE}/subsys/mgmt/mcumgr/util/include"
)
target_compile_definitions(app PRIVATE
  ZCBOR_CANONICAL
  APP_MASTER_OTA_TARGET_NAME="${B306_OTA_TARGET_NAME}"
  APP_MASTER_OTA_TARGET_NAME_PREFIX="BSF"
  APP_MASTER_OTA_TARGET_TOKEN_ID=-1
  APP_MASTER_OTA_UPLOAD_ENABLE=${b306_ota_upload_enable}
  APP_MASTER_OTA_VERIFY_ONLY=${b306_ota_verify_only}
  APP_MASTER_OTA_RESET_ONLY=${b306_ota_reset_only}
  APP_MASTER_OTA_CHUNK_SIZE=448
  APP_MASTER_OTA_BSF_PROFILE=1
  APP_MASTER_OTA_AUTO_START=1
  APP_MASTER_OTA_EXPECT_NUS=0
  APP_MASTER_OTA_PAYLOAD_MARKER="${B306_OTA_MARKER}"
  APP_MASTER_OTA_DIAG_FIRST_GATE_WRITE_REQ=0
)
target_sources(app PRIVATE
  "${generated_source_dir}/master_ota.c"
  "${NCS_BASE}/modules/lib/zcbor/src/zcbor_common.c"
  "${NCS_BASE}/modules/lib/zcbor/src/zcbor_encode.c"
  "${NCS_BASE}/modules/lib/zcbor/src/zcbor_decode.c"
  "${ZEPHYR_BASE}/subsys/mgmt/mcumgr/util/src/zcbor_bulk.c"
)
