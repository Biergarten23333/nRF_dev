#include "anchor_mcumgr_diag.h"

#include <errno.h>

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#if defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS)
#include <zephyr/mgmt/mcumgr/mgmt/callbacks.h>
#endif

#if defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS)
static volatile bool g_anchor_ota_active;

static const char *smp_event_name(uint32_t event)
{
    switch (event) {
    case MGMT_EVT_OP_CMD_RECV:
        return "CMD_RECV";
    case MGMT_EVT_OP_CMD_STATUS:
        return "CMD_STATUS";
    case MGMT_EVT_OP_CMD_DONE:
        return "CMD_DONE";
    default:
        return "CMD_OTHER";
    }
}

static enum mgmt_cb_return smp_cb(uint32_t event, enum mgmt_cb_return prev_status,
                                  int32_t *rc, uint16_t *group, bool *abort_more, void *data,
                                  size_t data_size)
{
    const struct mgmt_evt_op_cmd_arg *arg = data;
    int32_t rc_val = (rc != NULL) ? *rc : 0;
    uint16_t grp_val = (group != NULL) ? *group : 0U;

    ARG_UNUSED(prev_status);
    ARG_UNUSED(rc);
    ARG_UNUSED(group);
    ARG_UNUSED(abort_more);
    ARG_UNUSED(data_size);

    if (arg != NULL) {
        printk("MCUMGR_SMP_EVT %s grp=0x%04x id=%u val=%d rc=%d evt_grp=0x%04x\n",
               smp_event_name(event), (unsigned int)arg->group, (unsigned int)arg->id,
               (int)arg->op, (int)rc_val, (unsigned int)grp_val);
    } else {
        printk("MCUMGR_SMP_EVT %s (no-arg) rc=%d evt_grp=0x%04x\n",
               smp_event_name(event), (int)rc_val, (unsigned int)grp_val);
    }

    return MGMT_CB_OK;
}

#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
static const char *img_event_name(uint32_t event)
{
    switch (event) {
    case MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK:
        return "DFU_CHUNK";
    case MGMT_EVT_OP_IMG_MGMT_DFU_STOPPED:
        return "DFU_STOPPED";
    case MGMT_EVT_OP_IMG_MGMT_DFU_STARTED:
        return "DFU_STARTED";
    case MGMT_EVT_OP_IMG_MGMT_DFU_PENDING:
        return "DFU_PENDING";
    case MGMT_EVT_OP_IMG_MGMT_DFU_CONFIRMED:
        return "DFU_CONFIRMED";
    case MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK_WRITE_COMPLETE:
        return "DFU_CHUNK_WRITE_COMPLETE";
    case MGMT_EVT_OP_IMG_MGMT_IMAGE_SLOT_STATE:
        return "IMAGE_SLOT_STATE";
    default:
        return "IMG_OTHER";
    }
}

static enum mgmt_cb_return img_cb(uint32_t event, enum mgmt_cb_return prev_status,
                                  int32_t *rc, uint16_t *group, bool *abort_more, void *data,
                                  size_t data_size)
{
    ARG_UNUSED(prev_status);
    ARG_UNUSED(rc);
    ARG_UNUSED(group);
    ARG_UNUSED(abort_more);
    ARG_UNUSED(data);
    ARG_UNUSED(data_size);

    if (event == MGMT_EVT_OP_IMG_MGMT_DFU_STARTED ||
        event == MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK ||
        event == MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK_WRITE_COMPLETE) {
        g_anchor_ota_active = true;
    } else if (event == MGMT_EVT_OP_IMG_MGMT_DFU_STOPPED ||
               event == MGMT_EVT_OP_IMG_MGMT_DFU_PENDING ||
               event == MGMT_EVT_OP_IMG_MGMT_DFU_CONFIRMED) {
        g_anchor_ota_active = false;
    }

    printk("MCUMGR_IMG_EVT %s\n", img_event_name(event));
    return MGMT_CB_OK;
}
#endif

static struct mgmt_callback smp_evt_cb = {
    .callback = smp_cb,
    .event_id = MGMT_EVT_OP_CMD_ALL,
};

#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
static struct mgmt_callback img_evt_cb = {
    .callback = img_cb,
    .event_id = MGMT_EVT_OP_IMG_MGMT_ALL,
};
#endif
#endif

int anchor_mcumgr_diag_init(void)
{
#if !defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS)
    return -ENOTSUP;
#else
    g_anchor_ota_active = false;
    mgmt_callback_register(&smp_evt_cb);
#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
    mgmt_callback_register(&img_evt_cb);
#endif
    printk("anchor mcumgr diag callbacks registered\n");
    return 0;
#endif
}

bool anchor_mcumgr_diag_ota_active(void)
{
#if !defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS) || \
    !defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
    return false;
#else
    return g_anchor_ota_active;
#endif
}
