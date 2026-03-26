#include <zephyr/kernel.h>
#if APP_TAG_MCUBOOT_ENABLE
#include <zephyr/dfu/mcuboot.h>
#endif
#include <zephyr/drivers/uart.h>
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

#if APP_TAG_USB_DIAG_TRACE
static void tag_diag_write(const char *msg)
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

#ifndef APP_TAG_FW_MARKER
#define APP_TAG_FW_MARKER "unified-default"
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
    static uint8_t runtime_fixed_anchor_ids[] = {
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
    struct uwb_tag_runtime_config runtime_config = {
        .identity_code = 0U,
        .tag_id = 0U,
        .slot_source = UWB_TAG_SLOT_SOURCE_BUILD,
        .positioning_mode = UWB_TAG_POSITIONING_MODE_DYNAMIC,
        .anchor_selection_mode = (APP_TAG_FIXED_MODE != 0U) ?
            UWB_TAG_ANCHOR_SELECTION_FIXED_SUBSET :
            UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2,
        .anchor_ids = target_anchor_ids,
        .anchor_count = APP_TAG_ANCHOR_COUNT,
        .fixed_anchor_mode = (APP_TAG_FIXED_MODE != 0U),
        .fixed_anchor_ids = runtime_fixed_anchor_ids,
        .fixed_anchor_count = APP_TAG_FIXED_ANCHOR_COUNT,
        .multitag_anchor_plan_mode = false,
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
    runtime_config.identity_code = uwb_tag_ble_identity_code();
    runtime_config.tag_id = uwb_tag_ble_tag_id();
    {
        struct uwb_tag_runtime_params ble_params;

        if (uwb_tag_ble_runtime_config_get(&ble_params)) {
            if (ble_params.identity_code != 0U) {
                runtime_config.identity_code = ble_params.identity_code;
            }
            if (ble_params.logical_tag_id < UWB_MAX_TAGS) {
                runtime_config.tag_id = ble_params.logical_tag_id;
            }
            runtime_config.slot_source = ble_params.slot_source;
            runtime_config.positioning_mode = ble_params.positioning_mode;
            runtime_config.anchor_selection_mode =
                ble_params.anchor_selection_mode;

            if (ble_params.tdma.slot_count != 0U &&
                ble_params.tdma.slot_period_ms != 0U &&
                ble_params.tdma.slot_active_ms != 0U) {
                runtime_config.tdma = ble_params.tdma;
            }

            if (ble_params.anchor_selection_mode ==
                    UWB_TAG_ANCHOR_SELECTION_FIXED_SUBSET &&
                ble_params.fixed_anchor_count >= 4U) {
                runtime_config.fixed_anchor_mode = true;
                runtime_config.fixed_anchor_count = ble_params.fixed_anchor_count;
                for (size_t i = 0U; i < ble_params.fixed_anchor_count &&
                                    i < ARRAY_SIZE(runtime_fixed_anchor_ids);
                     ++i) {
                    runtime_fixed_anchor_ids[i] = ble_params.fixed_anchor_ids[i];
                }
            } else {
                runtime_config.fixed_anchor_mode = false;
            }
        }
    }

    if (runtime_config.tdma.enabled && runtime_config.tdma.slot_count != 0U) {
        printk("Tag TDMA runtime config: slot=%u/%u period=%u active=%u source=%u epoch_valid=%u gen=%u\n",
               (unsigned int)runtime_config.tdma.slot_index,
               (unsigned int)runtime_config.tdma.slot_count,
               (unsigned int)runtime_config.tdma.slot_period_ms,
               (unsigned int)runtime_config.tdma.slot_active_ms,
               (unsigned int)runtime_config.slot_source,
               (unsigned int)runtime_config.tdma.epoch_valid,
               (unsigned int)runtime_config.tdma.generation);
    } else {
        printk("Tag TDMA runtime config disabled\n");
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

#if APP_TAG_USB_DIAG_TRACE
    tag_diag_write("TAG_APP: handoff enter\n");
#endif
    printk("Tag firmware marker: %s\n", APP_TAG_FW_MARKER);
    printk("Tag app ready bs=BS%04X logical_tag=%u anchor_count=%u fixed=%u fixed_count=%u tdma=%u slot=%u/%u period=%u active=%u slot_src=%u pmode=%u amode=%u filter=%s meas_std=%u residual_gain=%u proc_accel=%u init_pos=%u init_vel=%u gate=%u motion_meas=%u motion_proc=%u motion_gate=%u speed_thr=%u imu_delta=%u imu_gerr=%u range_soft=%u range_hard=%u motion_soft=%u motion_hard=%u\n",
           (unsigned int)runtime_config.identity_code,
           runtime_config.tag_id, APP_TAG_ANCHOR_COUNT,
           (unsigned int)runtime_config.fixed_anchor_mode,
           (unsigned int)runtime_config.fixed_anchor_count,
           (unsigned int)runtime_config.tdma.enabled,
           (unsigned int)runtime_config.tdma.slot_index,
           (unsigned int)runtime_config.tdma.slot_count,
           (unsigned int)runtime_config.tdma.slot_period_ms,
           (unsigned int)runtime_config.tdma.slot_active_ms,
           (unsigned int)runtime_config.slot_source,
           (unsigned int)runtime_config.positioning_mode,
           (unsigned int)runtime_config.anchor_selection_mode,
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
    printk("Tag app anchors=[%u,%u,%u,%u,%u,%u,%u,%u] fixed_anchors=[%u,%u,%u,%u] active=[%u,%u,%u,%u] standby=[%u,%u] reserve=[%u,%u] refresh=%u/%u full=%u motion_full=%u\n",
           APP_TAG_ANCHOR_0_ID,
           APP_TAG_ANCHOR_1_ID, APP_TAG_ANCHOR_2_ID, APP_TAG_ANCHOR_3_ID,
           APP_TAG_ANCHOR_4_ID, APP_TAG_ANCHOR_5_ID, APP_TAG_ANCHOR_6_ID,
           APP_TAG_ANCHOR_7_ID,
           runtime_fixed_anchor_ids[0], runtime_fixed_anchor_ids[1],
           runtime_fixed_anchor_ids[2], runtime_fixed_anchor_ids[3],
           APP_TAG_ACTIVE_ANCHOR_0_ID,
           APP_TAG_ACTIVE_ANCHOR_1_ID, APP_TAG_ACTIVE_ANCHOR_2_ID,
           APP_TAG_ACTIVE_ANCHOR_3_ID, APP_TAG_STANDBY_ANCHOR_0_ID,
           APP_TAG_STANDBY_ANCHOR_1_ID, APP_TAG_RESERVE_ANCHOR_0_ID,
           APP_TAG_RESERVE_ANCHOR_1_ID, APP_TAG_REFRESH_ANCHOR_BUDGET,
           APP_TAG_REFRESH_INTERVAL, APP_TAG_MAINTENANCE_FULL_INTERVAL,
           APP_TAG_MOTION_FULL_SWEEP_INTERVAL);
    printk("Tag app handoff: entering SS-TWR\n");

    ret = ss_twr_init_start_with_config(&runtime_config);
    if (ret) {
        printk("ss_twr_init_start_with_config failed: %d\n", ret);
        return ret;
    }

    return 0;
}
