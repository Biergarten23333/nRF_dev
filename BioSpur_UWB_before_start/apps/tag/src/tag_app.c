#include <zephyr/sys/printk.h>

#include "ss_twr_init.h"
#include "uwb_bringup.h"

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
    int ret = uwb_hw_bringup_and_init();
    if (ret) {
        return ret;
    }

    printk("Tag app ready tag_id=%u anchor_count=%u anchors=[%u,%u,%u,%u,%u,%u,%u,%u]\n",
           APP_TAG_ID, APP_TAG_ANCHOR_COUNT, APP_TAG_ANCHOR_0_ID,
           APP_TAG_ANCHOR_1_ID, APP_TAG_ANCHOR_2_ID, APP_TAG_ANCHOR_3_ID,
           APP_TAG_ANCHOR_4_ID, APP_TAG_ANCHOR_5_ID, APP_TAG_ANCHOR_6_ID,
           APP_TAG_ANCHOR_7_ID);

    ret = ss_twr_init_start(APP_TAG_ID, target_anchor_ids,
                            APP_TAG_ANCHOR_COUNT);
    if (ret) {
        printk("ss_twr_init_start failed: %d\n", ret);
        return ret;
    }

    return 0;
}
