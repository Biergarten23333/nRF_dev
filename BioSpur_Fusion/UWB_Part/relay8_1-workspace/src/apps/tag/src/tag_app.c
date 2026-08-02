#include <zephyr/kernel.h>
#if APP_TAG_MCUBOOT_ENABLE
#include <zephyr/dfu/mcuboot.h>
#endif
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include "biospur_uart_link.h"
#include "ss_twr_init.h"
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

#ifndef APP_TAG_FW_MARKER
#define APP_TAG_FW_MARKER "unified-default"
#endif

#ifndef APP_TAG_UWB_ENABLE
#define APP_TAG_UWB_ENABLE 1U
#endif

#ifndef APP_TAG_OTP_DIAG
#define APP_TAG_OTP_DIAG 0U
#endif

static void tag_print_otp_diag(void)
{
#if APP_TAG_OTP_DIAG
    uint32 dev_id_port = 0U;
    uint32 otp_01c = 0U;
    uint32 otp_01d = 0U;
    uint32 otp_01e = 0U;
    uint16 otp_anchor_delay = 0U;
    uint16 otp_tag_delay = 0U;

    (void)uwb_port_set_spi_slow();
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

    {
        uint32 otp_words[0x20];
        int a;

        for (a = 0; a < 0x20; a++) {
            otp_words[a] = 0U;
            dwt_otpread((uint16)a, &otp_words[a], 1);
        }
        for (a = 0; a < 0x20; a += 4) {
            printk("OTP_DUMP[0x%02X] %08X %08X %08X %08X\n",
                   (unsigned int)a,
                   (unsigned int)otp_words[a], (unsigned int)otp_words[a + 1],
                   (unsigned int)otp_words[a + 2], (unsigned int)otp_words[a + 3]);
        }
        /* Vbat=V@3.3V(0x08.b0); Vtemp23=T@23C SAR(0x09.b0); tmeas_antcal=Tmeas@AntCal(0x09.b1);
         * xtrim=XTAL trim(0x1E low5); ant delay 0x1C: tag=high16 anchor=low16 */
        printk("OTP_DECODE ldotune=0x%08X partid=0x%08X lotid=0x%08X vbat=0x%02X vtemp23=0x%02X tmeas_antcal=0x%02X xtrim=0x%02X otprev=0x%02X ant_tag=%u ant_anchor=%u\n",
               (unsigned int)otp_words[0x04],
               (unsigned int)otp_words[0x06],
               (unsigned int)otp_words[0x07],
               (unsigned int)(otp_words[0x08] & 0xFFU),
               (unsigned int)(otp_words[0x09] & 0xFFU),
               (unsigned int)((otp_words[0x09] >> 8) & 0xFFU),
               (unsigned int)(otp_words[0x1E] & 0x1FU),
               (unsigned int)dwt_otprevision(),
               (unsigned int)((otp_words[0x1C] >> 16) & 0xFFFFU),
               (unsigned int)(otp_words[0x1C] & 0xFFFFU));
    }
    (void)uwb_port_set_spi_fast();
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
        .beacon_sync = false,
        .beacon_win_n = TAG_BEACON_WINDOW_N_DEFAULT,
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
        .dw_anchor = false,
#endif
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

#if !APP_TAG_MCUBOOT_ENABLE
    printk("MCUboot disabled, running direct USB serial image\n");
#endif

    ret = biospur_uart_link_init();
    if (ret) {
        printk("BioSpur UART link init failed: %d\n", ret);
        return ret;
    }

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
            runtime_config.beacon_sync = ble_params.beacon_sync;
            runtime_config.beacon_win_n = ble_params.beacon_win_n;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
            runtime_config.dw_anchor = ble_params.dw_anchor;
#endif

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
    printk("Tag app ready bs=BS%04X logical_tag=%u anchor_count=%u tdma=%u slot=%u/%u period=%u active=%u slot_src=%u mode=%s pmode=%u anchor_plan=dynamic range=instantaneous_unsmoothed\n",
           (unsigned int)runtime_config.identity_code,
           runtime_config.tag_id, APP_TAG_ANCHOR_COUNT,
           (unsigned int)runtime_config.tdma.enabled,
           (unsigned int)runtime_config.tdma.slot_index,
           (unsigned int)runtime_config.tdma.slot_count,
           (unsigned int)runtime_config.tdma.slot_period_ms,
           (unsigned int)runtime_config.tdma.slot_active_ms,
           (unsigned int)runtime_config.slot_source,
           (runtime_config.positioning_mode == UWB_TAG_MODE_IDLE) ? "IDLE" : "RUN",
           (unsigned int)runtime_config.positioning_mode);
    printk("Tag app anchors=[%u,%u,%u,%u,%u,%u,%u,%u] active=[%u,%u,%u,%u] standby=[%u,%u] reserve=[%u,%u] refresh=%u/%u full=%u\n",
           APP_TAG_ANCHOR_0_ID,
           APP_TAG_ANCHOR_1_ID, APP_TAG_ANCHOR_2_ID, APP_TAG_ANCHOR_3_ID,
           APP_TAG_ANCHOR_4_ID, APP_TAG_ANCHOR_5_ID, APP_TAG_ANCHOR_6_ID,
           APP_TAG_ANCHOR_7_ID,
           APP_TAG_ACTIVE_ANCHOR_0_ID,
           APP_TAG_ACTIVE_ANCHOR_1_ID, APP_TAG_ACTIVE_ANCHOR_2_ID,
           APP_TAG_ACTIVE_ANCHOR_3_ID, APP_TAG_STANDBY_ANCHOR_0_ID,
           APP_TAG_STANDBY_ANCHOR_1_ID, APP_TAG_RESERVE_ANCHOR_0_ID,
           APP_TAG_RESERVE_ANCHOR_1_ID, APP_TAG_REFRESH_ANCHOR_BUDGET,
           APP_TAG_REFRESH_INTERVAL, APP_TAG_MAINTENANCE_FULL_INTERVAL);
    if (APP_TAG_UWB_ENABLE) {
        printk("Tag app handoff: entering SS-TWR\n");
#if APP_TAG_BLE_ENABLE
        uwb_tag_ble_led_set_uwb_ready(true);
#endif
        ret = ss_twr_init_start_with_config(&runtime_config);
        if (ret) {
            printk("ss_twr_init_start_with_config failed: %d\n", ret);
#if APP_TAG_BLE_ENABLE
            uwb_tag_ble_led_set_uwb_ready(false);
#endif
            return ret;
        }
    } else {
        printk("Tag app handoff: UWB scheduler skipped (UWB disabled)\n");
        while (1) {
            k_sleep(K_SECONDS(1));
        }
    }

    return 0;
}
