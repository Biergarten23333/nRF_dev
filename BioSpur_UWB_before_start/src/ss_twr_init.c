#include "ss_twr_init.h"
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

#ifndef APP_TAG_TX_TO_RX_DLY_UUS
#define APP_TAG_TX_TO_RX_DLY_UUS 140U
#endif

#ifndef APP_TAG_RESP_RX_TIMEOUT_UUS
#define APP_TAG_RESP_RX_TIMEOUT_UUS 1500U
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

#ifndef APP_TAG_IMU_SAMPLE_PERIOD
#define APP_TAG_IMU_SAMPLE_PERIOD 4U
#endif

#ifndef APP_TAG_CONSOLE_SUMMARY_ENABLE
#define APP_TAG_CONSOLE_SUMMARY_ENABLE 1U
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
#define SS_TWR_INIT_TX_TO_RX_DLY_UUS APP_TAG_TX_TO_RX_DLY_UUS
#define SS_TWR_INIT_RESP_RX_TIMEOUT_UUS APP_TAG_RESP_RX_TIMEOUT_UUS

#define SS_TWR_INIT_RX_BUF_LEN 127U
#define SS_TWR_INIT_ALL_MSG_COMMON_LEN 10U
#define SS_TWR_INIT_MSG_SN_IDX 2U
#define SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX 10U
#define SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX 14U
#define SS_TWR_INIT_RESP_MSG_TS_LEN 4U

#define SS_TWR_INIT_SPEED_OF_LIGHT 299702547.0

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
static uint8_t ss_twr_init_tx_poll_msg[12];
static uint16_t ss_twr_init_local_addr;
static uint8_t ss_twr_init_local_tag_id;
static struct uwb_range_tracker ss_twr_init_trackers[UWB_MAX_ANCHORS];
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
static uint8_t ss_twr_init_refresh_anchor_cursor;
static bool ss_twr_init_current_sweep_full;
static bool ss_twr_init_current_sweep_refresh;
static uint32_t ss_twr_init_current_sweep_start_ms;
#if APP_TAG_STATUS_PERIOD_MS > 0U
static struct k_work_delayable ss_twr_init_status_work;
#endif
static struct uwb_imu_sample ss_twr_init_last_imu_sample;
static uint32_t ss_twr_init_perf_motion_dt_sum_ms;
static uint32_t ss_twr_init_perf_track_sweep_sum_ms;
static uint32_t ss_twr_init_perf_full_sweep_sum_ms;
static uint16_t ss_twr_init_perf_motion_dt_count;
static uint16_t ss_twr_init_perf_track_sweep_count;
static uint16_t ss_twr_init_perf_full_sweep_count;
static uint32_t ss_twr_init_last_motion_speed_mm_s;
static bool ss_twr_init_last_imu_indicates_motion;

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

static bool ss_twr_init_apply_range_continuity_gate(uint8_t anchor_id,
                                                    uint32_t range_mm,
                                                    uint8_t *quality_percent)
{
    uint32_t predicted_mm = 0U;
    uint32_t residual_mm;
    uint32_t soft_gate_mm;
    uint32_t hard_gate_mm;

    if (!APP_TAG_RANGE_CONTINUITY_ENABLE || quality_percent == NULL ||
        !ss_twr_init_have_last_location) {
        return true;
    }

    if (ss_twr_init_location_output_count < APP_TAG_RANGE_CONTINUITY_WARMUP_SWEEPS) {
        return true;
    }

    if (!ss_twr_init_predicted_range_mm(anchor_id, ss_twr_init_last_location_x_mm,
                                        ss_twr_init_last_location_y_mm,
                                        ss_twr_init_last_location_z_mm,
                                        &predicted_mm)) {
        return true;
    }

    residual_mm = (range_mm > predicted_mm) ? (range_mm - predicted_mm) :
                                              (predicted_mm - range_mm);

    soft_gate_mm = ss_twr_init_effective_range_soft_gate_mm();
    hard_gate_mm = ss_twr_init_effective_range_hard_gate_mm();

    if (residual_mm >= hard_gate_mm) {
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

    if (location == NULL) {
        return false;
    }

    if (APP_TAG_OUTPUT_MAX_RMS_MM != 0U &&
        location->residual_rms_mm > APP_TAG_OUTPUT_MAX_RMS_MM) {
        return false;
    }

    if (APP_TAG_OUTPUT_MAX_MAX_MM != 0U &&
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

static void ss_twr_init_sleep_between_ranges(void)
{
    if (SS_TWR_INIT_RNG_DELAY_MS > 0U) {
        k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
    }
}

static const char *ss_twr_init_plan_label(void)
{
    if (ss_twr_init_fixed_anchor_mode) {
        return "fixed";
    }

    if (ss_twr_init_current_sweep_refresh) {
        return "refresh";
    }

    return ss_twr_init_current_sweep_full ? "full" : "track";
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
        return;
    }

    if (ss_twr_init_multitag_anchor_plan_mode) {
        bool do_full =
            (ss_twr_init_full_sweep_interval_sweeps != 0U) &&
            (ss_twr_init_sweep_count != 0U) &&
            ((ss_twr_init_sweep_count %
              ss_twr_init_full_sweep_interval_sweeps) == 0U);
        bool do_refresh =
            (ss_twr_init_refresh_interval_sweeps != 0U) &&
            (ss_twr_init_sweep_count != 0U) &&
            ((ss_twr_init_sweep_count % ss_twr_init_refresh_interval_sweeps) ==
             0U);

        if (do_full) {
            for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
                ss_twr_init_active_anchor_ids[active_count++] =
                    ss_twr_init_anchor_ids[i];
            }
            full_sweep = true;
            refresh_sweep = false;
        } else {
            for (size_t i = 0; i < ss_twr_init_active_plan_count; ++i) {
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
        return;
    }

    {
        uint16_t effective_full_interval =
            ss_twr_init_effective_full_sweep_interval();

        if (APP_TAG_FAST_TRACKING != 0U && ss_twr_init_have_last_solution &&
            effective_full_interval > 1U &&
            (ss_twr_init_sweep_count % effective_full_interval) != 0U) {
            full_sweep = false;
        }
    }

    if (full_sweep) {
        for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
            ss_twr_init_active_anchor_ids[active_count++] =
                ss_twr_init_anchor_ids[i];
        }
    } else {
        size_t desired_count = APP_TAG_TRACK_ANCHOR_COUNT;

        if (desired_count < 4U) {
            desired_count = 4U;
        }
        if (desired_count > ss_twr_init_anchor_count) {
            desired_count = ss_twr_init_anchor_count;
        }

        for (size_t i = 0; i < ss_twr_init_last_solution_anchor_count &&
                           active_count < desired_count;
             ++i) {
            uint8_t anchor_id = ss_twr_init_last_solution_anchor_ids[i];

            if (!ss_twr_init_anchor_id_in_list(ss_twr_init_active_anchor_ids,
                                               active_count, anchor_id)) {
                ss_twr_init_active_anchor_ids[active_count++] = anchor_id;
            }
        }

        for (size_t offset = 0; offset < ss_twr_init_anchor_count &&
                                active_count < desired_count;
             ++offset) {
            size_t idx =
                (ss_twr_init_refresh_anchor_cursor + offset) %
                ss_twr_init_anchor_count;
            uint8_t anchor_id = ss_twr_init_anchor_ids[idx];

            if (!ss_twr_init_anchor_id_in_list(ss_twr_init_active_anchor_ids,
                                               active_count, anchor_id)) {
                ss_twr_init_active_anchor_ids[active_count++] = anchor_id;
            }
        }

        ss_twr_init_refresh_anchor_cursor =
            (uint8_t)((ss_twr_init_refresh_anchor_cursor + 1U) %
                      ss_twr_init_anchor_count);
    }

    ss_twr_init_current_sweep_full = full_sweep;
    ss_twr_init_current_sweep_refresh = false;
    ss_twr_init_active_anchor_count = active_count;
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_current_sweep_start_ms = (uint32_t)k_uptime_get();
}

static void ss_twr_init_read_ts(const uint8_t *ts_field, uint32 *ts)
{
    *ts = 0;

    for (int i = 0; i < SS_TWR_INIT_RESP_MSG_TS_LEN; ++i) {
        *ts |= ((uint32)ts_field[i]) << (i * 8);
    }
}

static void ss_twr_init_configure_radio(void)
{
    dwt_configure(&ss_twr_init_config);
    dwt_setrxantennadelay(SS_TWR_INIT_RX_ANT_DLY);
    dwt_settxantennadelay(SS_TWR_INIT_TX_ANT_DLY);
    dwt_setleds(DWT_LEDS_ENABLE);
    dwt_setrxaftertxdelay(SS_TWR_INIT_TX_TO_RX_DLY_UUS);
    dwt_setrxtimeout(SS_TWR_INIT_RESP_RX_TIMEOUT_UUS);
    dwt_setpreambledetecttimeout(0);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
}

static int ss_twr_init_load_runtime_config(
    const struct uwb_tag_runtime_config *config)
{
    if (config == NULL || config->tag_id >= UWB_MAX_TAGS ||
        config->anchor_ids == NULL || config->anchor_count == 0U ||
        config->anchor_count > UWB_MAX_ANCHORS) {
        return -1;
    }

    ss_twr_init_local_tag_id = config->tag_id;
    ss_twr_init_local_addr = uwb_tag_short_addr(ss_twr_init_local_tag_id);
    ss_twr_init_anchor_count = config->anchor_count;
    ss_twr_init_sweep_count = 0U;
    ss_twr_init_active_anchor_count = 0U;
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_have_last_solution = false;
    ss_twr_init_last_solution_anchor_count = 0U;
    ss_twr_init_have_last_location = false;
#if APP_TAG_STATUS_PERIOD_MS > 0U
    ss_twr_init_have_last_raw_location = false;
    ss_twr_init_location_output_count = 0U;
    ss_twr_init_last_location_update_ms = 0U;
    memset(&ss_twr_init_last_raw_location, 0, sizeof(ss_twr_init_last_raw_location));
    memset(&ss_twr_init_last_filtered_location, 0,
           sizeof(ss_twr_init_last_filtered_location));
    ss_twr_init_last_location_x_mm = 0;
    ss_twr_init_last_location_y_mm = 0;
    ss_twr_init_last_location_z_mm = 0;
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
    ss_twr_init_have_last_imu_sample = false;
    memset(&ss_twr_init_last_imu_sample, 0, sizeof(ss_twr_init_last_imu_sample));
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

    if (config->fixed_anchor_mode) {
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
    uint32_t sweep_elapsed_ms =
        (uint32_t)k_uptime_get() - ss_twr_init_current_sweep_start_ms;
    uint8_t valid_anchor_ids[UWB_MAX_ANCHORS];
    size_t valid_anchor_count = 0U;
    char received_anchors[32];

    memset(measurements, 0, sizeof(measurements));

    for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
        uint8_t anchor_id = ss_twr_init_anchor_ids[i];
        const struct uwb_anchor_pose_mm *pose =
            uwb_anchor_layout_get(anchor_id);
        struct uwb_range_tracker *tracker = &ss_twr_init_trackers[anchor_id];
        bool measured_this_sweep = ss_twr_init_anchor_id_in_list(
            ss_twr_init_active_anchor_ids, ss_twr_init_active_anchor_count,
            anchor_id);

        measurements[i].anchor_id = anchor_id;
        measurements[i].quality_percent =
            uwb_range_tracker_quality_percent(tracker);
        measurements[i].valid = measured_this_sweep && tracker->filtered_valid;
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

    if (uwb_tag_loc_solve(measurements, ss_twr_init_anchor_count, &location) !=
        0) {
        ss_twr_init_format_anchor_labels(valid_anchor_ids,
                                         valid_anchor_count,
                                         received_anchors,
                                         sizeof(received_anchors));
        if (APP_TAG_PENDING_PRINT_PERIOD != 0U &&
            (ss_twr_init_sweep_count % APP_TAG_PENDING_PRINT_PERIOD) == 0U) {
            printk("Tag solve pending: need >=4 valid anchors across both planes "
                   "plan=%s active=%u sweep_ms=%lu valid=[%s]\n",
                   ss_twr_init_plan_label(),
                   (unsigned int)ss_twr_init_active_anchor_count,
                   (unsigned long)sweep_elapsed_ms,
                    received_anchors);
        }
        return;
    }

    raw_location = location;
    {
        ss_twr_init_format_anchor_labels(location.anchor_ids,
                                         location.used_anchor_count,
                                         received_anchors,
                                         sizeof(received_anchors));
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
        return;
    }

    have_motion = uwb_motion_update(location.x_mm, location.y_mm, location.z_mm,
                                    k_uptime_get(), &motion);

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

    {
        char ble_summary[192];
        char ble_anchors[32];
        char ble_anchor_labels[32];
        size_t ble_anchors_len = 0U;
        size_t ble_anchor_labels_len = 0U;
        uint8_t ble_anchor_mask = 0U;
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
                if (pose->label >= 'A' && pose->label <= 'H') {
                    ble_anchor_mask |= BIT((uint8_t)(pose->label - 'A'));
                }
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
            if (have_motion) {
                snprintk(ble_summary, sizeof(ble_summary),
                         "TS s=%lu p=%s xyz=%ld,%ld,%ld r=%lu m=%lu a=%s d=%lu",
                         (unsigned long)ss_twr_init_sweep_count,
                         plan_label,
                         (long)location.x_mm, (long)location.y_mm,
                         (long)location.z_mm,
                         (unsigned long)location.residual_rms_mm,
                         (unsigned long)location.residual_max_mm,
                         ble_anchor_labels,
                         (unsigned long)motion.dt_ms);
            } else {
                snprintk(ble_summary, sizeof(ble_summary),
                         "TS s=%lu p=%s xyz=%ld,%ld,%ld r=%lu m=%lu a=%s motion=na",
                         (unsigned long)ss_twr_init_sweep_count,
                         plan_label,
                         (long)location.x_mm, (long)location.y_mm,
                         (long)location.z_mm,
                         (unsigned long)location.residual_rms_mm,
                         (unsigned long)location.residual_max_mm,
                         ble_anchor_labels);
            }
        } else {
            if (have_motion) {
                snprintk(ble_summary, sizeof(ble_summary),
                         "TagSummary sweep=%lu plan=%s xyz=(%ld,%ld,%ld) rms=%lu max=%lu anchors=%s motion_dt=%lu",
                         (unsigned long)ss_twr_init_sweep_count,
                         plan_label,
                         (long)location.x_mm, (long)location.y_mm,
                         (long)location.z_mm,
                         (unsigned long)location.residual_rms_mm,
                         (unsigned long)location.residual_max_mm,
                         ble_anchors,
                         (unsigned long)motion.dt_ms);
            } else {
                snprintk(ble_summary, sizeof(ble_summary),
                         "TagSummary sweep=%lu plan=%s xyz=(%ld,%ld,%ld) rms=%lu max=%lu anchors=%s motion=na",
                         (unsigned long)ss_twr_init_sweep_count,
                         plan_label,
                         (long)location.x_mm, (long)location.y_mm,
                         (long)location.z_mm,
                         (unsigned long)location.residual_rms_mm,
                         (unsigned long)location.residual_max_mm,
                         ble_anchors);
            }
        }

#if APP_TAG_BLE_ENABLE
#if APP_TAG_USB_MIRROR_BLE_STATUS
        printk("%s\n", ble_summary);
#endif
        if (APP_TAG_BLE_COMPACT_STATUS != 0U) {
            struct uwb_tag_ble_sample ble_sample = {
                .sweep = ss_twr_init_sweep_count,
                .x_mm = location.x_mm,
                .y_mm = location.y_mm,
                .z_mm = location.z_mm,
                .rms_mm = (uint16_t)MIN(location.residual_rms_mm, 0xffffU),
                .max_mm = (uint16_t)MIN(location.residual_max_mm, 0xffffU),
                .motion_dt_ms = have_motion ? (uint16_t)MIN(motion.dt_ms, 0xffffU) : 0U,
                .anchor_mask = ble_anchor_mask,
                .motion_valid = have_motion,
                .plan_code = UWB_TAG_BLE_PLAN_UNKNOWN,
            };

            if (strcmp(plan_label, "track") == 0) {
                ble_sample.plan_code = UWB_TAG_BLE_PLAN_TRACK;
            } else if (strcmp(plan_label, "full") == 0) {
                ble_sample.plan_code = UWB_TAG_BLE_PLAN_FULL;
            } else if (strcmp(plan_label, "fixed") == 0) {
                ble_sample.plan_code = UWB_TAG_BLE_PLAN_FIXED;
            }

            (void)uwb_tag_ble_publish_sample(&ble_sample);
        } else {
            (void)uwb_tag_ble_publish_status(ble_summary);
        }
#endif
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
}

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
    printk("Tag motion mode rng_delay_ms=%u tx_to_rx_uus=%u resp_timeout_uus=%u fast_tracking=%u full_interval=%u track_count=%u fixed=%u fixed_count=%u tdma=%u slot=%u/%u period=%u active=%u\n",
           (unsigned int)SS_TWR_INIT_RNG_DELAY_MS,
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
           (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms);
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
        uint32_t tdma_wait_ms =
            uwb_tdma_wait_until_slot(&ss_twr_init_tdma_schedule);
#if APP_TAG_USB_DIAG_TRACE
        ss_twr_diag_write("SS-TWR: wait_tdma done\n");
#endif
        printk("SS-TWR init trace: TDMA wait complete wait_ms=%lu slot=%u/%u period=%u active=%u\n",
               (unsigned long)tdma_wait_ms,
               (unsigned int)ss_twr_init_tdma_schedule.slot_index,
               (unsigned int)ss_twr_init_tdma_schedule.slot_count,
               (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
               (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms);
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
            dwt_forcetrxoff();
            k_msleep(20);
            continue;
        }
#endif

        uint8_t current_anchor_id =
            ss_twr_init_active_anchor_ids[ss_twr_init_active_anchor_index];
        uint16_t current_anchor_addr = uwb_anchor_short_addr(current_anchor_id);
        struct uwb_range_tracker *tracker =
            &ss_twr_init_trackers[current_anchor_id];
        uint32 status_reg;

        uwb_ss_twr_build_poll_frame(ss_twr_init_tx_poll_msg,
                                    ss_twr_init_frame_seq_nb, current_anchor_addr,
                                    ss_twr_init_local_addr);

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);

        if (ss_twr_init_sweep_count == 0U &&
            ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
            ss_twr_diag_write("SS-TWR: first poll tx prepare\n");
#endif
        }
        if (dwt_writetxdata(sizeof(ss_twr_init_tx_poll_msg),
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            printk("Initiator TX buffer write failed\n");
            ss_twr_init_sleep_between_ranges();
            continue;
        }

        dwt_writetxfctrl(sizeof(ss_twr_init_tx_poll_msg), 0, 1);

        if (dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED) !=
            DWT_SUCCESS) {
            printk("Initiator TX start failed\n");
            dwt_forcetrxoff();
            ss_twr_init_sleep_between_ranges();
            continue;
        }

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
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            filtered_mm = uwb_range_tracker_record_success(
                tracker, (uint32_t)raw_distance_mm);
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
        } else {
            if (tracker != NULL) {
                uwb_range_tracker_record_failure(tracker);
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
        if (ss_twr_init_active_anchor_index == 0U) {
            ss_twr_init_sweep_count++;
            ss_twr_init_print_location_if_ready();
            (void)uwb_tdma_wait_until_slot(&ss_twr_init_tdma_schedule);
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
    if (!ss_twr_init_tdma_schedule.enabled ||
        ss_twr_init_tdma_schedule.slot_count == 0U) {
        return -EINVAL;
    }

    if (slot_index >= ss_twr_init_tdma_schedule.slot_count) {
        return -ERANGE;
    }

    ss_twr_init_tdma_schedule.slot_index = slot_index;
    printk("Tag TDMA live slot update slot=%u/%u period=%u active=%u\n",
           (unsigned int)ss_twr_init_tdma_schedule.slot_index,
           (unsigned int)ss_twr_init_tdma_schedule.slot_count,
           (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
           (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms);
    return 0;
}
