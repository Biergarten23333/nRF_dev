cmake_minimum_required(VERSION 3.20.0)

if(NOT ZEPHYR_MODULES)
  execute_process(
    COMMAND west list --format={abspath}
    WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
    OUTPUT_VARIABLE WEST_MODULES_RAW
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE WEST_LIST_RESULT
  )
  if(WEST_LIST_RESULT EQUAL 0 AND WEST_MODULES_RAW)
    string(REPLACE "\n" ";" ZEPHYR_MODULES "${WEST_MODULES_RAW}")
  endif()
endif()

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(BioSpur_UWB_tag_usb)

if(DEFINED ENV{APP_TAG_PRELOAD_FILE} AND EXISTS $ENV{APP_TAG_PRELOAD_FILE})
  include($ENV{APP_TAG_PRELOAD_FILE})
endif()

set(APP_ROOT ${CMAKE_CURRENT_LIST_DIR}/../..)
set(APP_TAG_BLE_ENABLE "0" CACHE STRING "Disable BLE/NUS and OTA transport on the tag")
set(APP_TAG_MCUBOOT_ENABLE "0" CACHE STRING "Disable MCUboot confirm/OTA plumbing on the tag")
set(APP_TAG_USB_DIAG_TRACE "0" CACHE STRING "Enable low-level console trace for pure USB bringup debugging")

if(NOT DEFINED APP_TAG_ID OR APP_TAG_ID STREQUAL "")
set(APP_TAG_ID "0" CACHE STRING "Tag node ID")
endif()
set(APP_UWB_CHANNEL "5" CACHE STRING "DW1000 UWB channel number")
set(APP_UWB_PAN_ID "0xDECA" CACHE STRING "DW1000 PAN ID")
set(APP_TAG_ANCHOR_COUNT "8" CACHE STRING "Number of anchors to range against")
set(APP_TAG_ANCHOR_0_ID "0" CACHE STRING "Anchor slot 0 ID")
set(APP_TAG_ANCHOR_1_ID "1" CACHE STRING "Anchor slot 1 ID")
set(APP_TAG_ANCHOR_2_ID "2" CACHE STRING "Anchor slot 2 ID")
set(APP_TAG_ANCHOR_3_ID "3" CACHE STRING "Anchor slot 3 ID")
set(APP_TAG_ANCHOR_4_ID "4" CACHE STRING "Anchor slot 4 ID")
set(APP_TAG_ANCHOR_5_ID "5" CACHE STRING "Anchor slot 5 ID")
set(APP_TAG_ANCHOR_6_ID "6" CACHE STRING "Anchor slot 6 ID")
set(APP_TAG_ANCHOR_7_ID "7" CACHE STRING "Anchor slot 7 ID")
set(APP_TAG_RNG_DELAY_MS "0" CACHE STRING "Delay between anchor polls in milliseconds")
set(APP_TAG_TX_TO_RX_DLY_UUS "140" CACHE STRING "Initiator RX enable delay after poll TX in UWB usec")
set(APP_TAG_RESP_RX_TIMEOUT_UUS "1500" CACHE STRING "Initiator response RX timeout in UWB usec")
set(APP_TAG_FAST_TRACKING "1" CACHE STRING "Use reduced anchor subset between periodic full sweeps")
set(APP_TAG_FULL_SWEEP_INTERVAL "8" CACHE STRING "Run one full 8-anchor sweep every N sweeps")
set(APP_TAG_TRACK_ANCHOR_COUNT "4" CACHE STRING "Anchor count used in fast tracking sweeps")
set(APP_TAG_SUMMARY_PERIOD "1" CACHE STRING "Print one motion summary every N completed sweeps")
set(APP_TAG_STATUS_PERIOD_MS "0" CACHE STRING "Print one cached position status every N milliseconds (0 disables)")
set(APP_TAG_PENDING_PRINT_PERIOD "0" CACHE STRING "Print one solve-pending line every N failed sweeps (0 disables)")
set(APP_TAG_IMU_SAMPLE_PERIOD "8" CACHE STRING "Read the IMU once every N completed sweeps")
set(APP_TAG_VERBOSE_RANGING "0" CACHE STRING "Print per-anchor ranging logs")
set(APP_TAG_VERBOSE_MEASUREMENTS "0" CACHE STRING "Print per-anchor measurement logs")
set(APP_TAG_VERBOSE_PERF "0" CACHE STRING "Print perf summary lines")
set(APP_TAG_FIXED_MODE "0" CACHE STRING "Use a fixed anchor subset instead of adaptive reselection")
set(APP_TAG_LOC_MIN_QUALITY_PERCENT "50" CACHE STRING "Minimum per-anchor quality percentage accepted by the solver")
set(APP_TAG_FIXED_ANCHOR_COUNT "4" CACHE STRING "Number of anchors in fixed-anchor mode")
set(APP_TAG_FIXED_ANCHOR_0_ID "1" CACHE STRING "Fixed-anchor slot 0 ID")
set(APP_TAG_FIXED_ANCHOR_1_ID "3" CACHE STRING "Fixed-anchor slot 1 ID")
set(APP_TAG_FIXED_ANCHOR_2_ID "5" CACHE STRING "Fixed-anchor slot 2 ID")
set(APP_TAG_FIXED_ANCHOR_3_ID "6" CACHE STRING "Fixed-anchor slot 3 ID")
set(APP_TAG_MULTITAG_PLAN_MODE "0" CACHE STRING "Enable active/standby/reserve multi-tag anchor plan")
set(APP_TAG_ACTIVE_ANCHOR_0_ID "0" CACHE STRING "Multi-tag active anchor slot 0 ID")
set(APP_TAG_ACTIVE_ANCHOR_1_ID "1" CACHE STRING "Multi-tag active anchor slot 1 ID")
set(APP_TAG_ACTIVE_ANCHOR_2_ID "4" CACHE STRING "Multi-tag active anchor slot 2 ID")
set(APP_TAG_ACTIVE_ANCHOR_3_ID "5" CACHE STRING "Multi-tag active anchor slot 3 ID")
set(APP_TAG_STANDBY_ANCHOR_0_ID "2" CACHE STRING "Multi-tag standby anchor slot 0 ID")
set(APP_TAG_STANDBY_ANCHOR_1_ID "6" CACHE STRING "Multi-tag standby anchor slot 1 ID")
set(APP_TAG_RESERVE_ANCHOR_0_ID "3" CACHE STRING "Multi-tag reserve anchor slot 0 ID")
set(APP_TAG_RESERVE_ANCHOR_1_ID "7" CACHE STRING "Multi-tag reserve anchor slot 1 ID")
set(APP_TAG_REFRESH_ANCHOR_BUDGET "1" CACHE STRING "Additional refresh anchors measured on refresh sweeps")
set(APP_TAG_REFRESH_INTERVAL "10" CACHE STRING "Run one refresh sweep every N sweeps in multi-tag mode")
set(APP_TAG_MAINTENANCE_FULL_INTERVAL "100" CACHE STRING "Run one full 8-anchor maintenance sweep every N sweeps in multi-tag mode")
set(APP_TAG_TDMA_ENABLE "0" CACHE STRING "Enable TDMA slot gating for this tag")
set(APP_TAG_TDMA_SLOT_INDEX "0" CACHE STRING "Zero-based TDMA slot index for this tag")
set(APP_TAG_TDMA_SLOT_COUNT "1" CACHE STRING "Total TDMA slot count in the cycle")
set(APP_TAG_TDMA_SLOT_PERIOD_MS "25" CACHE STRING "TDMA slot period in milliseconds")
set(APP_TAG_TDMA_SLOT_ACTIVE_MS "20" CACHE STRING "Usable active time within each slot in milliseconds")
set(APP_TAG_EKF_ENABLE "1" CACHE STRING "Enable the post-solve linear position filter (legacy EKF name)")
set(APP_TAG_EKF_MEAS_STD_MM "35" CACHE STRING "Base measurement standard deviation for the post-solve position filter")
set(APP_TAG_EKF_RESIDUAL_GAIN_PCT "0" CACHE STRING "Additional measurement std as a percent of residual hint")
set(APP_TAG_EKF_PROC_ACCEL_MM_S2 "500" CACHE STRING "Process acceleration standard deviation for the post-solve position filter")
set(APP_TAG_EKF_INIT_POS_STD_MM "200" CACHE STRING "Initial position standard deviation for the post-solve position filter")
set(APP_TAG_EKF_INIT_VEL_STD_MM_S "1200" CACHE STRING "Initial velocity standard deviation for the post-solve position filter")
set(APP_TAG_EKF_OUTLIER_GATE_MM "120" CACHE STRING "Reject position updates larger than this gate in millimeters (0 disables)")
set(APP_TAG_RANGE_SOFT_RESIDUAL_MM "180" CACHE STRING "Soft residual gate for per-anchor range continuity weighting")
set(APP_TAG_RANGE_HARD_RESIDUAL_MM "350" CACHE STRING "Hard residual gate for per-anchor range continuity rejection")
set(APP_TAG_RANGE_CONTINUITY_ENABLE "1" CACHE STRING "Enable per-anchor range continuity gating")
set(APP_TAG_MOTION_FULL_SWEEP_INTERVAL "0" CACHE STRING "Use a shorter full-sweep interval while in dynamic context (0 disables)")
set(APP_TAG_MOTION_SPEED_THRESHOLD_MM_S "100" CACHE STRING "Recent speed threshold used to classify dynamic context")
set(APP_TAG_MOTION_RANGE_SOFT_BONUS_MM "140" CACHE STRING "Additional continuity soft gate while in dynamic context")
set(APP_TAG_MOTION_RANGE_HARD_BONUS_MM "260" CACHE STRING "Additional continuity hard gate while in dynamic context")
set(APP_TAG_MOTION_EKF_MEAS_STD_MM "35" CACHE STRING "Override measurement std while in dynamic context (0 keeps base)")
set(APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2 "1800" CACHE STRING "Override process acceleration std while in dynamic context (0 keeps base)")
set(APP_TAG_MOTION_EKF_OUTLIER_GATE_MM "220" CACHE STRING "Override outlier gate while in dynamic context (0 keeps base)")
set(APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG "250" CACHE STRING "IMU delta threshold used to classify dynamic context")
set(APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG "140" CACHE STRING "IMU gravity-error threshold used to classify dynamic context")

target_include_directories(app PRIVATE ${APP_ROOT}/include)
target_include_directories(app PRIVATE ${APP_ROOT}/drivers/dw1000/include)
target_include_directories(app PRIVATE ${CMAKE_CURRENT_LIST_DIR}/../tag/src)
target_compile_definitions(app PRIVATE
    APP_TAG_BLE_ENABLE=${APP_TAG_BLE_ENABLE}
    APP_TAG_USB_DIAG_TRACE=${APP_TAG_USB_DIAG_TRACE}
    APP_TAG_ID=${APP_TAG_ID}
    APP_TAG_ANCHOR_COUNT=${APP_TAG_ANCHOR_COUNT}
    APP_TAG_ANCHOR_0_ID=${APP_TAG_ANCHOR_0_ID}
    APP_TAG_ANCHOR_1_ID=${APP_TAG_ANCHOR_1_ID}
    APP_TAG_ANCHOR_2_ID=${APP_TAG_ANCHOR_2_ID}
    APP_TAG_ANCHOR_3_ID=${APP_TAG_ANCHOR_3_ID}
    APP_TAG_ANCHOR_4_ID=${APP_TAG_ANCHOR_4_ID}
    APP_TAG_ANCHOR_5_ID=${APP_TAG_ANCHOR_5_ID}
    APP_TAG_ANCHOR_6_ID=${APP_TAG_ANCHOR_6_ID}
    APP_TAG_ANCHOR_7_ID=${APP_TAG_ANCHOR_7_ID}
    APP_TAG_RNG_DELAY_MS=${APP_TAG_RNG_DELAY_MS}
    APP_TAG_TX_TO_RX_DLY_UUS=${APP_TAG_TX_TO_RX_DLY_UUS}
    APP_TAG_RESP_RX_TIMEOUT_UUS=${APP_TAG_RESP_RX_TIMEOUT_UUS}
    APP_TAG_FAST_TRACKING=${APP_TAG_FAST_TRACKING}
    APP_TAG_FULL_SWEEP_INTERVAL=${APP_TAG_FULL_SWEEP_INTERVAL}
    APP_TAG_TRACK_ANCHOR_COUNT=${APP_TAG_TRACK_ANCHOR_COUNT}
    APP_TAG_SUMMARY_PERIOD=${APP_TAG_SUMMARY_PERIOD}
    APP_TAG_STATUS_PERIOD_MS=${APP_TAG_STATUS_PERIOD_MS}
    APP_TAG_PENDING_PRINT_PERIOD=${APP_TAG_PENDING_PRINT_PERIOD}
    APP_TAG_IMU_SAMPLE_PERIOD=${APP_TAG_IMU_SAMPLE_PERIOD}
    APP_TAG_VERBOSE_RANGING=${APP_TAG_VERBOSE_RANGING}
    APP_TAG_VERBOSE_MEASUREMENTS=${APP_TAG_VERBOSE_MEASUREMENTS}
    APP_TAG_VERBOSE_PERF=${APP_TAG_VERBOSE_PERF}
    APP_TAG_LOC_MIN_QUALITY_PERCENT=${APP_TAG_LOC_MIN_QUALITY_PERCENT}
    APP_UWB_CHANNEL=${APP_UWB_CHANNEL}
    APP_UWB_PAN_ID=${APP_UWB_PAN_ID}
    APP_TAG_FIXED_MODE=${APP_TAG_FIXED_MODE}
    APP_TAG_FIXED_ANCHOR_COUNT=${APP_TAG_FIXED_ANCHOR_COUNT}
    APP_TAG_FIXED_ANCHOR_0_ID=${APP_TAG_FIXED_ANCHOR_0_ID}
    APP_TAG_FIXED_ANCHOR_1_ID=${APP_TAG_FIXED_ANCHOR_1_ID}
    APP_TAG_FIXED_ANCHOR_2_ID=${APP_TAG_FIXED_ANCHOR_2_ID}
    APP_TAG_FIXED_ANCHOR_3_ID=${APP_TAG_FIXED_ANCHOR_3_ID}
    APP_TAG_MULTITAG_PLAN_MODE=${APP_TAG_MULTITAG_PLAN_MODE}
    APP_TAG_ACTIVE_ANCHOR_0_ID=${APP_TAG_ACTIVE_ANCHOR_0_ID}
    APP_TAG_ACTIVE_ANCHOR_1_ID=${APP_TAG_ACTIVE_ANCHOR_1_ID}
    APP_TAG_ACTIVE_ANCHOR_2_ID=${APP_TAG_ACTIVE_ANCHOR_2_ID}
    APP_TAG_ACTIVE_ANCHOR_3_ID=${APP_TAG_ACTIVE_ANCHOR_3_ID}
    APP_TAG_STANDBY_ANCHOR_0_ID=${APP_TAG_STANDBY_ANCHOR_0_ID}
    APP_TAG_STANDBY_ANCHOR_1_ID=${APP_TAG_STANDBY_ANCHOR_1_ID}
    APP_TAG_RESERVE_ANCHOR_0_ID=${APP_TAG_RESERVE_ANCHOR_0_ID}
    APP_TAG_RESERVE_ANCHOR_1_ID=${APP_TAG_RESERVE_ANCHOR_1_ID}
    APP_TAG_REFRESH_ANCHOR_BUDGET=${APP_TAG_REFRESH_ANCHOR_BUDGET}
    APP_TAG_REFRESH_INTERVAL=${APP_TAG_REFRESH_INTERVAL}
    APP_TAG_MAINTENANCE_FULL_INTERVAL=${APP_TAG_MAINTENANCE_FULL_INTERVAL}
    APP_TAG_TDMA_ENABLE=${APP_TAG_TDMA_ENABLE}
    APP_TAG_TDMA_SLOT_INDEX=${APP_TAG_TDMA_SLOT_INDEX}
    APP_TAG_TDMA_SLOT_COUNT=${APP_TAG_TDMA_SLOT_COUNT}
    APP_TAG_TDMA_SLOT_PERIOD_MS=${APP_TAG_TDMA_SLOT_PERIOD_MS}
    APP_TAG_TDMA_SLOT_ACTIVE_MS=${APP_TAG_TDMA_SLOT_ACTIVE_MS}
    APP_TAG_EKF_ENABLE=${APP_TAG_EKF_ENABLE}
    APP_TAG_EKF_MEAS_STD_MM=${APP_TAG_EKF_MEAS_STD_MM}
    APP_TAG_EKF_RESIDUAL_GAIN_PCT=${APP_TAG_EKF_RESIDUAL_GAIN_PCT}
    APP_TAG_EKF_PROC_ACCEL_MM_S2=${APP_TAG_EKF_PROC_ACCEL_MM_S2}
    APP_TAG_EKF_INIT_POS_STD_MM=${APP_TAG_EKF_INIT_POS_STD_MM}
    APP_TAG_EKF_INIT_VEL_STD_MM_S=${APP_TAG_EKF_INIT_VEL_STD_MM_S}
    APP_TAG_EKF_OUTLIER_GATE_MM=${APP_TAG_EKF_OUTLIER_GATE_MM}
    APP_TAG_RANGE_SOFT_RESIDUAL_MM=${APP_TAG_RANGE_SOFT_RESIDUAL_MM}
    APP_TAG_RANGE_HARD_RESIDUAL_MM=${APP_TAG_RANGE_HARD_RESIDUAL_MM}
    APP_TAG_RANGE_CONTINUITY_ENABLE=${APP_TAG_RANGE_CONTINUITY_ENABLE}
    APP_TAG_MOTION_FULL_SWEEP_INTERVAL=${APP_TAG_MOTION_FULL_SWEEP_INTERVAL}
    APP_TAG_MOTION_SPEED_THRESHOLD_MM_S=${APP_TAG_MOTION_SPEED_THRESHOLD_MM_S}
    APP_TAG_MOTION_RANGE_SOFT_BONUS_MM=${APP_TAG_MOTION_RANGE_SOFT_BONUS_MM}
    APP_TAG_MOTION_RANGE_HARD_BONUS_MM=${APP_TAG_MOTION_RANGE_HARD_BONUS_MM}
    APP_TAG_MOTION_EKF_MEAS_STD_MM=${APP_TAG_MOTION_EKF_MEAS_STD_MM}
    APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2=${APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2}
    APP_TAG_MOTION_EKF_OUTLIER_GATE_MM=${APP_TAG_MOTION_EKF_OUTLIER_GATE_MM}
    APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG=${APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG}
    APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG=${APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG}
    APP_TAG_MCUBOOT_ENABLE=${APP_TAG_MCUBOOT_ENABLE}
)

target_sources(app PRIVATE
    ../tag/src/main.c
    ../tag/src/tag_app.c
    ${APP_ROOT}/src/uwb_bringup.c
    ${APP_ROOT}/src/uwb_port.c
    ${APP_ROOT}/src/uwb_anchor_layout.c
    ${APP_ROOT}/src/uwb_imu.c
    ${APP_ROOT}/src/uwb_ekf.c
    ${APP_ROOT}/src/uwb_motion.c
    ${APP_ROOT}/src/uwb_ss_twr_shared.c
    ${APP_ROOT}/src/uwb_tdma.c
    ${APP_ROOT}/src/uwb_range_tracker.c
    ${APP_ROOT}/src/uwb_tag_loc.c
    ${APP_ROOT}/src/ss_twr_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_device.c
    ${APP_ROOT}/drivers/dw1000/src/deca_params_init.c
    ${APP_ROOT}/drivers/dw1000/src/deca_range_tables.c
)
