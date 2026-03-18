#include <zephyr/sys/printk.h>

#include "ss_twr_anchor_init.h"
#include "ss_twr_resp.h"
#include "uwb_anchor_topology.h"
#include "uwb_bringup.h"

#ifndef APP_ANCHOR_ID
#define APP_ANCHOR_ID 4U
#endif

#ifndef APP_ANCHOR_MASTER
#define APP_ANCHOR_MASTER 0U
#endif

#ifndef APP_ANCHOR_PEER_COUNT
#define APP_ANCHOR_PEER_COUNT 3U
#endif

#ifndef APP_ANCHOR_PEER_0_ID
#define APP_ANCHOR_PEER_0_ID 5U
#endif

#ifndef APP_ANCHOR_PEER_1_ID
#define APP_ANCHOR_PEER_1_ID 6U
#endif

#ifndef APP_ANCHOR_PEER_2_ID
#define APP_ANCHOR_PEER_2_ID 7U
#endif

#ifndef APP_ANCHOR_USE_AUTO_SCHEDULE
#define APP_ANCHOR_USE_AUTO_SCHEDULE 1U
#endif

#ifndef APP_ANCHOR_SCHEDULE_MODE
#define APP_ANCHOR_SCHEDULE_MODE 1U
#endif

#ifndef APP_ANCHOR_ALLOW_TAG_POLLS
#define APP_ANCHOR_ALLOW_TAG_POLLS 1U
#endif

int anchor_app_run(void)
{
    static const uint8_t anchor_peer_ids_static[] = {
        APP_ANCHOR_PEER_0_ID,
        APP_ANCHOR_PEER_1_ID,
        APP_ANCHOR_PEER_2_ID,
    };
    uint8_t anchor_peer_ids[UWB_MAX_ANCHORS];
    const uint8_t *anchor_peer_ids_ptr = anchor_peer_ids_static;
    size_t peer_count = APP_ANCHOR_PEER_COUNT;
    int ret = uwb_hw_bringup_and_init();
    if (ret) {
        return ret;
    }

    printk("Anchor app ready anchor_id=%u master=%u\n", APP_ANCHOR_ID,
           APP_ANCHOR_MASTER);

    if (APP_ANCHOR_MASTER != 0U) {
        if (APP_ANCHOR_USE_AUTO_SCHEDULE != 0U) {
            if (APP_ANCHOR_SCHEDULE_MODE == 2U) {
                peer_count = uwb_anchor_schedule_all_except_self(
                    APP_ANCHOR_ID, anchor_peer_ids, sizeof(anchor_peer_ids));
            } else {
                peer_count = uwb_anchor_schedule_upper_triangle(
                    APP_ANCHOR_ID, anchor_peer_ids, sizeof(anchor_peer_ids));
            }
            anchor_peer_ids_ptr = anchor_peer_ids;

            printk("Anchor master auto schedule %c mode=%u peer_count=%u\n",
                   uwb_anchor_label(APP_ANCHOR_ID),
                   (unsigned int)APP_ANCHOR_SCHEDULE_MODE,
                   (unsigned int)peer_count);
        }

        ret = ss_twr_anchor_init_start(APP_ANCHOR_ID, anchor_peer_ids_ptr,
                                       peer_count);
        if (ret) {
            printk("ss_twr_anchor_init_start failed: %d\n", ret);
            return ret;
        }
        return 0;
    }

    ret = ss_twr_resp_start(APP_ANCHOR_ID, APP_ANCHOR_ALLOW_TAG_POLLS);
    if (ret) {
        printk("ss_twr_resp_start failed: %d\n", ret);
        return ret;
    }

    return 0;
}
