#include "ss_twr_init.h"
#include "broadcast_tdma.h"
#include "uwb_tdma.h"
#include "uwb_imu.h"
#include "uwb_anchor_layout.h"
#include "uwb_ekf.h"
#include "uwb_motion.h"
#if APP_TAG_BLE_ENABLE
#include "uwb_tag_ble.h"
#endif
#include "uwb_range_tracker.h"
#include "uwb_ss_twr_shared.h"
#include "uwb_tag_loc.h"

#include <math.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>

#include <deca_device_api.h>
#include <deca_regs.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#define SS_TWR_INIT_TX_ANT_DLY 16436U
#define SS_TWR_INIT_RX_ANT_DLY 16436U

#ifndef APP_TAG_RNG_DELAY_MS
#define APP_TAG_RNG_DELAY_MS 1000U
#endif

#ifndef APP_TAG_CAL_RNG_SETTLE_US
#define APP_TAG_CAL_RNG_SETTLE_US 0U
#endif

#ifndef APP_TAG_TX_TO_RX_DLY_UUS
#define APP_TAG_TX_TO_RX_DLY_UUS 140U
#endif

#ifndef APP_TAG_RESP_RX_TIMEOUT_UUS
#define APP_TAG_RESP_RX_TIMEOUT_UUS 1500U
#endif

#ifndef APP_ALT_SS_TWR_ENABLE
#define APP_ALT_SS_TWR_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_ENABLE
#define APP_ALT_SS_TWR_BCAST_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_MODE
#define APP_ALT_SS_TWR_MODE 2U
#endif

#define APP_ALT_SS_TWR_MODE_UNICAST 1U
#define APP_ALT_SS_TWR_MODE_BROADCAST 2U

#ifndef APP_UWB_HW_FRAME_FILTER_ENABLE
#define APP_UWB_HW_FRAME_FILTER_ENABLE 1U
#endif

#ifndef APP_ALT_SS_TWR_POLL_SPACING_US
#define APP_ALT_SS_TWR_POLL_SPACING_US 200U
#endif

#ifndef APP_ALT_SS_TWR_GUARD_US
#define APP_ALT_SS_TWR_GUARD_US 500U
#endif

#ifndef APP_ALT_SS_TWR_RESP_SPACING_US
#define APP_ALT_SS_TWR_RESP_SPACING_US 800U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP
#define APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP 0U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE
#define APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE
#define APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE
#define APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE 0U
#endif

#ifndef APP_TAG_LOC_FAST_ALL_VALID_ENABLE
#define APP_TAG_LOC_FAST_ALL_VALID_ENABLE 0U
#endif

#ifndef APP_TAG_SWEEP_DIAG_ENABLE
#define APP_TAG_SWEEP_DIAG_ENABLE 0U
#endif

#ifndef APP_TAG_SWEEP_DIAG_PERIOD
#define APP_TAG_SWEEP_DIAG_PERIOD 10U
#endif

#ifndef APP_TAG_POSITION_OUTPUT_ENABLE
#define APP_TAG_POSITION_OUTPUT_ENABLE 0U
#endif

#ifndef APP_TAG_TR_BCAST_V2_ENABLE
#define APP_TAG_TR_BCAST_V2_ENABLE 0U
#endif

#ifndef APP_TAG_WAND_MODE_ENABLE
#define APP_TAG_WAND_MODE_ENABLE 0U
#endif

#ifndef APP_TAG_WAND_DEFAULT_A_ID
#define APP_TAG_WAND_DEFAULT_A_ID 0xF4U
#endif

#ifndef APP_TAG_WAND_DEFAULT_B_ID
#define APP_TAG_WAND_DEFAULT_B_ID 0x36U
#endif

#ifndef APP_TAG_WAND_DEFAULT_C_ID
#define APP_TAG_WAND_DEFAULT_C_ID 0x5AU
#endif

#ifndef APP_TAG_WAND_RESP_RX_MS
#define APP_TAG_WAND_RESP_RX_MS 20U
#endif

#ifndef APP_TAG_WAND_RESP_DELAY_UUS
#define APP_TAG_WAND_RESP_DELAY_UUS 500U
#endif

#ifndef APP_TAG_TDMA_SLOT_PERIOD_MS
#define APP_TAG_TDMA_SLOT_PERIOD_MS 0U
#endif

#ifndef APP_TAG_TDMA_SLOT_COUNT
#define APP_TAG_TDMA_SLOT_COUNT 0U
#endif

#ifndef APP_TAG_ALT_POLL_DIAG_PERIOD_MS
#define APP_TAG_ALT_POLL_DIAG_PERIOD_MS 5000U
#endif

#ifndef APP_TAG_ALT_RXG_BLE_DIAG_ENABLE
#define APP_TAG_ALT_RXG_BLE_DIAG_ENABLE 1U
#endif

#ifndef APP_TAG_FAST_TRACKING
#define APP_TAG_FAST_TRACKING 0U
#endif

#ifndef APP_TAG_FULL_SWEEP_INTERVAL
#define APP_TAG_FULL_SWEEP_INTERVAL 1U
#endif

#ifndef APP_TAG_TRACK_ANCHOR_COUNT
#define APP_TAG_TRACK_ANCHOR_COUNT 5U
#endif

#ifndef APP_TAG_SUMMARY_PERIOD
#define APP_TAG_SUMMARY_PERIOD 1U
#endif

#ifndef APP_TAG_STATUS_PERIOD_MS
#define APP_TAG_STATUS_PERIOD_MS 0U
#endif

#ifndef APP_TAG_PENDING_PRINT_PERIOD
#define APP_TAG_PENDING_PRINT_PERIOD 20U
#endif

#ifndef APP_TAG_CALIBRATION_MODE
#define APP_TAG_CALIBRATION_MODE 0U
#endif

#ifndef APP_TAG_IMU_SAMPLE_PERIOD
#define APP_TAG_IMU_SAMPLE_PERIOD 4U
#endif

#ifndef APP_TAG_TR_IMU_SUMMARY_ENABLE
#define APP_TAG_TR_IMU_SUMMARY_ENABLE 0U
#endif

#ifndef APP_TAG_TR_IMU_SUMMARY_WINDOW
#define APP_TAG_TR_IMU_SUMMARY_WINDOW 5U
#endif

#define SS_TWR_INIT_IMU_SUMMARY_MAX_WINDOW 16U

#ifndef APP_TAG_CONSOLE_SUMMARY_ENABLE
#define APP_TAG_CONSOLE_SUMMARY_ENABLE 1U
#endif

#ifndef APP_TAG_CAL_ROTO_MIN_TETRA_VOLUME_M3
#define APP_TAG_CAL_ROTO_MIN_TETRA_VOLUME_M3 0.1
#endif

#ifndef APP_TAG_CAL_ROTO_PREWARM_MS
#define APP_TAG_CAL_ROTO_PREWARM_MS 5000U
#endif

#ifndef APP_TAG_EKF_ENABLE
#define APP_TAG_EKF_ENABLE 0U
#endif

#if APP_TAG_USB_DIAG_TRACE
static void ss_twr_diag_write(const char *msg)
{
    const struct device *console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

    if (!device_is_ready(console) || msg == NULL) {
        return;
    }

    while (*msg != '\0') {
        uart_poll_out(console, *msg++);
    }
}
#endif

#ifndef APP_TAG_EKF_MEAS_STD_MM
#define APP_TAG_EKF_MEAS_STD_MM 25U
#endif

#ifndef APP_TAG_EKF_RESIDUAL_GAIN_PCT
#define APP_TAG_EKF_RESIDUAL_GAIN_PCT 0U
#endif

#ifndef APP_TAG_EKF_PROC_ACCEL_MM_S2
#define APP_TAG_EKF_PROC_ACCEL_MM_S2 250U
#endif

#ifndef APP_TAG_EKF_INIT_POS_STD_MM
#define APP_TAG_EKF_INIT_POS_STD_MM 200U
#endif

#ifndef APP_TAG_EKF_INIT_VEL_STD_MM_S
#define APP_TAG_EKF_INIT_VEL_STD_MM_S 1000U
#endif

#ifndef APP_TAG_EKF_OUTLIER_GATE_MM
#define APP_TAG_EKF_OUTLIER_GATE_MM 0U
#endif

#ifndef APP_TAG_RANGE_SOFT_RESIDUAL_MM
#define APP_TAG_RANGE_SOFT_RESIDUAL_MM 180U
#endif

#ifndef APP_TAG_RANGE_HARD_RESIDUAL_MM
#define APP_TAG_RANGE_HARD_RESIDUAL_MM 350U
#endif

#ifndef APP_TAG_OUTPUT_MAX_RMS_MM
#define APP_TAG_OUTPUT_MAX_RMS_MM 0U
#endif

#ifndef APP_TAG_OUTPUT_MAX_MAX_MM
#define APP_TAG_OUTPUT_MAX_MAX_MM 0U
#endif

#ifndef APP_TAG_OUTPUT_MAX_STEP_MM
#define APP_TAG_OUTPUT_MAX_STEP_MM 0U
#endif

#ifndef APP_TAG_OUTPUT_FILTER_RMS_MM
#define APP_TAG_OUTPUT_FILTER_RMS_MM 0U
#endif

#ifndef APP_TAG_OUTPUT_FILTER_SPEED_MM_S
#define APP_TAG_OUTPUT_FILTER_SPEED_MM_S 0U
#endif

#ifndef APP_TAG_RANGE_FILTER_OUTLIER_MM
#define APP_TAG_RANGE_FILTER_OUTLIER_MM 450U
#endif

#ifndef APP_TAG_RANGE_CONTINUITY_WARMUP_SWEEPS
#define APP_TAG_RANGE_CONTINUITY_WARMUP_SWEEPS 3U
#endif

#ifndef APP_TAG_RANGE_CONTINUITY_ENABLE
#define APP_TAG_RANGE_CONTINUITY_ENABLE 1U
#endif

#ifndef APP_TAG_MOTION_FULL_SWEEP_INTERVAL
#define APP_TAG_MOTION_FULL_SWEEP_INTERVAL 0U
#endif

#ifndef APP_TAG_MOTION_SPEED_THRESHOLD_MM_S
#define APP_TAG_MOTION_SPEED_THRESHOLD_MM_S 250U
#endif

#ifndef APP_TAG_MOTION_RANGE_SOFT_BONUS_MM
#define APP_TAG_MOTION_RANGE_SOFT_BONUS_MM 0U
#endif

#ifndef APP_TAG_MOTION_RANGE_HARD_BONUS_MM
#define APP_TAG_MOTION_RANGE_HARD_BONUS_MM 0U
#endif

#ifndef APP_TAG_CAL_STATIC_SLOT_DIVIDER
#define APP_TAG_CAL_STATIC_SLOT_DIVIDER 1U
#endif

#ifndef APP_TAG_MOTION_EKF_MEAS_STD_MM
#define APP_TAG_MOTION_EKF_MEAS_STD_MM 0U
#endif

#ifndef APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2
#define APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2 0U
#endif

#ifndef APP_TAG_MOTION_EKF_OUTLIER_GATE_MM
#define APP_TAG_MOTION_EKF_OUTLIER_GATE_MM 0U
#endif

#ifndef APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG
#define APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG 750U
#endif

#ifndef APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG
#define APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG 400U
#endif

#ifndef APP_TAG_BLE_COMPACT_STATUS
#define APP_TAG_BLE_COMPACT_STATUS 0U
#endif

#ifndef APP_TAG_USB_MIRROR_BLE_STATUS
#define APP_TAG_USB_MIRROR_BLE_STATUS 0U
#endif

#ifndef APP_TAG_VERBOSE_RANGING
#define APP_TAG_VERBOSE_RANGING 1U
#endif

#ifndef APP_TAG_VERBOSE_MEASUREMENTS
#define APP_TAG_VERBOSE_MEASUREMENTS 1U
#endif

#ifndef APP_TAG_VERBOSE_PERF
#define APP_TAG_VERBOSE_PERF 1U
#endif

#define SS_TWR_INIT_RNG_DELAY_MS APP_TAG_RNG_DELAY_MS
#define SS_TWR_INIT_CAL_RNG_SETTLE_US APP_TAG_CAL_RNG_SETTLE_US
#define SS_TWR_INIT_TX_TO_RX_DLY_UUS APP_TAG_TX_TO_RX_DLY_UUS
#define SS_TWR_INIT_RESP_RX_TIMEOUT_UUS APP_TAG_RESP_RX_TIMEOUT_UUS

#define SS_TWR_INIT_RX_BUF_LEN 127U
#define SS_TWR_INIT_ALL_MSG_COMMON_LEN 10U
#define SS_TWR_INIT_MSG_SN_IDX 2U
#define SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX 10U
#define SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX 14U
#define SS_TWR_INIT_RESP_MSG_TS_LEN 4U
#define SS_TWR_INIT_LEGACY_POLL_FRAME_LEN 13U
#define SS_TWR_INIT_UUS_TO_DWT_TIME 65536ULL
#define SS_TWR_INIT_ALT_BCAST_POLL_SCHED_UUS 1000U
#define SS_TWR_INIT_ALT_BCAST_TX_DONE_TIMEOUT_US 5000U
#define SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US 300U
#define SS_TWR_INIT_ALT_BCAST_POLL_AIRTIME_US 335U
#define SS_TWR_INIT_ALT_BCAST_SLOT_RX_EARLY_US 150U
#define SS_TWR_INIT_ALT_BCAST_SLOT_RX_TIMEOUT_US 850U

#define SS_TWR_INIT_SPEED_OF_LIGHT 299702547.0
#define SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS \
	(((SS_TWR_INIT_TX_TO_RX_DLY_UUS + SS_TWR_INIT_RESP_RX_TIMEOUT_UUS + 999U) / \
	  1000U) + 1U)
#define SS_TWR_INIT_SLOT_GUARD_MARGIN_MS 1U

enum ss_twr_init_solve_reason {
	SS_TWR_INIT_SOLVE_NONE = 0,
	SS_TWR_INIT_SOLVE_SUCCESS,
	SS_TWR_INIT_SOLVE_PENDING,
	SS_TWR_INIT_SOLVE_REJECTED,
	SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT,
};

static dwt_config_t ss_twr_init_config = {
    APP_UWB_CHANNEL,
    DWT_PRF_64M,
    DWT_PLEN_128,
    DWT_PAC8,
    9,
    9,
    1,
    DWT_BR_6M8,
    DWT_PHRMODE_STD,
    129,
};

static uint8_t ss_twr_init_frame_seq_nb;
static uint8_t ss_twr_init_rx_buffer[SS_TWR_INIT_RX_BUF_LEN];
static uint8_t ss_twr_init_tx_poll_msg[UWB_MSG_ALT_POLL_FRAME_LEN];
static uint8_t ss_twr_init_tx_resp_msg[20];
static uint16_t ss_twr_init_local_addr;
static uint8_t ss_twr_init_local_tag_id;
static uint16_t ss_twr_init_identity_code;
static bool ss_twr_init_radio_configured;
static struct uwb_range_tracker ss_twr_init_trackers[UWB_MAX_ANCHORS];
#if APP_TAG_WAND_MODE_ENABLE
static volatile bool ss_twr_init_wand_enabled;
static volatile enum ss_twr_init_wand_role ss_twr_init_wand_role =
    SS_TWR_INIT_WAND_ROLE_IDLE;
static volatile uint16_t ss_twr_init_wand_pending_sweeps;
static char ss_twr_init_wand_label = '?';
static uint8_t ss_twr_init_wand_tags[3] = {
    APP_TAG_WAND_DEFAULT_A_ID,
    APP_TAG_WAND_DEFAULT_B_ID,
    APP_TAG_WAND_DEFAULT_C_ID,
};
static uint32_t ss_twr_init_wand_seq;
#endif
static uint8_t ss_twr_init_anchor_ids[UWB_MAX_ANCHORS];
static size_t ss_twr_init_anchor_count;
static bool ss_twr_init_fixed_anchor_mode;
static uint8_t ss_twr_init_fixed_anchor_ids[UWB_TAG_FIXED_ANCHOR_MAX];
static size_t ss_twr_init_fixed_anchor_count;
static bool ss_twr_init_multitag_anchor_plan_mode;
static uint8_t ss_twr_init_active_plan_ids[UWB_TAG_ACTIVE_ANCHOR_MAX];
static size_t ss_twr_init_active_plan_count;
static uint8_t ss_twr_init_standby_plan_ids[UWB_TAG_STANDBY_ANCHOR_MAX];
static size_t ss_twr_init_standby_plan_count;
static uint8_t ss_twr_init_reserve_plan_ids[UWB_TAG_RESERVE_ANCHOR_MAX];
static size_t ss_twr_init_reserve_plan_count;
static uint8_t ss_twr_init_refresh_anchor_budget;
static uint16_t ss_twr_init_refresh_interval_sweeps;
static uint16_t ss_twr_init_full_sweep_interval_sweeps;
static uint8_t ss_twr_init_plan_refresh_cursor;
static struct uwb_tdma_schedule ss_twr_init_tdma_schedule;
static uint8_t ss_twr_init_active_anchor_ids[UWB_MAX_ANCHORS];
static size_t ss_twr_init_active_anchor_count;
static size_t ss_twr_init_active_anchor_index;
static uint8_t ss_twr_init_current_anchor_retry_count;
static uint32_t ss_twr_init_sweep_count;
static bool ss_twr_init_imu_ready;
static bool ss_twr_init_have_last_imu_sample;
static bool ss_twr_init_have_last_solution;
static uint8_t ss_twr_init_last_solution_anchor_ids[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_last_solution_anchor_count;
static bool ss_twr_init_have_last_location;
static uint32_t ss_twr_init_location_output_count;
#if APP_TAG_STATUS_PERIOD_MS > 0U
static bool ss_twr_init_have_last_raw_location;
static struct uwb_tag_location_result ss_twr_init_last_raw_location;
static struct uwb_tag_location_result ss_twr_init_last_filtered_location;
static uint32_t ss_twr_init_last_location_update_ms;
#endif
static int32_t ss_twr_init_last_location_x_mm;
static int32_t ss_twr_init_last_location_y_mm;
static int32_t ss_twr_init_last_location_z_mm;
static uint32_t ss_twr_init_last_output_ms;
static uint8_t ss_twr_init_refresh_anchor_cursor;
static bool ss_twr_init_current_sweep_full;
static bool ss_twr_init_current_sweep_refresh;
static uint32_t ss_twr_init_current_sweep_start_ms;
#if APP_TAG_STATUS_PERIOD_MS > 0U
static struct k_work_delayable ss_twr_init_status_work;
#endif
static struct uwb_imu_sample ss_twr_init_last_imu_sample;
struct ss_twr_init_imu_summary_state {
    bool valid;
    uint8_t sample_count;
    int32_t mean_mg;
    int32_t std_mg;
    int32_t min_mg;
    int32_t max_mg;
    uint32_t skip_count;
};
#if APP_TAG_TR_IMU_SUMMARY_ENABLE != 0U
static struct ss_twr_init_imu_summary_state ss_twr_init_imu_summary;
static int32_t
    ss_twr_init_imu_norm_ring[SS_TWR_INIT_IMU_SUMMARY_MAX_WINDOW];
static uint8_t ss_twr_init_imu_norm_count;
static uint8_t ss_twr_init_imu_norm_pos;
static uint32_t ss_twr_init_imu_skip_count;
#endif
static uint32_t ss_twr_init_perf_motion_dt_sum_ms;
static uint32_t ss_twr_init_perf_track_sweep_sum_ms;
static uint32_t ss_twr_init_perf_full_sweep_sum_ms;
static uint16_t ss_twr_init_perf_motion_dt_count;
static uint16_t ss_twr_init_perf_track_sweep_count;
static uint16_t ss_twr_init_perf_full_sweep_count;
static uint32_t ss_twr_init_last_motion_speed_mm_s;
static bool ss_twr_init_last_imu_indicates_motion;
static struct uwb_tag_runtime_params ss_twr_init_runtime_params;
static struct uwb_tag_runtime_params ss_twr_init_pending_runtime_params;
static bool ss_twr_init_runtime_update_pending;
static bool ss_twr_init_last_sweep_cut_short;
static uint32_t ss_twr_init_last_tdma_wait_ms;
static uint32_t ss_twr_init_last_slot_guard_log_ms;
static uint32_t ss_twr_init_last_solve_pending_log_ms;
static uint32_t ss_twr_init_last_solve_diag_ms;
static enum ss_twr_init_solve_reason ss_twr_init_last_solve_reason;
static uint32_t ss_twr_init_sweep_first_poll_cycle;
static uint32_t ss_twr_init_sweep_last_poll_cycle;
static uint32_t ss_twr_init_sweep_done_cycle;
static uint8_t ss_twr_init_sweep_poll_count;
static bool ss_twr_init_sweep_timing_valid;
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
static uint32_t ss_twr_init_diag_t0_cycles;
static uint32_t ss_twr_init_diag_wait_done_cycles;
static uint32_t ss_twr_init_diag_tx_done_cycles;
static uint32_t ss_twr_init_diag_rx_start_cycles;
static uint32_t ss_twr_init_diag_rx_done_cycles;
static uint32_t ss_twr_init_diag_range_done_cycles;
static uint32_t ss_twr_init_diag_solve_start_cycles;
static uint32_t ss_twr_init_diag_solve_done_cycles;
static uint32_t ss_twr_init_diag_out_start_cycles;
static uint32_t ss_twr_init_diag_out_done_cycles;
static uint32_t ss_twr_init_diag_clean_done_cycles;
static uint32_t ss_twr_init_diag_sweep_count;
#endif
static uint8_t ss_twr_init_static_cal_group_cursor;
static uint8_t ss_twr_init_roto_cal_group_cursor;
static uint32_t ss_twr_init_static_cal_slot_tick;
static uint32_t ss_twr_init_roto_prewarm_deadline_ms;
static uint8_t ss_twr_init_sweep_anchor_status[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_sweep_anchor_quality[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_sweep_anchor_reason[UWB_MAX_ANCHORS];
static int32_t ss_twr_init_sweep_anchor_raw_mm[UWB_MAX_ANCHORS];
static uint32_t ss_twr_init_sweep_anchor_filt_mm[UWB_MAX_ANCHORS];
static uint32_t ss_twr_init_sweep_anchor_pred_mm[UWB_MAX_ANCHORS];
static uint32_t ss_twr_init_sweep_anchor_resid_mm[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_sweep_anchor_solve_quality[UWB_MAX_ANCHORS];
static bool ss_twr_init_sweep_anchor_diag_published[UWB_MAX_ANCHORS];

static void ss_twr_init_prepare_sweep_plan(void);
static bool ss_twr_init_runtime_any_calibration_mode(void);
static bool ss_twr_init_runtime_static_calibration_mode(void);
static bool ss_twr_init_runtime_roto_calibration_mode(void);
static bool ss_twr_init_roto_prewarm_active(void);
static const char *ss_twr_init_plan_label(void);
static char ss_twr_init_plan_code(const char *plan_label);
static const char *ss_twr_init_solve_reason_label(void);
static bool ss_twr_init_anchor_id_in_list(const uint8_t *anchor_ids, size_t count,
                                          uint8_t anchor_id);

#define SS_TWR_INIT_SWEEP_ANCHOR_PENDING 0xffU

enum ss_twr_init_cal_reason_code {
	SS_TWR_INIT_CAL_REASON_NONE = 0,
	SS_TWR_INIT_CAL_REASON_OK,
	SS_TWR_INIT_CAL_REASON_RAW_OUTLIER,
	SS_TWR_INIT_CAL_REASON_RX_TIMEOUT,
	SS_TWR_INIT_CAL_REASON_RX_ERROR,
	SS_TWR_INIT_CAL_REASON_CONTINUITY_HARD,
	SS_TWR_INIT_CAL_REASON_CONTINUITY_SOFT,
	SS_TWR_INIT_CAL_REASON_NOT_MEASURED,
};

#if APP_TAG_BLE_ENABLE
static void ss_twr_init_publish_cal_range(uint8_t anchor_id,
                                          enum uwb_tag_ble_cal_status status,
                                          int32_t raw_mm,
                                          uint32_t filt_mm,
                                          const struct uwb_range_tracker *tracker)
{
	if (!ss_twr_init_runtime_any_calibration_mode()) {
		return;
	}

	struct uwb_tag_ble_cal_range sample = {
	    .sweep = (uint32_t)ss_twr_init_sweep_count,
	    .raw_mm = raw_mm,
        .filt_mm = filt_mm,
        .ok_count = (tracker != NULL) ? tracker->success_count : 0U,
        .fail_count = (tracker != NULL) ? tracker->failure_count : 0U,
        .anchor_id = anchor_id,
        .status = (uint8_t)status,
	    .quality_percent =
	        (tracker != NULL) ?
	            uwb_range_tracker_quality_percent((struct uwb_range_tracker *)tracker) :
	            0U,
	};
	(void)uwb_tag_ble_publish_calibration_range(&sample);
}
#endif

static void ss_twr_init_reset_sweep_anchor_state(void)
{
    ss_twr_init_current_anchor_retry_count = 0U;
    for (size_t i = 0U; i < UWB_MAX_ANCHORS; ++i) {
        ss_twr_init_sweep_anchor_status[i] = SS_TWR_INIT_SWEEP_ANCHOR_PENDING;
        ss_twr_init_sweep_anchor_quality[i] = 0U;
        ss_twr_init_sweep_anchor_reason[i] = SS_TWR_INIT_CAL_REASON_NOT_MEASURED;
        ss_twr_init_sweep_anchor_raw_mm[i] = 0;
        ss_twr_init_sweep_anchor_filt_mm[i] = 0U;
        ss_twr_init_sweep_anchor_pred_mm[i] = 0U;
        ss_twr_init_sweep_anchor_resid_mm[i] = 0U;
        ss_twr_init_sweep_anchor_solve_quality[i] = 0U;
        ss_twr_init_sweep_anchor_diag_published[i] = false;
    }
}

static void ss_twr_init_record_sweep_anchor_state(
    uint8_t anchor_id, enum uwb_tag_ble_cal_status status,
    const struct uwb_range_tracker *tracker)
{
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    ss_twr_init_sweep_anchor_status[anchor_id] = (uint8_t)status;
    ss_twr_init_sweep_anchor_quality[anchor_id] =
        (tracker != NULL) ? uwb_range_tracker_quality_percent(
                                (struct uwb_range_tracker *)tracker)
                          : 0U;
    ss_twr_init_sweep_anchor_solve_quality[anchor_id] =
        ss_twr_init_sweep_anchor_quality[anchor_id];
}

static const char *ss_twr_init_cal_status_label(uint8_t status)
{
    switch (status) {
    case UWB_TAG_BLE_CAL_STATUS_OK:
        return "ok";
    case UWB_TAG_BLE_CAL_STATUS_REJECT:
        return "reject";
    case UWB_TAG_BLE_CAL_STATUS_TIMEOUT:
        return "timeout";
    case UWB_TAG_BLE_CAL_STATUS_ERROR:
        return "error";
    case SS_TWR_INIT_SWEEP_ANCHOR_PENDING:
    default:
        return "pending";
    }
}

static char ss_twr_init_range_status_code(uint8_t status)
{
    switch (status) {
    case UWB_TAG_BLE_CAL_STATUS_OK:
        return 'O';
    case UWB_TAG_BLE_CAL_STATUS_REJECT:
        return 'R';
    case UWB_TAG_BLE_CAL_STATUS_TIMEOUT:
        return 'T';
    case UWB_TAG_BLE_CAL_STATUS_ERROR:
        return 'E';
    case SS_TWR_INIT_SWEEP_ANCHOR_PENDING:
    default:
        return 'P';
    }
}

#if APP_TAG_BLE_ENABLE && APP_ALT_SS_TWR_ENABLE && \
    APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
static size_t ss_twr_init_append_csv_i32(char *buf, size_t len, size_t pos,
                                         int32_t value, bool first)
{
    if (pos >= len) {
        return pos;
    }

    pos += snprintk(&buf[pos], len - pos, first ? "%ld" : ",%ld",
                    (long)value);
    return pos;
}

static size_t ss_twr_init_append_csv_u32(char *buf, size_t len, size_t pos,
                                         uint32_t value, bool first)
{
    if (pos >= len) {
        return pos;
    }

    pos += snprintk(&buf[pos], len - pos, first ? "%lu" : ",%lu",
                    (unsigned long)value);
    return pos;
}

static void ss_twr_init_publish_tag_range_summary(
    const struct uwb_tag_measurement *measurements, size_t measurement_count,
    uint8_t qf_percent)
{
    char line[384];
    char raw_csv[64];
    char range_csv[64];
    char quality_csv[40];
    char status_codes[UWB_MAX_ANCHORS + 1U];
    size_t raw_pos = 0U;
    size_t range_pos = 0U;
    size_t quality_pos = 0U;
    size_t status_pos = 0U;
    uint32_t active_mask = 0U;
    uint32_t valid_mask = 0U;
    uint32_t rx_mask = 0U;
    uint32_t first_to_last_us = 0U;
    uint32_t frame_us = 0U;
    uint32_t cycle_us = 0U;
    bool first = true;
    int line_len;

    if (ss_twr_init_runtime_any_calibration_mode()) {
        return;
    }

    for (size_t i = 0U; i < measurement_count; ++i) {
        uint8_t anchor_id = measurements[i].anchor_id;

        if (anchor_id >= UWB_MAX_ANCHORS ||
            !ss_twr_init_anchor_id_in_list(ss_twr_init_active_anchor_ids,
                                           ss_twr_init_active_anchor_count,
                                           anchor_id)) {
            continue;
        }

        active_mask |= BIT(anchor_id);
        if (measurements[i].valid) {
            valid_mask |= BIT(anchor_id);
        }
        if (ss_twr_init_sweep_anchor_status[anchor_id] !=
            SS_TWR_INIT_SWEEP_ANCHOR_PENDING) {
            rx_mask |= BIT(anchor_id);
        }

        raw_pos = ss_twr_init_append_csv_i32(
            raw_csv, sizeof(raw_csv), raw_pos,
            ss_twr_init_sweep_anchor_raw_mm[anchor_id], first);
        range_pos = ss_twr_init_append_csv_u32(
            range_csv, sizeof(range_csv), range_pos,
            measurements[i].range_mm, first);
        quality_pos = ss_twr_init_append_csv_u32(
            quality_csv, sizeof(quality_csv), quality_pos,
            measurements[i].quality_percent, first);

        if (status_pos + 1U < sizeof(status_codes)) {
            status_codes[status_pos++] = ss_twr_init_range_status_code(
                ss_twr_init_sweep_anchor_status[anchor_id]);
        }
        first = false;
    }

    if (active_mask == 0U) {
        return;
    }

    raw_csv[MIN(raw_pos, sizeof(raw_csv) - 1U)] = '\0';
    range_csv[MIN(range_pos, sizeof(range_csv) - 1U)] = '\0';
    quality_csv[MIN(quality_pos, sizeof(quality_csv) - 1U)] = '\0';
    status_codes[MIN(status_pos, sizeof(status_codes) - 1U)] = '\0';

    if (ss_twr_init_sweep_timing_valid && ss_twr_init_sweep_poll_count != 0U) {
        first_to_last_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_last_poll_cycle -
            ss_twr_init_sweep_first_poll_cycle);
        frame_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_done_cycle -
            ss_twr_init_sweep_first_poll_cycle);
        cycle_us = frame_us;
    }

#if APP_TAG_TR_BCAST_V2_ENABLE
    line_len = snprintk(
        line, sizeof(line),
        "TR;2;%lu;%c;%u;%02lx;%02lx;%02lx;%s;%s;%s;%s;%u;%lu;%lu;%lu;%u",
        (unsigned long)ss_twr_init_sweep_count,
        ss_twr_init_plan_code(ss_twr_init_plan_label()),
        (unsigned int)ss_twr_init_runtime_params.positioning_mode,
        (unsigned long)active_mask, (unsigned long)valid_mask,
        (unsigned long)rx_mask, raw_csv, range_csv, quality_csv,
        status_codes, (unsigned int)qf_percent,
        (unsigned long)first_to_last_us,
        (unsigned long)frame_us,
        (unsigned long)cycle_us,
        (unsigned int)ss_twr_init_sweep_poll_count);
#else
    line_len = snprintk(
        line, sizeof(line),
        "TR;%u;%lu;%c;%u;%02lx;%02lx;%s;%s;%s;%s;%u;%lu;%lu;%u",
        (unsigned int)(APP_TAG_TR_IMU_SUMMARY_ENABLE != 0U ? 4U : 3U),
        (unsigned long)ss_twr_init_sweep_count,
        ss_twr_init_plan_code(ss_twr_init_plan_label()),
        (unsigned int)ss_twr_init_runtime_params.positioning_mode,
        (unsigned long)active_mask, (unsigned long)valid_mask,
        raw_csv, range_csv, quality_csv, status_codes,
        (unsigned int)qf_percent,
        (unsigned long)first_to_last_us,
        (unsigned long)frame_us,
        (unsigned int)ss_twr_init_sweep_poll_count);
#endif
#if APP_TAG_TR_IMU_SUMMARY_ENABLE != 0U
    if (line_len > 0 && ss_twr_init_imu_summary.valid) {
        size_t used = (size_t)line_len;

        if (used >= sizeof(line)) {
            used = sizeof(line) - 1U;
        }
        (void)snprintk(
            &line[used], sizeof(line) - used,
            ";I,%u,%ld,%ld,%ld,%ld,%lu",
            (unsigned int)ss_twr_init_imu_summary.sample_count,
            (long)ss_twr_init_imu_summary.mean_mg,
            (long)ss_twr_init_imu_summary.std_mg,
            (long)ss_twr_init_imu_summary.min_mg,
            (long)ss_twr_init_imu_summary.max_mg,
            (unsigned long)ss_twr_init_imu_summary.skip_count);
    }
#endif
    (void)uwb_tag_ble_publish_status(line);
}
#else
static void ss_twr_init_publish_tag_range_summary(
    const struct uwb_tag_measurement *measurements, size_t measurement_count,
    uint8_t qf_percent)
{
    ARG_UNUSED(measurements);
    ARG_UNUSED(measurement_count);
    ARG_UNUSED(qf_percent);
}
#endif

static const char *ss_twr_init_cal_reason_label(uint8_t reason)
{
    switch (reason) {
    case SS_TWR_INIT_CAL_REASON_OK:
        return "ok";
    case SS_TWR_INIT_CAL_REASON_RAW_OUTLIER:
        return "raw_outlier";
    case SS_TWR_INIT_CAL_REASON_RX_TIMEOUT:
        return "rx_timeout";
    case SS_TWR_INIT_CAL_REASON_RX_ERROR:
        return "rx_error";
    case SS_TWR_INIT_CAL_REASON_CONTINUITY_HARD:
        return "continuity_hard";
    case SS_TWR_INIT_CAL_REASON_CONTINUITY_SOFT:
        return "continuity_soft";
    case SS_TWR_INIT_CAL_REASON_NOT_MEASURED:
        return "not_measured";
    case SS_TWR_INIT_CAL_REASON_NONE:
    default:
        return "none";
    }
}

static void ss_twr_init_record_sweep_anchor_diag(
    uint8_t anchor_id, uint8_t reason, int32_t raw_mm, uint32_t filt_mm,
    uint32_t pred_mm, uint32_t resid_mm, uint8_t solve_quality_percent)
{
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    ss_twr_init_sweep_anchor_reason[anchor_id] = reason;
    ss_twr_init_sweep_anchor_raw_mm[anchor_id] = raw_mm;
    ss_twr_init_sweep_anchor_filt_mm[anchor_id] = filt_mm;
    ss_twr_init_sweep_anchor_pred_mm[anchor_id] = pred_mm;
    ss_twr_init_sweep_anchor_resid_mm[anchor_id] = resid_mm;
    ss_twr_init_sweep_anchor_solve_quality[anchor_id] = solve_quality_percent;
}

static void ss_twr_init_publish_cal_reason_line(uint8_t anchor_id)
{
    const struct uwb_anchor_pose_mm *pose;
    char line[192];

    if (!ss_twr_init_runtime_any_calibration_mode() ||
        anchor_id >= UWB_MAX_ANCHORS ||
        ss_twr_init_sweep_anchor_diag_published[anchor_id]) {
        return;
    }

    pose = uwb_anchor_layout_get(anchor_id);
    snprintk(line, sizeof(line),
             "CR;1;%lu;%s;%u;%c;%s;%s;%ld;%lu;%lu;%lu;%u;%u",
             (unsigned long)ss_twr_init_sweep_count, ss_twr_init_plan_label(),
             (unsigned int)ss_twr_init_runtime_params.positioning_mode,
             (pose != NULL) ? pose->label : '?',
             ss_twr_init_cal_status_label(ss_twr_init_sweep_anchor_status[anchor_id]),
             ss_twr_init_cal_reason_label(ss_twr_init_sweep_anchor_reason[anchor_id]),
             (long)ss_twr_init_sweep_anchor_raw_mm[anchor_id],
             (unsigned long)ss_twr_init_sweep_anchor_filt_mm[anchor_id],
             (unsigned long)ss_twr_init_sweep_anchor_pred_mm[anchor_id],
             (unsigned long)ss_twr_init_sweep_anchor_resid_mm[anchor_id],
             (unsigned int)ss_twr_init_sweep_anchor_quality[anchor_id],
             (unsigned int)ss_twr_init_sweep_anchor_solve_quality[anchor_id]);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
    ss_twr_init_sweep_anchor_diag_published[anchor_id] = true;
}

static void ss_twr_init_publish_cal_frame_summary(const char *plan_label,
                                                  uint8_t positioning_mode,
                                                  uint8_t qf_percent,
                                                  uint32_t rms_mm,
                                                  uint32_t max_mm,
                                                  uint32_t step_mm,
                                                  size_t valid_anchor_count)
{
    char line[192];
    size_t reported_valid_count = valid_anchor_count;
    uint32_t first_to_last_us = 0U;
    uint32_t frame_us = 0U;

    if (!ss_twr_init_runtime_any_calibration_mode()) {
        return;
    }

    /*
     * In CAL_STATIC/CAL_ROTO the active target set is the contract. A failed
     * leg must remain visible as timeout/reject in CS/CR/qf, not silently turn
     * the frame into a "3-anchor" record in CF.
     */
    if (ss_twr_init_active_anchor_count == 4U) {
        reported_valid_count = ss_twr_init_active_anchor_count;
    }

    if (ss_twr_init_sweep_timing_valid && ss_twr_init_sweep_poll_count != 0U) {
        first_to_last_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_last_poll_cycle -
            ss_twr_init_sweep_first_poll_cycle);
        frame_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_done_cycle -
            ss_twr_init_sweep_first_poll_cycle);
    }

    snprintk(line, sizeof(line), "CF;1;%lu;%s;%u;%s;%u;%u;%u;%lu;%lu;%lu;%lu;%lu;%u",
             (unsigned long)ss_twr_init_sweep_count, plan_label,
             (unsigned int)positioning_mode, ss_twr_init_solve_reason_label(),
             (unsigned int)qf_percent,
             (unsigned int)ss_twr_init_active_anchor_count,
             (unsigned int)reported_valid_count,
             (unsigned long)rms_mm, (unsigned long)max_mm,
             (unsigned long)step_mm, (unsigned long)first_to_last_us,
             (unsigned long)frame_us,
             (unsigned int)ss_twr_init_sweep_poll_count);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_ALT_RXG_BLE_DIAG_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_publish_solve_diag(char stage,
                                           const char *plan_label,
                                           uint8_t qf_percent,
                                           size_t valid_anchor_count,
                                           size_t used_anchor_count,
                                           uint32_t rms_mm,
                                           uint32_t max_mm,
                                           uint32_t step_mm,
                                           const char *anchors)
{
    uint32_t now_ms = (uint32_t)k_uptime_get();
    char line[192];

    if (ss_twr_init_last_solve_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_last_solve_diag_ms) < 1000U) {
        return;
    }
    ss_twr_init_last_solve_diag_ms = now_ms;

    snprintk(line, sizeof(line),
             "SD;1;%lu;%c;%s;%u;%u;%u;%u;%lu;%lu;%lu;%s;%u;%u",
             (unsigned long)ss_twr_init_sweep_count,
             stage,
             plan_label,
             (unsigned int)ss_twr_init_active_anchor_count,
             (unsigned int)valid_anchor_count,
             (unsigned int)used_anchor_count,
             (unsigned int)qf_percent,
             (unsigned long)rms_mm,
             (unsigned long)max_mm,
             (unsigned long)step_mm,
             (anchors != NULL) ? anchors : "",
             (unsigned int)ss_twr_init_tdma_schedule.slot_index,
             (unsigned int)ss_twr_init_tdma_schedule.slot_count);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}

#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
static uint32_t ss_twr_init_diag_delta_us(uint32_t end, uint32_t start)
{
    return k_cyc_to_us_floor32((uint32_t)(end - start));
}

static void ss_twr_init_sweep_diag_maybe_print(void)
{
    char line[192];
    uint32_t wait_ms;
    uint32_t tx_us;
    uint32_t rx_us;
    uint32_t coll_us;
    uint32_t range_us;
    uint32_t solve_us;
    uint32_t out_us;
    uint32_t clean_us;
    uint32_t total_ms;

    ss_twr_init_diag_sweep_count++;
    if (APP_TAG_SWEEP_DIAG_PERIOD != 0U &&
        (ss_twr_init_diag_sweep_count % APP_TAG_SWEEP_DIAG_PERIOD) != 0U) {
        return;
    }

    wait_ms = k_cyc_to_ms_floor32(
        (uint32_t)(ss_twr_init_diag_wait_done_cycles -
                   ss_twr_init_diag_t0_cycles));
    tx_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_tx_done_cycles,
                                      ss_twr_init_diag_wait_done_cycles);
    rx_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_rx_start_cycles,
                                      ss_twr_init_diag_tx_done_cycles);
    coll_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_rx_done_cycles,
                                        ss_twr_init_diag_rx_start_cycles);
    range_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_range_done_cycles,
                                         ss_twr_init_diag_rx_done_cycles);
    solve_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_solve_done_cycles,
                                         ss_twr_init_diag_solve_start_cycles);
    out_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_out_done_cycles,
                                       ss_twr_init_diag_out_start_cycles);
    clean_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_clean_done_cycles,
                                         ss_twr_init_diag_out_done_cycles);
    total_ms = k_cyc_to_ms_floor32(
        (uint32_t)(ss_twr_init_diag_clean_done_cycles -
                   ss_twr_init_diag_t0_cycles));

    snprintk(line, sizeof(line),
             "TDIAG;wait_ms=%lu;tx_us=%lu;rx_us=%lu;coll_us=%lu;range_us=%lu;solve_us=%lu;out_us=%lu;clean_us=%lu;total_ms=%lu",
             (unsigned long)wait_ms,
             (unsigned long)tx_us,
             (unsigned long)rx_us,
             (unsigned long)coll_us,
             (unsigned long)range_us,
             (unsigned long)solve_us,
             (unsigned long)out_us,
             (unsigned long)clean_us,
             (unsigned long)total_ms);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}
#endif

static void ss_twr_init_note_poll_started(void)
{
    uint32_t cycle = k_cycle_get_32();

    if (ss_twr_init_active_anchor_index == 0U ||
        ss_twr_init_sweep_poll_count == 0U) {
        ss_twr_init_sweep_first_poll_cycle = cycle;
        ss_twr_init_sweep_poll_count = 0U;
        ss_twr_init_sweep_timing_valid = true;
    }

    ss_twr_init_sweep_last_poll_cycle = cycle;
    if (ss_twr_init_sweep_poll_count < UINT8_MAX) {
        ss_twr_init_sweep_poll_count++;
    }
}

static void ss_twr_init_note_sweep_done(void)
{
    if (ss_twr_init_sweep_timing_valid) {
        ss_twr_init_sweep_done_cycle = k_cycle_get_32();
    }
}

static const char *ss_twr_init_slot_source_label(uint8_t slot_source)
{
	switch (slot_source) {
	case UWB_TAG_SLOT_SOURCE_MASTER:
		return "MASTER";
	case UWB_TAG_SLOT_SOURCE_SETTINGS:
		return "SETTINGS";
	default:
		return "BUILD";
	}
}

static char ss_twr_init_slot_source_code(uint8_t slot_source)
{
	switch (slot_source) {
	case UWB_TAG_SLOT_SOURCE_MASTER:
		return 'M';
	case UWB_TAG_SLOT_SOURCE_SETTINGS:
		return 'S';
	default:
		return 'B';
	}
}

static const char *ss_twr_init_solve_reason_label(void)
{
	switch (ss_twr_init_last_solve_reason) {
	case SS_TWR_INIT_SOLVE_SUCCESS:
		return "success";
	case SS_TWR_INIT_SOLVE_PENDING:
		return "pending";
	case SS_TWR_INIT_SOLVE_REJECTED:
		return "rejected";
	case SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT:
		return "slot_cut_short";
	default:
		return "none";
	}
}

static char ss_twr_init_solve_reason_code(void)
{
	switch (ss_twr_init_last_solve_reason) {
	case SS_TWR_INIT_SOLVE_SUCCESS:
		return 'S';
	case SS_TWR_INIT_SOLVE_PENDING:
		return 'P';
	case SS_TWR_INIT_SOLVE_REJECTED:
		return 'R';
	case SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT:
		return 'C';
	default:
		return 'N';
	}
}

static char ss_twr_init_plan_code(const char *plan_label)
{
	if (plan_label == NULL) {
		return 'x';
	}

	if (strcmp(plan_label, "track") == 0) {
		return 't';
	}
	if (strcmp(plan_label, "full") == 0) {
		return 'f';
	}
	if (strcmp(plan_label, "refresh") == 0) {
		return 'r';
	}
	if (strcmp(plan_label, "fixed") == 0) {
		return 'x';
	}
	if (strcmp(plan_label, "cal_static") == 0) {
		return 's';
	}
	if (strcmp(plan_label, "cal_roto") == 0) {
		return 'o';
	}

	return 'x';
}

static bool ss_twr_init_anchor_id_in_list(const uint8_t *anchor_ids, size_t count,
                                          uint8_t anchor_id)
{
    for (size_t i = 0; i < count; ++i) {
        if (anchor_ids[i] == anchor_id) {
            return true;
        }
    }

	return false;
}

static size_t ss_twr_init_append_anchor_if_present(uint8_t anchor_id,
                                                   uint8_t *dest_ids,
                                                   size_t dest_count,
                                                   size_t dest_capacity)
{
    if (dest_ids == NULL || dest_count >= dest_capacity ||
        !ss_twr_init_anchor_id_in_list(ss_twr_init_anchor_ids,
                                       ss_twr_init_anchor_count, anchor_id) ||
        ss_twr_init_anchor_id_in_list(dest_ids, dest_count, anchor_id)) {
        return dest_count;
    }

    dest_ids[dest_count++] = anchor_id;
    return dest_count;
}

static size_t ss_twr_init_append_static_cal_group(uint8_t *dest_ids,
                                                  size_t dest_count,
                                                  size_t dest_capacity)
{
    static const uint8_t groups[][4] = {
        {0U, 1U, 2U, 3U},
        {4U, 5U, 6U, 7U},
    };
    uint8_t group = (uint8_t)(ss_twr_init_static_cal_group_cursor % 2U);

    for (size_t i = 0U; i < 4U && dest_count < dest_capacity; ++i) {
        dest_count = ss_twr_init_append_anchor_if_present(
            groups[group][i], dest_ids, dest_count, dest_capacity);
    }

    for (size_t i = 0U; i < ss_twr_init_anchor_count &&
                       dest_count < dest_capacity;
         ++i) {
        dest_count = ss_twr_init_append_anchor_if_present(
            ss_twr_init_anchor_ids[i], dest_ids, dest_count, dest_capacity);
    }

    ss_twr_init_static_cal_group_cursor =
        (uint8_t)((ss_twr_init_static_cal_group_cursor + 1U) % 2U);

    return dest_count;
}

static uint8_t ss_twr_init_anchor_quality(uint8_t anchor_id)
{
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return 0U;
    }

    return uwb_range_tracker_quality_percent(&ss_twr_init_trackers[anchor_id]);
}

static double ss_twr_init_tetra_volume_m3(uint8_t a_id, uint8_t b_id,
                                          uint8_t c_id, uint8_t d_id)
{
    const struct uwb_anchor_pose_mm *a = uwb_anchor_layout_get(a_id);
    const struct uwb_anchor_pose_mm *b = uwb_anchor_layout_get(b_id);
    const struct uwb_anchor_pose_mm *c = uwb_anchor_layout_get(c_id);
    const struct uwb_anchor_pose_mm *d = uwb_anchor_layout_get(d_id);
    double abx, aby, abz;
    double acx, acy, acz;
    double adx, ady, adz;
    double cx, cy, cz;
    double det;

    if (a == NULL || b == NULL || c == NULL || d == NULL) {
        return 0.0;
    }

    abx = ((double)b->x_mm - (double)a->x_mm) / 1000.0;
    aby = ((double)b->y_mm - (double)a->y_mm) / 1000.0;
    abz = ((double)b->z_mm - (double)a->z_mm) / 1000.0;
    acx = ((double)c->x_mm - (double)a->x_mm) / 1000.0;
    acy = ((double)c->y_mm - (double)a->y_mm) / 1000.0;
    acz = ((double)c->z_mm - (double)a->z_mm) / 1000.0;
    adx = ((double)d->x_mm - (double)a->x_mm) / 1000.0;
    ady = ((double)d->y_mm - (double)a->y_mm) / 1000.0;
    adz = ((double)d->z_mm - (double)a->z_mm) / 1000.0;

    cx = acy * adz - acz * ady;
    cy = acz * adx - acx * adz;
    cz = acx * ady - acy * adx;
    det = abx * cx + aby * cy + abz * cz;

    return fabs(det) / 6.0;
}

static size_t ss_twr_init_append_roto_balanced(uint8_t *dest_ids,
                                               size_t dest_count,
                                               size_t dest_capacity)
{
    uint8_t lower_ids[UWB_MAX_ANCHORS];
    uint8_t upper_ids[UWB_MAX_ANCHORS];
    size_t lower_count = 0U;
    size_t upper_count = 0U;
    bool found = false;
    double best_volume = 0.0;
    uint32_t best_quality_sum = 0U;
    uint8_t best_min_quality = 0U;
    uint8_t best_ids[4] = {0U};

    if (dest_ids == NULL || dest_count >= dest_capacity || dest_capacity - dest_count < 4U) {
        return dest_count;
    }

    for (size_t offset = 0U; offset < ss_twr_init_anchor_count; ++offset) {
        size_t idx = (ss_twr_init_roto_cal_group_cursor + offset) %
                     ss_twr_init_anchor_count;
        uint8_t anchor_id = ss_twr_init_anchor_ids[idx];

        if (uwb_anchor_layout_is_lower_plane(anchor_id)) {
            lower_ids[lower_count++] = anchor_id;
        } else if (uwb_anchor_layout_is_upper_plane(anchor_id)) {
            upper_ids[upper_count++] = anchor_id;
        }
    }

    if (lower_count < 2U || upper_count < 2U) {
        return dest_count;
    }

    for (size_t i = 0U; i < lower_count; ++i) {
        for (size_t j = i + 1U; j < lower_count; ++j) {
            for (size_t k = 0U; k < upper_count; ++k) {
                for (size_t l = k + 1U; l < upper_count; ++l) {
                    uint8_t a = lower_ids[i];
                    uint8_t b = lower_ids[j];
                    uint8_t c = upper_ids[k];
                    uint8_t d = upper_ids[l];
                    uint8_t qa = ss_twr_init_anchor_quality(a);
                    uint8_t qb = ss_twr_init_anchor_quality(b);
                    uint8_t qc = ss_twr_init_anchor_quality(c);
                    uint8_t qd = ss_twr_init_anchor_quality(d);
                    uint8_t min_quality = MIN(MIN(qa, qb), MIN(qc, qd));
                    uint32_t quality_sum =
                        (uint32_t)qa + (uint32_t)qb + (uint32_t)qc + (uint32_t)qd;
                    double volume = ss_twr_init_tetra_volume_m3(a, b, c, d);

                    if (volume < APP_TAG_CAL_ROTO_MIN_TETRA_VOLUME_M3) {
                        continue;
                    }

                    if (!found ||
                        quality_sum > best_quality_sum ||
                        (quality_sum == best_quality_sum &&
                         min_quality > best_min_quality) ||
                        (quality_sum == best_quality_sum &&
                         min_quality == best_min_quality &&
                         volume > best_volume)) {
                        found = true;
                        best_quality_sum = quality_sum;
                        best_min_quality = min_quality;
                        best_volume = volume;
                        best_ids[0] = a;
                        best_ids[1] = b;
                        best_ids[2] = c;
                        best_ids[3] = d;
                    }
                }
            }
        }
    }

    if (!found) {
        return dest_count;
    }

    for (size_t i = 0U; i < 4U; ++i) {
        dest_ids[dest_count++] = best_ids[i];
    }

    return dest_count;
}

static size_t ss_twr_init_append_interleaved_plane_anchors(
	const uint8_t *source_ids, size_t source_count, uint8_t *dest_ids,
	size_t dest_count, size_t dest_capacity)
{
	uint8_t lower_ids[UWB_MAX_ANCHORS];
	uint8_t upper_ids[UWB_MAX_ANCHORS];
	size_t lower_count = 0U;
	size_t upper_count = 0U;
	size_t max_count;

	if (source_ids == NULL || dest_ids == NULL) {
		return dest_count;
	}

	for (size_t i = 0; i < source_count; ++i) {
		uint8_t anchor_id = source_ids[i];

		if (ss_twr_init_anchor_id_in_list(dest_ids, dest_count, anchor_id)) {
			continue;
		}

		if (uwb_anchor_layout_is_lower_plane(anchor_id)) {
			lower_ids[lower_count++] = anchor_id;
		} else if (uwb_anchor_layout_is_upper_plane(anchor_id)) {
			upper_ids[upper_count++] = anchor_id;
		}
	}

	max_count = MAX(lower_count, upper_count);
	for (size_t i = 0; i < max_count && dest_count < dest_capacity; ++i) {
		if (i < lower_count && dest_count < dest_capacity) {
			dest_ids[dest_count++] = lower_ids[i];
		}
		if (i < upper_count && dest_count < dest_capacity) {
			dest_ids[dest_count++] = upper_ids[i];
		}
	}

	return dest_count;
}

static bool ss_twr_init_predicted_range_mm(uint8_t anchor_id, int32_t x_mm,
                                           int32_t y_mm, int32_t z_mm,
                                           uint32_t *predicted_mm)
{
    const struct uwb_anchor_pose_mm *pose = uwb_anchor_layout_get(anchor_id);
    double dx;
    double dy;
    double dz;

    if (pose == NULL || predicted_mm == NULL) {
        return false;
    }

    dx = (double)x_mm - (double)pose->x_mm;
    dy = (double)y_mm - (double)pose->y_mm;
    dz = (double)z_mm - (double)pose->z_mm;
    *predicted_mm = (uint32_t)lround(sqrt(dx * dx + dy * dy + dz * dz));
    return true;
}

static bool ss_twr_init_imu_sample_indicates_motion(
    const struct uwb_imu_sample *sample)
{
    uint32_t gravity_error_abs;

    if (sample == NULL) {
        return false;
    }

    gravity_error_abs =
        (sample->gravity_error_mg < 0)
            ? (uint32_t)(-sample->gravity_error_mg)
            : (uint32_t)sample->gravity_error_mg;

    return sample->delta_magnitude_mg > APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG ||
           gravity_error_abs > APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG;
}

#if APP_TAG_TR_IMU_SUMMARY_ENABLE != 0U
static uint8_t ss_twr_init_imu_summary_window(void)
{
    uint32_t window = APP_TAG_TR_IMU_SUMMARY_WINDOW;

    if (window == 0U) {
        window = 1U;
    }
    if (window > SS_TWR_INIT_IMU_SUMMARY_MAX_WINDOW) {
        window = SS_TWR_INIT_IMU_SUMMARY_MAX_WINDOW;
    }
    return (uint8_t)window;
}

static void ss_twr_init_reset_imu_summary(void)
{
    memset(&ss_twr_init_imu_summary, 0, sizeof(ss_twr_init_imu_summary));
    memset(ss_twr_init_imu_norm_ring, 0, sizeof(ss_twr_init_imu_norm_ring));
    ss_twr_init_imu_norm_count = 0U;
    ss_twr_init_imu_norm_pos = 0U;
    ss_twr_init_imu_skip_count = 0U;
}

static void ss_twr_init_recompute_imu_summary(void)
{
    int64_t sum = 0;
    uint64_t var_sum = 0U;
    int32_t min_mg = INT32_MAX;
    int32_t max_mg = INT32_MIN;
    int32_t mean_mg;

    if (ss_twr_init_imu_norm_count == 0U) {
        ss_twr_init_imu_summary.valid = false;
        ss_twr_init_imu_summary.sample_count = 0U;
        ss_twr_init_imu_summary.skip_count = ss_twr_init_imu_skip_count;
        return;
    }

    for (uint8_t i = 0U; i < ss_twr_init_imu_norm_count; ++i) {
        int32_t value = ss_twr_init_imu_norm_ring[i];

        sum += value;
        if (value < min_mg) {
            min_mg = value;
        }
        if (value > max_mg) {
            max_mg = value;
        }
    }

    mean_mg = (int32_t)lround((double)sum /
                              (double)ss_twr_init_imu_norm_count);
    for (uint8_t i = 0U; i < ss_twr_init_imu_norm_count; ++i) {
        int64_t diff = (int64_t)ss_twr_init_imu_norm_ring[i] - mean_mg;

        var_sum += (uint64_t)(diff * diff);
    }

    ss_twr_init_imu_summary.valid = true;
    ss_twr_init_imu_summary.sample_count = ss_twr_init_imu_norm_count;
    ss_twr_init_imu_summary.mean_mg = mean_mg;
    ss_twr_init_imu_summary.std_mg =
        (int32_t)lround(sqrt((double)var_sum /
                             (double)ss_twr_init_imu_norm_count));
    ss_twr_init_imu_summary.min_mg = min_mg;
    ss_twr_init_imu_summary.max_mg = max_mg;
    ss_twr_init_imu_summary.skip_count = ss_twr_init_imu_skip_count;
}

static void ss_twr_init_push_imu_norm_sample(int32_t norm_mg)
{
    uint8_t window = ss_twr_init_imu_summary_window();

    ss_twr_init_imu_norm_ring[ss_twr_init_imu_norm_pos] = norm_mg;
    ss_twr_init_imu_norm_pos =
        (uint8_t)((ss_twr_init_imu_norm_pos + 1U) % window);
    if (ss_twr_init_imu_norm_count < window) {
        ss_twr_init_imu_norm_count++;
    }
    ss_twr_init_recompute_imu_summary();
}

static void ss_twr_init_update_imu_summary_for_sweep(
    struct uwb_imu_sample *sample, bool *have_sample)
{
    bool read_due;

    if (have_sample != NULL) {
        *have_sample = false;
    }

    if (!ss_twr_init_imu_ready) {
        return;
    }

    read_due = !ss_twr_init_have_last_imu_sample ||
               APP_TAG_IMU_SAMPLE_PERIOD <= 1U ||
               (ss_twr_init_sweep_count % APP_TAG_IMU_SAMPLE_PERIOD) == 0U;

    if (read_due) {
        struct uwb_imu_sample fresh;

        if (uwb_imu_read(&fresh)) {
            ss_twr_init_last_imu_sample = fresh;
            ss_twr_init_have_last_imu_sample = true;
            ss_twr_init_push_imu_norm_sample(fresh.norm_mg);
        } else {
            ss_twr_init_imu_skip_count++;
            ss_twr_init_imu_summary.skip_count = ss_twr_init_imu_skip_count;
        }
    }

    if (ss_twr_init_have_last_imu_sample) {
        if (sample != NULL) {
            *sample = ss_twr_init_last_imu_sample;
        }
        if (have_sample != NULL) {
            *have_sample = true;
        }
    }
}
#endif

static bool ss_twr_init_dynamic_context_active(void)
{
    return ss_twr_init_last_imu_indicates_motion ||
           ss_twr_init_last_motion_speed_mm_s >=
               APP_TAG_MOTION_SPEED_THRESHOLD_MM_S;
}

static uint32_t ss_twr_init_effective_range_soft_gate_mm(void)
{
    return APP_TAG_RANGE_SOFT_RESIDUAL_MM +
           (ss_twr_init_dynamic_context_active()
                ? APP_TAG_MOTION_RANGE_SOFT_BONUS_MM
                : 0U);
}

static uint32_t ss_twr_init_effective_range_hard_gate_mm(void)
{
    uint32_t soft_gate = ss_twr_init_effective_range_soft_gate_mm();
    uint32_t hard_gate = APP_TAG_RANGE_HARD_RESIDUAL_MM +
                         (ss_twr_init_dynamic_context_active()
                              ? APP_TAG_MOTION_RANGE_HARD_BONUS_MM
                              : 0U);

    if (hard_gate <= soft_gate) {
        hard_gate = soft_gate + 1U;
    }

    return hard_gate;
}

static uint16_t ss_twr_init_effective_full_sweep_interval(void)
{
    uint16_t interval = APP_TAG_FULL_SWEEP_INTERVAL;

    if (ss_twr_init_dynamic_context_active() &&
        APP_TAG_MOTION_FULL_SWEEP_INTERVAL > 1U &&
        (interval <= 1U || APP_TAG_MOTION_FULL_SWEEP_INTERVAL < interval)) {
        interval = APP_TAG_MOTION_FULL_SWEEP_INTERVAL;
    }

    return interval;
}

static uint16_t ss_twr_init_effective_multitag_full_interval(void)
{
	uint16_t interval = ss_twr_init_full_sweep_interval_sweeps;

	if (interval == 0U) {
		return 0U;
	}

	/*
	 * In 4+ tag TDMA, overly frequent maintenance full sweeps can dominate a
	 * tag's slot budget and create severe per-tag cadence imbalance. Enforce
	 * a minimum interval so track sweeps remain the steady-state path.
	 */
	if (ss_twr_init_tdma_schedule.enabled &&
	    ss_twr_init_tdma_schedule.slot_count >= 4U &&
	    interval < 4U) {
		interval = 4U;
	}

	return interval;
}

static size_t ss_twr_init_effective_track_anchor_budget(void)
{
	size_t desired_count = APP_TAG_TRACK_ANCHOR_COUNT;

	if (desired_count < 4U) {
		desired_count = 4U;
	}
	if (desired_count > ss_twr_init_anchor_count) {
		desired_count = ss_twr_init_anchor_count;
	}

	/*
	 * In multi-tag TDMA operation, keep candidate set bounded for cadence,
	 * but preserve enough diversity for robust 2+2 selection in solver.
	 * Target: ~6 candidates in track sweeps, final solve still selects best 4.
	 */
	if (ss_twr_init_tdma_schedule.enabled &&
	    ss_twr_init_tdma_schedule.slot_count >= 4U &&
	    desired_count > 6U) {
		desired_count = 6U;
	}

	return desired_count;
}

static bool ss_twr_init_apply_range_continuity_gate(uint8_t anchor_id,
                                                    uint32_t range_mm,
                                                    uint8_t *quality_percent)
{
    uint32_t predicted_mm = 0U;
    uint32_t residual_mm;
    uint32_t soft_gate_mm;
    uint32_t hard_gate_mm;
    uint8_t original_quality;

    if (!APP_TAG_RANGE_CONTINUITY_ENABLE || quality_percent == NULL ||
        !ss_twr_init_have_last_location) {
        ss_twr_init_record_sweep_anchor_diag(anchor_id, SS_TWR_INIT_CAL_REASON_OK,
                                             (int32_t)range_mm, range_mm, 0U, 0U,
                                             (quality_percent != NULL) ? *quality_percent : 0U);
        return true;
    }

    /*
     * Calibration modes are CM-first data collection modes.  The tag does not
     * own the authoritative layout here, so a stale pre-CFG location must not
     * suppress otherwise valid ranges.  Keep the leg visible and let offline
     * solver/QF logic judge quality.
     */
    if (ss_twr_init_runtime_any_calibration_mode()) {
        ss_twr_init_record_sweep_anchor_diag(anchor_id, SS_TWR_INIT_CAL_REASON_OK,
                                             (int32_t)range_mm, range_mm, 0U, 0U,
                                             *quality_percent);
        return true;
    }

    if (ss_twr_init_location_output_count < APP_TAG_RANGE_CONTINUITY_WARMUP_SWEEPS) {
        ss_twr_init_record_sweep_anchor_diag(anchor_id, SS_TWR_INIT_CAL_REASON_OK,
                                             (int32_t)range_mm, range_mm, 0U, 0U,
                                             *quality_percent);
        return true;
    }

    if (!ss_twr_init_predicted_range_mm(anchor_id, ss_twr_init_last_location_x_mm,
                                        ss_twr_init_last_location_y_mm,
                                        ss_twr_init_last_location_z_mm,
                                        &predicted_mm)) {
        ss_twr_init_record_sweep_anchor_diag(anchor_id, SS_TWR_INIT_CAL_REASON_OK,
                                             (int32_t)range_mm, range_mm, 0U, 0U,
                                             *quality_percent);
        return true;
    }

    residual_mm = (range_mm > predicted_mm) ? (range_mm - predicted_mm) :
                                              (predicted_mm - range_mm);

    soft_gate_mm = ss_twr_init_effective_range_soft_gate_mm();
    hard_gate_mm = ss_twr_init_effective_range_hard_gate_mm();
    original_quality = *quality_percent;

    if (residual_mm >= hard_gate_mm) {
        ss_twr_init_record_sweep_anchor_diag(anchor_id,
                                             SS_TWR_INIT_CAL_REASON_CONTINUITY_HARD,
                                             (int32_t)range_mm, range_mm,
                                             predicted_mm, residual_mm,
                                             *quality_percent);
        return false;
    }

    if (residual_mm > soft_gate_mm) {
        uint32_t overshoot = residual_mm - soft_gate_mm;
        uint32_t penalty = 20U + ((overshoot * 40U) /
                                  (hard_gate_mm - soft_gate_mm));

        if (penalty >= *quality_percent) {
            *quality_percent = 0U;
        } else {
            *quality_percent = (uint8_t)(*quality_percent - penalty);
        }
        ss_twr_init_record_sweep_anchor_diag(anchor_id,
                                             SS_TWR_INIT_CAL_REASON_CONTINUITY_SOFT,
                                             (int32_t)range_mm, range_mm,
                                             predicted_mm, residual_mm,
                                             *quality_percent);
    } else if (original_quality == *quality_percent) {
        ss_twr_init_record_sweep_anchor_diag(anchor_id, SS_TWR_INIT_CAL_REASON_OK,
                                             (int32_t)range_mm, range_mm,
                                             predicted_mm, residual_mm,
                                             *quality_percent);
    }

    return true;
}

static bool ss_twr_init_raw_range_plausible(
    const struct uwb_range_tracker *tracker, uint32_t raw_mm)
{
    uint32_t delta_mm;

    if (tracker == NULL) {
        return false;
    }

    if (raw_mm == 0U) {
        return false;
    }

    if (!tracker->filtered_valid ||
        tracker->raw_count < UWB_RANGE_TRACKER_WINDOW_SIZE) {
        return true;
    }

    if (APP_TAG_RANGE_FILTER_OUTLIER_MM == 0U) {
        return true;
    }

    /*
     * CAL_ROTO after prewarm deliberately tracks fast-changing geometry.
     * A fixed raw-vs-last-raw delta gate misclassifies real motion as an
     * outlier and drops otherwise valid LOS anchors before the continuity
     * gate or solver can score them. Keep the conservative gate for static
     * and prewarm phases, but let mature CAL_ROTO frames flow downstream.
     */
    if (ss_twr_init_runtime_roto_calibration_mode() &&
        !ss_twr_init_roto_prewarm_active()) {
        return true;
    }

    delta_mm = (raw_mm > tracker->filtered_mm)
                   ? (raw_mm - tracker->filtered_mm)
                   : (tracker->filtered_mm - raw_mm);
    return delta_mm <= APP_TAG_RANGE_FILTER_OUTLIER_MM;
}

static uint32_t ss_twr_init_location_step_mm(int32_t x_mm, int32_t y_mm,
                                             int32_t z_mm)
{
    int64_t dx;
    int64_t dy;
    int64_t dz;
    double dist_sq;

    if (!ss_twr_init_have_last_location) {
        return 0U;
    }

    dx = (int64_t)x_mm - (int64_t)ss_twr_init_last_location_x_mm;
    dy = (int64_t)y_mm - (int64_t)ss_twr_init_last_location_y_mm;
    dz = (int64_t)z_mm - (int64_t)ss_twr_init_last_location_z_mm;
    dist_sq = (double)(dx * dx + dy * dy + dz * dz);

    return (uint32_t)lround(sqrt(dist_sq));
}

static bool ss_twr_init_location_plausible(
    const struct uwb_tag_location_result *location,
    uint32_t *step_mm_out)
{
    uint32_t step_mm = 0U;
    bool enforce_quality_limits = true;

    if (location == NULL) {
        return false;
    }

    /*
     * In sparse-anchor operation (e.g. temporary visibility drop to 4-5 anchors),
     * strict residual gates can reject every estimate and stall output forever.
     */
    if (location->used_anchor_count < 6U) {
        enforce_quality_limits = false;
    }

    if (enforce_quality_limits &&
        APP_TAG_OUTPUT_MAX_RMS_MM != 0U &&
        location->residual_rms_mm > APP_TAG_OUTPUT_MAX_RMS_MM) {
        return false;
    }

    if (enforce_quality_limits &&
        APP_TAG_OUTPUT_MAX_MAX_MM != 0U &&
        location->residual_max_mm > APP_TAG_OUTPUT_MAX_MAX_MM) {
        return false;
    }

    if (ss_twr_init_have_last_location && APP_TAG_OUTPUT_MAX_STEP_MM != 0U) {
        step_mm = ss_twr_init_location_step_mm(location->x_mm, location->y_mm,
                                               location->z_mm);
        if (step_mm > APP_TAG_OUTPUT_MAX_STEP_MM) {
            return false;
        }
    }

    if (step_mm_out != NULL) {
        *step_mm_out = step_mm;
    }

    return true;
}

static uint32_t ss_twr_init_location_speed_mm_s(
    const struct uwb_tag_location_result *location, uint32_t now_ms,
    uint32_t *step_mm_out, uint32_t *dt_ms_out)
{
    uint32_t step_mm;
    uint32_t dt_ms;

    if (location == NULL || !ss_twr_init_have_last_location ||
        ss_twr_init_last_output_ms == 0U) {
        if (step_mm_out != NULL) {
            *step_mm_out = 0U;
        }
        if (dt_ms_out != NULL) {
            *dt_ms_out = 0U;
        }
        return 0U;
    }

    step_mm = ss_twr_init_location_step_mm(location->x_mm, location->y_mm,
                                          location->z_mm);
    dt_ms = now_ms - ss_twr_init_last_output_ms;
    if (dt_ms == 0U) {
        dt_ms = 1U;
    }

    if (step_mm_out != NULL) {
        *step_mm_out = step_mm;
    }
    if (dt_ms_out != NULL) {
        *dt_ms_out = dt_ms;
    }

    return (uint32_t)(((uint64_t)step_mm * 1000ULL) / (uint64_t)dt_ms);
}

static bool ss_twr_init_output_filter_reject(
    const struct uwb_tag_location_result *location, uint32_t now_ms,
    uint32_t *step_mm_out, uint32_t *dt_ms_out, uint32_t *speed_mm_s_out,
    const char **reason_out)
{
    uint32_t step_mm = 0U;
    uint32_t dt_ms = 0U;
    uint32_t speed_mm_s = 0U;
    const char *reason = "ok";

    if (location == NULL) {
        reason = "null";
        goto reject;
    }

    if (!ss_twr_init_runtime_any_calibration_mode() &&
        APP_TAG_OUTPUT_FILTER_RMS_MM != 0U &&
        location->residual_rms_mm > APP_TAG_OUTPUT_FILTER_RMS_MM) {
        reason = "rms";
        goto reject;
    }

    speed_mm_s = ss_twr_init_location_speed_mm_s(location, now_ms, &step_mm,
                                                &dt_ms);
    if (!ss_twr_init_runtime_any_calibration_mode() &&
        APP_TAG_OUTPUT_FILTER_SPEED_MM_S != 0U && dt_ms != 0U &&
        speed_mm_s > APP_TAG_OUTPUT_FILTER_SPEED_MM_S) {
        reason = "speed";
        goto reject;
    }

    if (step_mm_out != NULL) {
        *step_mm_out = step_mm;
    }
    if (dt_ms_out != NULL) {
        *dt_ms_out = dt_ms;
    }
    if (speed_mm_s_out != NULL) {
        *speed_mm_s_out = speed_mm_s;
    }
    if (reason_out != NULL) {
        *reason_out = reason;
    }
    return false;

reject:
    if (step_mm_out != NULL) {
        *step_mm_out = step_mm;
    }
    if (dt_ms_out != NULL) {
        *dt_ms_out = dt_ms;
    }
    if (speed_mm_s_out != NULL) {
        *speed_mm_s_out = speed_mm_s;
    }
    if (reason_out != NULL) {
        *reason_out = reason;
    }
    return true;
}

static void ss_twr_init_publish_filtered_position(
    const struct uwb_tag_location_result *location, const char *plan_label,
    uint8_t qf_percent, const char *anchor_labels, const char *filter_reason,
    uint32_t step_mm, uint32_t dt_ms, uint32_t speed_mm_s)
{
    char line[224];

    if (APP_TAG_POSITION_OUTPUT_ENABLE == 0U || location == NULL ||
        ss_twr_init_runtime_any_calibration_mode()) {
        return;
    }

    snprintk(line, sizeof(line),
             "TF;1;%lu;%c;%ld;%ld;%ld;%lu;%lu;%s;%u;%u;%c;%u;%c;%u;%s;%lu;%lu;%lu;%u;%s;%u",
             (unsigned long)ss_twr_init_sweep_count,
             ss_twr_init_plan_code(plan_label),
             (long)location->x_mm, (long)location->y_mm,
             (long)location->z_mm,
             (unsigned long)location->residual_rms_mm,
             (unsigned long)location->residual_max_mm,
             (anchor_labels != NULL) ? anchor_labels : "",
             (unsigned int)ss_twr_init_tdma_schedule.slot_index,
             (unsigned int)ss_twr_init_tdma_schedule.slot_count,
             ss_twr_init_slot_source_code(ss_twr_init_runtime_params.slot_source),
             (unsigned int)ss_twr_init_last_sweep_cut_short,
             ss_twr_init_solve_reason_code(),
             (unsigned int)ss_twr_init_runtime_params.positioning_mode,
             (filter_reason != NULL) ? filter_reason : "unknown",
             (unsigned long)step_mm,
             (unsigned long)dt_ms,
             (unsigned long)speed_mm_s,
             (unsigned int)qf_percent,
             plan_label,
             (unsigned int)location->used_anchor_count);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_anchor_label_string(
    const struct uwb_tag_location_result *location, char *out, size_t out_len)
{
    size_t len = 0U;

    if (out == NULL || out_len == 0U) {
        return;
    }

    out[0] = '\0';
    if (location == NULL) {
        return;
    }

    for (size_t i = 0; i < location->used_anchor_count &&
                       len + 2U < out_len;
         ++i) {
        const struct uwb_anchor_pose_mm *pose =
            uwb_anchor_layout_get(location->anchor_ids[i]);

        if (pose != NULL) {
            out[len++] = pose->label;
        } else {
            len += snprintk(&out[len], out_len - len, "%u",
                            (unsigned int)location->anchor_ids[i]);
        }
    }
    out[len] = '\0';
}

static void ss_twr_init_sleep_between_ranges(void)
{
    if (SS_TWR_INIT_RNG_DELAY_MS > 0U) {
        k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
    }

    if (ss_twr_init_runtime_any_calibration_mode() &&
        SS_TWR_INIT_CAL_RNG_SETTLE_US > 0U) {
        k_busy_wait(SS_TWR_INIT_CAL_RNG_SETTLE_US);
    }
}

static void ss_twr_init_prepare_radio_for_poll(void)
{
    /*
     * Consecutive 4-anchor CAL sweeps stress the DW1000 state machine more than
     * single-leg debug tests.  Return to idle and clear stale TX/RX state before
     * every poll so the next immediate TX cannot inherit a previous RX timeout or
     * good-frame latch.
     */
    dwt_forcetrxoff();
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
}

static bool ss_twr_init_should_retry_current_cal_anchor(void)
{
    if (!ss_twr_init_runtime_any_calibration_mode() ||
        ss_twr_init_roto_prewarm_active() ||
        ss_twr_init_active_anchor_count != 4U ||
        ss_twr_init_current_anchor_retry_count >= 1U) {
        return false;
    }

    return true;
}

static const char *ss_twr_init_plan_label(void)
{
    if (ss_twr_init_runtime_roto_calibration_mode()) {
        return "cal_roto";
    }

    if (ss_twr_init_runtime_static_calibration_mode()) {
        return "cal_static";
    }

    if (ss_twr_init_fixed_anchor_mode) {
        return "fixed";
    }

    if (ss_twr_init_current_sweep_refresh) {
        return "refresh";
    }

    return ss_twr_init_current_sweep_full ? "full" : "track";
}

static uint32_t ss_twr_init_alt_bcast_response_window_us(size_t anchor_count)
{
    uint32_t window_us;

    if (anchor_count == 0U) {
        return 0U;
    }

    window_us = APP_ALT_SS_TWR_GUARD_US +
                (((uint32_t)anchor_count - 1U) *
                 APP_ALT_SS_TWR_RESP_SPACING_US) +
                SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US;

#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
    /*
     * The broadcast collector starts after TXFRS, not at poll TX start.  Keep
     * it open until the last response plus tail, but do not charge the poll
     * airtime twice.
     */
    if (window_us > SS_TWR_INIT_ALT_BCAST_POLL_AIRTIME_US) {
        window_us -= SS_TWR_INIT_ALT_BCAST_POLL_AIRTIME_US;
    }
#endif

    return window_us;
}

static uint32_t ss_twr_init_alt_bcast_response_window_estimated_us(size_t anchor_count)
{
    uint32_t window_us = ss_twr_init_alt_bcast_response_window_us(anchor_count);

    /*
     * The collector window starts after TX-done/RX-enable work has already
     * consumed part of the poll-to-last-response interval.  Use this estimate
     * only for TDMA admission budgeting; the actual collector still uses the
     * full response window.
     */
    if (window_us > 800U) {
        return window_us - 800U;
    }

    return window_us;
}

static uint32_t ss_twr_init_tdma_period_remaining_ms(void)
{
    const struct uwb_tdma_schedule *schedule = &ss_twr_init_tdma_schedule;
    uint32_t cycle_ms;
    uint32_t phase_ms;
    uint32_t slot_start_ms;
    uint32_t slot_end_ms;
    uint8_t slot;
    uint16_t slot_mask;

    if (!uwb_tdma_schedule_is_valid(schedule) ||
        schedule->slot_period_ms == 0U || schedule->slot_count == 0U) {
        return UINT32_MAX;
    }
    if (schedule->epoch_valid &&
        (int32_t)(k_uptime_get_32() - schedule->sync_local_ms) < 0) {
        return 0U;
    }

    cycle_ms = (uint32_t)schedule->slot_count *
               (uint32_t)schedule->slot_period_ms;
    if (cycle_ms == 0U) {
        return 0U;
    }

    phase_ms = uwb_tdma_schedule_now_ms(schedule) % cycle_ms;
    slot = (uint8_t)(phase_ms / (uint32_t)schedule->slot_period_ms);
    if (slot >= schedule->slot_count) {
        return 0U;
    }

    slot_mask = schedule->slot_mask;
    if (slot_mask == 0U && schedule->slot_index < schedule->slot_count) {
        slot_mask = (uint16_t)(1U << schedule->slot_index);
    }
    if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
        return 0U;
    }

    slot_start_ms = (uint32_t)slot * (uint32_t)schedule->slot_period_ms;
    slot_end_ms = slot_start_ms + (uint32_t)schedule->slot_period_ms;
    if (phase_ms < slot_start_ms || phase_ms >= slot_end_ms) {
        return 0U;
    }

    return slot_end_ms - phase_ms;
}

static bool ss_twr_init_tdma_exchange_can_start(void)
{
	uint32_t required_ms = SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS;

#if APP_ALT_SS_TWR_ENABLE && APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
	if (ss_twr_init_active_anchor_index == 0U &&
	    ss_twr_init_active_anchor_count > 1U) {
		uint32_t window_us = ss_twr_init_alt_bcast_response_window_estimated_us(
			ss_twr_init_active_anchor_count);

		required_ms = (window_us + 999U) / 1000U;
		if (ss_twr_init_tdma_schedule.enabled) {
			return ss_twr_init_tdma_period_remaining_ms() >= required_ms;
		}
	}
#endif

	return uwb_tdma_schedule_exchange_fits(&ss_twr_init_tdma_schedule,
					       required_ms,
					       SS_TWR_INIT_SLOT_GUARD_MARGIN_MS);
}

static bool ss_twr_init_tdma_active_guard_enabled(void)
{
	/*
	 * CAL_ROTO prewarm is a responder/link-state probe, not a positioning
	 * frame.  It must be allowed to run the full 8-anchor handshake even when
	 * the later fast 4-anchor positioning slot uses a shorter active window.
	 */
	return !ss_twr_init_roto_prewarm_active();
}

static void ss_twr_init_publish_tdma_diag(const char *reason,
					  uint32_t remain_ms,
					  uint32_t need_ms)
{
	static uint32_t last_diag_ms;
	uint32_t now_ms = (uint32_t)k_uptime_get();
	char line[192];

	if ((now_ms - last_diag_ms) < 5000U) {
		return;
	}
	last_diag_ms = now_ms;

	snprintk(line, sizeof(line),
		 "TD;1;%lu;%u;%s;%u;%u;%u;%u;%u;%lu;%lu;%u;%u;%lu",
		 (unsigned long)ss_twr_init_sweep_count,
		 (unsigned int)ss_twr_init_runtime_params.positioning_mode,
		 reason != NULL ? reason : "-",
		 (unsigned int)ss_twr_init_tdma_schedule.enabled,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_index,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_count,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_mask,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
		 (unsigned long)remain_ms,
		 (unsigned long)need_ms,
		 (unsigned int)ss_twr_init_active_anchor_index,
		 (unsigned int)ss_twr_init_active_anchor_count,
		 (unsigned long)ss_twr_init_last_tdma_wait_ms);
#if APP_TAG_BLE_ENABLE
	(void)uwb_tag_ble_publish_status(line);
#endif
	printk("%s\n", line);
}

static bool ss_twr_init_runtime_static_calibration_mode(void)
{
	if (APP_TAG_CALIBRATION_MODE != 0U) {
		return true;
	}

	return ss_twr_init_runtime_params.positioning_mode ==
	       UWB_TAG_POSITIONING_MODE_CAL_STATIC;
}

static bool ss_twr_init_runtime_roto_calibration_mode(void)
{
	return ss_twr_init_runtime_params.positioning_mode ==
	       UWB_TAG_POSITIONING_MODE_CAL_ROTO;
}

static bool ss_twr_init_roto_prewarm_active(void)
{
	if (!ss_twr_init_runtime_roto_calibration_mode() ||
	    ss_twr_init_roto_prewarm_deadline_ms == 0U) {
		return false;
	}

	return (uint32_t)k_uptime_get() < ss_twr_init_roto_prewarm_deadline_ms;
}

static bool ss_twr_init_runtime_any_calibration_mode(void)
{
	return ss_twr_init_runtime_static_calibration_mode() ||
	       ss_twr_init_runtime_roto_calibration_mode();
}

static bool ss_twr_init_runtime_anchor_ota_mode(void)
{
	return ss_twr_init_runtime_params.positioning_mode ==
	       UWB_TAG_POSITIONING_MODE_ANCHOR_OTA;
}

static bool ss_twr_init_tdma_exchange_can_start_if_needed(void)
{
	if (!ss_twr_init_tdma_active_guard_enabled()) {
		return true;
	}

	return ss_twr_init_tdma_exchange_can_start();
}

static void ss_twr_init_set_ble_tx_paused(bool paused)
{
#if APP_TAG_BLE_ENABLE
	uwb_tag_ble_set_tx_paused(paused);
#else
	ARG_UNUSED(paused);
#endif
}

static void ss_twr_init_release_ble_tx_after_active_slot(void)
{
#if APP_TAG_BLE_ENABLE
	uwb_tag_ble_set_tx_paused(false);

	if (ss_twr_init_tdma_schedule.enabled) {
		uint32_t remain_ms =
			uwb_tdma_schedule_time_remaining_ms(&ss_twr_init_tdma_schedule);

		if (remain_ms > 0U) {
			k_msleep(remain_ms + 1U);
		}
	}
#endif
}

static uint32_t ss_twr_init_wait_until_slot_if_needed(void)
{
	return uwb_tdma_wait_until_slot(&ss_twr_init_tdma_schedule);
}

static uint32_t ss_twr_init_wait_until_next_slot_if_needed(void)
{
	return uwb_tdma_wait_until_next_slot(&ss_twr_init_tdma_schedule);
}

static void ss_twr_init_reset_tracking_history(void)
{
	ss_twr_init_have_last_solution = false;
	ss_twr_init_last_solution_anchor_count = 0U;
	ss_twr_init_have_last_location = false;
	ss_twr_init_location_output_count = 0U;
#if APP_TAG_STATUS_PERIOD_MS > 0U
	ss_twr_init_have_last_raw_location = false;
	ss_twr_init_last_location_update_ms = 0U;
	memset(&ss_twr_init_last_raw_location, 0, sizeof(ss_twr_init_last_raw_location));
	memset(&ss_twr_init_last_filtered_location, 0,
	       sizeof(ss_twr_init_last_filtered_location));
#endif
	ss_twr_init_last_location_x_mm = 0;
	ss_twr_init_last_location_y_mm = 0;
	ss_twr_init_last_location_z_mm = 0;
	ss_twr_init_last_output_ms = 0U;
	ss_twr_init_last_solve_pending_log_ms = 0U;
	ss_twr_init_last_solve_diag_ms = 0U;
	ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_NONE;
	ss_twr_init_perf_motion_dt_sum_ms = 0U;
	ss_twr_init_perf_track_sweep_sum_ms = 0U;
	ss_twr_init_perf_full_sweep_sum_ms = 0U;
	ss_twr_init_perf_motion_dt_count = 0U;
	ss_twr_init_perf_track_sweep_count = 0U;
	ss_twr_init_perf_full_sweep_count = 0U;
	uwb_motion_reset();
	uwb_ekf_reset();

	for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
		uint8_t anchor_id = ss_twr_init_anchor_ids[i];

		if (anchor_id < UWB_MAX_ANCHORS) {
			uwb_range_tracker_init(&ss_twr_init_trackers[anchor_id],
					       uwb_anchor_short_addr(anchor_id));
		}
	}
}

static void ss_twr_init_apply_runtime_params(
	const struct uwb_tag_runtime_params *params)
{
	bool reset_history;
	uint16_t previous_local_addr;

	if (params == NULL) {
		return;
	}

	reset_history =
		params->positioning_mode != ss_twr_init_runtime_params.positioning_mode ||
		params->anchor_selection_mode != ss_twr_init_runtime_params.anchor_selection_mode ||
		params->tdma.generation != ss_twr_init_tdma_schedule.generation ||
		params->tdma.slot_index != ss_twr_init_tdma_schedule.slot_index ||
		params->tdma.slot_count != ss_twr_init_tdma_schedule.slot_count ||
		params->tdma.slot_period_ms != ss_twr_init_tdma_schedule.slot_period_ms ||
		params->tdma.slot_active_ms != ss_twr_init_tdma_schedule.slot_active_ms;

	ss_twr_init_runtime_params = *params;
	previous_local_addr = ss_twr_init_local_addr;
	ss_twr_init_local_tag_id = params->logical_tag_id;
	ss_twr_init_local_addr = uwb_tag_short_addr(ss_twr_init_local_tag_id);
	if (ss_twr_init_radio_configured &&
	    ss_twr_init_local_addr != previous_local_addr) {
		dwt_setaddress16(ss_twr_init_local_addr);
		printk("Tag UWB short addr updated 0x%04x -> 0x%04x\n",
		       (unsigned int)previous_local_addr,
		       (unsigned int)ss_twr_init_local_addr);
	}
	ss_twr_init_tdma_schedule = params->tdma;
	ss_twr_init_static_cal_group_cursor = 0U;
	ss_twr_init_static_cal_slot_tick = 0U;
	ss_twr_init_roto_cal_group_cursor = 0U;
	ss_twr_init_roto_prewarm_deadline_ms =
		(params->positioning_mode == UWB_TAG_POSITIONING_MODE_CAL_ROTO) ?
			((uint32_t)k_uptime_get() + APP_TAG_CAL_ROTO_PREWARM_MS) :
			0U;

	if (params->anchor_selection_mode == UWB_TAG_ANCHOR_SELECTION_FIXED_SUBSET &&
	    params->fixed_anchor_count >= 4U) {
		ss_twr_init_fixed_anchor_mode = true;
		ss_twr_init_fixed_anchor_count =
			MIN((size_t)params->fixed_anchor_count,
			    (size_t)UWB_TAG_FIXED_ANCHOR_MAX);
		memcpy(ss_twr_init_fixed_anchor_ids, params->fixed_anchor_ids,
		       sizeof(ss_twr_init_fixed_anchor_ids));
	} else if (params->anchor_selection_mode ==
		   UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2) {
		ss_twr_init_fixed_anchor_mode = false;
		ss_twr_init_fixed_anchor_count = 0U;
	}

	if (reset_history) {
		ss_twr_init_reset_tracking_history();
		printk("Tag runtime tracking reset pmode=%u slot=%u/%u gen=%u\n",
		       (unsigned int)ss_twr_init_runtime_params.positioning_mode,
		       (unsigned int)ss_twr_init_tdma_schedule.slot_index,
		       (unsigned int)ss_twr_init_tdma_schedule.slot_count,
		       (unsigned int)ss_twr_init_tdma_schedule.generation);
	}
}

static void ss_twr_init_apply_pending_runtime_config_if_any(void)
{
	if (!ss_twr_init_runtime_update_pending) {
		return;
	}

	ss_twr_init_apply_runtime_params(&ss_twr_init_pending_runtime_params);
	ss_twr_init_runtime_update_pending = false;
	ss_twr_init_active_anchor_index = 0U;
	ss_twr_init_prepare_sweep_plan();
	printk("Tag runtime config applied tag=%u slot=%u/%u period=%u active=%u active_us=%u source=%s gen=%u amode=%u\n",
	       (unsigned int)ss_twr_init_runtime_params.logical_tag_id,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_index,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_count,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_active_us,
	       ss_twr_init_slot_source_label(ss_twr_init_runtime_params.slot_source),
	       (unsigned int)ss_twr_init_tdma_schedule.generation,
	       (unsigned int)ss_twr_init_runtime_params.anchor_selection_mode);
}

static void ss_twr_init_format_anchor_labels(const uint8_t *anchor_ids,
                                             size_t anchor_count, char *out,
                                             size_t out_len)
{
    size_t pos = 0U;

    if (out == NULL || out_len == 0U) {
        return;
    }

    out[0] = '\0';
    if (anchor_ids == NULL || anchor_count == 0U) {
        return;
    }

    for (size_t i = 0; i < anchor_count && pos + 2U < out_len; ++i) {
        const struct uwb_anchor_pose_mm *pose =
            uwb_anchor_layout_get(anchor_ids[i]);

        if (i != 0U && pos + 1U < out_len) {
            out[pos++] = ',';
        }

        if (pose != NULL) {
            out[pos++] = pose->label;
        } else {
            pos += (size_t)snprintk(out + pos, out_len - pos, "%u",
                                    (unsigned int)anchor_ids[i]);
        }
    }

    out[pos < out_len ? pos : out_len - 1U] = '\0';
}

static uint8_t ss_twr_init_compute_target_quality_percent(
    const struct uwb_tag_measurement *measurements, size_t measurement_count)
{
    uint32_t quality_sum = 0U;
    uint8_t quality_count = 0U;

    for (size_t i = 0U; i < ss_twr_init_active_anchor_count; ++i) {
        uint8_t target_anchor_id = ss_twr_init_active_anchor_ids[i];

        for (size_t j = 0U; j < measurement_count; ++j) {
            if (measurements[j].anchor_id == target_anchor_id) {
                quality_sum += measurements[j].quality_percent;
                quality_count++;
                break;
            }
        }
    }

    return (quality_count != 0U) ? (uint8_t)(quality_sum / quality_count) : 0U;
}

static void ss_twr_init_publish_calibration_summary(
    const char *plan_label, uint8_t positioning_mode, uint8_t qf_percent)
{
    char targets[32];
    char statuses[64];
    char qualities[32];
    char line[256];
    size_t targets_pos = 0U;
    size_t statuses_pos = 0U;
    size_t qualities_pos = 0U;

    if (!ss_twr_init_runtime_any_calibration_mode() ||
        ss_twr_init_active_anchor_count == 0U) {
        return;
    }

    targets[0] = '\0';
    statuses[0] = '\0';
    qualities[0] = '\0';

    for (size_t i = 0U; i < ss_twr_init_active_anchor_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        const struct uwb_anchor_pose_mm *pose = uwb_anchor_layout_get(anchor_id);

        if (i != 0U) {
            if (targets_pos + 1U < sizeof(targets)) {
                targets[targets_pos++] = ',';
                targets[targets_pos] = '\0';
            }
            statuses_pos += (size_t)snprintk(
                statuses + statuses_pos, sizeof(statuses) - statuses_pos, ",");
            qualities_pos += (size_t)snprintk(
                qualities + qualities_pos, sizeof(qualities) - qualities_pos, ",");
        }

        if (pose != NULL && targets_pos + 1U < sizeof(targets)) {
            targets[targets_pos++] = pose->label;
            targets[targets_pos] = '\0';
        } else {
            targets_pos += (size_t)snprintk(
                targets + targets_pos, sizeof(targets) - targets_pos, "%u",
                (unsigned int)anchor_id);
        }

        statuses_pos += (size_t)snprintk(
            statuses + statuses_pos, sizeof(statuses) - statuses_pos, "%s",
            ss_twr_init_cal_status_label(ss_twr_init_sweep_anchor_status[anchor_id]));
        qualities_pos += (size_t)snprintk(
            qualities + qualities_pos, sizeof(qualities) - qualities_pos, "%u",
            (unsigned int)ss_twr_init_sweep_anchor_quality[anchor_id]);
    }

    snprintk(line, sizeof(line), "CS;1;%lu;%s;%u;%u;%s;%s;%s",
             (unsigned long)ss_twr_init_sweep_count, plan_label,
             (unsigned int)positioning_mode, (unsigned int)qf_percent, targets,
             statuses, qualities);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}

#if APP_TAG_STATUS_PERIOD_MS > 0U
static void ss_twr_init_status_work_handler(struct k_work *work)
{
    char anchors[32];
    uint32_t now_ms = (uint32_t)k_uptime_get();
    uint32_t age_ms = 0U;

    ARG_UNUSED(work);

    if (APP_TAG_STATUS_PERIOD_MS == 0U) {
        return;
    }

    if (!ss_twr_init_have_last_raw_location ||
        !ss_twr_init_have_last_location) {
        printk("UWB TAG STATUS pending plan=%s\n", ss_twr_init_plan_label());
    } else {
        age_ms = now_ms - ss_twr_init_last_location_update_ms;
        ss_twr_init_format_anchor_labels(
            ss_twr_init_last_filtered_location.anchor_ids,
            ss_twr_init_last_filtered_location.used_anchor_count, anchors,
            sizeof(anchors));
        printk("UWB TAG STATUS age=%lu ms plan=%s raw_xyz=(%ld,%ld,%ld) mm "
               "xyz=(%ld,%ld,%ld) mm used=%u lower=%u upper=%u rms=%lu mm "
               "max=%lu mm anchors=[%s]\n",
               (unsigned long)age_ms, ss_twr_init_plan_label(),
               (long)ss_twr_init_last_raw_location.x_mm,
               (long)ss_twr_init_last_raw_location.y_mm,
               (long)ss_twr_init_last_raw_location.z_mm,
               (long)ss_twr_init_last_filtered_location.x_mm,
               (long)ss_twr_init_last_filtered_location.y_mm,
               (long)ss_twr_init_last_filtered_location.z_mm,
               (unsigned int)ss_twr_init_last_filtered_location.used_anchor_count,
               (unsigned int)ss_twr_init_last_filtered_location.lower_anchor_count,
               (unsigned int)ss_twr_init_last_filtered_location.upper_anchor_count,
               (unsigned long)ss_twr_init_last_filtered_location.residual_rms_mm,
               (unsigned long)ss_twr_init_last_filtered_location.residual_max_mm,
               anchors);
    }

    (void)k_work_reschedule(&ss_twr_init_status_work,
                            K_MSEC(APP_TAG_STATUS_PERIOD_MS));
}
#endif

static void ss_twr_init_add_refresh_plan_anchor(uint8_t anchor_id,
                                                size_t *active_count)
{
    if (*active_count >= UWB_MAX_ANCHORS) {
        return;
    }

    if (!ss_twr_init_anchor_id_in_list(ss_twr_init_active_anchor_ids, *active_count,
                                       anchor_id)) {
        ss_twr_init_active_anchor_ids[(*active_count)++] = anchor_id;
    }
}

static void ss_twr_init_prepare_sweep_plan(void)
{
    bool full_sweep = true;
    bool refresh_sweep = false;
    size_t active_count = 0U;

    if (ss_twr_init_runtime_static_calibration_mode()) {
        /*
         * CAL_STATIC is for calibration data coverage, not for a sticky
         * last-solution tracking set. Alternate A/B/C/D and E/F/G/H so CM logs
         * cover all anchors deterministically without collapsing to DEFH.
         */
        active_count = ss_twr_init_append_static_cal_group(
            ss_twr_init_active_anchor_ids, active_count,
            MIN((size_t)4U, ss_twr_init_anchor_count));

        ss_twr_init_current_sweep_full = false;
        ss_twr_init_current_sweep_refresh = false;
        ss_twr_init_active_anchor_count = active_count;
        ss_twr_init_active_anchor_index = 0U;
        ss_twr_init_current_sweep_start_ms = (uint32_t)k_uptime_get();
        ss_twr_init_reset_sweep_anchor_state();
        return;
    }

    if (ss_twr_init_fixed_anchor_mode) {
        for (size_t i = 0; i < ss_twr_init_fixed_anchor_count; ++i) {
            ss_twr_init_active_anchor_ids[active_count++] =
                ss_twr_init_fixed_anchor_ids[i];
        }

        ss_twr_init_current_sweep_full = false;
        ss_twr_init_current_sweep_refresh = false;
        ss_twr_init_active_anchor_count = active_count;
        ss_twr_init_active_anchor_index = 0U;
        ss_twr_init_current_sweep_start_ms = (uint32_t)k_uptime_get();
        ss_twr_init_reset_sweep_anchor_state();
        return;
    }

    if (ss_twr_init_runtime_roto_calibration_mode()) {
        if (ss_twr_init_roto_prewarm_active()) {
            for (size_t i = 0U; i < ss_twr_init_anchor_count; ++i) {
                ss_twr_init_active_anchor_ids[active_count++] =
                    ss_twr_init_anchor_ids[i];
            }

            ss_twr_init_current_sweep_full = true;
            ss_twr_init_current_sweep_refresh = false;
            ss_twr_init_active_anchor_count = active_count;
            ss_twr_init_active_anchor_index = 0U;
            ss_twr_init_current_sweep_start_ms = (uint32_t)k_uptime_get();
            ss_twr_init_reset_sweep_anchor_state();
            return;
        }

        size_t desired_count = MIN((size_t)4U, ss_twr_init_anchor_count);

        /*
         * CAL_ROTO front-end selection is constrained before ranging:
         * exactly 2 anchors from ids 0..3 and 2 anchors from ids 4..7.
         * Among those balanced 2+2 candidates, choose the set with the best
         * aggregate tracker quality, but reject geometrically weak tetrahedra.
         */
        active_count = ss_twr_init_append_roto_balanced(
            ss_twr_init_active_anchor_ids, active_count, desired_count);

        if (ss_twr_init_anchor_count != 0U) {
            ss_twr_init_roto_cal_group_cursor =
                (uint8_t)((ss_twr_init_roto_cal_group_cursor + 1U) %
                          ss_twr_init_anchor_count);
        }

        ss_twr_init_current_sweep_full = false;
        ss_twr_init_current_sweep_refresh = false;
        ss_twr_init_active_anchor_count = active_count;
        ss_twr_init_active_anchor_index = 0U;
        ss_twr_init_current_sweep_start_ms = (uint32_t)k_uptime_get();
        ss_twr_init_reset_sweep_anchor_state();
        return;
    }

    if (ss_twr_init_multitag_anchor_plan_mode) {
        uint16_t effective_full_interval =
            ss_twr_init_effective_multitag_full_interval();
        bool do_full =
            (effective_full_interval != 0U) &&
            (ss_twr_init_sweep_count != 0U) &&
            ((ss_twr_init_sweep_count %
              effective_full_interval) == 0U);
        bool do_refresh =
            (ss_twr_init_refresh_interval_sweeps != 0U) &&
            (ss_twr_init_sweep_count != 0U) &&
            ((ss_twr_init_sweep_count % ss_twr_init_refresh_interval_sweeps) ==
             0U);

#if APP_ALT_SS_TWR_ENABLE && APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
        if (APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP != 0U) {
            do_full = true;
            do_refresh = false;
        }
#endif

        if (do_full) {
            for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
                ss_twr_init_active_anchor_ids[active_count++] =
                    ss_twr_init_anchor_ids[i];
            }
            full_sweep = true;
            refresh_sweep = false;
        } else {
            size_t desired_count =
                ss_twr_init_effective_track_anchor_budget();

            for (size_t i = 0; i < ss_twr_init_active_plan_count &&
                               active_count < desired_count;
                 ++i) {
                ss_twr_init_active_anchor_ids[active_count++] =
                    ss_twr_init_active_plan_ids[i];
            }

            if (do_refresh) {
                uint8_t refresh_pool[UWB_TAG_STANDBY_ANCHOR_MAX +
                                     UWB_TAG_RESERVE_ANCHOR_MAX];
                size_t refresh_pool_count = 0U;

                for (size_t i = 0; i < ss_twr_init_standby_plan_count; ++i) {
                    refresh_pool[refresh_pool_count++] =
                        ss_twr_init_standby_plan_ids[i];
                }
                for (size_t i = 0; i < ss_twr_init_reserve_plan_count; ++i) {
                    refresh_pool[refresh_pool_count++] =
                        ss_twr_init_reserve_plan_ids[i];
                }

                for (size_t i = 0;
                     i < ss_twr_init_refresh_anchor_budget &&
                     active_count < desired_count &&
                     refresh_pool_count > 0U;
                     ++i) {
                    size_t idx =
                        (ss_twr_init_plan_refresh_cursor + i) %
                        refresh_pool_count;
                    ss_twr_init_add_refresh_plan_anchor(refresh_pool[idx],
                                                        &active_count);
                }

                if (refresh_pool_count > 0U) {
                    ss_twr_init_plan_refresh_cursor =
                        (uint8_t)((ss_twr_init_plan_refresh_cursor +
                                   ss_twr_init_refresh_anchor_budget) %
                                  refresh_pool_count);
                }
                refresh_sweep = true;
            }

            full_sweep = false;
        }

        ss_twr_init_current_sweep_full = full_sweep;
        ss_twr_init_current_sweep_refresh = refresh_sweep;
        ss_twr_init_active_anchor_count = active_count;
        ss_twr_init_active_anchor_index = 0U;
        ss_twr_init_current_sweep_start_ms = (uint32_t)k_uptime_get();
        ss_twr_init_reset_sweep_anchor_state();
        return;
    }

    {
        uint16_t effective_full_interval =
            ss_twr_init_effective_full_sweep_interval();

#if APP_ALT_SS_TWR_ENABLE && APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
        if (APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP != 0U) {
            full_sweep = true;
        } else
#endif
        if (APP_TAG_FAST_TRACKING != 0U && ss_twr_init_have_last_solution &&
            effective_full_interval > 1U &&
            (ss_twr_init_sweep_count % effective_full_interval) != 0U) {
            full_sweep = false;
        }
    }

    if (full_sweep) {
        active_count = ss_twr_init_append_interleaved_plane_anchors(
            ss_twr_init_anchor_ids, ss_twr_init_anchor_count,
            ss_twr_init_active_anchor_ids, active_count,
            ARRAY_SIZE(ss_twr_init_active_anchor_ids));
    } else {
        size_t desired_count = ss_twr_init_effective_track_anchor_budget();
        uint8_t rotated_anchor_ids[UWB_MAX_ANCHORS];

        for (size_t i = 0; i < ss_twr_init_last_solution_anchor_count &&
                           active_count < desired_count;
             ++i) {
            uint8_t anchor_id = ss_twr_init_last_solution_anchor_ids[i];

            if (!ss_twr_init_anchor_id_in_list(ss_twr_init_active_anchor_ids,
                                               active_count, anchor_id)) {
                ss_twr_init_active_anchor_ids[active_count++] = anchor_id;
            }
        }

        for (size_t offset = 0; offset < ss_twr_init_anchor_count; ++offset) {
            size_t idx =
                (ss_twr_init_refresh_anchor_cursor + offset) %
                ss_twr_init_anchor_count;
            rotated_anchor_ids[offset] = ss_twr_init_anchor_ids[idx];
        }

        active_count = ss_twr_init_append_interleaved_plane_anchors(
            rotated_anchor_ids, ss_twr_init_anchor_count,
            ss_twr_init_active_anchor_ids, active_count, desired_count);

        ss_twr_init_refresh_anchor_cursor =
            (uint8_t)((ss_twr_init_refresh_anchor_cursor + 1U) %
                      ss_twr_init_anchor_count);
    }

    ss_twr_init_current_sweep_full = full_sweep;
    ss_twr_init_current_sweep_refresh = false;
    ss_twr_init_active_anchor_count = active_count;
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_current_sweep_start_ms = (uint32_t)k_uptime_get();
    ss_twr_init_last_sweep_cut_short = false;
    ss_twr_init_reset_sweep_anchor_state();
}

static void ss_twr_init_read_ts(const uint8_t *ts_field, uint32 *ts)
{
    *ts = 0;

    for (int i = 0; i < SS_TWR_INIT_RESP_MSG_TS_LEN; ++i) {
        *ts |= ((uint32)ts_field[i]) << (i * 8);
    }
}

static void ss_twr_init_write_ts(uint8_t *ts_field, uint32 ts)
{
    for (int i = 0; i < SS_TWR_INIT_RESP_MSG_TS_LEN; ++i) {
        ts_field[i] = (uint8_t)(ts >> (i * 8));
    }
}

static void ss_twr_init_configure_radio(void)
{
    dwt_configure(&ss_twr_init_config);
    dwt_setpanid(APP_UWB_PAN_ID);
    dwt_setaddress16(ss_twr_init_local_addr);
#if APP_UWB_HW_FRAME_FILTER_ENABLE
    dwt_enableframefilter(SYS_CFG_FFAD);
#else
    dwt_enableframefilter(0);
#endif
    dwt_setrxantennadelay(SS_TWR_INIT_RX_ANT_DLY);
    dwt_settxantennadelay(SS_TWR_INIT_TX_ANT_DLY);
    dwt_setleds(DWT_LEDS_ENABLE);
    dwt_setrxaftertxdelay(SS_TWR_INIT_TX_TO_RX_DLY_UUS);
    dwt_setrxtimeout(SS_TWR_INIT_RESP_RX_TIMEOUT_UUS);
    dwt_setpreambledetecttimeout(0);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
    ss_twr_init_radio_configured = true;
}

#if APP_TAG_WAND_MODE_ENABLE
int ss_twr_init_wand_set_enabled(bool enabled, char label)
{
    if (label >= 'a' && label <= 'z') {
        label = (char)(label - 'a' + 'A');
    }
    if (label != 'A' && label != 'B' && label != 'C' && label != '?') {
        return -EINVAL;
    }

    ss_twr_init_wand_enabled = enabled;
    ss_twr_init_wand_label = enabled ? label : '?';
    if (!enabled) {
        ss_twr_init_wand_role = SS_TWR_INIT_WAND_ROLE_IDLE;
        ss_twr_init_wand_pending_sweeps = 0U;
    }
    return 0;
}

int ss_twr_init_wand_set_role(enum ss_twr_init_wand_role role)
{
    if (role != SS_TWR_INIT_WAND_ROLE_IDLE &&
        role != SS_TWR_INIT_WAND_ROLE_INIT &&
        role != SS_TWR_INIT_WAND_ROLE_RESP) {
        return -EINVAL;
    }
    ss_twr_init_wand_role = role;
    return 0;
}

int ss_twr_init_wand_set_peers(uint8_t tag_a, uint8_t tag_b, uint8_t tag_c)
{
    ss_twr_init_wand_tags[0] = tag_a;
    ss_twr_init_wand_tags[1] = tag_b;
    ss_twr_init_wand_tags[2] = tag_c;
    return 0;
}

int ss_twr_init_wand_request_sweep(uint16_t count)
{
    if (!ss_twr_init_wand_enabled) {
        return -EACCES;
    }
    if (count == 0U) {
        count = 1U;
    }
    ss_twr_init_wand_role = SS_TWR_INIT_WAND_ROLE_INIT;
    ss_twr_init_wand_pending_sweeps = count;
    return 0;
}

static char ss_twr_init_wand_label_for_index(uint8_t index)
{
    return (char)('A' + index);
}

static void ss_twr_init_wand_publish(const char *line)
{
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#else
    ARG_UNUSED(line);
#endif
}

static bool ss_twr_init_wand_range_peer(uint8_t peer_tag_id, long *raw_mm_out)
{
    uint16_t peer_addr = uwb_tag_short_addr(peer_tag_id);
    uint32 status_reg;

    if (raw_mm_out == NULL) {
        return false;
    }
    *raw_mm_out = 0L;

    ss_twr_init_configure_radio();
    uwb_ss_twr_build_poll_frame(ss_twr_init_tx_poll_msg,
                                ss_twr_init_frame_seq_nb,
                                peer_addr,
                                ss_twr_init_local_addr);
    ss_twr_init_prepare_radio_for_poll();
    if (dwt_writetxdata(SS_TWR_INIT_LEGACY_POLL_FRAME_LEN,
                        ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
        return false;
    }
    dwt_writetxfctrl(SS_TWR_INIT_LEGACY_POLL_FRAME_LEN, 0, 1);
    if (dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED) !=
        DWT_SUCCESS) {
        dwt_forcetrxoff();
        return false;
    }

    do {
        status_reg = dwt_read32bitreg(SYS_STATUS_ID);
    } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                            SYS_STATUS_ALL_RX_ERR)) == 0U);

    ss_twr_init_frame_seq_nb++;

    if ((status_reg & SYS_STATUS_RXFCG) == 0U) {
        dwt_write32bitreg(SYS_STATUS_ID,
                          SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
        dwt_rxreset();
        return false;
    }

    uint32 frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFLEN_MASK;
    if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
        dwt_forcetrxoff();
        dwt_rxreset();
        return false;
    }

    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
    memset(ss_twr_init_rx_buffer, 0, sizeof(ss_twr_init_rx_buffer));
    dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
    ss_twr_init_rx_buffer[SS_TWR_INIT_MSG_SN_IDX] = 0;

    if (!uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                 ss_twr_init_local_addr, peer_addr)) {
        return false;
    }

    uint32 poll_tx_ts = dwt_readtxtimestamplo32();
    uint32 resp_rx_ts = dwt_readrxtimestamplo32();
    uint32 poll_rx_ts;
    uint32 resp_tx_ts;
    ss_twr_init_read_ts(
        &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
        &poll_rx_ts);
    ss_twr_init_read_ts(
        &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
        &resp_tx_ts);

    double clock_offset_ratio =
        (double)dwt_readcarrierintegrator() *
        (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 / 1.0e6);
    int32 rtd_init = (int32)(resp_rx_ts - poll_tx_ts);
    int32 rtd_resp = (int32)(resp_tx_ts - poll_rx_ts);
    double tof = ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
                 DWT_TIME_UNITS;
    long raw_mm = (long)(tof * SS_TWR_INIT_SPEED_OF_LIGHT * 1000.0);
    if (raw_mm < 0L) {
        raw_mm = 0L;
    }
    *raw_mm_out = raw_mm;
    return true;
}

static void ss_twr_init_wand_responder_once(void)
{
    uint32_t start_ms = k_uptime_get_32();
    uint32 status_reg;

    ss_twr_init_configure_radio();
    dwt_setrxtimeout(0);
    dwt_rxenable(DWT_START_RX_IMMEDIATE);

    while ((uint32_t)(k_uptime_get_32() - start_ms) < APP_TAG_WAND_RESP_RX_MS) {
        status_reg = dwt_read32bitreg(SYS_STATUS_ID);
        if ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_ERR)) != 0U) {
            break;
        }
        k_yield();
    }

    status_reg = dwt_read32bitreg(SYS_STATUS_ID);
    if ((status_reg & SYS_STATUS_RXFCG) == 0U) {
        if ((status_reg & SYS_STATUS_ALL_RX_ERR) != 0U) {
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_ERR);
            dwt_rxreset();
        }
        dwt_forcetrxoff();
        return;
    }

    uint32 frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFL_MASK_1023;
    if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
        dwt_forcetrxoff();
        dwt_rxreset();
        return;
    }
    dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
    if (!uwb_ss_twr_poll_matches(ss_twr_init_rx_buffer,
                                 ss_twr_init_local_addr)) {
        return;
    }

    uint16_t poll_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);
    if (!uwb_short_addr_is_tag(poll_src_addr)) {
        return;
    }

    uint32 poll_rx_ts = dwt_readrxtimestamplo32();
    uint32 resp_tx_time =
        (uint32)(((uint64_t)poll_rx_ts +
                  ((uint64_t)APP_TAG_WAND_RESP_DELAY_UUS *
                   SS_TWR_INIT_UUS_TO_DWT_TIME)) >> 8);
    uint32 resp_tx_ts =
        ((resp_tx_time & 0xFFFFFFFEUL) << 8) + SS_TWR_INIT_TX_ANT_DLY;

    uwb_ss_twr_build_resp_frame(ss_twr_init_tx_resp_msg,
                                ss_twr_init_frame_seq_nb,
                                poll_src_addr,
                                ss_twr_init_local_addr);
    ss_twr_init_write_ts(&ss_twr_init_tx_resp_msg
                         [SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                         poll_rx_ts);
    ss_twr_init_write_ts(&ss_twr_init_tx_resp_msg
                         [SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                         resp_tx_ts);
    if (dwt_writetxdata(sizeof(ss_twr_init_tx_resp_msg),
                        ss_twr_init_tx_resp_msg, 0) != DWT_SUCCESS) {
        return;
    }
    dwt_writetxfctrl(sizeof(ss_twr_init_tx_resp_msg), 0, 1);
    dwt_setdelayedtrxtime(resp_tx_time);
    if (dwt_starttx(DWT_START_TX_DELAYED) != DWT_SUCCESS) {
        dwt_forcetrxoff();
        dwt_rxreset();
        return;
    }
    while ((dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_TXFRS) == 0U) {
        k_yield();
    }
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
    ss_twr_init_frame_seq_nb++;
}

static void ss_twr_init_wand_sweep_once(void)
{
    char line[128];
    uint8_t self_index = 0xffU;
    uint8_t ok_count = 0U;

    for (uint8_t i = 0U; i < 3U; ++i) {
        if (ss_twr_init_wand_label == ss_twr_init_wand_label_for_index(i) ||
            ss_twr_init_wand_tags[i] == ss_twr_init_local_tag_id) {
            self_index = i;
            break;
        }
    }

    ss_twr_init_wand_seq++;
    for (uint8_t i = 0U; i < 3U; ++i) {
        long raw_mm = 0L;
        bool ok;

        if (i == self_index || ss_twr_init_wand_tags[i] == ss_twr_init_local_tag_id) {
            continue;
        }
        ok = ss_twr_init_wand_range_peer(ss_twr_init_wand_tags[i], &raw_mm);
        if (ok) {
            ok_count++;
        }
        snprintk(line, sizeof(line),
                 "WR;%lu;%c;%c;0x%02X;%ld;%u",
                 (unsigned long)ss_twr_init_wand_seq,
                 ss_twr_init_wand_label,
                 ss_twr_init_wand_label_for_index(i),
                 (unsigned int)ss_twr_init_wand_tags[i],
                 raw_mm,
                 (unsigned int)(ok ? 1U : 0U));
        ss_twr_init_wand_publish(line);
        k_msleep(5);
    }

    snprintk(line, sizeof(line),
             "WS;%lu;%c;ok=%u;peers=%02X,%02X,%02X",
             (unsigned long)ss_twr_init_wand_seq,
             ss_twr_init_wand_label,
             (unsigned int)ok_count,
             (unsigned int)ss_twr_init_wand_tags[0],
             (unsigned int)ss_twr_init_wand_tags[1],
             (unsigned int)ss_twr_init_wand_tags[2]);
    ss_twr_init_wand_publish(line);
}
#else
int ss_twr_init_wand_set_enabled(bool enabled, char label)
{
    ARG_UNUSED(enabled);
    ARG_UNUSED(label);
    return -ENOTSUP;
}

int ss_twr_init_wand_set_role(enum ss_twr_init_wand_role role)
{
    ARG_UNUSED(role);
    return -ENOTSUP;
}

int ss_twr_init_wand_set_peers(uint8_t tag_a, uint8_t tag_b, uint8_t tag_c)
{
    ARG_UNUSED(tag_a);
    ARG_UNUSED(tag_b);
    ARG_UNUSED(tag_c);
    return -ENOTSUP;
}

int ss_twr_init_wand_request_sweep(uint16_t count)
{
    ARG_UNUSED(count);
    return -ENOTSUP;
}
#endif

static int ss_twr_init_load_runtime_config(
    const struct uwb_tag_runtime_config *config)
{
    if (config == NULL || config->tag_id >= UWB_MAX_TAGS ||
        config->anchor_ids == NULL || config->anchor_count == 0U ||
        config->anchor_count > UWB_MAX_ANCHORS) {
        return -1;
    }

    ss_twr_init_identity_code = config->identity_code;
    ss_twr_init_local_tag_id = config->tag_id;
    ss_twr_init_local_addr = uwb_tag_short_addr(ss_twr_init_local_tag_id);
    ss_twr_init_anchor_count = config->anchor_count;
    ss_twr_init_sweep_count = 0U;
    ss_twr_init_active_anchor_count = 0U;
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_current_anchor_retry_count = 0U;
    ss_twr_init_have_last_solution = false;
    ss_twr_init_last_solution_anchor_count = 0U;
    ss_twr_init_have_last_location = false;
    ss_twr_init_location_output_count = 0U;
    ss_twr_init_last_location_x_mm = 0;
    ss_twr_init_last_location_y_mm = 0;
    ss_twr_init_last_location_z_mm = 0;
    ss_twr_init_last_output_ms = 0U;
#if APP_TAG_STATUS_PERIOD_MS > 0U
    ss_twr_init_have_last_raw_location = false;
    ss_twr_init_last_location_update_ms = 0U;
    memset(&ss_twr_init_last_raw_location, 0, sizeof(ss_twr_init_last_raw_location));
    memset(&ss_twr_init_last_filtered_location, 0,
           sizeof(ss_twr_init_last_filtered_location));
#endif
    ss_twr_init_refresh_anchor_cursor = 0U;
    ss_twr_init_current_sweep_full = true;
    ss_twr_init_current_sweep_start_ms = 0U;
    ss_twr_init_fixed_anchor_mode = false;
    ss_twr_init_fixed_anchor_count = 0U;
    ss_twr_init_multitag_anchor_plan_mode = false;
    ss_twr_init_active_plan_count = 0U;
    ss_twr_init_standby_plan_count = 0U;
    ss_twr_init_reserve_plan_count = 0U;
    ss_twr_init_refresh_anchor_budget = 0U;
    ss_twr_init_refresh_interval_sweeps = 0U;
    ss_twr_init_full_sweep_interval_sweeps = 0U;
    ss_twr_init_plan_refresh_cursor = 0U;
    ss_twr_init_tdma_schedule = config->tdma;
    if (ss_twr_init_tdma_schedule.enabled &&
        !ss_twr_init_tdma_schedule.epoch_valid) {
        ss_twr_init_tdma_schedule.epoch_ms = 0U;
        ss_twr_init_tdma_schedule.sync_local_ms = 0U;
        ss_twr_init_tdma_schedule.generation = 0U;
    }
    ss_twr_init_have_last_imu_sample = false;
    ss_twr_init_runtime_update_pending = false;
    ss_twr_init_last_sweep_cut_short = false;
    ss_twr_init_last_tdma_wait_ms = 0U;
    ss_twr_init_last_slot_guard_log_ms = 0U;
    ss_twr_init_last_solve_pending_log_ms = 0U;
    ss_twr_init_last_solve_diag_ms = 0U;
    ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_NONE;
    ss_twr_init_roto_prewarm_deadline_ms = 0U;
    memset(&ss_twr_init_last_imu_sample, 0, sizeof(ss_twr_init_last_imu_sample));
#if APP_TAG_TR_IMU_SUMMARY_ENABLE != 0U
    ss_twr_init_reset_imu_summary();
#endif
    ss_twr_init_perf_motion_dt_sum_ms = 0U;
    ss_twr_init_perf_track_sweep_sum_ms = 0U;
    ss_twr_init_perf_full_sweep_sum_ms = 0U;
    ss_twr_init_perf_motion_dt_count = 0U;
    ss_twr_init_perf_track_sweep_count = 0U;
    ss_twr_init_perf_full_sweep_count = 0U;
    uwb_motion_reset();
    uwb_ekf_reset();
    memset(ss_twr_init_trackers, 0, sizeof(ss_twr_init_trackers));

    for (size_t i = 0; i < config->anchor_count; ++i) {
        if (config->anchor_ids[i] >= UWB_MAX_ANCHORS) {
            printk("Invalid anchor id in table: %u\n",
                   (unsigned int)config->anchor_ids[i]);
            return -1;
        }

        ss_twr_init_anchor_ids[i] = config->anchor_ids[i];
        uwb_range_tracker_init(&ss_twr_init_trackers[config->anchor_ids[i]],
                               uwb_anchor_short_addr(config->anchor_ids[i]));
    }

    if (config->anchor_selection_mode == UWB_TAG_ANCHOR_SELECTION_FIXED_SUBSET ||
        config->fixed_anchor_mode) {
        if (config->fixed_anchor_ids == NULL || config->fixed_anchor_count < 4U ||
            config->fixed_anchor_count > UWB_TAG_FIXED_ANCHOR_MAX) {
            printk("Invalid fixed-anchor config count=%u\n",
                   (unsigned int)config->fixed_anchor_count);
            return -1;
        }

        for (size_t i = 0; i < config->fixed_anchor_count; ++i) {
            uint8_t anchor_id = config->fixed_anchor_ids[i];

            if (anchor_id >= UWB_MAX_ANCHORS ||
                !ss_twr_init_anchor_id_in_list(ss_twr_init_anchor_ids,
                                               ss_twr_init_anchor_count,
                                               anchor_id) ||
                ss_twr_init_anchor_id_in_list(ss_twr_init_fixed_anchor_ids,
                                              ss_twr_init_fixed_anchor_count,
                                              anchor_id)) {
                printk("Invalid fixed anchor id: %u\n",
                       (unsigned int)anchor_id);
                return -1;
            }

            ss_twr_init_fixed_anchor_ids[ss_twr_init_fixed_anchor_count++] =
                anchor_id;
        }

        ss_twr_init_fixed_anchor_mode = true;
    }

    if (config->multitag_anchor_plan_mode) {
        const uint8_t *group_sets[3] = {
            config->active_anchor_ids,
            config->standby_anchor_ids,
            config->reserve_anchor_ids,
        };
        const size_t group_counts[3] = {
            config->active_anchor_count,
            config->standby_anchor_count,
            config->reserve_anchor_count,
        };
        uint8_t *group_dests[3] = {
            ss_twr_init_active_plan_ids,
            ss_twr_init_standby_plan_ids,
            ss_twr_init_reserve_plan_ids,
        };
        size_t *group_dest_counts[3] = {
            &ss_twr_init_active_plan_count,
            &ss_twr_init_standby_plan_count,
            &ss_twr_init_reserve_plan_count,
        };
        const size_t group_caps[3] = {
            UWB_TAG_ACTIVE_ANCHOR_MAX,
            UWB_TAG_STANDBY_ANCHOR_MAX,
            UWB_TAG_RESERVE_ANCHOR_MAX,
        };

        if (config->active_anchor_ids == NULL || config->active_anchor_count < 4U ||
            config->active_anchor_count > UWB_TAG_ACTIVE_ANCHOR_MAX) {
            printk("Invalid multitag active anchor plan count=%u\n",
                   (unsigned int)config->active_anchor_count);
            return -1;
        }

        for (size_t group = 0; group < 3; ++group) {
            if (group_counts[group] > group_caps[group]) {
                printk("Invalid multitag anchor group size=%u group=%u\n",
                       (unsigned int)group_counts[group], (unsigned int)group);
                return -1;
            }

            for (size_t i = 0; i < group_counts[group]; ++i) {
                uint8_t anchor_id = group_sets[group][i];

                if (anchor_id >= UWB_MAX_ANCHORS ||
                    !ss_twr_init_anchor_id_in_list(ss_twr_init_anchor_ids,
                                                   ss_twr_init_anchor_count,
                                                   anchor_id) ||
                    ss_twr_init_anchor_id_in_list(ss_twr_init_active_plan_ids,
                                                  ss_twr_init_active_plan_count,
                                                  anchor_id) ||
                    ss_twr_init_anchor_id_in_list(ss_twr_init_standby_plan_ids,
                                                  ss_twr_init_standby_plan_count,
                                                  anchor_id) ||
                    ss_twr_init_anchor_id_in_list(ss_twr_init_reserve_plan_ids,
                                                  ss_twr_init_reserve_plan_count,
                                                  anchor_id)) {
                    printk("Invalid multitag anchor id=%u group=%u\n",
                           (unsigned int)anchor_id, (unsigned int)group);
                    return -1;
                }

                group_dests[group][(*group_dest_counts[group])++] = anchor_id;
            }
        }

        ss_twr_init_multitag_anchor_plan_mode = true;
        ss_twr_init_refresh_anchor_budget = config->refresh_anchor_budget;
        ss_twr_init_refresh_interval_sweeps = config->refresh_interval_sweeps;
        ss_twr_init_full_sweep_interval_sweeps =
            config->full_sweep_interval_sweeps;
    }

    if (ss_twr_init_tdma_schedule.enabled) {
        if (ss_twr_init_tdma_schedule.slot_count == 0U ||
            ss_twr_init_tdma_schedule.slot_index >=
                ss_twr_init_tdma_schedule.slot_count ||
            ss_twr_init_tdma_schedule.slot_period_ms == 0U ||
            ss_twr_init_tdma_schedule.slot_active_ms == 0U ||
            ss_twr_init_tdma_schedule.slot_active_ms >
                ss_twr_init_tdma_schedule.slot_period_ms) {
            printk("Invalid TDMA config slot=%u/%u period=%u active=%u\n",
                   (unsigned int)ss_twr_init_tdma_schedule.slot_index,
                   (unsigned int)ss_twr_init_tdma_schedule.slot_count,
                   (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
                   (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms);
            return -1;
        }
    }

    ss_twr_init_runtime_params.identity_code = config->identity_code;
    ss_twr_init_runtime_params.logical_tag_id = config->tag_id;
    ss_twr_init_runtime_params.slot_source = config->slot_source;
    ss_twr_init_runtime_params.positioning_mode = config->positioning_mode;
    ss_twr_init_runtime_params.anchor_selection_mode =
        config->anchor_selection_mode;
    ss_twr_init_runtime_params.fixed_anchor_count =
        (uint8_t)MIN(config->fixed_anchor_count, (size_t)UWB_TAG_FIXED_ANCHOR_MAX);
    memset(ss_twr_init_runtime_params.fixed_anchor_ids, 0,
           sizeof(ss_twr_init_runtime_params.fixed_anchor_ids));
    if (config->fixed_anchor_ids != NULL) {
        memcpy(ss_twr_init_runtime_params.fixed_anchor_ids,
               config->fixed_anchor_ids,
               sizeof(uint8_t) * ss_twr_init_runtime_params.fixed_anchor_count);
    }
    ss_twr_init_runtime_params.tdma = ss_twr_init_tdma_schedule;

    return 0;
}

static void ss_twr_init_print_location_if_ready(void)
{
    struct uwb_tag_measurement measurements[UWB_MAX_ANCHORS];
    struct uwb_tag_location_result location;
    struct uwb_tag_location_result raw_location;
    struct uwb_ekf_sample filtered_location;
    struct uwb_motion_sample motion;
    struct uwb_imu_sample imu;
    bool have_motion = false;
    bool have_imu = false;
    uint32_t candidate_step_mm = 0U;
    uint8_t solution_quality_percent = 0U;
    uint32_t sweep_elapsed_ms =
        (uint32_t)k_uptime_get() - ss_twr_init_current_sweep_start_ms;
    uint8_t valid_anchor_ids[UWB_MAX_ANCHORS];
    size_t valid_anchor_count = 0U;
    char received_anchors[32];

    memset(measurements, 0, sizeof(measurements));
#if APP_TAG_TR_IMU_SUMMARY_ENABLE != 0U
    ss_twr_init_update_imu_summary_for_sweep(&imu, &have_imu);
#endif

    for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
        uint8_t anchor_id = ss_twr_init_anchor_ids[i];
        const struct uwb_anchor_pose_mm *pose =
            uwb_anchor_layout_get(anchor_id);
        struct uwb_range_tracker *tracker = &ss_twr_init_trackers[anchor_id];
        bool measured_this_sweep = ss_twr_init_anchor_id_in_list(
            ss_twr_init_active_anchor_ids, ss_twr_init_active_anchor_count,
            anchor_id);
        bool range_ok_this_sweep =
            ss_twr_init_sweep_anchor_status[anchor_id] ==
            UWB_TAG_BLE_CAL_STATUS_OK;

        measurements[i].anchor_id = anchor_id;
        measurements[i].quality_percent =
            uwb_range_tracker_quality_percent(tracker);
        /*
         * A timeout/reject on this sweep must not reuse the previous sweep's
         * filtered range as if it were a fresh measurement. Otherwise the
         * solver consumes stale data and CR/CS diagnostics become misleading.
         */
        measurements[i].valid = measured_this_sweep && range_ok_this_sweep &&
                                tracker->filtered_valid;
        measurements[i].range_mm = tracker->filtered_mm;

        if (measurements[i].valid &&
            !ss_twr_init_apply_range_continuity_gate(
                anchor_id, measurements[i].range_mm,
                &measurements[i].quality_percent)) {
            measurements[i].valid = false;
        }

        if (APP_TAG_VERBOSE_MEASUREMENTS != 0U && pose != NULL &&
            tracker->filtered_valid) {
            printk("Tag meas anchor=%c(%u) range=%lu mm q=%u%%\n", pose->label,
                   (unsigned int)measurements[i].anchor_id,
                   (unsigned long)measurements[i].range_mm,
                   (unsigned int)measurements[i].quality_percent);
        }

        if (measurements[i].valid &&
            valid_anchor_count < UWB_MAX_ANCHORS) {
            valid_anchor_ids[valid_anchor_count++] = anchor_id;
        }
    }

    enum uwb_tag_loc_subset_policy subset_policy =
        (ss_twr_init_current_sweep_full || ss_twr_init_current_sweep_refresh)
            ? UWB_TAG_LOC_SUBSET_POLICY_MIN4
            : UWB_TAG_LOC_SUBSET_POLICY_EXACT4;

#if APP_ALT_SS_TWR_ENABLE && APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
    if (APP_TAG_LOC_FAST_ALL_VALID_ENABLE != 0U &&
        ss_twr_init_current_sweep_full) {
        subset_policy = UWB_TAG_LOC_SUBSET_POLICY_ALL_VALID;
    }
#endif

#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_solve_start_cycles = k_cycle_get_32();
#endif
    if (uwb_tag_loc_solve(measurements, ss_twr_init_anchor_count, subset_policy,
                          &location) != 0) {
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
        ss_twr_init_diag_solve_done_cycles = k_cycle_get_32();
        ss_twr_init_diag_out_start_cycles = ss_twr_init_diag_solve_done_cycles;
#endif
        ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_PENDING;
        solution_quality_percent =
            ss_twr_init_compute_target_quality_percent(measurements,
                                                       ss_twr_init_anchor_count);
        ss_twr_init_format_anchor_labels(valid_anchor_ids,
                                         valid_anchor_count,
                                         received_anchors,
                                         sizeof(received_anchors));
        if (APP_TAG_PENDING_PRINT_PERIOD != 0U &&
            (ss_twr_init_sweep_count % APP_TAG_PENDING_PRINT_PERIOD) == 0U) {
            uint32_t now_ms = (uint32_t)k_uptime_get();
            if ((now_ms - ss_twr_init_last_solve_pending_log_ms) >= 1000U) {
                ss_twr_init_last_solve_pending_log_ms = now_ms;
                printk("Tag solve pending: need >=4 valid anchors with required plane coverage "
                       "plan=%s active=%u sweep_ms=%lu valid=[%s]\n",
                       ss_twr_init_plan_label(),
                       (unsigned int)ss_twr_init_active_anchor_count,
                       (unsigned long)sweep_elapsed_ms,
                        received_anchors);
            }
        }
        if (ss_twr_init_runtime_any_calibration_mode()) {
            for (size_t i = 0U; i < ss_twr_init_active_anchor_count; ++i) {
                ss_twr_init_publish_cal_reason_line(
                    ss_twr_init_active_anchor_ids[i]);
            }
            ss_twr_init_publish_cal_frame_summary(
                ss_twr_init_plan_label(),
                ss_twr_init_runtime_params.positioning_mode,
                solution_quality_percent,
                0U,
                0U,
                0U,
                valid_anchor_count);
            ss_twr_init_publish_calibration_summary(
                ss_twr_init_plan_label(),
                ss_twr_init_runtime_params.positioning_mode,
                solution_quality_percent);
        }
        ss_twr_init_publish_solve_diag('p',
                                       ss_twr_init_plan_label(),
                                       solution_quality_percent,
                                       valid_anchor_count,
                                       0U,
                                       0U,
                                       0U,
                                       0U,
                                       received_anchors);
        ss_twr_init_publish_tag_range_summary(measurements,
                                              ss_twr_init_anchor_count,
                                              solution_quality_percent);
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
        ss_twr_init_diag_out_done_cycles = k_cycle_get_32();
#endif
        return;
    }
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_solve_done_cycles = k_cycle_get_32();
    ss_twr_init_diag_out_start_cycles = ss_twr_init_diag_solve_done_cycles;
#endif

    raw_location = location;
    {
        ss_twr_init_format_anchor_labels(location.anchor_ids,
                                         location.used_anchor_count,
                                         received_anchors,
                                         sizeof(received_anchors));
        if (location.used_anchor_count != 0U) {
            if (ss_twr_init_runtime_roto_calibration_mode() &&
                !ss_twr_init_roto_prewarm_active() &&
                ss_twr_init_active_anchor_count != 0U) {
                solution_quality_percent =
                    ss_twr_init_compute_target_quality_percent(
                        measurements, ss_twr_init_anchor_count);
            } else {
                uint32_t quality_sum = 0U;
                uint8_t quality_count = 0U;
                for (size_t i = 0U; i < location.used_anchor_count; ++i) {
                    for (size_t j = 0U; j < ss_twr_init_anchor_count; ++j) {
                        if (measurements[j].anchor_id == location.anchor_ids[i]) {
                            quality_sum += measurements[j].quality_percent;
                            quality_count++;
                            break;
                        }
                    }
                }
                if (quality_count != 0U) {
                    solution_quality_percent =
                        (uint8_t)(quality_sum / quality_count);
                }
            }
        }

        struct uwb_ekf_runtime_params ekf_params = {
            .meas_std_mm = APP_TAG_EKF_MEAS_STD_MM,
            .residual_gain_pct = APP_TAG_EKF_RESIDUAL_GAIN_PCT,
            .proc_accel_mm_s2 = APP_TAG_EKF_PROC_ACCEL_MM_S2,
            .outlier_gate_mm = APP_TAG_EKF_OUTLIER_GATE_MM,
        };

        if (ss_twr_init_dynamic_context_active()) {
            if (APP_TAG_MOTION_EKF_MEAS_STD_MM != 0U) {
                ekf_params.meas_std_mm = APP_TAG_MOTION_EKF_MEAS_STD_MM;
            }
            if (APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2 != 0U) {
                ekf_params.proc_accel_mm_s2 =
                    APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2;
            }
            if (APP_TAG_MOTION_EKF_OUTLIER_GATE_MM != 0U) {
                ekf_params.outlier_gate_mm =
                    APP_TAG_MOTION_EKF_OUTLIER_GATE_MM;
            }
        }

        uwb_ekf_filter_with_params(location.x_mm, location.y_mm, location.z_mm,
                                   k_uptime_get(), location.residual_rms_mm,
                                   location.residual_max_mm, &ekf_params,
                                   &filtered_location);
    }

    if (filtered_location.valid) {
        location.x_mm = filtered_location.x_mm;
        location.y_mm = filtered_location.y_mm;
        location.z_mm = filtered_location.z_mm;
        (void)uwb_tag_loc_evaluate_solution(
            measurements, ss_twr_init_anchor_count, location.anchor_ids,
            location.used_anchor_count, location.x_mm, location.y_mm,
            location.z_mm, &location.residual_rms_mm,
            &location.residual_max_mm, &location.lower_anchor_count,
            &location.upper_anchor_count);
    }

    if (!ss_twr_init_location_plausible(&location, &candidate_step_mm)) {
        ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_REJECTED;
        if (ss_twr_init_runtime_any_calibration_mode()) {
            for (size_t i = 0U; i < ss_twr_init_active_anchor_count; ++i) {
                ss_twr_init_publish_cal_reason_line(
                    ss_twr_init_active_anchor_ids[i]);
            }
            ss_twr_init_publish_cal_frame_summary(
                ss_twr_init_plan_label(),
                ss_twr_init_runtime_params.positioning_mode,
                solution_quality_percent,
                location.residual_rms_mm,
                location.residual_max_mm,
                candidate_step_mm,
                valid_anchor_count);
        }
        if (APP_TAG_PENDING_PRINT_PERIOD != 0U &&
            (ss_twr_init_sweep_count % APP_TAG_PENDING_PRINT_PERIOD) == 0U) {
            printk("Tag solve rejected plan=%s active=%u xyz=(%ld,%ld,%ld) rms=%lu max=%lu step=%lu received=[%s]\n",
                   ss_twr_init_plan_label(),
                   (unsigned int)ss_twr_init_active_anchor_count,
                   (long)location.x_mm, (long)location.y_mm,
                   (long)location.z_mm,
                   (unsigned long)location.residual_rms_mm,
                   (unsigned long)location.residual_max_mm,
                   (unsigned long)candidate_step_mm,
                   received_anchors);
        }
        ss_twr_init_publish_solve_diag('r',
                                       ss_twr_init_plan_label(),
                                       solution_quality_percent,
                                       valid_anchor_count,
                                       location.used_anchor_count,
                                       location.residual_rms_mm,
                                       location.residual_max_mm,
                                       candidate_step_mm,
                                       received_anchors);
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
        ss_twr_init_diag_out_done_cycles = k_cycle_get_32();
#endif
        return;
    }

    ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_SUCCESS;
    if (ss_twr_init_runtime_any_calibration_mode()) {
        for (size_t i = 0U; i < ss_twr_init_active_anchor_count; ++i) {
            ss_twr_init_publish_cal_reason_line(
                ss_twr_init_active_anchor_ids[i]);
        }
        ss_twr_init_publish_cal_frame_summary(
            ss_twr_init_plan_label(),
            ss_twr_init_runtime_params.positioning_mode,
            solution_quality_percent,
            location.residual_rms_mm,
            location.residual_max_mm,
            candidate_step_mm,
            valid_anchor_count);
    }

    if (!ss_twr_init_runtime_any_calibration_mode()) {
        uint32_t filter_step_mm = 0U;
        uint32_t filter_dt_ms = 0U;
        uint32_t filter_speed_mm_s = 0U;
        const char *filter_reason = "ok";
        uint32_t now_ms = (uint32_t)k_uptime_get();

        if (ss_twr_init_output_filter_reject(
                &location, now_ms, &filter_step_mm, &filter_dt_ms,
                &filter_speed_mm_s, &filter_reason)) {
            char filter_anchor_labels[32];

            ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_REJECTED;
            ss_twr_init_anchor_label_string(&location, filter_anchor_labels,
                                            sizeof(filter_anchor_labels));
            ss_twr_init_publish_filtered_position(
                &location, ss_twr_init_plan_label(), solution_quality_percent,
                filter_anchor_labels, filter_reason, filter_step_mm,
                filter_dt_ms, filter_speed_mm_s);
            ss_twr_init_publish_solve_diag('f',
                                           ss_twr_init_plan_label(),
                                           solution_quality_percent,
                                           valid_anchor_count,
                                           location.used_anchor_count,
                                           location.residual_rms_mm,
                                           location.residual_max_mm,
                                           filter_step_mm,
                                           received_anchors);
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
            ss_twr_init_diag_out_done_cycles = k_cycle_get_32();
#endif
            return;
        }
    }

    have_motion = uwb_motion_update(location.x_mm, location.y_mm, location.z_mm,
                                    k_uptime_get(), &motion);

#if APP_TAG_TR_IMU_SUMMARY_ENABLE == 0U
    if (ss_twr_init_imu_ready) {
        if (!ss_twr_init_have_last_imu_sample ||
            APP_TAG_IMU_SAMPLE_PERIOD <= 1U ||
            (ss_twr_init_sweep_count % APP_TAG_IMU_SAMPLE_PERIOD) == 0U) {
            have_imu = uwb_imu_read(&imu);
            if (have_imu) {
                ss_twr_init_last_imu_sample = imu;
                ss_twr_init_have_last_imu_sample = true;
            }
        } else if (ss_twr_init_have_last_imu_sample) {
            imu = ss_twr_init_last_imu_sample;
            have_imu = true;
        }
    }
#endif

    if (ss_twr_init_current_sweep_full) {
        ss_twr_init_perf_full_sweep_sum_ms += sweep_elapsed_ms;
        ss_twr_init_perf_full_sweep_count++;
    } else {
        ss_twr_init_perf_track_sweep_sum_ms += sweep_elapsed_ms;
        ss_twr_init_perf_track_sweep_count++;
    }

    if (have_motion) {
        ss_twr_init_perf_motion_dt_sum_ms += motion.dt_ms;
        ss_twr_init_perf_motion_dt_count++;
        ss_twr_init_last_motion_speed_mm_s = motion.speed_mm_s;
    }

    ss_twr_init_have_last_location = true;
    ss_twr_init_last_location_x_mm = location.x_mm;
    ss_twr_init_last_location_y_mm = location.y_mm;
    ss_twr_init_last_location_z_mm = location.z_mm;
    ss_twr_init_last_output_ms = (uint32_t)k_uptime_get();
#if APP_TAG_STATUS_PERIOD_MS > 0U
    ss_twr_init_last_location_update_ms = (uint32_t)k_uptime_get();
    ss_twr_init_have_last_raw_location = true;
    ss_twr_init_last_raw_location = raw_location;
    ss_twr_init_last_filtered_location = location;
#endif

    if ((ss_twr_init_sweep_count % APP_TAG_SUMMARY_PERIOD) != 0U) {
        for (size_t i = 0; i < location.used_anchor_count; ++i) {
            ss_twr_init_last_solution_anchor_ids[i] = location.anchor_ids[i];
        }
        ss_twr_init_last_solution_anchor_count = location.used_anchor_count;
        ss_twr_init_have_last_solution = true;
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
        ss_twr_init_diag_out_done_cycles = k_cycle_get_32();
#endif
        return;
    }

    {
        char summary[320];
        size_t summary_len = 0U;
        bool imu_is_moving = false;

        if (have_imu) {
            imu_is_moving = ss_twr_init_imu_sample_indicates_motion(&imu);
            ss_twr_init_last_imu_indicates_motion = imu_is_moving;
        }

#if APP_TAG_CONSOLE_SUMMARY_ENABLE
        summary[0] = '\0';
        summary_len += (size_t)snprintk(
            summary + summary_len, sizeof(summary) - summary_len,
            "Tag motion summary sweep=%lu plan=%s sweep_ms=%lu active=%u "
            "used=%u lower=%u upper=%u raw_xyz=(%ld,%ld,%ld) mm xyz=(%ld,%ld,%ld) mm rms=%lu mm "
            "max=%lu mm",
            (unsigned long)ss_twr_init_sweep_count,
            ss_twr_init_plan_label(),
            (unsigned long)sweep_elapsed_ms,
            (unsigned int)ss_twr_init_active_anchor_count,
            (unsigned int)location.used_anchor_count,
            (unsigned int)location.lower_anchor_count,
            (unsigned int)location.upper_anchor_count,
            (long)raw_location.x_mm, (long)raw_location.y_mm,
            (long)raw_location.z_mm,
            (long)location.x_mm, (long)location.y_mm, (long)location.z_mm,
            (unsigned long)location.residual_rms_mm,
            (unsigned long)location.residual_max_mm);

        summary_len += (size_t)snprintk(summary + summary_len,
                                        sizeof(summary) - summary_len,
                                        " anchors=[");
        for (size_t i = 0; i < location.used_anchor_count &&
                           summary_len + 4U < sizeof(summary);
             ++i) {
            const struct uwb_anchor_pose_mm *pose =
                uwb_anchor_layout_get(location.anchor_ids[i]);
            if (pose != NULL) {
                summary_len += (size_t)snprintk(summary + summary_len,
                                                sizeof(summary) - summary_len,
                                                "%c", pose->label);
            } else {
                summary_len += (size_t)snprintk(summary + summary_len,
                                                sizeof(summary) - summary_len,
                                                "%u",
                                                (unsigned int)
                                                    location.anchor_ids[i]);
            }
            if (i + 1U < location.used_anchor_count) {
                summary_len += (size_t)snprintk(summary + summary_len,
                                                sizeof(summary) - summary_len,
                                                ",");
            }
        }
        summary_len += (size_t)snprintk(summary + summary_len,
                                        sizeof(summary) - summary_len, "]");

        if (have_motion) {
            summary_len += (size_t)snprintk(
                summary + summary_len, sizeof(summary) - summary_len,
                " motion_dt=%lu ms disp=%lu mm vel=(%ld,%ld,%ld) mm/s "
                "speed=%lu mm/s",
                (unsigned long)motion.dt_ms,
                (unsigned long)motion.displacement_mm,
                (long)motion.vx_mm_s,
                (long)motion.vy_mm_s,
                (long)motion.vz_mm_s,
                (unsigned long)motion.speed_mm_s);
        } else {
            summary_len += (size_t)snprintk(summary + summary_len,
                                            sizeof(summary) - summary_len,
                                            " motion=na");
        }

        if (have_imu) {
            summary_len += (size_t)snprintk(
                summary + summary_len, sizeof(summary) - summary_len,
                " accel=(%ld,%ld,%ld) mg norm=%ld err=%ld delta=%lu "
                "state=%s",
                (long)imu.ax_mg, (long)imu.ay_mg, (long)imu.az_mg,
                (long)imu.norm_mg, (long)imu.gravity_error_mg,
                (unsigned long)imu.delta_magnitude_mg,
                imu_is_moving ? "moving" : "stable");
        } else {
            summary_len += (size_t)snprintk(summary + summary_len,
                                            sizeof(summary) - summary_len,
                                            " accel=na");
        }

        printk("%s\n", summary);
        printk("UWB TAG POSITION raw_xyz=(%ld,%ld,%ld) mm xyz=(%ld,%ld,%ld) mm used=%u lower=%u upper=%u sweep=%lu plan=%s rms=%lu mm max=%lu mm received=[%s]\n",
               (long)raw_location.x_mm, (long)raw_location.y_mm,
               (long)raw_location.z_mm, (long)location.x_mm,
               (long)location.y_mm, (long)location.z_mm,
               (unsigned int)location.used_anchor_count,
               (unsigned int)location.lower_anchor_count,
               (unsigned int)location.upper_anchor_count,
               (unsigned long)ss_twr_init_sweep_count,
               ss_twr_init_plan_label(),
               (unsigned long)location.residual_rms_mm,
               (unsigned long)location.residual_max_mm,
               received_anchors);
#endif
    }

    ss_twr_init_location_output_count++;

    if (ss_twr_init_perf_motion_dt_count != 0U ||
        ss_twr_init_perf_track_sweep_count != 0U ||
        ss_twr_init_perf_full_sweep_count != 0U) {
#if APP_TAG_VERBOSE_PERF
        uint32_t avg_motion_dt_ms =
            (ss_twr_init_perf_motion_dt_count == 0U)
                ? 0U
                : (ss_twr_init_perf_motion_dt_sum_ms /
                   ss_twr_init_perf_motion_dt_count);
        uint32_t avg_track_sweep_ms =
            (ss_twr_init_perf_track_sweep_count == 0U)
                ? 0U
                : (ss_twr_init_perf_track_sweep_sum_ms /
                   ss_twr_init_perf_track_sweep_count);
        uint32_t avg_full_sweep_ms =
            (ss_twr_init_perf_full_sweep_count == 0U)
                ? 0U
                : (ss_twr_init_perf_full_sweep_sum_ms /
                   ss_twr_init_perf_full_sweep_count);

        printk("Tag perf window=%u avg_motion_dt=%lu ms avg_track_sweep=%lu ms avg_full_sweep=%lu ms track_samples=%u full_samples=%u\n",
               (unsigned int)APP_TAG_SUMMARY_PERIOD,
               (unsigned long)avg_motion_dt_ms,
               (unsigned long)avg_track_sweep_ms,
               (unsigned long)avg_full_sweep_ms,
               (unsigned int)ss_twr_init_perf_track_sweep_count,
               (unsigned int)ss_twr_init_perf_full_sweep_count);
#endif
    }

    if (!ss_twr_init_runtime_any_calibration_mode()) {
#if APP_TAG_POSITION_OUTPUT_ENABLE != 0U
        char ble_summary[256];
        char ble_anchors[32];
        char ble_anchor_labels[32];
        size_t ble_anchors_len = 0U;
        size_t ble_anchor_labels_len = 0U;
        const char *plan_label = ss_twr_init_plan_label();

        ble_anchors[ble_anchors_len++] = '[';
        ble_anchor_labels[0] = '\0';
        for (size_t i = 0; i < location.used_anchor_count &&
                           ble_anchors_len + 3U < sizeof(ble_anchors) &&
                           ble_anchor_labels_len + 2U <
                               sizeof(ble_anchor_labels);
             ++i) {
            const struct uwb_anchor_pose_mm *pose =
                uwb_anchor_layout_get(location.anchor_ids[i]);

            if (i != 0U) {
                ble_anchors[ble_anchors_len++] = ',';
            }

            if (pose != NULL) {
                ble_anchors[ble_anchors_len++] = pose->label;
                ble_anchor_labels[ble_anchor_labels_len++] = pose->label;
            } else {
                ble_anchors_len += snprintk(&ble_anchors[ble_anchors_len],
                                            sizeof(ble_anchors) - ble_anchors_len,
                                            "%u",
                                            (unsigned int)location.anchor_ids[i]);
                ble_anchor_labels_len += snprintk(
                    &ble_anchor_labels[ble_anchor_labels_len],
                    sizeof(ble_anchor_labels) - ble_anchor_labels_len, "%u",
                    (unsigned int)location.anchor_ids[i]);
            }
        }
        ble_anchors[ble_anchors_len++] = ']';
        ble_anchors[ble_anchors_len] = '\0';
        ble_anchor_labels[ble_anchor_labels_len] = '\0';

        if (APP_TAG_BLE_COMPACT_STATUS != 0U) {
	            snprintk(ble_summary, sizeof(ble_summary),
	                     "TS;1;%lu;%c;%ld;%ld;%ld;%lu;%lu;%s;%u;%u;%c;%u;%c;%lu;%u;%s;%u",
	                     (unsigned long)ss_twr_init_sweep_count,
	                     ss_twr_init_plan_code(plan_label),
	                     (long)location.x_mm, (long)location.y_mm,
                     (long)location.z_mm,
                     (unsigned long)location.residual_rms_mm,
                     (unsigned long)location.residual_max_mm,
                     ble_anchor_labels,
                     (unsigned int)ss_twr_init_tdma_schedule.slot_index,
                     (unsigned int)ss_twr_init_tdma_schedule.slot_count,
                     ss_twr_init_slot_source_code(
                         ss_twr_init_runtime_params.slot_source),
	                     (unsigned int)ss_twr_init_last_sweep_cut_short,
	                     ss_twr_init_solve_reason_code(),
	                     (unsigned long)(have_motion ? motion.dt_ms : 0U),
	                     (unsigned int)ss_twr_init_runtime_params.positioning_mode,
	                     plan_label,
	                     (unsigned int)solution_quality_percent);
        } else {
            if (have_motion) {
                snprintk(ble_summary, sizeof(ble_summary),
                         "TagSummary sweep=%lu plan=%s pmode=%u qf=%u xyz=(%ld,%ld,%ld) rms=%lu max=%lu anchors=%s slot=%u/%u src=%s cut=%u reason=%s motion_dt=%lu",
                         (unsigned long)ss_twr_init_sweep_count,
                         plan_label,
                         (unsigned int)ss_twr_init_runtime_params.positioning_mode,
                         (unsigned int)solution_quality_percent,
                         (long)location.x_mm, (long)location.y_mm,
                         (long)location.z_mm,
                         (unsigned long)location.residual_rms_mm,
                         (unsigned long)location.residual_max_mm,
                         ble_anchors,
                         (unsigned int)ss_twr_init_tdma_schedule.slot_index,
                         (unsigned int)ss_twr_init_tdma_schedule.slot_count,
                         ss_twr_init_slot_source_label(
                             ss_twr_init_runtime_params.slot_source),
                         (unsigned int)ss_twr_init_last_sweep_cut_short,
                         ss_twr_init_solve_reason_label(),
                         (unsigned long)motion.dt_ms);
            } else {
                snprintk(ble_summary, sizeof(ble_summary),
                         "TagSummary sweep=%lu plan=%s pmode=%u qf=%u xyz=(%ld,%ld,%ld) rms=%lu max=%lu anchors=%s slot=%u/%u src=%s cut=%u reason=%s motion=na",
                         (unsigned long)ss_twr_init_sweep_count,
                         plan_label,
                         (unsigned int)ss_twr_init_runtime_params.positioning_mode,
                         (unsigned int)solution_quality_percent,
                         (long)location.x_mm, (long)location.y_mm,
                         (long)location.z_mm,
                         (unsigned long)location.residual_rms_mm,
                         (unsigned long)location.residual_max_mm,
                         ble_anchors,
                         (unsigned int)ss_twr_init_tdma_schedule.slot_index,
                         (unsigned int)ss_twr_init_tdma_schedule.slot_count,
                         ss_twr_init_slot_source_label(
                             ss_twr_init_runtime_params.slot_source),
                         (unsigned int)ss_twr_init_last_sweep_cut_short,
                         ss_twr_init_solve_reason_label());
            }
        }

#if APP_TAG_BLE_ENABLE
#if APP_TAG_USB_MIRROR_BLE_STATUS
        printk("%s\n", ble_summary);
#endif
        (void)uwb_tag_ble_publish_status(ble_summary);
#endif
#endif
#if APP_TAG_BLE_ENABLE
        ss_twr_init_publish_tag_range_summary(measurements,
                                              ss_twr_init_anchor_count,
                                              solution_quality_percent);
#endif
    }

    if (ss_twr_init_runtime_any_calibration_mode()) {
        ss_twr_init_publish_calibration_summary(
            ss_twr_init_plan_label(),
            ss_twr_init_runtime_params.positioning_mode,
            solution_quality_percent);
    }

    ss_twr_init_perf_motion_dt_sum_ms = 0U;
    ss_twr_init_perf_track_sweep_sum_ms = 0U;
    ss_twr_init_perf_full_sweep_sum_ms = 0U;
    ss_twr_init_perf_motion_dt_count = 0U;
    ss_twr_init_perf_track_sweep_count = 0U;
    ss_twr_init_perf_full_sweep_count = 0U;

    for (size_t i = 0; i < location.used_anchor_count; ++i) {
        ss_twr_init_last_solution_anchor_ids[i] = location.anchor_ids[i];
    }
    ss_twr_init_last_solution_anchor_count = location.used_anchor_count;
    ss_twr_init_have_last_solution = true;
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_out_done_cycles = k_cycle_get_32();
#endif
}

#if APP_ALT_SS_TWR_ENABLE
static uint32_t ss_twr_init_alt_last_poll_diag_ms;
static uint32_t ss_twr_init_alt_last_poll_timing_diag_ms;
static uint32_t ss_twr_init_alt_last_rx_diag_ms;
static uint32_t ss_twr_init_alt_last_rx_gap_diag_ms;
static uint32_t ss_twr_init_alt_ltdma_slot_start_cycles;
static uint32_t ss_twr_init_alt_last_sweep_entry_cycles;
static uint32_t ss_twr_init_alt_last_tx_sched_cycles;
static uint32_t ss_twr_init_alt_last_tx_write_done_cycles;
static uint32_t ss_twr_init_alt_last_tx_cmd_cycles;
static bool ss_twr_init_alt_bcast_tx_prearmed;
static bool ss_twr_init_alt_last_tx_prearmed;
static uint8_t ss_twr_init_alt_bcast_prearmed_seq;
static uint8_t ss_twr_init_alt_bcast_prearmed_mask;
static uint8_t ss_twr_init_alt_bcast_prearmed_count;

static void ss_twr_init_alt_publish_rx_gap_diag(uint32_t tx_done_cycles,
                                                uint32_t rx_start_cycles,
                                                uint32_t rx_done_cycles,
                                                uint32_t response_window_us,
                                                uint8_t poll_count,
                                                uint8_t anchor_mask,
                                                int rxenable_rc)
{
    uint32_t now_ms = (uint32_t)k_uptime_get();
    char line[384];
    uint32_t slot_to_txdone_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles, tx_done_cycles);
    uint32_t slot_to_entry_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_sweep_entry_cycles);
    uint32_t slot_to_sched_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_tx_sched_cycles);
    uint32_t slot_to_write_done_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_tx_write_done_cycles);
    uint32_t slot_to_txcmd_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_tx_cmd_cycles);
    uint32_t txcmd_to_txdone_us =
        (ss_twr_init_alt_last_tx_cmd_cycles != 0U &&
         tx_done_cycles != 0U) ?
            k_cyc_to_us_floor32(tx_done_cycles -
                                ss_twr_init_alt_last_tx_cmd_cycles) :
            UINT_MAX;

    if (tx_done_cycles == 0U || rx_start_cycles == 0U || rx_done_cycles == 0U) {
        return;
    }

    if (ss_twr_init_alt_last_rx_gap_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_rx_gap_diag_ms) < 1000U) {
        return;
    }
    ss_twr_init_alt_last_rx_gap_diag_ms = now_ms;

    snprintk(line, sizeof(line),
             "RXG;1;%lu;tag=%u;mask=0x%02x;pc=%u;guard=%u;spacing=%u;win=%lu;pre=%u;slot_to_entry_us=%lu;slot_to_sched_us=%lu;slot_to_write_done_us=%lu;slot_to_txcmd_us=%lu;slot_to_txdone_us=%lu;txcmd_to_txdone_us=%lu;txdone_to_rxstart_us=%lu;txdone_to_rxend_us=%lu;rxenable_us=%lu;rc=%d;slot=%u/%u;period=%u;active=%u;active_us=%u;lperiod=%u;lcount=%u",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)ss_twr_init_local_tag_id,
             (unsigned int)anchor_mask,
             (unsigned int)poll_count,
             (unsigned int)APP_ALT_SS_TWR_GUARD_US,
             (unsigned int)APP_ALT_SS_TWR_RESP_SPACING_US,
             (unsigned long)response_window_us,
             (unsigned int)ss_twr_init_alt_last_tx_prearmed,
             (unsigned long)slot_to_entry_us,
             (unsigned long)slot_to_sched_us,
             (unsigned long)slot_to_write_done_us,
             (unsigned long)slot_to_txcmd_us,
             (unsigned long)slot_to_txdone_us,
             (unsigned long)txcmd_to_txdone_us,
             (unsigned long)k_cyc_to_us_floor32(rx_start_cycles - tx_done_cycles),
             (unsigned long)k_cyc_to_us_floor32(rx_done_cycles - tx_done_cycles),
             (unsigned long)k_cyc_to_us_floor32(rx_done_cycles - rx_start_cycles),
             rxenable_rc,
             (unsigned int)ss_twr_init_tdma_schedule.slot_index,
             (unsigned int)ss_twr_init_tdma_schedule.slot_count,
             (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
             (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
             (unsigned int)ss_twr_init_tdma_schedule.slot_active_us,
             (unsigned int)APP_TAG_TDMA_SLOT_PERIOD_MS,
             (unsigned int)APP_TAG_TDMA_SLOT_COUNT);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_alt_publish_rx_diag(uint32_t status_reg,
                                            uint32_t rx_finfo,
                                            uint32_t response_window_us,
                                            uint8_t poll_count,
                                            uint8_t anchor_mask,
                                            uint8_t responses,
                                            uint8_t unexpected_count,
                                            uint32_t last_frame_len,
                                            uint16_t last_src_addr,
                                            uint16_t last_dst_addr,
                                            uint8_t last_code)
{
    uint32_t now_ms = (uint32_t)k_uptime_get();
    char line[160];

    if (ss_twr_init_alt_last_rx_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_rx_diag_ms) < 1000U) {
        return;
    }
    ss_twr_init_alt_last_rx_diag_ms = now_ms;

    snprintk(line, sizeof(line),
             "CD;1;%lu;tag=%u;local=0x%04x;status=0x%08lx;rxf=0x%08lx;win=%lu;pc=%u;mask=0x%02x;resp=%u;unexp=%u;last_len=%lu;last_src=0x%04x;last_dst=0x%04x;last_code=0x%02x",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)ss_twr_init_local_tag_id,
             (unsigned int)ss_twr_init_local_addr,
             (unsigned long)status_reg,
             (unsigned long)rx_finfo,
             (unsigned long)response_window_us,
             (unsigned int)poll_count,
             (unsigned int)anchor_mask,
             (unsigned int)responses,
             (unsigned int)unexpected_count,
             (unsigned long)last_frame_len,
             (unsigned int)last_src_addr,
             (unsigned int)last_dst_addr,
             (unsigned int)last_code);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_alt_mark_scheduled_poll_timing(uint8_t poll_count)
{
    uint32_t now = k_cycle_get_32();
    ss_twr_init_sweep_first_poll_cycle = now;
    /*
     * Alt v3 uses one broadcast poll carrying the active anchor mask. All
     * selected anchors share the same measurement instant, so first-to-last
     * poll skew is intentionally zero even when poll_count is 4/8.
     */
    ss_twr_init_sweep_last_poll_cycle = now;
    ss_twr_init_sweep_poll_count = poll_count;
    ss_twr_init_sweep_timing_valid = true;
}

static void ss_twr_init_alt_mark_unicast_poll_timing(uint32_t first_cycle,
                                                     uint32_t last_cycle,
                                                     uint8_t poll_count)
{
    ss_twr_init_sweep_first_poll_cycle = first_cycle;
    ss_twr_init_sweep_last_poll_cycle = last_cycle;
    ss_twr_init_sweep_poll_count = poll_count;
    ss_twr_init_sweep_timing_valid = true;
}

static void ss_twr_init_alt_print_poll_diag(uint8_t poll_count,
                                            uint8_t anchor_mask)
{
    uint32_t now_ms = (uint32_t)k_uptime_get();
    uint16_t poll_dst = uwb_frame_get_dst_addr(ss_twr_init_tx_poll_msg);
    uint8_t frame_tag_id = uwb_ss_twr_poll_tag_id(ss_twr_init_tx_poll_msg);
    uint64_t frame_poll_tx_ts = uwb_ss_twr_poll_tx_ts(ss_twr_init_tx_poll_msg);

    if (ss_twr_init_alt_last_poll_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_poll_diag_ms) <
            APP_TAG_ALT_POLL_DIAG_PERIOD_MS) {
        return;
    }

    ss_twr_init_alt_last_poll_diag_ms = now_ms;
    printk("Alt poll diag tag=%u src=0x%04x dst=0x%04x seq=%u mode=%u poll_count=%u mask=0x%02x "
           "frame_tag=%u poll_tx_ts=0x%08lx%02lx active=%u,%u,%u,%u,%u,%u,%u,%u\n",
           (unsigned int)ss_twr_init_local_tag_id,
           (unsigned int)ss_twr_init_local_addr,
           (unsigned int)poll_dst,
           (unsigned int)ss_twr_init_frame_seq_nb,
           (unsigned int)APP_ALT_SS_TWR_MODE,
           (unsigned int)poll_count,
           (unsigned int)anchor_mask,
           (unsigned int)frame_tag_id,
           (unsigned long)(frame_poll_tx_ts >> 8U),
           (unsigned long)(frame_poll_tx_ts & 0xffU),
           (unsigned int)ss_twr_init_active_anchor_ids[0],
           (unsigned int)ss_twr_init_active_anchor_ids[1],
           (unsigned int)ss_twr_init_active_anchor_ids[2],
           (unsigned int)ss_twr_init_active_anchor_ids[3],
           (unsigned int)ss_twr_init_active_anchor_ids[4],
           (unsigned int)ss_twr_init_active_anchor_ids[5],
           (unsigned int)ss_twr_init_active_anchor_ids[6],
           (unsigned int)ss_twr_init_active_anchor_ids[7]);
}

static void ss_twr_init_alt_print_unicast_timing_diag(
    uint8_t poll_count,
    const uint32_t *target_poll_cycles,
    const uint32_t *write_start_cycles,
    const uint32_t *write_done_cycles,
    const uint32_t *starttx_cycles,
    const uint32_t *txfrs_cycles)
{
    uint32_t now_ms = (uint32_t)k_uptime_get();
    uint32_t poll_start_gap_us[4] = {0};
    uint32_t write_us[4] = {0};
    uint32_t start_to_frs_us[4] = {0};
    int32_t lateness_us[4] = {0};
    uint8_t diag_count;
    char line[192];

    if (poll_count == 0U) {
        return;
    }
    if (ss_twr_init_alt_last_poll_timing_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_poll_timing_diag_ms) <
            APP_TAG_ALT_POLL_DIAG_PERIOD_MS) {
        return;
    }
    ss_twr_init_alt_last_poll_timing_diag_ms = now_ms;

    diag_count = (poll_count > 4U) ? 4U : poll_count;
    for (uint8_t i = 0U; i < diag_count; ++i) {
        write_us[i] = k_cyc_to_us_floor32(
            write_done_cycles[i] - write_start_cycles[i]);
        start_to_frs_us[i] = k_cyc_to_us_floor32(
            txfrs_cycles[i] - starttx_cycles[i]);
        lateness_us[i] = (int32_t)k_cyc_to_us_floor32(
            starttx_cycles[i] - target_poll_cycles[i]);
        if (i > 0U) {
            poll_start_gap_us[i - 1U] = k_cyc_to_us_floor32(
                starttx_cycles[i] - starttx_cycles[i - 1U]);
        }
    }

    snprintk(line, sizeof(line),
             "CD;2;%lu;tag=%u;src=0x%04x;pc=%u;spacing=%u;gap=%lu,%lu,%lu;write=%lu,%lu,%lu,%lu;txfrs=%lu,%lu,%lu,%lu;late=%ld,%ld,%ld,%ld",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)ss_twr_init_local_tag_id,
             (unsigned int)ss_twr_init_local_addr,
             (unsigned int)poll_count,
             (unsigned int)APP_ALT_SS_TWR_POLL_SPACING_US,
             (unsigned long)poll_start_gap_us[0],
             (unsigned long)poll_start_gap_us[1],
             (unsigned long)poll_start_gap_us[2],
             (unsigned long)write_us[0],
             (unsigned long)write_us[1],
             (unsigned long)write_us[2],
             (unsigned long)write_us[3],
             (unsigned long)start_to_frs_us[0],
             (unsigned long)start_to_frs_us[1],
             (unsigned long)start_to_frs_us[2],
             (unsigned long)start_to_frs_us[3],
             (long)lateness_us[0],
             (long)lateness_us[1],
             (long)lateness_us[2],
             (long)lateness_us[3]);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_alt_record_range(uint8_t anchor_id, long raw_distance_mm)
{
    struct uwb_range_tracker *tracker = &ss_twr_init_trackers[anchor_id];
    uint32_t filtered_mm;

    if (raw_distance_mm < 0L) {
        raw_distance_mm = 0L;
    }

    if (!ss_twr_init_raw_range_plausible(tracker, (uint32_t)raw_distance_mm)) {
        uwb_range_tracker_record_failure(tracker);
        ss_twr_init_record_sweep_anchor_state(anchor_id,
                                              UWB_TAG_BLE_CAL_STATUS_REJECT,
                                              tracker);
        ss_twr_init_record_sweep_anchor_diag(
            anchor_id, SS_TWR_INIT_CAL_REASON_RAW_OUTLIER, raw_distance_mm,
            tracker->filtered_mm, 0U, 0U,
            uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
        ss_twr_init_publish_cal_range(anchor_id, UWB_TAG_BLE_CAL_STATUS_REJECT,
                                      raw_distance_mm, tracker->filtered_mm,
                                      tracker);
#endif
        return;
    }

    filtered_mm = uwb_range_tracker_record_success(tracker, (uint32_t)raw_distance_mm);
    ss_twr_init_record_sweep_anchor_state(anchor_id, UWB_TAG_BLE_CAL_STATUS_OK,
                                          tracker);
    ss_twr_init_record_sweep_anchor_diag(
        anchor_id, SS_TWR_INIT_CAL_REASON_OK, raw_distance_mm, filtered_mm, 0U,
        0U, uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
    ss_twr_init_publish_cal_range(anchor_id, UWB_TAG_BLE_CAL_STATUS_OK,
                                  raw_distance_mm, filtered_mm, tracker);
#endif
}

static void ss_twr_init_alt_record_timeout(uint8_t anchor_id, uint8_t reason)
{
    struct uwb_range_tracker *tracker = &ss_twr_init_trackers[anchor_id];

    uwb_range_tracker_record_failure(tracker);
    ss_twr_init_record_sweep_anchor_state(anchor_id,
                                          UWB_TAG_BLE_CAL_STATUS_TIMEOUT,
                                          tracker);
    ss_twr_init_record_sweep_anchor_diag(
        anchor_id, reason, 0, tracker->filtered_mm, 0U, 0U,
        uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
    ss_twr_init_publish_cal_range(anchor_id, UWB_TAG_BLE_CAL_STATUS_TIMEOUT, 0,
                                  tracker->filtered_mm, tracker);
#endif
}

static void ss_twr_init_alt_finish_sweep(void)
{
    ss_twr_init_sweep_count++;
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
    APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE != 0U
    ss_twr_init_note_sweep_done();
    ss_twr_init_print_location_if_ready();
    ss_twr_init_apply_pending_runtime_config_if_any();
    ss_twr_init_prepare_sweep_plan();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_clean_done_cycles = k_cycle_get_32();
    ss_twr_init_sweep_diag_maybe_print();
#endif
#else
    ss_twr_init_release_ble_tx_after_active_slot();
    ss_twr_init_note_sweep_done();
    ss_twr_init_print_location_if_ready();
    ss_twr_init_apply_pending_runtime_config_if_any();
    ss_twr_init_last_tdma_wait_ms = ss_twr_init_wait_until_next_slot_if_needed();
    ss_twr_init_prepare_sweep_plan();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_clean_done_cycles = k_cycle_get_32();
    ss_twr_init_sweep_diag_maybe_print();
#endif
#endif
}

static bool ss_twr_init_alt_wait_tx_done(uint32_t timeout_us)
{
    uint32_t start = k_cycle_get_32();
    uint32_t timeout_cycles = k_us_to_cyc_floor32(timeout_us);

    while ((uint32_t)(k_cycle_get_32() - start) < timeout_cycles) {
        uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
        if ((status & SYS_STATUS_TXFRS) != 0U) {
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
            return true;
        }
    }

    return false;
}

static void ss_twr_init_alt_wait_until_cycle(uint32_t target_cycle)
{
    while ((int32_t)(k_cycle_get_32() - target_cycle) < 0) {
    }
}

static uint8_t ss_twr_init_alt_mask_rank(uint8_t mask, uint8_t anchor_id)
{
    uint8_t rank = 0U;

    for (uint8_t i = 0U; i < anchor_id && i < UWB_MAX_ANCHORS; ++i) {
        if ((mask & (uint8_t)(1U << i)) != 0U) {
            rank++;
        }
    }

    return rank;
}

static void ss_twr_init_alt_rx_restart(uint32_t response_window_us)
{
    dwt_forcetrxoff();
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_RX_GOOD | SYS_STATUS_ALL_RX_ERR |
                          SYS_STATUS_ALL_RX_TO);
    dwt_setrxtimeout(response_window_us);
    dwt_rxenable(DWT_START_RX_IMMEDIATE);
}

static void ss_twr_init_alt_set_rx_auto_reenable(bool enable)
{
    uint32_t sys_cfg = dwt_read32bitreg(SYS_CFG_ID);

    if (enable) {
        sys_cfg |= SYS_CFG_RXAUTR;
    } else {
        sys_cfg &= ~SYS_CFG_RXAUTR;
    }
    dwt_write32bitreg(SYS_CFG_ID, sys_cfg);
}

static uint8_t ss_twr_init_alt_active_anchor_mask(uint8_t poll_count)
{
    uint8_t anchor_mask = 0U;

    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        if (anchor_id < UWB_MAX_ANCHORS) {
            anchor_mask |= (uint8_t)(1U << anchor_id);
        }
    }

    return anchor_mask;
}

static bool ss_twr_init_alt_bcast_prewrite_tx(void)
{
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
    APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE != 0U && \
    APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE != 0U
    uint8_t poll_count = (uint8_t)ss_twr_init_active_anchor_count;
    uint8_t anchor_mask;

    ss_twr_init_alt_bcast_tx_prearmed = false;
    if (poll_count == 0U || poll_count > UWB_MAX_ANCHORS) {
        return false;
    }

    anchor_mask = ss_twr_init_alt_active_anchor_mask(poll_count);
    if (anchor_mask == 0U) {
        return false;
    }

    ss_twr_init_prepare_radio_for_poll();
    uwb_ss_twr_build_alt_broadcast_poll_frame(ss_twr_init_tx_poll_msg,
                                              ss_twr_init_frame_seq_nb,
                                              ss_twr_init_local_addr,
                                              anchor_mask,
                                              ss_twr_init_local_tag_id,
                                              0U);
    if (dwt_writetxdata(UWB_MSG_ALT_POLL_FRAME_LEN,
                        ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
        return false;
    }
    dwt_writetxfctrl(UWB_MSG_ALT_POLL_FRAME_LEN, 0, 1);

    ss_twr_init_alt_bcast_prearmed_seq = ss_twr_init_frame_seq_nb;
    ss_twr_init_alt_bcast_prearmed_mask = anchor_mask;
    ss_twr_init_alt_bcast_prearmed_count = poll_count;
    ss_twr_init_alt_bcast_tx_prearmed = true;
    return true;
#else
    return false;
#endif
}

static bool ss_twr_init_alt_burst_sweep_once(void)
{
    ss_twr_init_alt_last_sweep_entry_cycles = k_cycle_get_32();
    ss_twr_init_alt_last_tx_sched_cycles = 0U;
    ss_twr_init_alt_last_tx_write_done_cycles = 0U;
    ss_twr_init_alt_last_tx_cmd_cycles = 0U;
    ss_twr_init_alt_last_tx_prearmed = false;

    uint8_t poll_count = (uint8_t)ss_twr_init_active_anchor_count;
    uint32_t poll_tx_ts[UWB_MAX_ANCHORS] = {0};
    uint32_t resp_rx_ts_by_anchor[UWB_MAX_ANCHORS] = {0};
    uint32_t poll_rx_ts_by_anchor[UWB_MAX_ANCHORS] = {0};
    uint32_t resp_tx_ts_by_anchor[UWB_MAX_ANCHORS] = {0};
    int32_t carrier_integrator_by_anchor[UWB_MAX_ANCHORS] = {0};
    long raw_distance_mm[UWB_MAX_ANCHORS] = {0};
    bool received[UWB_MAX_ANCHORS] = {0};
    uint8_t responses = 0U;
    uint8_t unexpected_count = 0U;
    uint32_t response_window_us;
    uint32_t last_status_reg = 0U;
    uint32_t last_rx_finfo = 0U;
    uint32_t last_frame_len = 0U;
    uint16_t last_src_addr = 0U;
    uint16_t last_dst_addr = 0U;
    uint8_t last_code = 0U;
    uint8_t anchor_mask = 0U;
    uint32_t response_window_start_cycles = 0U;
    uint32_t response_window_cycles = 0U;
    uint32_t first_poll_cycle = 0U;
    uint32_t last_poll_cycle = 0U;
    uint32_t poll_tx_done_cycles = 0U;
    uint32_t rx_enable_start_cycles = 0U;
    uint32_t rx_enable_done_cycles = 0U;
    int rxenable_rc = 0;
    bool use_prearmed_tx = false;

    if (poll_count == 0U || poll_count > UWB_MAX_ANCHORS) {
        return false;
    }

#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST || \
    APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE == 0U
    ss_twr_init_set_ble_tx_paused(true);
#endif
    anchor_mask = ss_twr_init_alt_active_anchor_mask(poll_count);
    response_window_us = ss_twr_init_alt_bcast_response_window_us(poll_count);
    response_window_cycles = k_us_to_cyc_floor32(response_window_us);
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
    ss_twr_init_alt_set_rx_auto_reenable(false);
    dwt_setrxtimeout(0U);
#endif

#if !(APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
      APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE != 0U && \
      APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE != 0U)
    ss_twr_init_prepare_radio_for_poll();
#else
    use_prearmed_tx =
        ss_twr_init_alt_bcast_tx_prearmed &&
        ss_twr_init_alt_bcast_prearmed_seq == ss_twr_init_frame_seq_nb &&
        ss_twr_init_alt_bcast_prearmed_mask == anchor_mask &&
        ss_twr_init_alt_bcast_prearmed_count == poll_count;

    if (!use_prearmed_tx) {
        ss_twr_init_prepare_radio_for_poll();
    }
#endif

#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_UNICAST
    ss_twr_init_alt_print_poll_diag(poll_count, 0U);
    uint32_t poll_spacing_cycles =
        k_us_to_cyc_floor32(APP_ALT_SS_TWR_POLL_SPACING_US);
    uint32_t target_poll_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t write_start_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t write_done_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t starttx_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t txfrs_cycles[UWB_MAX_ANCHORS] = {0};
    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        uint16_t anchor_addr = uwb_anchor_short_addr(anchor_id);
        uint32_t target_poll_cycle = 0U;
        uint32_t poll_start_cycle;

        write_start_cycles[i] = k_cycle_get_32();
        uwb_ss_twr_build_alt_poll_frame(ss_twr_init_tx_poll_msg,
                                        ss_twr_init_frame_seq_nb,
                                        anchor_addr,
                                        ss_twr_init_local_addr,
                                        i, poll_count);
        if (dwt_writetxdata(UWB_MSG_ALT_UNICAST_POLL_FRAME_LEN,
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            for (uint8_t j = 0U; j < poll_count; ++j) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[j],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        dwt_writetxfctrl(UWB_MSG_ALT_UNICAST_POLL_FRAME_LEN, 0, 1);
        write_done_cycles[i] = k_cycle_get_32();
        if (i > 0U) {
            target_poll_cycle = first_poll_cycle +
                ((uint32_t)i * poll_spacing_cycles);
            ss_twr_init_alt_wait_until_cycle(target_poll_cycle);
        } else {
            target_poll_cycle = k_cycle_get_32();
        }
        poll_start_cycle = k_cycle_get_32();
        if (first_poll_cycle == 0U) {
            first_poll_cycle = poll_start_cycle;
            target_poll_cycle = poll_start_cycle;
        }
        last_poll_cycle = poll_start_cycle;
        target_poll_cycles[i] = target_poll_cycle;
        starttx_cycles[i] = poll_start_cycle;
        if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS) {
            for (uint8_t j = 0U; j < poll_count; ++j) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[j],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        if (!ss_twr_init_alt_wait_tx_done(APP_ALT_SS_TWR_POLL_SPACING_US + 1000U)) {
            dwt_forcetrxoff();
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
            for (uint8_t j = 0U; j < poll_count; ++j) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[j],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        txfrs_cycles[i] = k_cycle_get_32();
        poll_tx_ts[anchor_id] = dwt_readtxtimestamplo32();
        ss_twr_init_frame_seq_nb++;
    }
    ss_twr_init_alt_mark_unicast_poll_timing(first_poll_cycle, last_poll_cycle,
                                             poll_count);
    ss_twr_init_alt_print_unicast_timing_diag(
        poll_count, target_poll_cycles, write_start_cycles, write_done_cycles,
        starttx_cycles, txfrs_cycles);
#else
#if APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE != 0U
    ss_twr_init_alt_last_tx_prearmed = use_prearmed_tx;
    if (use_prearmed_tx) {
        ss_twr_init_alt_last_tx_sched_cycles =
            ss_twr_init_alt_ltdma_slot_start_cycles;
        ss_twr_init_alt_last_tx_write_done_cycles =
            ss_twr_init_alt_ltdma_slot_start_cycles;
    } else {
        ss_twr_init_alt_last_tx_sched_cycles = k_cycle_get_32();
        uwb_ss_twr_build_alt_broadcast_poll_frame(ss_twr_init_tx_poll_msg,
                                                  ss_twr_init_frame_seq_nb,
                                                  ss_twr_init_local_addr,
                                                  anchor_mask,
                                                  ss_twr_init_local_tag_id,
                                                  0U);
        ss_twr_init_alt_print_poll_diag(poll_count, anchor_mask);
        if (dwt_writetxdata(UWB_MSG_ALT_POLL_FRAME_LEN,
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            for (uint8_t i = 0U; i < poll_count; ++i) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        dwt_writetxfctrl(UWB_MSG_ALT_POLL_FRAME_LEN, 0, 1);
        ss_twr_init_alt_last_tx_write_done_cycles = k_cycle_get_32();
    }
    ss_twr_init_alt_last_tx_cmd_cycles = k_cycle_get_32();
    ss_twr_init_alt_bcast_tx_prearmed = false;
    if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS) {
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    if (!ss_twr_init_alt_wait_tx_done(SS_TWR_INIT_ALT_BCAST_TX_DONE_TIMEOUT_US)) {
        dwt_forcetrxoff();
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    poll_tx_done_cycles = k_cycle_get_32();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_tx_done_cycles = poll_tx_done_cycles;
#endif
#else
    uint32_t first_tx_time_hi =
        dwt_readsystimestamphi32() +
        (uint32_t)((SS_TWR_INIT_ALT_BCAST_POLL_SCHED_UUS *
                    SS_TWR_INIT_UUS_TO_DWT_TIME) >> 8);
    uint64_t scheduled_poll_sys_ts =
        ((uint64_t)(first_tx_time_hi & 0xFFFFFFFEUL)) << 8;
    uint64_t scheduled_poll_tx_ts =
        scheduled_poll_sys_ts + SS_TWR_INIT_TX_ANT_DLY;
    ss_twr_init_alt_last_tx_sched_cycles = k_cycle_get_32();

    uwb_ss_twr_build_alt_broadcast_poll_frame(ss_twr_init_tx_poll_msg,
                                              ss_twr_init_frame_seq_nb,
                                              ss_twr_init_local_addr,
                                              anchor_mask,
                                              ss_twr_init_local_tag_id,
                                              scheduled_poll_tx_ts);
    ss_twr_init_alt_print_poll_diag(poll_count, anchor_mask);
	    dwt_setdelayedtrxtime(first_tx_time_hi);
	    if (dwt_writetxdata(UWB_MSG_ALT_POLL_FRAME_LEN,
	                        ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
	    }
	    dwt_writetxfctrl(UWB_MSG_ALT_POLL_FRAME_LEN, 0, 1);
    ss_twr_init_alt_last_tx_write_done_cycles = k_cycle_get_32();
    ss_twr_init_alt_last_tx_cmd_cycles = k_cycle_get_32();
	    if (dwt_starttx(DWT_START_TX_DELAYED) != DWT_SUCCESS) {
	        for (uint8_t i = 0U; i < poll_count; ++i) {
	            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
	                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    if (!ss_twr_init_alt_wait_tx_done(SS_TWR_INIT_ALT_BCAST_TX_DONE_TIMEOUT_US)) {
        dwt_forcetrxoff();
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    poll_tx_done_cycles = k_cycle_get_32();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_tx_done_cycles = poll_tx_done_cycles;
#endif
    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        poll_tx_ts[anchor_id] = (uint32_t)scheduled_poll_tx_ts;
    }
#endif
#endif

	#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
	    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
	    rx_enable_start_cycles = k_cycle_get_32();
	    rxenable_rc = dwt_rxenable(DWT_START_RX_IMMEDIATE);
	    rx_enable_done_cycles = k_cycle_get_32();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_rx_start_cycles = rx_enable_done_cycles;
#endif
    response_window_start_cycles = rx_enable_done_cycles;

    {
        uint32_t actual_poll_tx_ts = dwt_readtxtimestamplo32();

        for (uint8_t i = 0U; i < poll_count; ++i) {
            uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
            poll_tx_ts[anchor_id] = actual_poll_tx_ts;
        }
    }

    while ((uint32_t)(k_cycle_get_32() - response_window_start_cycles) <
           response_window_cycles) {
        uint32_t status_reg = dwt_read32bitreg(SYS_STATUS_ID);
        last_status_reg = status_reg;

        if ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                           SYS_STATUS_ALL_RX_ERR)) == 0U) {
            continue;
        }
        last_rx_finfo = dwt_read32bitreg(RX_FINFO_ID);

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32 frame_len;
            uint16_t resp_src_addr;
            uint8_t anchor_id;
            uint32 resp_rx_ts;
            uint32 poll_rx_ts;
            uint32 resp_tx_ts;
            int32_t carrier_integrator;

            frame_len = last_rx_finfo & RX_FINFO_RXFLEN_MASK;
            last_frame_len = frame_len;
            if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
                dwt_write32bitreg(SYS_STATUS_ID,
                                  SYS_STATUS_ALL_RX_GOOD |
                                      SYS_STATUS_ALL_RX_ERR |
                                      SYS_STATUS_ALL_RX_TO);
                dwt_rxreset();
                (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
                continue;
            }

            dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
            resp_rx_ts = dwt_readrxtimestamplo32();
            carrier_integrator = dwt_readcarrierintegrator();
            dwt_write32bitreg(SYS_STATUS_ID,
                              SYS_STATUS_ALL_RX_GOOD | SYS_STATUS_ALL_RX_ERR |
                                  SYS_STATUS_ALL_RX_TO);

            resp_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);
            last_src_addr = resp_src_addr;
            last_dst_addr = uwb_frame_get_dst_addr(ss_twr_init_rx_buffer);
            last_code = frame_len > UWB_MSG_CODE_IDX ?
                        ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX] : 0U;
            if (!uwb_short_addr_is_anchor(resp_src_addr) ||
                !uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                         ss_twr_init_local_addr,
                                         resp_src_addr)) {
                unexpected_count++;
                (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
                continue;
            }

            anchor_id = uwb_anchor_id_from_addr(resp_src_addr);
            if (anchor_id >= UWB_MAX_ANCHORS || received[anchor_id] ||
                poll_tx_ts[anchor_id] == 0U) {
                unexpected_count++;
                (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
                continue;
            }

            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            resp_rx_ts_by_anchor[anchor_id] = resp_rx_ts;
            poll_rx_ts_by_anchor[anchor_id] = poll_rx_ts;
            resp_tx_ts_by_anchor[anchor_id] = resp_tx_ts;
            carrier_integrator_by_anchor[anchor_id] = carrier_integrator;
            received[anchor_id] = true;
            responses++;
            if (responses >= poll_count) {
                break;
            }
            (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
            continue;
        }

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_TO |
                                             SYS_STATUS_ALL_RX_ERR);
        dwt_rxreset();
            (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
    }
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_rx_done_cycles = k_cycle_get_32();
#endif
    dwt_forcetrxoff();
    ss_twr_init_frame_seq_nb++;
    ss_twr_init_alt_mark_scheduled_poll_timing(poll_count);
    ss_twr_init_alt_publish_rx_gap_diag(poll_tx_done_cycles,
                                        rx_enable_start_cycles,
                                        rx_enable_done_cycles,
                                        response_window_us, poll_count,
                                        anchor_mask, rxenable_rc);
#else
    response_window_start_cycles = k_cycle_get_32();
    ss_twr_init_alt_rx_restart(response_window_us);

    while (responses < poll_count) {
        uint32_t status_reg;
        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
            if ((uint32_t)(k_cycle_get_32() - response_window_start_cycles) >=
                response_window_cycles) {
                break;
            }
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                                SYS_STATUS_ALL_RX_ERR)) == 0U);
        last_status_reg = status_reg;
        last_rx_finfo = dwt_read32bitreg(RX_FINFO_ID);

        if ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                           SYS_STATUS_ALL_RX_ERR)) == 0U) {
            dwt_forcetrxoff();
            break;
        }

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32 frame_len;
            uint16_t resp_src_addr;
            uint8_t anchor_id;
            uint32 resp_rx_ts;
            uint32 poll_rx_ts;
            uint32 resp_tx_ts;
            int32_t carrier_integrator;

            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
            frame_len = last_rx_finfo & RX_FINFO_RXFLEN_MASK;
            last_frame_len = frame_len;
            if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
                dwt_forcetrxoff();
                dwt_rxreset();
                break;
            }

            dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
            resp_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);
            last_src_addr = resp_src_addr;
            last_dst_addr = uwb_frame_get_dst_addr(ss_twr_init_rx_buffer);
            last_code = frame_len > UWB_MSG_CODE_IDX ?
                        ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX] : 0U;
            if (!uwb_short_addr_is_anchor(resp_src_addr) ||
                !uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                         ss_twr_init_local_addr,
                                         resp_src_addr)) {
                unexpected_count++;
#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST
                ss_twr_init_alt_rx_restart(response_window_us);
#endif
                continue;
            }
            anchor_id = uwb_anchor_id_from_addr(resp_src_addr);
            if (anchor_id >= UWB_MAX_ANCHORS || received[anchor_id] ||
                poll_tx_ts[anchor_id] == 0U) {
                unexpected_count++;
#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST
                ss_twr_init_alt_rx_restart(response_window_us);
#endif
                continue;
            }

            resp_rx_ts = dwt_readrxtimestamplo32();
            carrier_integrator = dwt_readcarrierintegrator();
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            resp_rx_ts_by_anchor[anchor_id] = resp_rx_ts;
            poll_rx_ts_by_anchor[anchor_id] = poll_rx_ts;
            resp_tx_ts_by_anchor[anchor_id] = resp_tx_ts;
            carrier_integrator_by_anchor[anchor_id] = carrier_integrator;
            received[anchor_id] = true;
            responses++;
#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST
            if (responses < poll_count) {
                ss_twr_init_alt_rx_restart(response_window_us);
            }
#endif
            continue;
        }

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_TO |
                                             SYS_STATUS_ALL_RX_ERR);
        dwt_rxreset();
        break;
    }
#endif

    ss_twr_init_alt_publish_rx_diag(last_status_reg, last_rx_finfo,
                                    response_window_us, poll_count, anchor_mask,
                                    responses, unexpected_count, last_frame_len,
                                    last_src_addr, last_dst_addr, last_code);

#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    if (ss_twr_init_diag_rx_done_cycles == 0U) {
        ss_twr_init_diag_rx_done_cycles = k_cycle_get_32();
    }
#endif
    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        if (received[anchor_id]) {
            int32 rtd_init =
                (int32)(resp_rx_ts_by_anchor[anchor_id] - poll_tx_ts[anchor_id]);
            int32 rtd_resp =
                (int32)(resp_tx_ts_by_anchor[anchor_id] -
                        poll_rx_ts_by_anchor[anchor_id]);
            double clock_offset_ratio =
                (double)carrier_integrator_by_anchor[anchor_id] *
                (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 /
                 1.0e6);
            double tof =
                ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
                DWT_TIME_UNITS;
            double distance_m = tof * SS_TWR_INIT_SPEED_OF_LIGHT;
            raw_distance_mm[anchor_id] = (long)(distance_m * 1000.0);
            ss_twr_init_alt_record_range(anchor_id, raw_distance_mm[anchor_id]);
        } else {
            ss_twr_init_alt_record_timeout(anchor_id,
                                           SS_TWR_INIT_CAL_REASON_RX_TIMEOUT);
        }
    }
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_range_done_cycles = k_cycle_get_32();
#endif

    dwt_forcetrxoff();
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
    ss_twr_init_alt_set_rx_auto_reenable(false);
#endif
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_current_anchor_retry_count = 0U;
    ss_twr_init_alt_finish_sweep();
    return true;
}
#endif /* APP_ALT_SS_TWR_ENABLE */

int ss_twr_init_start_with_config(const struct uwb_tag_runtime_config *config)
{
    if (ss_twr_init_load_runtime_config(config) != 0) {
        printk("Invalid SS-TWR initiator runtime config\n");
        return -1;
    }

    printk("SS-TWR initiator ready tag=%u addr=0x%04x anchor_count=%u\n",
           (unsigned int)ss_twr_init_local_tag_id,
           (unsigned int)ss_twr_init_local_addr,
           (unsigned int)ss_twr_init_anchor_count);
    printk("Tag motion mode rng_delay_ms=%u cal_settle_us=%u tx_to_rx_uus=%u resp_timeout_uus=%u fast_tracking=%u full_interval=%u track_count=%u fixed=%u fixed_count=%u tdma=%u slot=%u/%u period=%u active=%u source=%s epoch_valid=%u gen=%u\n",
	           (unsigned int)SS_TWR_INIT_RNG_DELAY_MS,
	           (unsigned int)SS_TWR_INIT_CAL_RNG_SETTLE_US,
	           (unsigned int)SS_TWR_INIT_TX_TO_RX_DLY_UUS,
	           (unsigned int)SS_TWR_INIT_RESP_RX_TIMEOUT_UUS,
           (unsigned int)APP_TAG_FAST_TRACKING,
           (unsigned int)APP_TAG_FULL_SWEEP_INTERVAL,
           (unsigned int)APP_TAG_TRACK_ANCHOR_COUNT,
           (unsigned int)ss_twr_init_fixed_anchor_mode,
           (unsigned int)ss_twr_init_fixed_anchor_count,
           (unsigned int)ss_twr_init_tdma_schedule.enabled,
           (unsigned int)ss_twr_init_tdma_schedule.slot_index,
           (unsigned int)ss_twr_init_tdma_schedule.slot_count,
           (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
           (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
           ss_twr_init_slot_source_label(ss_twr_init_runtime_params.slot_source),
           (unsigned int)ss_twr_init_tdma_schedule.epoch_valid,
           (unsigned int)ss_twr_init_tdma_schedule.generation);
    printk("Tag multitag plan enabled=%u active=%u standby=%u reserve=%u refresh_budget=%u refresh_interval=%u maintenance_full=%u\n",
           (unsigned int)ss_twr_init_multitag_anchor_plan_mode,
           (unsigned int)ss_twr_init_active_plan_count,
           (unsigned int)ss_twr_init_standby_plan_count,
           (unsigned int)ss_twr_init_reserve_plan_count,
           (unsigned int)ss_twr_init_refresh_anchor_budget,
           (unsigned int)ss_twr_init_refresh_interval_sweeps,
           (unsigned int)ss_twr_init_full_sweep_interval_sweeps);
    printk("Tag perf config summary_period=%u imu_sample_period=%u\n",
           (unsigned int)APP_TAG_SUMMARY_PERIOD,
           (unsigned int)APP_TAG_IMU_SAMPLE_PERIOD);
    printk("Tag status config output_period_ms=%u\n",
           (unsigned int)APP_TAG_STATUS_PERIOD_MS);
    printk("Tag ekf config enable=%u meas_std=%u residual_gain=%u proc_accel=%u init_pos=%u init_vel=%u gate=%u motion_meas=%u motion_proc=%u motion_gate=%u motion_full=%u speed_thr=%u imu_delta=%u imu_gerr=%u range_soft=%u range_hard=%u motion_soft=%u motion_hard=%u\n",
           (unsigned int)APP_TAG_EKF_ENABLE,
           (unsigned int)APP_TAG_EKF_MEAS_STD_MM,
           (unsigned int)APP_TAG_EKF_RESIDUAL_GAIN_PCT,
           (unsigned int)APP_TAG_EKF_PROC_ACCEL_MM_S2,
           (unsigned int)APP_TAG_EKF_INIT_POS_STD_MM,
           (unsigned int)APP_TAG_EKF_INIT_VEL_STD_MM_S,
           (unsigned int)APP_TAG_EKF_OUTLIER_GATE_MM,
           (unsigned int)APP_TAG_MOTION_EKF_MEAS_STD_MM,
           (unsigned int)APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2,
           (unsigned int)APP_TAG_MOTION_EKF_OUTLIER_GATE_MM,
           (unsigned int)APP_TAG_MOTION_FULL_SWEEP_INTERVAL,
           (unsigned int)APP_TAG_MOTION_SPEED_THRESHOLD_MM_S,
           (unsigned int)APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG,
           (unsigned int)APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG,
           (unsigned int)APP_TAG_RANGE_SOFT_RESIDUAL_MM,
           (unsigned int)APP_TAG_RANGE_HARD_RESIDUAL_MM,
           (unsigned int)APP_TAG_MOTION_RANGE_SOFT_BONUS_MM,
           (unsigned int)APP_TAG_MOTION_RANGE_HARD_BONUS_MM);
    printk("SS-TWR init trace: waiting for TDMA slot\n");
#if APP_TAG_USB_DIAG_TRACE
    ss_twr_diag_write("SS-TWR: wait_tdma enter\n");
#endif
#if APP_TAG_STATUS_PERIOD_MS > 0U
        k_work_init_delayable(&ss_twr_init_status_work,
                              ss_twr_init_status_work_handler);
        (void)k_work_reschedule(&ss_twr_init_status_work,
                                K_MSEC(APP_TAG_STATUS_PERIOD_MS));
#endif
    ss_twr_init_imu_ready = (uwb_imu_init() == 0);
    ss_twr_init_configure_radio();
	{
	    uint32_t tdma_wait_ms = ss_twr_init_wait_until_slot_if_needed();
	    ss_twr_init_last_tdma_wait_ms = tdma_wait_ms;
#if APP_TAG_USB_DIAG_TRACE
        ss_twr_diag_write("SS-TWR: wait_tdma done\n");
#endif
	    printk("SS-TWR init trace: TDMA wait complete wait_ms=%lu slot=%u/%u period=%u active=%u cal_mode=%u\n",
	           (unsigned long)tdma_wait_ms,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_index,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_count,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
	           (unsigned int)ss_twr_init_runtime_any_calibration_mode());
	}
    ss_twr_init_prepare_sweep_plan();
    printk("SS-TWR init trace: sweep plan prepared active=%u sweep=%lu full=%u refresh=%u plan=%s\n",
           (unsigned int)ss_twr_init_active_anchor_count,
           (unsigned long)ss_twr_init_sweep_count,
           (unsigned int)ss_twr_init_current_sweep_full,
           (unsigned int)ss_twr_init_current_sweep_refresh,
           ss_twr_init_plan_label());
#if APP_TAG_USB_DIAG_TRACE
    ss_twr_diag_write("SS-TWR: sweep plan ready\n");
#endif
    printk("SS-TWR init trace: main loop enter\n");
#if APP_TAG_USB_DIAG_TRACE
    ss_twr_diag_write("SS-TWR: main loop enter\n");
#endif

    while (1) {
#if APP_TAG_BLE_ENABLE
        if (uwb_tag_ble_ota_active()) {
            ss_twr_init_publish_tdma_diag("ota_active", 0U, 0U);
            ss_twr_init_set_ble_tx_paused(false);
            dwt_forcetrxoff();
            k_msleep(20);
            continue;
        }
#endif
        ss_twr_init_apply_pending_runtime_config_if_any();

#if APP_TAG_WAND_MODE_ENABLE
        if (ss_twr_init_wand_enabled) {
            ss_twr_init_set_ble_tx_paused(false);
            if (ss_twr_init_wand_role == SS_TWR_INIT_WAND_ROLE_RESP) {
                ss_twr_init_wand_responder_once();
                continue;
            }
            if (ss_twr_init_wand_pending_sweeps > 0U &&
                ss_twr_init_wand_role == SS_TWR_INIT_WAND_ROLE_INIT) {
                ss_twr_init_wand_pending_sweeps--;
                ss_twr_init_wand_sweep_once();
                continue;
            }
        }
#endif

        if (ss_twr_init_runtime_anchor_ota_mode()) {
            ss_twr_init_publish_tdma_diag("anchor_ota", 0U, 0U);
            ss_twr_init_set_ble_tx_paused(false);
            dwt_forcetrxoff();
            k_msleep(20);
            continue;
        }

#if APP_ALT_SS_TWR_ENABLE && \
    APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
    APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE != 0U
        if (ss_twr_init_active_anchor_index == 0U &&
            ss_twr_init_active_anchor_count > 1U) {
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
            ss_twr_init_diag_t0_cycles = k_cycle_get_32();
            ss_twr_init_diag_wait_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_tx_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_rx_start_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_rx_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_range_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_solve_start_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_solve_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_out_start_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_out_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_clean_done_cycles = ss_twr_init_diag_t0_cycles;
#endif
            (void)ss_twr_init_alt_bcast_prewrite_tx();
            ss_twr_init_alt_ltdma_slot_start_cycles =
                broadcast_tdma_wait_next_slot_start(
                    &ss_twr_init_tdma_schedule);
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
            ss_twr_init_diag_wait_done_cycles =
                ss_twr_init_alt_ltdma_slot_start_cycles;
#endif

            if (ss_twr_init_alt_burst_sweep_once()) {
                continue;
            }
        }
#endif

        if (!ss_twr_init_tdma_exchange_can_start_if_needed()) {
            uint32_t remain_ms =
                uwb_tdma_schedule_time_remaining_ms(&ss_twr_init_tdma_schedule);

            ss_twr_init_last_sweep_cut_short = true;
            ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT;
            ss_twr_init_publish_tdma_diag("slot_guard", remain_ms,
                                          SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS +
                                              SS_TWR_INIT_SLOT_GUARD_MARGIN_MS);
            {
                uint32_t now_ms = (uint32_t)k_uptime_get();
                if ((now_ms - ss_twr_init_last_slot_guard_log_ms) >= 1000U) {
                    ss_twr_init_last_slot_guard_log_ms = now_ms;
                    printk("Tag slot guard: cut short plan=%s next_anchor=%u active=%u remain=%lu ms slot=%u/%u gen=%u\n",
                           ss_twr_init_plan_label(),
                           (unsigned int)ss_twr_init_active_anchor_index,
                           (unsigned int)ss_twr_init_active_anchor_count,
                           (unsigned long)remain_ms,
                           (unsigned int)ss_twr_init_tdma_schedule.slot_index,
                           (unsigned int)ss_twr_init_tdma_schedule.slot_count,
                           (unsigned int)ss_twr_init_tdma_schedule.generation);
                }
            }
            /*
             * CAL_ROTO must preserve the selected 4-anchor intent even when a
             * TDMA slot boundary lands mid-sweep. Do not finalize/replan a
             * partial sweep; wait for the next slot and continue the same
             * anchor group so output does not silently collapse to 2/3 anchors.
             */
            if (ss_twr_init_runtime_roto_calibration_mode() &&
                !ss_twr_init_roto_prewarm_active() &&
                ss_twr_init_active_anchor_index != 0U &&
                ss_twr_init_active_anchor_count >= 4U) {
                dwt_forcetrxoff();
                ss_twr_init_release_ble_tx_after_active_slot();
                ss_twr_init_last_tdma_wait_ms =
                    ss_twr_init_wait_until_next_slot_if_needed();
                continue;
            }

            ss_twr_init_sweep_count++;
            ss_twr_init_release_ble_tx_after_active_slot();
            ss_twr_init_note_sweep_done();
            ss_twr_init_print_location_if_ready();
            ss_twr_init_apply_pending_runtime_config_if_any();
	        ss_twr_init_last_tdma_wait_ms = ss_twr_init_wait_until_next_slot_if_needed();
            ss_twr_init_prepare_sweep_plan();
            continue;
        }

	        if (ss_twr_init_active_anchor_index == 0U &&
	            ss_twr_init_active_anchor_count > 1U &&
	            ss_twr_init_tdma_schedule.enabled &&
	            ss_twr_init_tdma_active_guard_enabled()) {
            uint32_t remain_ms =
                uwb_tdma_schedule_time_remaining_ms(&ss_twr_init_tdma_schedule);
            uint32_t sweep_budget_ms =
                ((uint32_t)ss_twr_init_active_anchor_count *
                 SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS) +
                SS_TWR_INIT_SLOT_GUARD_MARGIN_MS;

#if APP_ALT_SS_TWR_ENABLE && APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
            sweep_budget_ms =
                (ss_twr_init_alt_bcast_response_window_estimated_us(
                     ss_twr_init_active_anchor_count) + 999U) /
                1000U;
            remain_ms = ss_twr_init_tdma_period_remaining_ms();
#endif

            if (remain_ms < sweep_budget_ms) {
                ss_twr_init_publish_tdma_diag("sweep_budget", remain_ms,
                                              sweep_budget_ms);
                dwt_forcetrxoff();
                ss_twr_init_release_ble_tx_after_active_slot();
                ss_twr_init_last_tdma_wait_ms =
                    ss_twr_init_wait_until_next_slot_if_needed();
                continue;
            }
        }

        if (ss_twr_init_runtime_static_calibration_mode() &&
            APP_TAG_CAL_STATIC_SLOT_DIVIDER > 1U &&
            ss_twr_init_active_anchor_index == 0U) {
            uint32_t slot_tick = ss_twr_init_static_cal_slot_tick++;

                    if ((slot_tick % APP_TAG_CAL_STATIC_SLOT_DIVIDER) != 0U) {
                        dwt_forcetrxoff();
                        ss_twr_init_release_ble_tx_after_active_slot();
                        ss_twr_init_last_tdma_wait_ms =
                    ss_twr_init_wait_until_next_slot_if_needed();
                        continue;
                    }
        }

#if APP_ALT_SS_TWR_ENABLE
        if (ss_twr_init_active_anchor_index == 0U &&
            ss_twr_init_active_anchor_count > 1U) {
            ss_twr_init_publish_tdma_diag("alt_burst",
                                          uwb_tdma_schedule_time_remaining_ms(
                                              &ss_twr_init_tdma_schedule),
                                          0U);
            if (ss_twr_init_alt_burst_sweep_once()) {
                continue;
            }
        }
#endif

        ss_twr_init_set_ble_tx_paused(true);

        uint8_t current_anchor_id =
            ss_twr_init_active_anchor_ids[ss_twr_init_active_anchor_index];
        uint16_t current_anchor_addr = uwb_anchor_short_addr(current_anchor_id);
        struct uwb_range_tracker *tracker =
            &ss_twr_init_trackers[current_anchor_id];
        uint32 status_reg;

        uwb_ss_twr_build_poll_frame(ss_twr_init_tx_poll_msg,
                                    ss_twr_init_frame_seq_nb, current_anchor_addr,
                                    ss_twr_init_local_addr);

	        ss_twr_init_prepare_radio_for_poll();

        if (ss_twr_init_sweep_count == 0U &&
            ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
            ss_twr_diag_write("SS-TWR: first poll tx prepare\n");
#endif
        }
        if (dwt_writetxdata(SS_TWR_INIT_LEGACY_POLL_FRAME_LEN,
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            printk("Initiator TX buffer write failed\n");
            ss_twr_init_sleep_between_ranges();
            continue;
        }

        dwt_writetxfctrl(SS_TWR_INIT_LEGACY_POLL_FRAME_LEN, 0, 1);

        if (dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED) !=
            DWT_SUCCESS) {
            printk("Initiator TX start failed\n");
            dwt_forcetrxoff();
            ss_twr_init_sleep_between_ranges();
            continue;
        }
        ss_twr_init_note_poll_started();

        if (ss_twr_init_sweep_count == 0U &&
            ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
            ss_twr_diag_write("SS-TWR: first poll tx started\n");
#endif
        }

        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                                SYS_STATUS_ALL_RX_ERR)) == 0U);

        if (ss_twr_init_sweep_count == 0U &&
            ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
            ss_twr_diag_write("SS-TWR: first poll rx status\n");
#endif
            printk("SS-TWR init trace: first poll status=0x%08lx\n",
                   (unsigned long)status_reg);
        }

        ss_twr_init_frame_seq_nb++;

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32 frame_len;
            uint16_t resp_src_addr;
            uint32 poll_tx_ts;
            uint32 resp_rx_ts;
            uint32 poll_rx_ts;
            uint32 resp_tx_ts;
            int32 rtd_init;
            int32 rtd_resp;
            double tof;
            double distance_m;
            double clock_offset_ratio;
            long raw_distance_mm;
            uint32 filtered_mm;

            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);

            frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFLEN_MASK;
            if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
                printk("Initiator RX frame too long: %lu status=0x%08lx\n",
                       (unsigned long)frame_len, (unsigned long)status_reg);
                printk("RX_FINFO raw=0x%08lx\n",
                       (unsigned long)dwt_read32bitreg(RX_FINFO_ID));
                dwt_forcetrxoff();
                dwt_rxreset();
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            memset(ss_twr_init_rx_buffer, 0, sizeof(ss_twr_init_rx_buffer));
            dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
            ss_twr_init_rx_buffer[SS_TWR_INIT_MSG_SN_IDX] = 0;

            if (!uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                         ss_twr_init_local_addr, current_anchor_addr)) {
#if APP_TAG_VERBOSE_MEASUREMENTS
                printk("Initiator got unexpected frame src=0x%04x dst=0x%04x code=0x%02x\n",
                       (unsigned int)uwb_frame_get_src_addr(ss_twr_init_rx_buffer),
                       (unsigned int)uwb_frame_get_dst_addr(ss_twr_init_rx_buffer),
                       (unsigned int)ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX]);
#endif
                if (ss_twr_init_sweep_count == 0U &&
                    ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
                    ss_twr_diag_write("SS-TWR: first poll unexpected frame\n");
#endif
                    printk("SS-TWR init trace: first poll unexpected frame src=0x%04x dst=0x%04x code=0x%02x\n",
                           (unsigned int)uwb_frame_get_src_addr(ss_twr_init_rx_buffer),
                           (unsigned int)uwb_frame_get_dst_addr(ss_twr_init_rx_buffer),
                           (unsigned int)ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX]);
#if APP_TAG_USB_DIAG_TRACE
                    {
                        char buf[96];
                        snprintk(buf, sizeof(buf),
                                 "SS-TWR: first poll frame src=0x%04x dst=0x%04x code=0x%02x\n",
                                 (unsigned int)uwb_frame_get_src_addr(
                                     ss_twr_init_rx_buffer),
                                 (unsigned int)uwb_frame_get_dst_addr(
                                     ss_twr_init_rx_buffer),
                                 (unsigned int)ss_twr_init_rx_buffer
                                     [UWB_MSG_CODE_IDX]);
                        ss_twr_diag_write(buf);
                    }
#endif
                }
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            poll_tx_ts = dwt_readtxtimestamplo32();
            resp_rx_ts = dwt_readrxtimestamplo32();
            clock_offset_ratio =
                (double)dwt_readcarrierintegrator() *
                (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 /
                 1.0e6);

            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            rtd_init = (int32)(resp_rx_ts - poll_tx_ts);
            rtd_resp = (int32)(resp_tx_ts - poll_rx_ts);
            tof = ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
                  DWT_TIME_UNITS;
            distance_m = tof * SS_TWR_INIT_SPEED_OF_LIGHT;
            raw_distance_mm = (long)(distance_m * 1000.0);
            if (raw_distance_mm < 0L) {
                raw_distance_mm = 0L;
            }

            if (tracker == NULL) {
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            if (!ss_twr_init_raw_range_plausible(
                    tracker, (uint32_t)raw_distance_mm)) {
                uwb_range_tracker_record_failure(tracker);
                ss_twr_init_record_sweep_anchor_state(
                    current_anchor_id, UWB_TAG_BLE_CAL_STATUS_REJECT, tracker);
                ss_twr_init_record_sweep_anchor_diag(
                    current_anchor_id, SS_TWR_INIT_CAL_REASON_RAW_OUTLIER,
                    raw_distance_mm, tracker->filtered_mm, 0U, 0U,
                    uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
                ss_twr_init_publish_cal_range(current_anchor_id,
                                              UWB_TAG_BLE_CAL_STATUS_REJECT,
                                              raw_distance_mm,
                                              tracker->filtered_mm,
                                              tracker);
#endif
                if (APP_TAG_VERBOSE_RANGING != 0U) {
                    printk("Initiator range reject anchor=%u addr=0x%04x raw=%ld mm last_filt=%lu mm ok=%lu fail=%lu q=%u%%\n",
                           (unsigned int)current_anchor_id,
                           (unsigned int)current_anchor_addr,
                           raw_distance_mm,
                           (unsigned long)tracker->filtered_mm,
                           (unsigned long)tracker->success_count,
                           (unsigned long)tracker->failure_count,
                           (unsigned int)uwb_range_tracker_quality_percent(
                               tracker));
                }
	                if (ss_twr_init_runtime_any_calibration_mode()) {
	                    /*
	                     * In calibration mode, do not stall on one problematic anchor.
	                     * Advance to the next anchor so coverage/throughput stays balanced.
	                     */
                    ss_twr_init_active_anchor_index =
                        (ss_twr_init_active_anchor_index + 1U) %
                        ss_twr_init_active_anchor_count;
                    ss_twr_init_current_anchor_retry_count = 0U;
		                    if (ss_twr_init_active_anchor_index == 0U) {
		                        ss_twr_init_sweep_count++;
		                        ss_twr_init_release_ble_tx_after_active_slot();
		                        ss_twr_init_note_sweep_done();
		                        ss_twr_init_print_location_if_ready();
		                        ss_twr_init_apply_pending_runtime_config_if_any();
	                        ss_twr_init_last_tdma_wait_ms =
	                            ss_twr_init_wait_until_next_slot_if_needed();
	                        ss_twr_init_prepare_sweep_plan();
	                    }
		                }
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            filtered_mm = uwb_range_tracker_record_success(
                tracker, (uint32_t)raw_distance_mm);
            ss_twr_init_record_sweep_anchor_state(
                current_anchor_id, UWB_TAG_BLE_CAL_STATUS_OK, tracker);
            ss_twr_init_record_sweep_anchor_diag(
                current_anchor_id, SS_TWR_INIT_CAL_REASON_OK, raw_distance_mm,
                filtered_mm, 0U, 0U, uwb_range_tracker_quality_percent(tracker));
            resp_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);

            if (APP_TAG_VERBOSE_RANGING != 0U) {
                printk("Range anchor=%u addr=0x%04x raw=%ld mm filt=%lu mm ok=%lu fail=%lu q=%u%%\n",
                       (unsigned int)uwb_anchor_id_from_addr(resp_src_addr),
                       (unsigned int)resp_src_addr, raw_distance_mm,
                       (unsigned long)filtered_mm,
                       (unsigned long)tracker->success_count,
                       (unsigned long)tracker->failure_count,
                       (unsigned int)uwb_range_tracker_quality_percent(
                           tracker));
            }
#if APP_TAG_BLE_ENABLE
            ss_twr_init_publish_cal_range(current_anchor_id,
                                          UWB_TAG_BLE_CAL_STATUS_OK,
                                          raw_distance_mm,
                                          filtered_mm,
                                          tracker);
#endif
        } else {
            if (tracker != NULL) {
                uint8_t timeout_reason =
                    ((status_reg & SYS_STATUS_ALL_RX_TO) != 0U)
                        ? SS_TWR_INIT_CAL_REASON_RX_TIMEOUT
                        : SS_TWR_INIT_CAL_REASON_RX_ERROR;

                if (ss_twr_init_should_retry_current_cal_anchor()) {
                    ss_twr_init_current_anchor_retry_count++;
                    dwt_write32bitreg(SYS_STATUS_ID,
                                      SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
	                    dwt_rxreset();
	                    if (!ss_twr_init_tdma_exchange_can_start_if_needed()) {
	                        dwt_forcetrxoff();
	                        ss_twr_init_release_ble_tx_after_active_slot();
	                        ss_twr_init_last_tdma_wait_ms =
	                            ss_twr_init_wait_until_next_slot_if_needed();
	                    }
                    continue;
                }

                uwb_range_tracker_record_failure(tracker);
                ss_twr_init_record_sweep_anchor_state(
                    current_anchor_id, UWB_TAG_BLE_CAL_STATUS_TIMEOUT, tracker);
                ss_twr_init_record_sweep_anchor_diag(
                    current_anchor_id, timeout_reason, 0, tracker->filtered_mm,
                    0U, 0U, uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
                ss_twr_init_publish_cal_range(current_anchor_id,
                                              UWB_TAG_BLE_CAL_STATUS_TIMEOUT,
                                              0,
                                              tracker->filtered_mm,
                                              tracker);
#endif
                if (APP_TAG_VERBOSE_RANGING != 0U) {
                    printk("Initiator RX timeout/error anchor=%u addr=0x%04x status=0x%08lx ok=%lu fail=%lu q=%u%%\n",
                           (unsigned int)current_anchor_id,
                           (unsigned int)current_anchor_addr,
                           (unsigned long)status_reg,
                           (unsigned long)tracker->success_count,
                           (unsigned long)tracker->failure_count,
                           (unsigned int)uwb_range_tracker_quality_percent(
                               tracker));
                }
                if (ss_twr_init_sweep_count == 0U &&
                    ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
                    ss_twr_diag_write("SS-TWR: first poll timeout/error\n");
#endif
                    printk("SS-TWR init trace: first poll timeout/error anchor=%u addr=0x%04x status=0x%08lx\n",
                           (unsigned int)current_anchor_id,
                           (unsigned int)current_anchor_addr,
                           (unsigned long)status_reg);
                }
            }
            dwt_write32bitreg(SYS_STATUS_ID,
                              SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
            dwt_rxreset();
        }

        ss_twr_init_active_anchor_index =
            (ss_twr_init_active_anchor_index + 1U) %
            ss_twr_init_active_anchor_count;
	        ss_twr_init_current_anchor_retry_count = 0U;
	        if (ss_twr_init_active_anchor_index == 0U) {
	            ss_twr_init_sweep_count++;
	            ss_twr_init_release_ble_tx_after_active_slot();
	            ss_twr_init_note_sweep_done();
	            ss_twr_init_print_location_if_ready();
	            ss_twr_init_apply_pending_runtime_config_if_any();
	            ss_twr_init_last_tdma_wait_ms =
	                ss_twr_init_wait_until_next_slot_if_needed();
	            ss_twr_init_prepare_sweep_plan();
	        }
        ss_twr_init_sleep_between_ranges();
    }
}

int ss_twr_init_start(unsigned int tag_id, const uint8_t *anchor_ids,
                      size_t anchor_count)
{
    const struct uwb_tag_runtime_config config = {
        .tag_id = (uint8_t)tag_id,
        .anchor_ids = anchor_ids,
        .anchor_count = anchor_count,
        .fixed_anchor_mode = false,
        .fixed_anchor_ids = NULL,
        .fixed_anchor_count = 0U,
        .multitag_anchor_plan_mode = false,
        .active_anchor_ids = NULL,
        .active_anchor_count = 0U,
        .standby_anchor_ids = NULL,
        .standby_anchor_count = 0U,
        .reserve_anchor_ids = NULL,
        .reserve_anchor_count = 0U,
        .refresh_anchor_budget = 0U,
        .refresh_interval_sweeps = 0U,
        .full_sweep_interval_sweeps = 0U,
        .tdma =
            {
                .enabled = false,
                .slot_index = 0U,
                .slot_count = 1U,
                .slot_period_ms = 0U,
                .slot_active_ms = 0U,
            },
    };

    return ss_twr_init_start_with_config(&config);
}

int ss_twr_init_tdma_set_slot(uint8_t slot_index)
{
    struct uwb_tag_runtime_params params = ss_twr_init_runtime_params;

    if (!ss_twr_init_tdma_schedule.enabled ||
        ss_twr_init_tdma_schedule.slot_count == 0U) {
        return -EINVAL;
    }

    if (slot_index >= ss_twr_init_tdma_schedule.slot_count) {
        return -ERANGE;
    }

    params.slot_source = UWB_TAG_SLOT_SOURCE_SETTINGS;
    params.tdma.slot_index = slot_index;
    return ss_twr_init_runtime_configure(&params);
}

int ss_twr_init_runtime_configure(const struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return -EINVAL;
	}

	if (params->logical_tag_id >= UWB_MAX_TAGS) {
		return -ERANGE;
	}

	if (params->positioning_mode != UWB_TAG_POSITIONING_MODE_DYNAMIC &&
	    params->positioning_mode != UWB_TAG_POSITIONING_MODE_FIXED &&
	    params->positioning_mode != UWB_TAG_POSITIONING_MODE_ANCHOR_OTA &&
	    params->positioning_mode != UWB_TAG_POSITIONING_MODE_CAL_STATIC &&
	    params->positioning_mode != UWB_TAG_POSITIONING_MODE_CAL_ROTO) {
		return -ERANGE;
	}

	if (params->tdma.enabled &&
	    !uwb_tdma_schedule_is_valid(&params->tdma)) {
		return -EINVAL;
	}

	if (params->anchor_selection_mode ==
	    UWB_TAG_ANCHOR_SELECTION_FIXED_SUBSET &&
	    params->fixed_anchor_count < 4U) {
		return -EINVAL;
	}

	ss_twr_init_pending_runtime_params = *params;
	if (ss_twr_init_pending_runtime_params.tdma.epoch_valid) {
		uwb_tdma_sync_schedule_epoch(&ss_twr_init_pending_runtime_params.tdma,
					     ss_twr_init_pending_runtime_params.tdma.epoch_ms,
					     ss_twr_init_pending_runtime_params.tdma.generation);
	}
	ss_twr_init_runtime_update_pending = true;
	return 0;
}

bool ss_twr_init_runtime_config_snapshot(struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return false;
	}

	*params = ss_twr_init_runtime_params;
	return true;
}
