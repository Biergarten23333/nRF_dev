#include <zephyr/kernel.h>
#include <zephyr/dfu/mcuboot.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include "ss_twr_init.h"
#include "uwb_bringup.h"
#include "uwb_tag_ble.h"

#ifndef APP_TAG_ID
#define APP_TAG_ID 0U
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

    if (!boot_is_img_confirmed()) {
        ret = boot_write_img_confirmed();
        printk("MCUboot confirm rc=%d\n", ret);
        if (ret) {
            printk("MCUboot confirm failed, continuing: %d\n", ret);
        }
    }

    ret = uwb_hw_bringup_and_init();
    if (ret) {
        return ret;
    }

    ret = uwb_tag_ble_init();
    if (ret) {
        printk("Tag BLE init failed, continuing with UWB only: %d\n", ret);
    } else {
        /* Give the Bluetooth host/controller a short window to settle before
         * entering the long-running SS-TWR loop.
         */
        k_msleep(100);
    }

    printk("Tag app ready tag_id=%u anchor_count=%u anchors=[%u,%u,%u,%u,%u,%u,%u,%u] fixed=%u fixed_anchors=[%u,%u,%u,%u] multitag=%u active=[%u,%u,%u,%u] standby=[%u,%u] reserve=[%u,%u] refresh=%u/%u full=%u tdma=%u slot=%u/%u period=%u active=%u\n",
           APP_TAG_ID, APP_TAG_ANCHOR_COUNT, APP_TAG_ANCHOR_0_ID,
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
           APP_TAG_TDMA_ENABLE, APP_TAG_TDMA_SLOT_INDEX,
           APP_TAG_TDMA_SLOT_COUNT, APP_TAG_TDMA_SLOT_PERIOD_MS,
           APP_TAG_TDMA_SLOT_ACTIVE_MS);

    ret = ss_twr_init_start_with_config(&runtime_config);
    if (ret) {
        printk("ss_twr_init_start_with_config failed: %d\n", ret);
        return ret;
    }

    return 0;
}
