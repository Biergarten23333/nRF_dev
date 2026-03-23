#include <zephyr/kernel.h>
#if APP_TAG_MCUBOOT_ENABLE
#include <zephyr/dfu/mcuboot.h>
#endif
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include "ss_twr_init.h"
#include "uwb_bringup.h"
#if APP_TAG_BLE_ENABLE
#include "uwb_tag_ble.h"
#endif

#ifndef APP_TAG_MCUBOOT_ENABLE
#define APP_TAG_MCUBOOT_ENABLE 1U
#endif

#ifndef APP_TAG_ID
#define APP_TAG_ID 0U
#endif

#ifndef APP_TAG_BLE_TOKEN_ID
#define APP_TAG_BLE_TOKEN_ID APP_TAG_ID
#endif

#ifndef APP_TAG_ANCHOR_COUNT
#define APP_TAG_ANCHOR_COUNT 8U
#endif

#ifndef APP_TAG_ANCHOR_0_ID
#define APP_TAG_ANCHOR_0_ID 0U
#endif

#ifndef APP_TAG_ANCHOR_1_ID
#define APP_TAG_ANCHOR_1_ID 1U
#endif

#ifndef APP_TAG_ANCHOR_2_ID
#define APP_TAG_ANCHOR_2_ID 2U
#endif

#ifndef APP_TAG_ANCHOR_3_ID
#define APP_TAG_ANCHOR_3_ID 3U
#endif

#ifndef APP_TAG_ANCHOR_4_ID
#define APP_TAG_ANCHOR_4_ID 4U
#endif

#ifndef APP_TAG_ANCHOR_5_ID
#define APP_TAG_ANCHOR_5_ID 5U
#endif

#ifndef APP_TAG_ANCHOR_6_ID
#define APP_TAG_ANCHOR_6_ID 6U
#endif

#ifndef APP_TAG_ANCHOR_7_ID
#define APP_TAG_ANCHOR_7_ID 7U
#endif

#ifndef APP_TAG_FIXED_MODE
#define APP_TAG_FIXED_MODE 0U
#endif

#ifndef APP_TAG_FIXED_ANCHOR_COUNT
#define APP_TAG_FIXED_ANCHOR_COUNT 4U
#endif

#ifndef APP_TAG_FIXED_ANCHOR_0_ID
#define APP_TAG_FIXED_ANCHOR_0_ID 1U
#endif

#ifndef APP_TAG_FIXED_ANCHOR_1_ID
#define APP_TAG_FIXED_ANCHOR_1_ID 2U
#endif

#ifndef APP_TAG_FIXED_ANCHOR_2_ID
#define APP_TAG_FIXED_ANCHOR_2_ID 5U
#endif

#ifndef APP_TAG_FIXED_ANCHOR_3_ID
#define APP_TAG_FIXED_ANCHOR_3_ID 6U
#endif

#ifndef APP_TAG_MULTITAG_PLAN_MODE
#define APP_TAG_MULTITAG_PLAN_MODE 0U
#endif

#ifndef APP_TAG_ACTIVE_ANCHOR_0_ID
#define APP_TAG_ACTIVE_ANCHOR_0_ID 0U
#endif

#ifndef APP_TAG_ACTIVE_ANCHOR_1_ID
#define APP_TAG_ACTIVE_ANCHOR_1_ID 1U
#endif

#ifndef APP_TAG_ACTIVE_ANCHOR_2_ID
#define APP_TAG_ACTIVE_ANCHOR_2_ID 4U
#endif

#ifndef APP_TAG_ACTIVE_ANCHOR_3_ID
#define APP_TAG_ACTIVE_ANCHOR_3_ID 5U
#endif

#ifndef APP_TAG_STANDBY_ANCHOR_0_ID
#define APP_TAG_STANDBY_ANCHOR_0_ID 2U
#endif

#ifndef APP_TAG_STANDBY_ANCHOR_1_ID
#define APP_TAG_STANDBY_ANCHOR_1_ID 6U
#endif

#ifndef APP_TAG_RESERVE_ANCHOR_0_ID
#define APP_TAG_RESERVE_ANCHOR_0_ID 3U
#endif

#ifndef APP_TAG_RESERVE_ANCHOR_1_ID
#define APP_TAG_RESERVE_ANCHOR_1_ID 7U
#endif

#ifndef APP_TAG_REFRESH_ANCHOR_BUDGET
#define APP_TAG_REFRESH_ANCHOR_BUDGET 1U
#endif

#ifndef APP_TAG_REFRESH_INTERVAL
#define APP_TAG_REFRESH_INTERVAL 10U
#endif

#ifndef APP_TAG_MAINTENANCE_FULL_INTERVAL
#define APP_TAG_MAINTENANCE_FULL_INTERVAL 100U
#endif

#ifndef APP_TAG_TDMA_ENABLE
#define APP_TAG_TDMA_ENABLE 0U
#endif

#ifndef APP_TAG_TDMA_SLOT_INDEX
#define APP_TAG_TDMA_SLOT_INDEX 0U
#endif

#ifndef APP_TAG_TDMA_SLOT_COUNT
#define APP_TAG_TDMA_SLOT_COUNT 1U
#endif

#ifndef APP_TAG_TDMA_SLOT_PERIOD_MS
#define APP_TAG_TDMA_SLOT_PERIOD_MS 25U
#endif

#ifndef APP_TAG_TDMA_SLOT_ACTIVE_MS
#define APP_TAG_TDMA_SLOT_ACTIVE_MS 20U
#endif

#ifndef APP_TAG_EKF_ENABLE
#define APP_TAG_EKF_ENABLE 0U
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

int tag_app_run(void)
{
    static const uint8_t target_anchor_ids[] = {
        APP_TAG_ANCHOR_0_ID,
        APP_TAG_ANCHOR_1_ID,
        APP_TAG_ANCHOR_2_ID,
        APP_TAG_ANCHOR_3_ID,
        APP_TAG_ANCHOR_4_ID,
        APP_TAG_ANCHOR_5_ID,
        APP_TAG_ANCHOR_6_ID,
        APP_TAG_ANCHOR_7_ID,
    };
    static const uint8_t fixed_anchor_ids[] = {
        APP_TAG_FIXED_ANCHOR_0_ID,
        APP_TAG_FIXED_ANCHOR_1_ID,
        APP_TAG_FIXED_ANCHOR_2_ID,
        APP_TAG_FIXED_ANCHOR_3_ID,
    };
    static const uint8_t active_anchor_ids[] = {
        APP_TAG_ACTIVE_ANCHOR_0_ID,
        APP_TAG_ACTIVE_ANCHOR_1_ID,
        APP_TAG_ACTIVE_ANCHOR_2_ID,
        APP_TAG_ACTIVE_ANCHOR_3_ID,
    };
    static const uint8_t standby_anchor_ids[] = {
        APP_TAG_STANDBY_ANCHOR_0_ID,
        APP_TAG_STANDBY_ANCHOR_1_ID,
    };
    static const uint8_t reserve_anchor_ids[] = {
        APP_TAG_RESERVE_ANCHOR_0_ID,
        APP_TAG_RESERVE_ANCHOR_1_ID,
    };
    static const struct uwb_tag_runtime_config runtime_config = {
        .tag_id = APP_TAG_ID,
        .anchor_ids = target_anchor_ids,
        .anchor_count = APP_TAG_ANCHOR_COUNT,
        .fixed_anchor_mode = (APP_TAG_FIXED_MODE != 0U),
        .fixed_anchor_ids = fixed_anchor_ids,
        .fixed_anchor_count = APP_TAG_FIXED_ANCHOR_COUNT,
        .multitag_anchor_plan_mode = (APP_TAG_MULTITAG_PLAN_MODE != 0U),
        .active_anchor_ids = active_anchor_ids,
        .active_anchor_count = ARRAY_SIZE(active_anchor_ids),
        .standby_anchor_ids = standby_anchor_ids,
        .standby_anchor_count = ARRAY_SIZE(standby_anchor_ids),
        .reserve_anchor_ids = reserve_anchor_ids,
        .reserve_anchor_count = ARRAY_SIZE(reserve_anchor_ids),
        .refresh_anchor_budget = APP_TAG_REFRESH_ANCHOR_BUDGET,
        .refresh_interval_sweeps = APP_TAG_REFRESH_INTERVAL,
        .full_sweep_interval_sweeps = APP_TAG_MAINTENANCE_FULL_INTERVAL,
        .tdma =
            {
                .enabled = (APP_TAG_TDMA_ENABLE != 0U),
                .slot_index = APP_TAG_TDMA_SLOT_INDEX,
                .slot_count = APP_TAG_TDMA_SLOT_COUNT,
                .slot_period_ms = APP_TAG_TDMA_SLOT_PERIOD_MS,
                .slot_active_ms = APP_TAG_TDMA_SLOT_ACTIVE_MS,
            },
    };
    int ret;

#if APP_TAG_MCUBOOT_ENABLE
    if (!boot_is_img_confirmed()) {
        ret = boot_write_img_confirmed();
        printk("MCUboot confirm rc=%d\n", ret);
        if (ret) {
            printk("MCUboot confirm failed, continuing: %d\n", ret);
        }
    }
#else
    printk("MCUboot disabled, running direct USB serial image\n");
#endif

#if APP_TAG_BLE_ENABLE
    ret = uwb_tag_ble_init();
    if (ret) {
        printk("Tag BLE init failed, continuing with UWB only: %d\n", ret);
    }
#else
    printk("Tag BLE disabled, running pure UWB/USB serial mode\n");
#endif

    printk("Tag UWB bringup starting\n");
    k_msleep(100);
    ret = uwb_hw_bringup_and_init();
    if (ret) {
        printk("Tag UWB bringup failed: %d\n", ret);
        return ret;
    }
    printk("Tag UWB bringup done\n");

    printk("Tag app ready tag_id=%u ble_token=%u anchor_count=%u anchors=[%u,%u,%u,%u,%u,%u,%u,%u] fixed=%u fixed_anchors=[%u,%u,%u,%u] multitag=%u active=[%u,%u,%u,%u] standby=[%u,%u] reserve=[%u,%u] refresh=%u/%u full=%u motion_full=%u tdma=%u slot=%u/%u period=%u active=%u filter=%s meas_std=%u residual_gain=%u proc_accel=%u init_pos=%u init_vel=%u gate=%u motion_meas=%u motion_proc=%u motion_gate=%u speed_thr=%u imu_delta=%u imu_gerr=%u range_soft=%u range_hard=%u motion_soft=%u motion_hard=%u\n",
           APP_TAG_ID, APP_TAG_BLE_TOKEN_ID, APP_TAG_ANCHOR_COUNT, APP_TAG_ANCHOR_0_ID,
           APP_TAG_ANCHOR_1_ID, APP_TAG_ANCHOR_2_ID, APP_TAG_ANCHOR_3_ID,
           APP_TAG_ANCHOR_4_ID, APP_TAG_ANCHOR_5_ID, APP_TAG_ANCHOR_6_ID,
           APP_TAG_ANCHOR_7_ID, APP_TAG_FIXED_MODE,
           APP_TAG_FIXED_ANCHOR_0_ID, APP_TAG_FIXED_ANCHOR_1_ID,
           APP_TAG_FIXED_ANCHOR_2_ID, APP_TAG_FIXED_ANCHOR_3_ID,
           APP_TAG_MULTITAG_PLAN_MODE, APP_TAG_ACTIVE_ANCHOR_0_ID,
           APP_TAG_ACTIVE_ANCHOR_1_ID, APP_TAG_ACTIVE_ANCHOR_2_ID,
           APP_TAG_ACTIVE_ANCHOR_3_ID, APP_TAG_STANDBY_ANCHOR_0_ID,
           APP_TAG_STANDBY_ANCHOR_1_ID, APP_TAG_RESERVE_ANCHOR_0_ID,
           APP_TAG_RESERVE_ANCHOR_1_ID, APP_TAG_REFRESH_ANCHOR_BUDGET,
           APP_TAG_REFRESH_INTERVAL, APP_TAG_MAINTENANCE_FULL_INTERVAL,
           APP_TAG_MOTION_FULL_SWEEP_INTERVAL,
           APP_TAG_TDMA_ENABLE, APP_TAG_TDMA_SLOT_INDEX,
           APP_TAG_TDMA_SLOT_COUNT, APP_TAG_TDMA_SLOT_PERIOD_MS,
           APP_TAG_TDMA_SLOT_ACTIVE_MS,
           (APP_TAG_EKF_ENABLE != 0U) ? "post_kf" : "raw",
           APP_TAG_EKF_MEAS_STD_MM, APP_TAG_EKF_RESIDUAL_GAIN_PCT,
           APP_TAG_EKF_PROC_ACCEL_MM_S2, APP_TAG_EKF_INIT_POS_STD_MM,
           APP_TAG_EKF_INIT_VEL_STD_MM_S, APP_TAG_EKF_OUTLIER_GATE_MM,
           APP_TAG_MOTION_EKF_MEAS_STD_MM,
           APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2,
           APP_TAG_MOTION_EKF_OUTLIER_GATE_MM,
           APP_TAG_MOTION_SPEED_THRESHOLD_MM_S,
           APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG,
           APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG,
           APP_TAG_RANGE_SOFT_RESIDUAL_MM, APP_TAG_RANGE_HARD_RESIDUAL_MM,
           APP_TAG_MOTION_RANGE_SOFT_BONUS_MM,
           APP_TAG_MOTION_RANGE_HARD_BONUS_MM);

    ret = ss_twr_init_start_with_config(&runtime_config);
    if (ret) {
        printk("ss_twr_init_start_with_config failed: %d\n", ret);
        return ret;
    }

    return 0;
}
