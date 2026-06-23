#include <zephyr/kernel.h>
#if APP_TAG_MCUBOOT_ENABLE
#include <zephyr/dfu/mcuboot.h>
#endif
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include "ss_twr_init.h"
#include "uwb_anchor_layout.h"
#include "uwb_bringup.h"
#include "uwb_port.h"
#include <deca_device_api.h>
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

#ifndef APP_TAG_TDMA_SLOT_ACTIVE_US
#define APP_TAG_TDMA_SLOT_ACTIVE_US 0U
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

#ifndef APP_TAG_UWB_ENABLE
#define APP_TAG_UWB_ENABLE 1U
#endif

#ifndef APP_TAG_OTP_DIAG
#define APP_TAG_OTP_DIAG 0U
#endif

#define TAG_BLUE_LED_NODE DT_ALIAS(led3)

#if DT_NODE_HAS_STATUS(TAG_BLUE_LED_NODE, okay)
#define TAG_BLUE_LED_AVAILABLE 1
static const struct gpio_dt_spec tag_blue_led =
    GPIO_DT_SPEC_GET(TAG_BLUE_LED_NODE, gpios);
#else
#define TAG_BLUE_LED_AVAILABLE 0
#endif

static bool tag_blue_led_ready;

static void tag_blue_led_set(bool on)
{
#if TAG_BLUE_LED_AVAILABLE
    if (!tag_blue_led_ready) {
        return;
    }

    (void)gpio_pin_set_dt(&tag_blue_led, on ? 1 : 0);
#else
    ARG_UNUSED(on);
#endif
}

static void tag_blue_led_init(void)
{
#if TAG_BLUE_LED_AVAILABLE
    int ret;

    if (!device_is_ready(tag_blue_led.port)) {
        printk("Tag blue LED GPIO not ready\n");
        return;
    }

    ret = gpio_pin_configure_dt(&tag_blue_led, GPIO_OUTPUT_INACTIVE);
    if (ret != 0) {
        printk("Tag blue LED configure failed: %d\n", ret);
        return;
    }

    tag_blue_led_ready = true;
    tag_blue_led_set(false);
#endif
}

static void tag_print_otp_diag(void)
{
#if APP_TAG_OTP_DIAG
    uint32 dev_id_port = 0U;
    uint32 otp_01c = 0U;
    uint32 otp_01d = 0U;
    uint32 otp_01e = 0U;
    uint16 otp_anchor_delay = 0U;
    uint16 otp_tag_delay = 0U;

    (void)uwb_port_read_dev_id(&dev_id_port);
    dwt_otpread(0x01C, &otp_01c, 1);
    dwt_otpread(0x01D, &otp_01d, 1);
    dwt_otpread(0x01E, &otp_01e, 1);
    otp_anchor_delay = (uint16)(otp_01c & 0xFFFFU);
    otp_tag_delay = (uint16)((otp_01c >> 16) & 0xFFFFU);

    printk("OTP_DIAG port_devid=0x%08X dwt_devid=0x%08X otp_rev=0x%02X addr_01C=0x%08X addr_01D=0x%08X addr_01E=0x%08X anchor_delay=%u tag_delay=%u\n",
           (unsigned int)dev_id_port,
           (unsigned int)dwt_readdevid(),
           (unsigned int)dwt_otprevision(),
           (unsigned int)otp_01c,
           (unsigned int)otp_01d,
           (unsigned int)otp_01e,
           (unsigned int)otp_anchor_delay,
           (unsigned int)otp_tag_delay);
#endif
}

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
        .positioning_mode = UWB_TAG_MODE_RUN,
        .anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2,
        .anchor_ids = target_anchor_ids,
        .anchor_count = APP_TAG_ANCHOR_COUNT,
        .fixed_anchor_mode = false,
        .fixed_anchor_ids = NULL,
        .fixed_anchor_count = 0U,
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
                .slot_active_us = APP_TAG_TDMA_SLOT_ACTIVE_US,
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

    tag_blue_led_init();

    uwb_anchor_layout_init();

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
                UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
            runtime_config.fixed_anchor_mode = false;
            runtime_config.fixed_anchor_count = 0U;

            if (ble_params.tdma.slot_count != 0U &&
                ble_params.tdma.slot_period_ms != 0U &&
                ble_params.tdma.slot_active_ms != 0U) {
                runtime_config.tdma = ble_params.tdma;
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

    if (APP_TAG_UWB_ENABLE) {
        printk("Tag UWB bringup starting\n");
        k_msleep(100);
        ret = uwb_hw_bringup_and_init();
        if (ret) {
            printk("Tag UWB bringup failed: %d\n", ret);
            tag_blue_led_set(false);
            return ret;
        }
        printk("Tag UWB bringup done\n");
        tag_print_otp_diag();
    } else {
        printk("Tag UWB disabled by build profile (BLE/OTA only)\n");
    }

#if APP_TAG_USB_DIAG_TRACE
    tag_diag_write("TAG_APP: handoff enter\n");
#endif
    printk("Tag firmware marker: %s\n", APP_TAG_FW_MARKER);
    printk("Tag app ready bs=BS%04X logical_tag=%u anchor_count=%u tdma=%u slot=%u/%u period=%u active=%u slot_src=%u mode=%s pmode=%u anchor_plan=dynamic filter=%s meas_std=%u residual_gain=%u proc_accel=%u init_pos=%u init_vel=%u gate=%u motion_meas=%u motion_proc=%u motion_gate=%u speed_thr=%u imu_delta=%u imu_gerr=%u range_soft=%u range_hard=%u motion_soft=%u motion_hard=%u\n",
           (unsigned int)runtime_config.identity_code,
           runtime_config.tag_id, APP_TAG_ANCHOR_COUNT,
           (unsigned int)runtime_config.tdma.enabled,
           (unsigned int)runtime_config.tdma.slot_index,
           (unsigned int)runtime_config.tdma.slot_count,
           (unsigned int)runtime_config.tdma.slot_period_ms,
           (unsigned int)runtime_config.tdma.slot_active_ms,
           (unsigned int)runtime_config.slot_source,
           (runtime_config.positioning_mode == UWB_TAG_MODE_IDLE) ? "IDLE" : "RUN",
           (unsigned int)runtime_config.positioning_mode,
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
    printk("Tag app anchors=[%u,%u,%u,%u,%u,%u,%u,%u] active=[%u,%u,%u,%u] standby=[%u,%u] reserve=[%u,%u] refresh=%u/%u full=%u motion_full=%u\n",
           APP_TAG_ANCHOR_0_ID,
           APP_TAG_ANCHOR_1_ID, APP_TAG_ANCHOR_2_ID, APP_TAG_ANCHOR_3_ID,
           APP_TAG_ANCHOR_4_ID, APP_TAG_ANCHOR_5_ID, APP_TAG_ANCHOR_6_ID,
           APP_TAG_ANCHOR_7_ID,
           APP_TAG_ACTIVE_ANCHOR_0_ID,
           APP_TAG_ACTIVE_ANCHOR_1_ID, APP_TAG_ACTIVE_ANCHOR_2_ID,
           APP_TAG_ACTIVE_ANCHOR_3_ID, APP_TAG_STANDBY_ANCHOR_0_ID,
           APP_TAG_STANDBY_ANCHOR_1_ID, APP_TAG_RESERVE_ANCHOR_0_ID,
           APP_TAG_RESERVE_ANCHOR_1_ID, APP_TAG_REFRESH_ANCHOR_BUDGET,
           APP_TAG_REFRESH_INTERVAL, APP_TAG_MAINTENANCE_FULL_INTERVAL,
           APP_TAG_MOTION_FULL_SWEEP_INTERVAL);
    if (APP_TAG_UWB_ENABLE) {
        printk("Tag app handoff: entering SS-TWR\n");
        tag_blue_led_set(true);
        ret = ss_twr_init_start_with_config(&runtime_config);
        if (ret) {
            printk("ss_twr_init_start_with_config failed: %d\n", ret);
            tag_blue_led_set(false);
            return ret;
        }
    } else {
        printk("Tag app handoff: UWB scheduler skipped (UWB disabled)\n");
        tag_blue_led_set(false);
        while (1) {
            k_sleep(K_SECONDS(1));
        }
    }

    return 0;
}
